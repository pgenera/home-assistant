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
    CONF_POSITION_AMBIGUITY,
    CONF_SYMBOL,
    CONF_WEATHER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import (
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant

from .conftest import WEATHER_OPTIONS

START = "2026-10-21T09:05:00+00:00"
HOME = {ATTR_LATITUDE: 42.562956, ATTR_LONGITUDE: -70.86915}
CAR = "device_tracker.car"
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


@pytest.mark.parametrize("options", [MOVING_OPTIONS])
async def test_moving_location(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a device tracker's latest position is sent on the interval."""
    hass.states.async_set(CAR, "not_home", {ATTR_LATITUDE: 42.5, ATTR_LONGITUDE: -71})
    await _async_setup(hass, mock_station_entry)

    hass.states.async_set(CAR, "not_home", {ATTR_LATITUDE: 42.6, ATTR_LONGITUDE: -71})
    await _async_tick(hass, freezer, timedelta(minutes=30))

    assert _sent(mock_client_is) == [
        "N0CALL-13>APZHAS,TCPIP*:/210905z4230.00N/07100.00W>",
        "N0CALL-13>APZHAS,TCPIP*:/210935z4236.00N/07100.00W>",
    ]


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
