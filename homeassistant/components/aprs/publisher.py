"""Publish position and weather reports to APRS-IS."""

from collections.abc import Mapping
from datetime import datetime
import logging
from typing import Any

from aprslib import IS, ConnectionError as AprsConnectionError, LoginError

from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
    UnitOfIrradiance,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
    split_entity_id,
)
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .const import (
    CONF_COMMENT,
    CONF_HUMIDITY,
    CONF_LOCATION_ENTITY,
    CONF_LUMINOSITY,
    CONF_POSITION_AMBIGUITY,
    CONF_PRESSURE,
    CONF_RAIN_1H,
    CONF_RAIN_24H,
    CONF_RAIN_SINCE_MIDNIGHT,
    CONF_SYMBOL,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_WIND_DIRECTION,
    CONF_WIND_GUST,
    CONF_WIND_SPEED,
    FILTER_PORT,
    MOVING_MIN_INTERVAL,
    POSITION_INTERVAL,
    WEATHER_INTERVAL,
    WEATHER_SYMBOL_CODE,
)
from .packet import WeatherData, build_report, format_position

_LOGGER = logging.getLogger(__name__)

ZONE_DOMAIN = "zone"

# Converter and APRS unit for each weather field. Fields without a converter
# are sent as-is when they are in the given unit (or have no unit at all).
WEATHER_UNITS: dict[str, tuple[type[BaseUnitConverter] | None, str | None]] = {
    CONF_TEMPERATURE: (TemperatureConverter, UnitOfTemperature.FAHRENHEIT),
    CONF_HUMIDITY: (None, None),
    CONF_PRESSURE: (PressureConverter, UnitOfPressure.HPA),
    CONF_WIND_DIRECTION: (None, None),
    CONF_WIND_SPEED: (SpeedConverter, UnitOfSpeed.MILES_PER_HOUR),
    CONF_WIND_GUST: (SpeedConverter, UnitOfSpeed.MILES_PER_HOUR),
    CONF_RAIN_1H: (DistanceConverter, UnitOfLength.INCHES),
    CONF_RAIN_24H: (DistanceConverter, UnitOfLength.INCHES),
    CONF_RAIN_SINCE_MIDNIGHT: (DistanceConverter, UnitOfLength.INCHES),
    CONF_LUMINOSITY: (None, UnitOfIrradiance.WATTS_PER_SQUARE_METER),
}


class AprsIsClient:
    """Short-lived APRS-IS connections for sending packets."""

    def __init__(self, callsign: str, passcode: str, host: str) -> None:
        """Initialize the client."""
        self.callsign = callsign
        self._passcode = passcode
        self._host = host

    def _connect(self) -> IS:
        ais = IS(
            self.callsign, passwd=self._passcode, host=self._host, port=FILTER_PORT
        )
        ais.connect()
        return ais

    def check_login(self) -> None:
        """Log in and disconnect, raising if the server refuses us."""
        self._connect().close()

    def send(self, packet: str) -> None:
        """Send one packet.

        A connection per report means we never hold a socket whose server
        keepalives nobody reads.
        """
        ais = self._connect()
        try:
            ais.sendall(packet)
        finally:
            ais.close()


