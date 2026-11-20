"""Home Assistant Gateway: replay calls captured by a public gateway on this instance."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ADDRESS,
    CONF_FORWARD_CLIENT_IP,
    CONF_LOCAL_URL,
    CONF_TOKEN,
    CONF_USE_TLS,
    SIGNAL_STATE,
    VERSION,
)
from .tunnel import TunnelClient

PLATFORMS = [Platform.BINARY_SENSOR]

type GatewayConfigEntry = ConfigEntry[TunnelClient]


async def async_setup_entry(hass: HomeAssistant, entry: GatewayConfigEntry) -> bool:
    """Open the tunnel to the gateway.

    Setup does not wait for the gateway: the tunnel connects in the background
    and keeps reconnecting, so Home Assistant starts normally when the gateway
    or the internet is down. The connection sensor shows where it stands.
    """

    @callback
    def state_changed(connected: bool) -> None:
        async_dispatcher_send(hass, f"{SIGNAL_STATE}_{entry.entry_id}", connected)

    @callback
    def token_rejected() -> None:
        # Asks the user for a new token (Settings > Devices & services).
        entry.async_start_reauth(hass)

    # Dedicated session: bodies must be relayed undecoded and one remote
    # caller's cookies must never leak into another caller's request.
    session = async_create_clientsession(
        hass, auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    client = TunnelClient(
        address=entry.data[CONF_ADDRESS],
        token=entry.data[CONF_TOKEN],
        use_tls=entry.data[CONF_USE_TLS],
        local_url=entry.data[CONF_LOCAL_URL],
        forward_client_ip=entry.data[CONF_FORWARD_CLIENT_IP],
        session=session,
        agent_version=VERSION,
        ha_version=HA_VERSION,
        on_state=state_changed,
        on_token_rejected=token_rejected,
    )
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Cancelled automatically when the entry is unloaded.
    entry.async_create_background_task(hass, client.run(), "ha_gateway tunnel")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GatewayConfigEntry) -> bool:
    """Close the tunnel (the background task is cancelled by the entry)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
