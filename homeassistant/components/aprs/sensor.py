"""Sensor for the time of the last APRS-IS report."""

from datetime import datetime
from typing import override

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AprsConfigEntry
from .const import DOMAIN
from .publisher import AprsPublisher

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AprsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the last report sensor."""
    async_add_entities([AprsLastReportSensor(entry)])


class AprsLastReportSensor(SensorEntity):
    """When the station last reported to APRS-IS."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "last_report"

    def __init__(self, entry: AprsConfigEntry) -> None:
        """Initialize the sensor."""
        assert isinstance(entry.runtime_data, AprsPublisher)
        self._publisher = entry.runtime_data
        assert entry.unique_id
        self._attr_unique_id = f"{entry.unique_id}_last_report"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://aprs.fi/info/a/{entry.unique_id}",
        )

    @override
    async def async_added_to_hass(self) -> None:
        """Update when a report is sent."""
        self.async_on_remove(
            self._publisher.async_add_listener(self.async_write_ha_state)
        )

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the time of the last report."""
        return self._publisher.last_report
