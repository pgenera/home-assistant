"""Test sending reports to APRS-IS."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.aprs.const import (
    CONF_COMMENT,
    CONF_LOCATION_ENTITY,
    CONF_MOTION_ENTITY,
    CONF_POSITION_AMBIGUITY,
    CONF_REPORT_INTERVAL,
    CONF_SEND_ALTITUDE,
    CONF_SMART_BEACONING,
    CONF_SUPPRESS_TEMPLATE,
    CONF_SYMBOL,
    CONF_WEATHER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
)
from homeassistant.core import CoreState, HomeAssistant

from .conftest import WEATHER_OPTIONS

START = "2026-10-21T09:05:00+00:00"
HOME = {ATTR_LATITUDE: 42.562956, ATTR_LONGITUDE: -70.86915}
CAR = "device_tracker.car"
PHONE = "device_tracker.phone"
LAST_REPORT = "sensor.n0call_13_last_report"

POSITION_OPTIONS = {
    CONF_LOCATION_ENTITY: "zone.home",
    CONF_SYMBOL: "/-",
    CONF_COMMENT: "QTH",
    CONF_POSITION_AMBIGUITY: 2,
    CONF_WEATHER: {},
}
MOVING_OPTIONS = {
    CONF_LOCATION_ENTITY: CAR,
    CONF_SYMBOL: "/>",
    CONF_POSITION_AMBIGUITY: 0,
    CONF_WEATHER: {},
}

WEATHER_STATES = {
    "sensor.temperature": ("10.1", "°C"),
    "sensor.humidity": ("76", "%"),
    "sensor.pressure": ("1023.7", "hPa"),
    "sensor.wind_direction": ("96", "°"),
    "sensor.wind_speed": ("13.3", "km/h"),
    "sensor.wind_gust": ("5.1", "m/s"),
    "sensor.rain_1h": ("0.3", "mm"),
    "sensor.rain_24h": ("12.7", "mm"),
    "sensor.rain_today": ("3.1", "cm"),
    "sensor.irradiance": ("156.59", "W/m²"),
}


@pytest.fixture(autouse=True)
def frozen_time(freezer: FrozenDateTimeFactory) -> None:
    """Freeze time so report timestamps are stable."""
    freezer.move_to(START)


def _set_states(hass: HomeAssistant, states: dict[str, tuple[str, str]]) -> None:
    for entity_id, (state, unit) in states.items():
        hass.states.async_set(entity_id, state, {ATTR_UNIT_OF_MEASUREMENT: unit})


async def _async_setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _async_tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    # Interval reports run as background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)


def _sent(mock_client_is: MagicMock) -> list[str]:
    return [call.args[0] for call in mock_client_is.sendall.call_args_list]


async def test_weather_report(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    snapshot: SnapshotAssertion,
) -> None:
    """Test a weather report is sent on setup, converted to APRS units."""
    hass.states.async_set("zone.home", "0", HOME)
    _set_states(hass, WEATHER_STATES)

    await _async_setup(hass, mock_station_entry)

    assert _sent(mock_client_is) == snapshot
    assert mock_client_is.close.call_count == 2


async def test_weather_report_unusable_values(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    snapshot: SnapshotAssertion,
) -> None:
    """Test unavailable, non-numeric, unconvertible and missing sensors are left out."""
    hass.states.async_set("zone.home", "0", HOME)
    _set_states(
        hass,
        {
            **WEATHER_STATES,
            "sensor.temperature": (STATE_UNAVAILABLE, "°C"),
            "sensor.humidity": ("wet", "%"),
            "sensor.wind_direction": ("nan", "°"),
            "sensor.pressure": ("1023.7", "W"),
            "sensor.irradiance": ("50", "BTU/(h⋅ft²)"),
        },
    )
    hass.states.async_remove("sensor.wind_speed")

    await _async_setup(hass, mock_station_entry)

    assert _sent(mock_client_is) == snapshot


@pytest.mark.parametrize(
    ("options", "interval"),
    [
        pytest.param(WEATHER_OPTIONS, timedelta(minutes=10), id="weather"),
        pytest.param(POSITION_OPTIONS, timedelta(minutes=30), id="position"),
    ],
)
async def test_report_interval(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    snapshot: SnapshotAssertion,
    interval: timedelta,
) -> None:
    """Test reports repeat on a fixed interval."""
    hass.states.async_set("zone.home", "0", HOME)
    await _async_setup(hass, mock_station_entry)
    assert len(_sent(mock_client_is)) == 1

    await _async_tick(hass, freezer, interval - timedelta(minutes=1))
    assert len(_sent(mock_client_is)) == 1

    await _async_tick(hass, freezer, timedelta(minutes=1, seconds=1))
    assert _sent(mock_client_is) == snapshot


# One mile, north and east of the starting point.
MILE_NORTH = 1609.344 / 111_050
MILE_EAST = 1609.344 / 81_890

# What a phone reports, in m/s and degrees, at 60 and 20 mph.
NORTH_60 = {"speed": 26.8224, "course": 0}
EAST_20 = {"speed": 8.9408, "course": 90}
SOUTH_60 = {"speed": 26.8224, "course": 180}


def _move(hass: HomeAssistant, latitude: float, longitude: float, **extra: Any) -> None:
    hass.states.async_set(
        CAR, "not_home", {ATTR_LATITUDE: latitude, ATTR_LONGITUDE: longitude, **extra}
    )


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_smart_beaconing(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    snapshot: SnapshotAssertion,
) -> None:
    """Test a moving station reports by speed and turns, queuing early updates."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)
    assert len(_sent(mock_client_is)) == 1

    # 60 mph north: due 3 minutes after the last report, so this waits.
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0, **NORTH_60)
    await hass.async_block_till_done()
    assert len(_sent(mock_client_is)) == 1

    await _async_tick(hass, freezer, timedelta(seconds=121))
    assert len(_sent(mock_client_is)) == 2

    # Turning east is reported straight away; that mile took 3 minutes.
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0 + MILE_EAST, **EAST_20)
    await hass.async_block_till_done()
    assert len(_sent(mock_client_is)) == 3

    # Another turn within 30 seconds waits for the minimum turn time, and
    # goes out at the next check after it.
    freezer.tick(timedelta(seconds=10))
    _move(hass, 42.5 + MILE_NORTH - MILE_NORTH / 6, -71.0 + MILE_EAST, **SOUTH_60)
    await hass.async_block_till_done()
    assert len(_sent(mock_client_is)) == 3

    await _async_tick(hass, freezer, timedelta(seconds=31))
    assert len(_sent(mock_client_is)) == 4

    # Without new positions it falls back to the stopped rate.
    await _async_tick(hass, freezer, timedelta(minutes=29))
    assert len(_sent(mock_client_is)) == 4

    await _async_tick(hass, freezer, timedelta(minutes=1, seconds=1))
    assert _sent(mock_client_is) == snapshot


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_stop_reported(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test stopping is reported soon, without the speed from before."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)

    # 60 mph north, not due yet; then the same position a minute later.
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0, gps_accuracy=5)
    await hass.async_block_till_done()

    assert _sent(mock_client_is)[1:] == [
        "N0CALL-13>APZHAS,TCPIP*:/210907z4230.87N/07100.00W>"
    ]


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_location_appears(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test the first position is sent as soon as the entity has one."""
    await _async_setup(hass, mock_station_entry)
    assert _sent(mock_client_is) == []

    _move(hass, 42.5, -71.0)
    await hass.async_block_till_done()

    assert _sent(mock_client_is) == [
        "N0CALL-13>APZHAS,TCPIP*:/210905z4230.00N/07100.00W>"
    ]


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_failed_report_not_counted(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a position that failed to send is sent again when next due."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)
    mock_client_is.sendall.side_effect = AprsConnectionError("not connected")

    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0, **NORTH_60)
    await _async_tick(hass, freezer, timedelta(seconds=121))
    assert len(_sent(mock_client_is)) == 2

    # Due again 3 minutes after the failed attempt, at the speed it was going.
    mock_client_is.sendall.side_effect = None
    await _async_tick(hass, freezer, timedelta(minutes=3, seconds=30))
    assert _sent(mock_client_is)[2] == (
        "N0CALL-13>APZHAS,TCPIP*:/210911z4230.87N/07100.00W>360/052"
    )
    assert hass.states.get(LAST_REPORT).state == "2026-10-21T09:11:31+00:00"


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_moving_location_ignored_updates(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test updates that are not movement do not cause reports."""
    _move(hass, 42.5, -71.0, gps_accuracy=50)
    await _async_setup(hass, mock_station_entry)

    freezer.tick(timedelta(minutes=5))
    _move(hass, 42.5001, -71.0, gps_accuracy=50)
    hass.states.async_set(CAR, "work", {ATTR_LATITUDE: 42.5001, ATTR_LONGITUDE: -71})
    hass.states.async_set(CAR, "unavailable")
    hass.states.async_remove(CAR)
    await _async_tick(hass, freezer, timedelta(minutes=1))

    assert len(_sent(mock_client_is)) == 1


@pytest.mark.parametrize(
    "options",
    [
        {
            **MOVING_OPTIONS,
            CONF_SUPPRESS_TEMPLATE: "{{ is_state('input_boolean.hide', 'on') }}",
        }
    ],
)
async def test_suppress_template(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test nothing is sent while the suppress template renders true."""
    hass.states.async_set("input_boolean.hide", "on")
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)

    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0)
    await _async_tick(hass, freezer, timedelta(minutes=31))
    assert _sent(mock_client_is) == []

    hass.states.async_set("input_boolean.hide", "off")
    await _async_tick(hass, freezer, timedelta(seconds=30))
    assert len(_sent(mock_client_is)) == 1


@pytest.mark.parametrize(
    "options", [{**MOVING_OPTIONS, CONF_SUPPRESS_TEMPLATE: "{{ 1 / 0 }}"}]
)
async def test_suppress_template_error(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a failing suppress template keeps the position hidden."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)
    await _async_tick(hass, freezer, timedelta(minutes=31))

    assert _sent(mock_client_is) == []
    assert caplog.text.count("the suppress template failed") == 1


async def test_no_location(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test nothing is sent without a location, logging only once."""
    await _async_setup(hass, mock_station_entry)
    await _async_tick(hass, freezer, timedelta(minutes=10, seconds=1))

    assert _sent(mock_client_is) == []
    assert (
        caplog.text.count(
            "Could not send APRS-IS report for N0CALL-13: zone.home has no location"
        )
        == 1
    )

    hass.states.async_set("zone.home", "0", HOME)
    await _async_tick(hass, freezer, timedelta(minutes=10))

    assert len(_sent(mock_client_is)) == 1
    assert "Sending APRS-IS reports for N0CALL-13 again" in caplog.text


@pytest.mark.parametrize(
    ("method", "side_effect", "reauth"),
    [
        pytest.param(
            "sendall", AprsConnectionError("not connected"), False, id="send_failed"
        ),
        pytest.param(
            "connect", [None, LoginError("Failed to login")], False, id="login_failed"
        ),
        pytest.param(
            "connect",
            [None, LoginError("Password is incorrect")],
            True,
            id="passcode_refused",
        ),
    ],
)
async def test_send_failure(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    caplog: pytest.LogCaptureFixture,
    method: str,
    side_effect: Any,
    reauth: bool,
) -> None:
    """Test a failed report is retried on the next interval."""
    hass.states.async_set("zone.home", "0", HOME)
    getattr(mock_client_is, method).side_effect = side_effect

    await _async_setup(hass, mock_station_entry)

    assert "Could not send APRS-IS report for N0CALL-13" in caplog.text
    assert hass.states.get(LAST_REPORT).state == "unknown"
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == (
        [SOURCE_REAUTH] if reauth else []
    )

    getattr(mock_client_is, method).side_effect = None
    await _async_tick(hass, freezer, timedelta(minutes=10, seconds=1))

    assert "Sending APRS-IS reports for N0CALL-13 again" in caplog.text
    assert hass.states.get(LAST_REPORT).state == "2026-10-21T09:15:01+00:00"


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_stop_after_report(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test stopping right after a moving report is reported soon."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0, **NORTH_60)
    await _async_tick(hass, freezer, timedelta(seconds=121))
    assert _sent(mock_client_is)[-1].endswith(">360/052")

    freezer.tick(timedelta(seconds=30))
    _move(hass, 42.5 + MILE_NORTH, -71.0, speed=0, course=0)
    await hass.async_block_till_done()
    await _async_tick(hass, freezer, timedelta(seconds=30))

    assert _sent(mock_client_is)[2:] == [
        "N0CALL-13>APZHAS,TCPIP*:/210908z4230.87N/07100.00W>"
    ]


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_small_steps_add_up(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test steps within the accuracy still count as travel when they add up."""
    _move(hass, 42.5, -71.0, gps_accuracy=65)
    await _async_setup(hass, mock_station_entry)

    # 30 mph north, due every 6 minutes, with fixes every 4 seconds 54 m apart.
    for step in range(1, 101):
        freezer.tick(timedelta(seconds=4))
        _move(hass, 42.5 + MILE_NORTH * step / 30, -71.0, gps_accuracy=65)
        await hass.async_block_till_done()
    await _async_tick(hass, freezer, timedelta(seconds=1))

    # Paced by the speed worked out from positions, which is never sent.
    assert len(_sent(mock_client_is)) == 2
    assert _sent(mock_client_is)[1].endswith("W>")


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_jump_is_not_travel(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a jump, such as a person changing trackers, is sent without a speed."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)

    freezer.tick(timedelta(seconds=5))
    _move(hass, 42.6, -71.0)
    await hass.async_block_till_done()
    await _async_tick(hass, freezer, timedelta(minutes=30, seconds=1))

    assert _sent(mock_client_is)[1:] == [
        "N0CALL-13>APZHAS,TCPIP*:/210935z4236.00N/07100.00W>"
    ]


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_slow_walk(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test walking within a large accuracy is paced as walking, without stops."""
    _move(hass, 42.5, -71.0, gps_accuracy=50)
    await _async_setup(hass, mock_station_entry)

    # About 1 mph north, 20 m every 40 seconds, for 29 minutes.
    for step in range(1, 44):
        freezer.tick(timedelta(seconds=40))
        _move(hass, 42.5 + step * 20 / 111_050, -71.0, gps_accuracy=50)
        await hass.async_block_till_done()
    await _async_tick(hass, freezer, timedelta(seconds=1))

    # At 1.1 mph walking reports every 23 minutes.
    assert len(_sent(mock_client_is)) == 2


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_waits_for_start(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a moving station sends its first report once started."""
    hass.set_state(CoreState.not_running)
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0)
    await hass.async_block_till_done()
    assert _sent(mock_client_is) == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert len(_sent(mock_client_is)) == 1


@pytest.mark.parametrize(
    "options",
    [
        {
            **POSITION_OPTIONS,
            CONF_SUPPRESS_TEMPLATE: "{{ is_state('input_boolean.hide', 'on') }}",
        }
    ],
)
async def test_suppress_template_fixed_station(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a fixed station reports as soon as the template renders false."""
    hass.states.async_set("zone.home", "0", HOME)
    hass.states.async_set("input_boolean.hide", "on")
    await _async_setup(hass, mock_station_entry)
    assert _sent(mock_client_is) == []

    hass.states.async_set("input_boolean.hide", "off")
    await hass.async_block_till_done()

    assert len(_sent(mock_client_is)) == 1


@pytest.mark.parametrize(
    "options",
    [
        {
            **POSITION_OPTIONS,
            CONF_SUPPRESS_TEMPLATE: "{{ states('input_number.x') | float > 1 }}",
        }
    ],
)
async def test_suppress_template_recovers(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test reports resume once a failing template renders false."""
    hass.states.async_set("zone.home", "0", HOME)
    hass.states.async_set("input_number.x", "5")
    await _async_setup(hass, mock_station_entry)
    hass.states.async_set("input_number.x", "not a number")
    await hass.async_block_till_done()
    assert _sent(mock_client_is) == []
    assert "the suppress template failed" in caplog.text

    hass.states.async_set("input_number.x", "0")
    await hass.async_block_till_done()

    assert "The suppress template for N0CALL-13 renders again" in caplog.text
    assert len(_sent(mock_client_is)) == 1


@pytest.mark.parametrize(
    "options",
    [{**MOVING_OPTIONS, CONF_MOTION_ENTITY: PHONE, CONF_COMMENT: "x" * 36}],
)
async def test_motion_entity(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test course, speed and altitude come from the motion entity only."""
    _move(hass, 42.5, -71.0)
    hass.states.async_set(PHONE, "not_home", {**NORTH_60, "altitude": 100.4})
    await _async_setup(hass, mock_station_entry)

    # Altitude goes first, and the comment is shortened to make room.
    assert _sent(mock_client_is) == [
        "N0CALL-13>APZHAS,TCPIP*:/210905z4230.00N/07100.00W>360/052/A=000329" + "x" * 27
    ]


@pytest.mark.parametrize(
    "options",
    [{**MOVING_OPTIONS, CONF_MOTION_ENTITY: PHONE, CONF_SEND_ALTITUDE: False}],
)
async def test_send_altitude_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a reported altitude is left out when turned off."""
    _move(hass, 42.5, -71.0)
    hass.states.async_set(PHONE, "not_home", {**NORTH_60, "altitude": 100.4})
    await _async_setup(hass, mock_station_entry)

    assert _sent(mock_client_is) == [
        "N0CALL-13>APZHAS,TCPIP*:/210905z4230.00N/07100.00W>360/052"
    ]


@pytest.mark.parametrize("options", [{**MOVING_OPTIONS, CONF_MOTION_ENTITY: PHONE}])
async def test_reported_zero_speed_ignored(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a reported speed of 0 does not hide travel between positions."""
    hass.states.async_set(PHONE, "not_home", {"speed": 0, "course": 0})
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)

    # A mile a minute is due at the fast rate, 3 minutes after the last report.
    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0)
    await hass.async_block_till_done()
    assert len(_sent(mock_client_is)) == 1

    await _async_tick(hass, freezer, timedelta(seconds=121))
    assert _sent(mock_client_is)[1:] == [
        "N0CALL-13>APZHAS,TCPIP*:/210908z4230.87N/07100.00W>"
    ]


@pytest.mark.parametrize(
    "options",
    [
        {
            **MOVING_OPTIONS,
            CONF_SMART_BEACONING: False,
            CONF_REPORT_INTERVAL: 5,
        }
    ],
)
async def test_smart_beaconing_off(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a moving station reports at the report interval without it."""
    _move(hass, 42.5, -71.0)
    await _async_setup(hass, mock_station_entry)

    freezer.tick(timedelta(seconds=60))
    _move(hass, 42.5 + MILE_NORTH, -71.0, **NORTH_60)
    await hass.async_block_till_done()
    assert len(_sent(mock_client_is)) == 1

    await _async_tick(hass, freezer, timedelta(minutes=4))
    assert _sent(mock_client_is)[1:] == [
        "N0CALL-13>APZHAS,TCPIP*:/210910z4230.87N/07100.00W>360/052"
    ]


@pytest.mark.parametrize("options", [{**WEATHER_OPTIONS, CONF_REPORT_INTERVAL: 5}])
async def test_weather_report_interval(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test the report interval option replaces the default."""
    hass.states.async_set("zone.home", "0", HOME)
    await _async_setup(hass, mock_station_entry)

    await _async_tick(hass, freezer, timedelta(minutes=5, seconds=1))

    assert len(_sent(mock_client_is)) == 2
