"""Support for APRS device tracking."""

from typing import Any, override

import probatio

from homeassistant.components.device_tracker import (
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
from homeassistant.helpers.restore_state import RestoreEntity
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

    if result["type"] is FlowResultType.ABORT and result["reason"] in (
        "cannot_connect",
        "invalid_auth",
    ):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"yaml_import_{result['reason']}",
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
    assert isinstance(receiver, AprsReceiver)
    assert entry.unique_id
    unique_id = entry.unique_id
    prefix = f"{unique_id}_"
    entities: dict[str, AprsTrackerEntity] = {
        name: AprsTrackerEntity(unique_id, name)
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if (name := registry_entry.unique_id.removeprefix(prefix))
    }
    async_add_entities(entities.values())

    @callback
    def _async_station_heard(station: AprsStation) -> None:
        if (entity := entities.get(station.name)) is None:
            entity = entities[station.name] = AprsTrackerEntity(
                unique_id, station.name, station
            )
            async_add_entities([entity])
            return
        entity.async_update_station(station)

    entry.async_on_unload(receiver.async_add_listener(_async_station_heard))


class AprsTrackerEntity(TrackerEntity, RestoreEntity):
    """The last position heard from an APRS station or object."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(
        self, entry_unique_id: str, name: str, station: AprsStation | None = None
    ) -> None:
        """Initialize the tracker."""
        self._attr_unique_id = f"{entry_unique_id}_{name}"
        self._device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=name,
            configuration_url=f"https://aprs.fi/info/a/{name}",
        )
        self._name = name
        self._station = station

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last position when nothing has been heard yet."""
        await super().async_added_to_hass()
        if (
            self._station is not None
            or (state := await self.async_get_last_state()) is None
        ):
            return
        attributes = state.attributes
        if ATTR_LATITUDE not in attributes or ATTR_LONGITUDE not in attributes:
            return
        self._station = AprsStation(
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
    def device_info(self) -> DeviceInfo:
        """Return a device per station."""
        return self._device_info

    @callback
    def async_update_station(self, station: AprsStation) -> None:
        """Update with a newly heard position."""
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
