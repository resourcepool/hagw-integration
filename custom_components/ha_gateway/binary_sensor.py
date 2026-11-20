"""Whether this Home Assistant is currently on the line with the gateway."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GatewayConfigEntry
from .const import DOMAIN, SIGNAL_STATE


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GatewayConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the connection sensor."""
    async_add_entities([ConnectionSensor(entry)])


class ConnectionSensor(BinarySensorEntity):
    """On while the tunnel to the gateway is up. Useful in automations and alerts."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: GatewayConfigEntry) -> None:
        """Initialize."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Home Assistant Gateway",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        """Return whether the tunnel is up."""
        return self._entry.runtime_data.connected

    async def async_added_to_hass(self) -> None:
        """Follow the tunnel's state."""

        @callback
        def update(_connected: bool) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{SIGNAL_STATE}_{self._entry.entry_id}", update)
        )
