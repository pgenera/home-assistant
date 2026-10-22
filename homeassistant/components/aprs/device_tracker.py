"""Support for APRS device tracking."""

import contextlib
from dataclasses import asdict
from typing import Any, override
from urllib.parse import quote

import probatio

from homeassistant.components.device_tracker import (
    DOMAIN as DEVICE_TRACKER_DOMAIN,
    PLATFORM_SCHEMA as DEVICE_TRACKER_PLATFORM_SCHEMA,
    AsyncSeeCallback,
    TrackerEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import AprsConfigEntry
from .const import (
    CONF_CALLSIGN,
    CONF_CALLSIGNS,
    DEFAULT_HOST,
    DOMAIN,
    RECEIVE_ONLY_PASSCODE,
)
from .receiver import EXTRA_ATTRIBUTES, AprsReceiver, AprsStation

PARALLEL_UPDATES = 0

DEFAULT_TIMEOUT = 30.0

PLATFORM_SCHEMA = DEVICE_TRACKER_PLATFORM_SCHEMA.extend(
    {
        probatio.Required(CONF_CALLSIGNS): probatio.EnsureList(),
        probatio.Required(CONF_USERNAME): cv.string,
        probatio.Optional(
            probatio.Secret(CONF_PASSWORD), default=RECEIVE_ONLY_PASSCODE
        ): cv.string,
        probatio.Optional(CONF_HOST, default=DEFAULT_HOST): cv.string,
        probatio.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): probatio.Coerce(
            float
        ),
    }
)


async def async_setup_scanner(
    hass: HomeAssistant,
    config: ConfigType,
    _async_see: AsyncSeeCallback,
    _discovery_info: DiscoveryInfoType | None = None,
) -> bool:
    """Import the YAML configuration into a receiver config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_IMPORT},
        data={
            CONF_CALLSIGN: config[CONF_USERNAME],
            CONF_PASSWORD: config[CONF_PASSWORD],
            CONF_HOST: config[CONF_HOST],
            CONF_CALLSIGNS: config[CONF_CALLSIGNS],
        },
    )

    if result["type"] is FlowResultType.ABORT and result["reason"] not in (
        "already_configured",
        "already_in_progress",
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"yaml_import_{result['reason']}_{config[CONF_USERNAME]}",
            is_fixable=False,
            issue_domain=DOMAIN,
            severity=ir.IssueSeverity.ERROR,
            translation_key=f"yaml_import_{result['reason']}",
            translation_placeholders={
                CONF_CALLSIGN: config[CONF_USERNAME],
                CONF_HOST: config[CONF_HOST],
            },
        )
        return False

    ir.async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        is_fixable=False,
        issue_domain=DOMAIN,
        severity=ir.IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={"domain": DOMAIN, "integration_title": "APRS"},
    )
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AprsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Track the stations a receiver hears."""
    receiver = entry.runtime_data
    assert entry.unique_id
    unique_id = entry.unique_id
    prefix = f"{unique_id}_"
    entity_registry = er.async_get(hass)
    entities: dict[str, AprsTrackerEntity] = {}

    @callback
    def _async_add(name: str, station: AprsStation | None = None) -> None:
        entity = entities[name] = AprsTrackerEntity(receiver, unique_id, name, station)

        @callback
        def _async_forget() -> None:
            if entities.get(name) is entity:
                del entities[name]

        # Also called if the entity is not added, such as when it is disabled.
        entity.async_on_remove(_async_forget)
        async_add_entities([entity])

    for registry_entry in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if not registry_entry.disabled and (
            name := registry_entry.unique_id.removeprefix(prefix)
        ):
            _async_add(name)

    @callback
    def _async_station_heard(station: AprsStation) -> None:
        # Known stations update their own tracker.
        if station.name in entities:
            return
        if (
            entity_id := entity_registry.async_get_entity_id(
                DEVICE_TRACKER_DOMAIN, DOMAIN, f"{prefix}{station.name}"
            )
        ) is not None and entity_registry.entities[entity_id].disabled:
            return
        _async_add(station.name, station)

    entry.async_on_unload(receiver.async_add_listener(_async_station_heard))


class AprsTrackerEntity(TrackerEntity, RestoreEntity):
    """The last position heard from an APRS station or object."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(
        self,
        receiver: AprsReceiver,
        entry_unique_id: str,
        name: str,
        station: AprsStation | None = None,
    ) -> None:
        """Initialize the tracker."""
        self._receiver = receiver
        self._attr_unique_id = f"{entry_unique_id}_{name}"
        self._device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=name,
            configuration_url=f"https://aprs.fi/info/a/{quote(name)}",
        )
        self._name = name
        self._station = station

    @override
    async def async_added_to_hass(self) -> None:
        """Follow the station, restoring its last position if not heard yet."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._receiver.async_add_listener(self._async_station_heard, self._name)
        )
        self.async_on_remove(
            self._receiver.async_add_connection_listener(self.async_write_ha_state)
        )
        if self._station is None:
            self._station = await self._async_restore_station()

    async def _async_restore_station(self) -> AprsStation | None:
        # An unavailable state has no position, so it is kept separately.
        if (extra_data := await self.async_get_last_extra_data()) is not None:
            # Data saved with other fields falls back to the state.
            with contextlib.suppress(TypeError):
                return AprsStation(**extra_data.as_dict())
        if (state := await self.async_get_last_state()) is None:
            return None
        attributes = state.attributes
        if ATTR_LATITUDE not in attributes or ATTR_LONGITUDE not in attributes:
            return None
        return AprsStation(
            name=self._name,
            latitude=attributes[ATTR_LATITUDE],
            longitude=attributes[ATTR_LONGITUDE],
            gps_accuracy=attributes.get(ATTR_GPS_ACCURACY, 0),
            attributes={
                attr: attributes[attr]
                for attr in EXTRA_ATTRIBUTES
                if attr in attributes
            },
        )

    @property
    @override
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Return the last position heard, to restore after a restart."""
        return (
            None if self._station is None else RestoredExtraData(asdict(self._station))
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the receiver is connected."""
        return self._receiver.connected

    @property
    @override
    def device_info(self) -> DeviceInfo:
        """Return a device per station."""
        return self._device_info

    @callback
    def _async_station_heard(self, station: AprsStation) -> None:
        self._station = station
        self.async_write_ha_state()

    @property
    @override
    def latitude(self) -> float | None:
        """Return the latitude."""
        return None if self._station is None else self._station.latitude

    @property
    @override
    def longitude(self) -> float | None:
        """Return the longitude."""
        return None if self._station is None else self._station.longitude

    @property
    @override
    def location_accuracy(self) -> float:
        """Return the accuracy implied by the position ambiguity."""
        return 0 if self._station is None else self._station.gps_accuracy

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return altitude, course, speed and comment when reported."""
        return None if self._station is None else self._station.attributes
