"""Config flow for the Home Assistant Gateway integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import (
    CONF_ADDRESS,
    CONF_FORWARD_CLIENT_IP,
    CONF_LOCAL_URL,
    CONF_TOKEN,
    CONF_USE_TLS,
    DEFAULT_LOCAL_URL,
    DOMAIN,
)
from .tunnel import CannotConnect, InvalidToken, UnsupportedGateway, authenticate

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

# What the gateway's manager shows under "Home connection": address and token.
# The rest has defaults that fit a standard installation.
USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): str,
        vol.Required(CONF_TOKEN): _PASSWORD,
        vol.Required(CONF_USE_TLS, default=True): bool,
        vol.Required(CONF_LOCAL_URL, default=DEFAULT_LOCAL_URL): str,
        vol.Required(CONF_FORWARD_CLIENT_IP, default=True): bool,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_TOKEN): _PASSWORD})


def _clean_address(value: str) -> str:
    """Accept what people paste: a bare host:port, or a URL."""
    address = value.strip()
    for prefix in ("https://", "http://", "grpc://", "grpcs://"):
        address = address.removeprefix(prefix)
    return address.rstrip("/")


async def _check(address: str, token: str, use_tls: bool) -> tuple[str | None, str | None]:
    """Try the gateway. Returns (host of the account, None) or (None, error key)."""
    try:
        return await authenticate(address, token, use_tls), None
    except InvalidToken:
        return None, "invalid_auth"
    except UnsupportedGateway:
        return None, "unsupported_gateway"
    except CannotConnect as err:
        _LOGGER.info("Gateway at %s not reachable: %s", address, err)
        return None, "cannot_connect"
    except Exception:  # noqa: BLE001 - e.g. a malformed address or token rejected by the gRPC library
        _LOGGER.exception("Unexpected error checking the gateway at %s", address)
        return None, "unknown"


class GatewayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the tunnel to a gateway, verifying the address and token first."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the gateway address and token, and try them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._normalise(user_input)
            if not data[CONF_LOCAL_URL].startswith(("http://", "https://")):
                errors[CONF_LOCAL_URL] = "invalid_url"
            else:
                host, error = await _check(data[CONF_ADDRESS], data[CONF_TOKEN], data[CONF_USE_TLS])
                if error:
                    errors[CONF_TOKEN if error == "invalid_auth" else "base"] = error
                else:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=host, data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The gateway rejected the stored token (replaced or revoked in the manager)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the new token only."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            host, error = await _check(entry.data[CONF_ADDRESS], token, entry.data[CONF_USE_TLS])
            if error:
                errors[CONF_TOKEN if error == "invalid_auth" else "base"] = error
            else:
                # A token of another account would silently point this home at someone else's address.
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"host": entry.title},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address or the other settings of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = self._normalise(user_input)
            if not data[CONF_LOCAL_URL].startswith(("http://", "https://")):
                errors[CONF_LOCAL_URL] = "invalid_url"
            else:
                host, error = await _check(data[CONF_ADDRESS], data[CONF_TOKEN], data[CONF_USE_TLS])
                if error:
                    errors[CONF_TOKEN if error == "invalid_auth" else "base"] = error
                else:
                    await self.async_set_unique_id(host)
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(entry, data_updates=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or entry.data),
            errors=errors,
        )

    @staticmethod
    def _normalise(user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            **user_input,
            CONF_ADDRESS: _clean_address(user_input[CONF_ADDRESS]),
            CONF_TOKEN: user_input[CONF_TOKEN].strip(),
            CONF_LOCAL_URL: user_input[CONF_LOCAL_URL].strip().rstrip("/"),
        }
