"""gRPC tunnel client.

Dials out to the public gateway, receives the HTTP requests it captured and
replays them against the local Home Assistant HTTP API.

This module deliberately does not import ``homeassistant`` so it can be run and
tested on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import random

import aiohttp
import grpc
from multidict import CIMultiDict
from yarl import URL

from . import tunnel_pb2 as pb

_LOGGER = logging.getLogger(__name__)

_CONNECT_METHOD = "/hagateway.v1.Tunnel/Connect"
_AUTHENTICATE_METHOD = "/hagateway.v1.Tunnel/Authenticate"

# Must stay below the gateway's limit (32 MiB per gRPC message).
_MAX_MESSAGE_BYTES = 32 * 1024 * 1024
_MAX_RESPONSE_BODY_BYTES = 24 * 1024 * 1024

_MAX_BACKOFF_SECONDS = 60

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Never taken from the remote caller: aiohttp computes the first two, and the
# forwarding headers are only ever set by us from what the gateway observed.
_DROPPED_REQUEST_HEADERS = _HOP_BY_HOP | {
    "content-length",
    "host",
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
}
_DROPPED_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length"}


class CannotConnect(Exception):
    """The gateway could not be reached at that address."""


class InvalidToken(Exception):
    """The gateway is there but does not accept the token."""


class UnsupportedGateway(Exception):
    """Something answers gRPC there, but it is not a gateway this version can talk to."""


async def authenticate(address: str, token: str, use_tls: bool, timeout: float = 10) -> str:
    """Check an address and token against the gateway.

    Returns the hostname the token's owner is served on. Opens no tunnel, so it
    never displaces an integration that is already connected.
    """
    try:
        async with open_channel(address, use_tls) as channel:
            call = channel.unary_unary(
                _AUTHENTICATE_METHOD,
                request_serializer=pb.AuthenticateRequest.SerializeToString,
                response_deserializer=pb.AuthenticateResponse.FromString,
            )
            reply = await call(
                pb.AuthenticateRequest(),
                metadata=(("authorization", f"Bearer {token}"),),
                timeout=timeout,
            )
    except grpc.aio.AioRpcError as err:
        if err.code() == grpc.StatusCode.UNAUTHENTICATED:
            raise InvalidToken from err
        if err.code() == grpc.StatusCode.UNIMPLEMENTED:
            raise UnsupportedGateway from err
        raise CannotConnect(f"{err.code().name}: {err.details()}") from err
    return reply.host


class _TunnelClosed(Exception):
    """The tunnel stream ended with a gRPC error."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(f"{code.name}: {details}")
        self.code = code
        self.details = details


