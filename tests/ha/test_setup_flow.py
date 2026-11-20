"""The integration as a user meets it: add it, enter the credentials, get connected."""

from __future__ import annotations

import asyncio

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.ha_gateway.diagnostics import async_get_config_entry_diagnostics

DOMAIN = "ha_gateway"
SENSOR = "binary_sensor.home_gw_test_gateway_connection"


def form(gateway, token: str, **overrides) -> dict:
    return {
        "address": gateway.address,
        "token": token,
        "use_tls": False,
        "local_url": "http://127.0.0.1:8123",
        "forward_client_ip": True,
        **overrides,
    }


async def wait_until(hass: HomeAssistant, condition, what: str, timeout: float = 15) -> None:
    deadline = hass.loop.time() + timeout
    while not condition():
        if hass.loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()


def sensor_state(hass: HomeAssistant) -> str | None:
    entity_id = er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{entry_of(hass).entry_id}_connection")
    state = hass.states.get(entity_id) if entity_id else None
    return state.state if state else None


def entry_of(hass: HomeAssistant):
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_setup_checks_the_credentials_then_connects(hass: HomeAssistant, gateway) -> None:
    token = gateway.new_token()

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    # A wrong token is refused by the gateway itself, and the form says so on the token field.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form(gateway, "not-the-token"))
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"token": "invalid_auth"}, result

    # Nothing listening there.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], form(gateway, token, address="127.0.0.1:9")
    )
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "cannot_connect"}, result

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], form(gateway, token, local_url="localhost:8123")
    )
    assert result["errors"] == {"local_url": "invalid_url"}, result

    # The right ones, pasted the way people paste: as a URL, with stray spaces.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], form(gateway, f"  {token} ", address=f" http://{gateway.address}/ ")
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["title"] == gateway.host
    assert result["data"]["address"] == gateway.address and result["data"]["token"] == token

    entry = entry_of(hass)
    assert entry.unique_id == gateway.host
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # The tunnel comes up, the sensor follows, and the gateway sees this Home Assistant.
    await wait_until(hass, lambda: sensor_state(hass) == "on", "the connection sensor to turn on")
    agent = gateway.agent()
    assert agent["connected"] and agent["agent_version"] == "0.2.0" and agent["ha_version"], agent

    # Removing the integration takes the home off the line.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    for _ in range(50):
        if not gateway.agent()["connected"]:
            break
        await asyncio.sleep(0.1)
    assert gateway.agent() == {"connected": False}


async def test_a_replaced_token_asks_for_the_new_one(hass: HomeAssistant, gateway) -> None:
    token = gateway.new_token()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form(gateway, token))
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await wait_until(hass, lambda: sensor_state(hass) == "on", "the first connection")

    # The user replaces the token in the manager: the gateway drops the tunnel
    # and refuses the old token, and Home Assistant asks for the new one.
    new_token = gateway.new_token()
    await wait_until(hass, lambda: sensor_state(hass) == "off", "the sensor to turn off")

    def reauth_flows():
        return [f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == SOURCE_REAUTH]

    await wait_until(hass, lambda: len(reauth_flows()) == 1, "the request for a new token")
    flow = reauth_flows()[0]
    assert flow["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"token": token})  # the old one again
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"token": "invalid_auth"}, result

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"token": new_token})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful", result
    await hass.async_block_till_done()

    entry = entry_of(hass)
    assert entry.data["token"] == new_token
    await wait_until(hass, lambda: sensor_state(hass) == "on", "the connection to come back")
    assert gateway.agent()["connected"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_diagnostics_never_contain_the_token(hass: HomeAssistant, gateway) -> None:
    token = gateway.new_token()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form(gateway, token))
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()

    entry = entry_of(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert token not in str(diagnostics), diagnostics
    assert diagnostics["account_host"] == gateway.host and diagnostics["settings"]["address"] == gateway.address

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
