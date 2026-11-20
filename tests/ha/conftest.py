"""Runs the integration inside a real Home Assistant, against a real gateway binary.

    pytest (Home Assistant core, in process) --gRPC--> gateway binary (subprocess)

Usage: GATEWAY_BINARY=/path/to/gateway make test
(defaults to ../home-assistant-gateway/.bin/gateway, built by `make build` there)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

import grpc
import pytest
import pytest_socket

# Imported before any test runs: Home Assistant's test harness ships a
# `custom_components` package of its own, and the first one imported wins.
import custom_components.ha_gateway  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
ADMIN_TOKEN = "ha-test-admin-token-0123456789abcdef0123"
BASE = "gw.test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Gateway:
    """A running gateway with one account, and its admin API."""

    def __init__(self, grpc_port: int, admin_port: int) -> None:
        self.address = f"127.0.0.1:{grpc_port}"
        self.host = f"home.{BASE}"
        self._admin = f"http://127.0.0.1:{admin_port}"
        self.user_id = 0

    def admin(self, method: str, path: str, body: dict | None = None):
        request = urllib.request.Request(
            self._admin + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def new_token(self) -> str:
        """Create (or replace) the account's token, as the manager does."""
        return self.admin("POST", f"/users/{self.user_id}/agent/token")["token"]

    def agent(self) -> dict:
        return self.admin("GET", f"/users/{self.user_id}/status")["agent"]


@pytest.fixture(scope="session")
def gateway(tmp_path_factory: pytest.TempPathFactory):
    # The Home Assistant test harness blocks sockets before fixtures run, and
    # this session fixture is set up ahead of the per-test one that allows them.
    pytest_socket.enable_socket()
    # gRPC starts one process-wide poller thread on first use and keeps it for
    # the life of the process. Started here, it predates the harness's
    # per-test check for threads a test left behind.
    grpc.aio.init_grpc_aio()
    binary = Path(os.environ.get("GATEWAY_BINARY", ROOT.parent / "home-assistant-gateway" / ".bin" / "gateway"))
    if not binary.exists():
        pytest.fail(f"gateway binary missing at {binary}: build the gateway, or set GATEWAY_BINARY")
    grpc_port, admin_port = _free_port(), _free_port()
    process = subprocess.Popen(
        [str(binary)],
        env={
            **os.environ,
            "BASE_DOMAIN": BASE,
            "ADMIN_TOKEN": ADMIN_TOKEN,
            "SUPERADMIN_PASSWORD": "ha-test-password-1234",
            "DB_PATH": str(tmp_path_factory.mktemp("gateway") / "gateway.db"),
            "DATABASE_URL": "",
            "LOCAL_CERT_URL": "",
            "LOCAL_CERT_API_KEY": "",
            "MANAGER_HOST": "",
            "MANAGER_UPSTREAM": "",
            "GRPC_ADDR": f"127.0.0.1:{grpc_port}",
            "ADMIN_ADDR": f"127.0.0.1:{admin_port}",
            "HTTP_ADDR": f"127.0.0.1:{_free_port()}",
        },
        stdout=subprocess.DEVNULL,
    )
    running = Gateway(grpc_port, admin_port)
    try:
        for _ in range(100):
            try:
                running.user_id = running.admin("GET", "/users")[0]["id"]
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.05)
        else:
            pytest.fail("the gateway did not start")
        yield running
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ha_gateway."""


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled):
    """These tests talk to a real gateway on the loopback interface."""