class AprsPublisher:
    """Build reports from Home Assistant state and send them on a schedule."""

    def __init__(
        self, hass: HomeAssistant, client: AprsIsClient, options: Mapping[str, Any]
    ) -> None:
        """Initialize the publisher."""
        self.hass = hass
        self.client = client
        self.last_report: datetime | None = None
        self._location_entity: str = options[CONF_LOCATION_ENTITY]
        self._symbol: str = options[CONF_SYMBOL]
        self._comment: str = options.get(CONF_COMMENT, "")
        self._ambiguity = int(options[CONF_POSITION_AMBIGUITY])
        self._weather_entities: dict[str, str] = {
            key: entity_id
            for key, entity_id in options.get(CONF_WEATHER, {}).items()
            if entity_id
        }
        self._last_attempt: datetime | None = None
        self._failing = False
        self._listeners: list[CALLBACK_TYPE] = []

    @property
    def sends_weather(self) -> bool:
        """Return whether reports carry weather data."""
        return self._symbol[1] == WEATHER_SYMBOL_CODE

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start sending reports; returns a callback that stops it."""
        unsubscribers = [
            async_track_time_interval(
                self.hass,
                self._async_send_scheduled,
                WEATHER_INTERVAL if self.sends_weather else POSITION_INTERVAL,
                name="APRS-IS report",
                cancel_on_shutdown=True,
            ),
            async_at_started(self.hass, self._async_send_scheduled),
        ]
        if split_entity_id(self._location_entity)[0] != ZONE_DOMAIN:
            unsubscribers.append(
                async_track_state_change_event(
                    self.hass, self._location_entity, self._async_location_changed
                )
            )

        @callback
        def stop() -> None:
            for unsubscribe in unsubscribers:
                unsubscribe()

        return stop

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Call update_callback after each report is sent."""
        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(update_callback)

        return remove_listener

    async def _async_send_scheduled(self, *_: Any) -> None:
        await self.async_send_report()

    async def _async_location_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        if new_state is None or (
            old_state is not None
            and old_state.attributes.get(ATTR_LATITUDE)
            == new_state.attributes.get(ATTR_LATITUDE)
            and old_state.attributes.get(ATTR_LONGITUDE)
            == new_state.attributes.get(ATTR_LONGITUDE)
        ):
            return
        if (
            self._last_attempt is not None
            and dt_util.utcnow() - self._last_attempt < MOVING_MIN_INTERVAL
        ):
            return
        await self.async_send_report()

    def _read_weather(self, key: str) -> float | None:
        if (entity_id := self._weather_entities.get(key)) is None or (
            state := self.hass.states.get(entity_id)
        ) is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        converter, aprs_unit = WEATHER_UNITS[key]
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if converter is None:
            return value if aprs_unit is None or unit == aprs_unit else None
        if unit not in converter.VALID_UNITS:
            _LOGGER.debug("Ignoring %s: cannot convert from %s", entity_id, unit)
            return None
        return converter.convert(value, unit, aprs_unit)

    def build_report(self, now: datetime) -> str | None:
        """Build the report for the current state, or None without a location."""
        if (state := self.hass.states.get(self._location_entity)) is None or (
            ATTR_LATITUDE not in state.attributes
            or ATTR_LONGITUDE not in state.attributes
        ):
            return None
        position = format_position(
            state.attributes[ATTR_LATITUDE],
            state.attributes[ATTR_LONGITUDE],
            self._symbol,
            self._ambiguity,
        )
        weather = (
            WeatherData(**{key: self._read_weather(key) for key in WEATHER_UNITS})
            if self.sends_weather
            else None
        )
        return build_report(self.client.callsign, now, position, weather, self._comment)

    async def async_send_report(self) -> None:
        """Send a report now."""
        now = dt_util.utcnow()
        self._last_attempt = now
        if (packet := self.build_report(now)) is None:
            self._set_failing(f"{self._location_entity} has no location")
            return
        try:
            await self.hass.async_add_executor_job(self.client.send, packet)
        except (AprsConnectionError, LoginError) as err:
            self._set_failing(str(err))
            return
        _LOGGER.debug("Sent %s", packet)
        if self._failing:
            _LOGGER.info("Sending APRS-IS reports for %s again", self.client.callsign)
            self._failing = False
        self.last_report = now
        for update_callback in list(self._listeners):
            update_callback()

    def _set_failing(self, reason: str) -> None:
        if not self._failing:
            _LOGGER.warning(
                "Could not send APRS-IS report for %s: %s",
                self.client.callsign,
                reason,
            )
        self._failing = True