class TunnelClient:
    """Keeps the tunnel to the gateway open and replays incoming requests."""

    def __init__(
        self,
        *,
        address: str,
        token: str,
        use_tls: bool,
        local_url: str,
        session: aiohttp.ClientSession,
        forward_client_ip: bool = True,
        agent_version: str = "",
        ha_version: str = "",
        request_timeout: float = 25,
        max_concurrency: int = 32,
        on_state: Callable[[bool], None] | None = None,
        on_token_rejected: Callable[[], None] | None = None,
    ) -> None:
        """Initialize.

        ``session`` must be created with ``auto_decompress=False`` so response
        bodies are relayed byte for byte, and should not keep cookies.
        """
        self._address = address
        self._token = token
        self._use_tls = use_tls
        self._local_url = local_url.rstrip("/")
        self._session = session
        self._forward_client_ip = forward_client_ip
        self._hello = pb.Hello(agent_version=agent_version, ha_version=ha_version)
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._on_state = on_state
        self._on_token_rejected = on_token_rejected
        self.connected = False

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            if self._on_state:
                self._on_state(connected)

    def _channel(self) -> grpc.aio.Channel:
        return open_channel(self._address, self._use_tls)

    async def run(self) -> None:
        """Run until cancelled, reconnecting with exponential backoff."""
        backoff = 1.0
        while True:
            try:
                await self._serve_once()
                _LOGGER.warning("Gateway closed the tunnel, reconnecting")
                backoff = 1.0
            except _TunnelClosed as err:
                if self.connected:
                    backoff = 1.0
                if err.code == grpc.StatusCode.UNAUTHENTICATED:
                    _LOGGER.error("Gateway rejected the token; check the integration configuration")
                    backoff = _MAX_BACKOFF_SECONDS
                    if self._on_token_rejected:
                        self._on_token_rejected()
                else:
                    _LOGGER.warning("Tunnel to %s lost: %s %s", self._address, err.code.name, err.details)
            finally:
                self._set_connected(False)
            await asyncio.sleep(backoff * random.uniform(0.5, 1.5))
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _serve_once(self) -> None:
        outbox: asyncio.Queue[pb.AgentMessage] = asyncio.Queue()
        tasks: set[asyncio.Task] = set()

        async with self._channel() as channel:
            call = channel.stream_stream(
                _CONNECT_METHOD,
                request_serializer=pb.AgentMessage.SerializeToString,
                response_deserializer=pb.GatewayMessage.FromString,
            )(metadata=(("authorization", f"Bearer {self._token}"),))

            async def writer() -> None:
                # gRPC allows a single concurrent writer per stream.
                while True:
                    await call.write(await outbox.get())

            async def handle(request: pb.HttpRequest) -> None:
                async with self._semaphore:
                    response = await self._replay(request)
                outbox.put_nowait(pb.AgentMessage(response=response))

            writer_task = asyncio.create_task(writer())
            try:
                # The gateway sends its headers once the token is accepted; a
                # rejected token raises here. Nothing is written before that:
                # a write racing the rejection would mask it as a generic error.
                await call.initial_metadata()
                outbox.put_nowait(pb.AgentMessage(hello=self._hello))
                self._set_connected(True)
                _LOGGER.info("Tunnel to gateway %s established", self._address)
                async for message in call:
                    if message.WhichOneof("kind") == "request":
                        task = asyncio.create_task(handle(message.request))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
            except grpc.aio.AioRpcError as err:
                # Whichever operation notices a dead call first may report a
                # generic error; the call's final status is the real reason
                # (e.g. UNAUTHENTICATED).
                code, details = err.code(), err.details()
                try:
                    async with asyncio.timeout(5):
                        code, details = await call.code(), await call.details()
                except (TimeoutError, grpc.aio.AioRpcError):
                    pass
                raise _TunnelClosed(code, details or "") from err
            finally:
                for task in (writer_task, *tasks):
                    task.cancel()
                await asyncio.gather(writer_task, *tasks, return_exceptions=True)

    async def _replay(self, request: pb.HttpRequest) -> pb.HttpResponse:
        """Replay one request on the local instance. Never raises."""
        headers: CIMultiDict[str] = CIMultiDict()
        for header in request.headers:
            if header.name.lower() in _DROPPED_REQUEST_HEADERS:
                continue
            for value in header.values:
                headers.add(header.name, value)
        if self._forward_client_ip and request.remote_addr:
            headers["X-Forwarded-For"] = request.remote_addr
            headers["X-Forwarded-Proto"] = request.scheme or "https"
            if request.host:
                headers["X-Forwarded-Host"] = request.host

        url = self._local_url + request.path
        if request.raw_query:
            url += "?" + request.raw_query

        try:
            async with self._session.request(
                request.method,
                URL(url, encoded=True),
                headers=headers,
                data=request.body or None,
                allow_redirects=False,
                timeout=self._timeout,
                skip_auto_headers=("Accept-Encoding", "User-Agent", "Content-Type"),
            ) as local:
                body = await local.read()
                if len(body) > _MAX_RESPONSE_BODY_BYTES:
                    return pb.HttpResponse(id=request.id, error="response body too large for the tunnel")
                grouped: dict[str, list[str]] = {}
                for name, value in local.headers.items():
                    if name.lower() not in _DROPPED_RESPONSE_HEADERS:
                        grouped.setdefault(name, []).append(value)
                return pb.HttpResponse(
                    id=request.id,
                    status=local.status,
                    headers=[pb.Header(name=n, values=v) for n, v in grouped.items()],
                    body=body,
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Replaying %s %s locally failed: %r", request.method, request.path, err)
            return pb.HttpResponse(id=request.id, error=f"local request failed: {type(err).__name__}")


def open_channel(address: str, use_tls: bool) -> grpc.aio.Channel:
    """Open a channel to the gateway."""
    options = [
        ("grpc.max_send_message_length", _MAX_MESSAGE_BYTES),
        ("grpc.max_receive_message_length", _MAX_MESSAGE_BYTES),
        ("grpc.keepalive_time_ms", 30_000),
        ("grpc.keepalive_timeout_ms", 10_000),
        ("grpc.keepalive_permit_without_calls", 1),
    ]
    if use_tls:
        return grpc.aio.secure_channel(address, grpc.ssl_channel_credentials(), options=options)
    return grpc.aio.insecure_channel(address, options=options)
