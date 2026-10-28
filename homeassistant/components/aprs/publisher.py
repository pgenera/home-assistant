"""Publish position and weather reports to APRS-IS."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
from typing import Any

from aprslib import ConnectionError as AprsConnectionError, LoginError

from homeassistant.const import (
    ATTR_GPS_ACCURACY,
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
    State,
    callback,
    split_entity_id,
)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.template import Template, result_as_boolean
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import (
    BaseUnitConverter,
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .client import AprsIsClient, passcode_refused
from .const import (
    CONF_COMMENT,
    CONF_HUMIDITY,
    CONF_LOCATION_ENTITY,
    CONF_LUMINOSITY,
    CONF_MOTION_ENTITY,
    CONF_POSITION_AMBIGUITY,
    CONF_PRESSURE,
    CONF_RAIN_1H,
    CONF_RAIN_24H,
    CONF_RAIN_SINCE_MIDNIGHT,
    CONF_REPORT_INTERVAL,
    CONF_SEND_ALTITUDE,
    CONF_SMART_BEACONING,
    CONF_SUPPRESS_TEMPLATE,
    CONF_SYMBOL,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_WIND_DIRECTION,
    CONF_WIND_GUST,
    CONF_WIND_SPEED,
    MAX_SPEED,
    MIN_TURN_TIME,
    POSITION_INTERVAL,
    WEATHER_INTERVAL,
    WEATHER_SYMBOL_CODE,
    ZONE_DOMAIN,
)
from .packet import WeatherData, build_report, format_course_speed, format_position
from .smartbeacon import (
    METERS_PER_SECOND_TO_MPH,
    STOPPED,
    Fix,
    Motion,
    SpeedHistory,
    beacon_rate,
    travel_speed,
    turned,
)

_LOGGER = logging.getLogger(__name__)

# Device tracker attributes the Companion apps report, in m/s, degrees and m.
ATTR_ALTITUDE = "altitude"
ATTR_COURSE = "course"
ATTR_SPEED = "speed"

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


@dataclass(frozen=True, kw_only=True)
class ReportedMotion:
    """Course, speed and altitude as the device reported them."""

    speed: float | None = None
    """Mph."""
    course: float | None = None
    altitude: float | None = None
    """Meters."""


class AprsPublisher:
    """Build reports from Home Assistant state and send them when due."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AprsIsClient,
        options: Mapping[str, Any],
        passcode_refused_callback: CALLBACK_TYPE,
    ) -> None:
        """Initialize the publisher."""
        self.hass = hass
        self.client = client
        self._passcode_refused_callback = passcode_refused_callback
        self.last_report: datetime | None = None
        self._location_entity: str = options[CONF_LOCATION_ENTITY]
        self._motion_entity: str = (
            options.get(CONF_MOTION_ENTITY) or self._location_entity
        )
        self._send_altitude: bool = options.get(CONF_SEND_ALTITUDE, True)
        self._symbol: str = options[CONF_SYMBOL]
        self._comment: str = options.get(CONF_COMMENT, "")
        self._ambiguity = int(options[CONF_POSITION_AMBIGUITY])
        self._weather_entities: dict[str, str] = {
            key: entity_id
            for key, entity_id in options.get(CONF_WEATHER, {}).items()
            if entity_id
        }
        self._suppress = (
            Template(template, hass)
            if (template := options.get(CONF_SUPPRESS_TEMPLATE))
            else None
        )
        self._moving = split_entity_id(self._location_entity)[0] != ZONE_DOMAIN
        self._smart_beaconing = self._moving and options.get(CONF_SMART_BEACONING, True)
        self._interval = (
            timedelta(minutes=interval)
            if (interval := options.get(CONF_REPORT_INTERVAL))
            else WEATHER_INTERVAL
            if self.sends_weather
            else POSITION_INTERVAL
        )
        self._fix: Fix | None = None
        self._motion = STOPPED
        self._history = SpeedHistory()
        self._reported_fix: Fix | None = None
        self._reported_course: float | None = None
        self._stop_unreported = False
        self._last_attempt: datetime | None = None
        self._failing = False
        self._suppress_failing = False
        self._suppressed = False
        self._listeners: list[CALLBACK_TYPE] = []

    @property
    def sends_weather(self) -> bool:
        """Return whether reports carry weather data."""
        return self._symbol[1] == WEATHER_SYMBOL_CODE

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start sending reports; returns a callback that stops it."""
        unsubscribers: list[CALLBACK_TYPE] = []
        if self._suppress is not None:
            info = async_track_template_result(
                self.hass,
                [TrackTemplate(self._suppress, None)],
                self._async_suppress_changed,
            )
            info.async_refresh()
            unsubscribers.append(info.async_remove)
        unsubscribers.append(async_at_started(self.hass, self._async_send_scheduled))
        if self._smart_beaconing:
            if (state := self.hass.states.get(self._location_entity)) is not None:
                self._fix = _fix_from_state(state)
            unsubscribers.append(
                async_track_state_change_event(
                    self.hass, self._location_entity, self._async_location_changed
                )
            )
        # SmartBeaconing checks often, so a report that is not due yet goes
        # out with the latest position as soon as it is.
        unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._async_evaluate
                if self._smart_beaconing
                else self._async_send_scheduled,
                MIN_TURN_TIME if self._smart_beaconing else self._interval,
                name="APRS-IS report",
                cancel_on_shutdown=True,
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

    def _reported_motion(self) -> ReportedMotion:
        if (
            not self._moving
            or (state := self.hass.states.get(self._motion_entity)) is None
        ):
            return ReportedMotion()
        values = {
            attr: value
            for attr in (ATTR_SPEED, ATTR_COURSE, ATTR_ALTITUDE)
            if isinstance(value := state.attributes.get(attr), int | float)
            and math.isfinite(value)
        }
        if not self._send_altitude:
            values.pop(ATTR_ALTITUDE, None)
        # Some apps report a speed and course of 0 whenever they have none, even
        # while moving, so those are left to be worked out from positions.
        if (speed := values.get(ATTR_SPEED)) is None or speed <= 0:
            return ReportedMotion(altitude=values.get(ATTR_ALTITUDE))
        return ReportedMotion(
            speed=speed * METERS_PER_SECOND_TO_MPH,
            course=values.get(ATTR_COURSE),
            altitude=values.get(ATTR_ALTITUDE),
        )

    async def _async_location_changed(
        self, event: Event[EventStateChangedData]
    ) -> None:
        if (new_state := event.data["new_state"]) is None or (
            fix := _fix_from_state(new_state)
        ) is None:
            return
        reported = self._reported_motion()
        if self._fix is None:
            self._fix = fix
            current = Motion(speed=reported.speed or 0, course=reported.course)
        elif (speed := travel_speed(self._fix, fix)) is None:
            # Within the last fix's accuracy, so distance keeps adding up from
            # it. It has stopped once it would have left that circle at the
            # slow speed.
            if reported.speed is None and fix.time - self._fix.time < max(
                MIN_TURN_TIME,
                timedelta(
                    seconds=max(self._fix.accuracy, fix.accuracy)
                    * METERS_PER_SECOND_TO_MPH
                    / self._history.profile.slow_speed
                ),
            ):
                return
            current = Motion(speed=reported.speed or 0, course=reported.course)
        elif speed > MAX_SPEED:
            # A jump, such as a person switching trackers, is not travel.
            self._fix = fix
            current = STOPPED
        else:
            self._fix = fix
            current = Motion(
                speed=speed if reported.speed is None else reported.speed,
                course=reported.course,
            )
        self._history.add(fix.time, current.speed)
        self._set_motion(current)
        await self._async_evaluate()

    def _set_motion(self, current: Motion) -> None:
        slow_speed = self._history.profile.slow_speed
        if current.speed <= slow_speed:
            # Below the slow speed the stopped rate applies anyway.
            self._stop_unreported |= self._motion.speed > slow_speed
        else:
            self._stop_unreported = False
        self._motion = current

    async def _async_evaluate(self, *_: Any) -> None:
        """Send a report if SmartBeaconing says one is due."""
        if not self.hass.is_running:
            # The first report goes out once Home Assistant has started.
            return
        if self._last_attempt is not None:
            profile = self._history.profile
            elapsed = dt_util.utcnow() - self._last_attempt
            rate = (
                self._interval
                if self._fix == self._reported_fix
                else beacon_rate(self._motion.speed, profile, self._interval)
            )
            # Turning or stopping is worth reporting before the next one is
            # due. A clock that went back must not hold reports off.
            due = not timedelta(0) <= elapsed < rate or (
                elapsed >= MIN_TURN_TIME
                and (
                    self._stop_unreported
                    or turned(self._reported_course, self._motion, profile)
                )
            )
            if not due:
                return
        await self.async_send_report()

    def _suppressed_now(self) -> bool:
        # Rendered again, since the entities it uses may have changed after
        # the location but before the tracked result caught up.
        if self._suppress is None:
            return False
        try:
            return result_as_boolean(self._suppress.async_render(parse_result=False))
        except TemplateError:
            # Not knowing whether to hide the position, keep it hidden.
            return True

    @callback
    def _async_suppress_changed(
        self,
        event: Event[EventStateChangedData] | None,
        updates: list[TrackTemplateResult],
    ) -> None:
        was_suppressed = self._suppressed
        result = updates.pop().result
        if isinstance(result, TemplateError):
            _LOGGER.warning(
                "Not sending APRS-IS reports for %s, the suppress template failed: %s",
                self.client.callsign,
                result,
            )
            self._suppress_failing = self._suppressed = True
        else:
            if self._suppress_failing:
                _LOGGER.info(
                    "The suppress template for %s renders again", self.client.callsign
                )
                self._suppress_failing = False
            self._suppressed = result_as_boolean(result)
        if self._suppressed == was_suppressed:
            return
        _LOGGER.debug(
            "%s APRS-IS reports for %s",
            "Suppressing" if self._suppressed else "Resuming",
            self.client.callsign,
        )
        if not self._suppressed:
            # Report now rather than at the next interval.
            self.hass.async_create_task(
                self._async_evaluate()
                if self._smart_beaconing
                else self.async_send_report()
            )

    def _read_weather(self, key: str) -> float | None:
        if (entity_id := self._weather_entities.get(key)) is None or (
            state := self.hass.states.get(entity_id)
        ) is None:
            return None
        try:
            value = float(state.state)
        except ValueError:
            return None
        if not math.isfinite(value):
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
        # Only what the device reported is sent, never what was worked out.
        reported = self._reported_motion()
        course_speed = (
            format_course_speed(
                reported.course,
                SpeedConverter.convert(
                    reported.speed, UnitOfSpeed.MILES_PER_HOUR, UnitOfSpeed.KNOTS
                ),
            )
            if reported.course is not None and reported.speed
            else ""
        )
        return build_report(
            self.client.callsign,
            now,
            position,
            weather,
            self._comment,
            course_speed,
            reported.altitude,
        )

    async def async_send_report(self) -> None:
        """Send a report now, unless the suppress template says not to."""
        if self._suppressed_now():
            return
        now = dt_util.utcnow()
        if self._fix == self._reported_fix and self._reported_motion().speed is None:
            # Not having moved since the last report means stopped, whatever
            # speed was last worked out.
            self._motion = STOPPED
        if (packet := self.build_report(now)) is None:
            self._set_failing(f"{self._location_entity} has no location")
            return
        self._last_attempt = now
        reported = (self._fix, self._motion.course)
        try:
            await self.hass.async_add_executor_job(self.client.send, packet)
        except AprsConnectionError as err:
            self._set_failing(str(err))
            return
        except LoginError as err:
            if passcode_refused(err):
                self._passcode_refused_callback()
            self._set_failing(str(err))
            return
        self._reported_fix, self._reported_course = reported
        self._stop_unreported = False
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


def _fix_from_state(state: State) -> Fix | None:
    if ATTR_LATITUDE not in state.attributes or ATTR_LONGITUDE not in state.attributes:
        return None
    return Fix(
        latitude=state.attributes[ATTR_LATITUDE],
        longitude=state.attributes[ATTR_LONGITUDE],
        time=state.last_updated,
        accuracy=state.attributes.get(ATTR_GPS_ACCURACY) or 0,
    )
