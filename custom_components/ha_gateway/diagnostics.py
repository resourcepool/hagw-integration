"""Diagnostics download for the Home Assistant Gateway integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import GatewayConfigEntry
from .const import CONF_TOKEN, VERSION


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GatewayConfigEntry) -> dict[str, Any]:
    """Return the settings (without the token) and whether the tunnel is up."""
    return {
        "integration_version": VERSION,
        "account_host": entry.title,
        "connected": entry.runtime_data.connected,
        "settings": async_redact_data(dict(entry.data), {CONF_TOKEN}),
    }
