"""Test APRS device tracker."""

from typing import Any

import aprslib
from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.aprs import device_tracker
from homeassistant.components.device_tracker import DOMAIN as DEVICE_TRACKER_DOMAIN
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PLATFORM,
    CONF_TIMEOUT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import MockAprsIs

TEST_CALLSIGN = "testcall"
TEST_HOST = "testhost"
TEST_PASSWORD = "testpass"
TEST_COORDS_NULL_ISLAND = (0, 0)
PLATFORM_COMPONENT = "aprs.device_tracker"

CONFIG = {
    CONF_PLATFORM: "aprs",
    CONF_USERNAME: TEST_CALLSIGN,
    CONF_PASSWORD: TEST_PASSWORD,
    CONF_HOST: TEST_HOST,
    device_tracker.CONF_CALLSIGNS: ["XX0FOO*", "YY0BAR-1"],
}


async def _async_setup(hass: HomeAssistant, config: dict[str, Any]) -> None:
    assert await async_setup_component(
        hass, DEVICE_TRACKER_DOMAIN, {DEVICE_TRACKER_DOMAIN: config}
    )
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("callsigns", "expected"),
    [
        pytest.param(["CALLSIGN1", "callsign2"], "b/CALLSIGN1 b/CALLSIGN2", id="two"),
        pytest.param(["xx0foo*"], "b/XX0FOO*", id="wildcard"),
    ],
)
def test_make_filter(callsigns: list[str], expected: str) -> None:
    """Test the server-side filter built from callsigns."""
    assert device_tracker.make_filter(callsigns) == expected


@pytest.mark.parametrize(
    ("posambiguity", "accuracy"),
    [(0, 0), (1, 186), (2, 1855), (3, 18553), (4, 111319)],
)
def test_gps_accuracy(posambiguity: int, accuracy: int) -> None:
    """Test GPS accuracy for each APRS position ambiguity level."""
    assert device_tracker.gps_accuracy(TEST_COORDS_NULL_ISLAND, posambiguity) == (
        accuracy
    )


@pytest.mark.parametrize("posambiguity", [5, "not an int", 1.2])
def test_gps_accuracy_invalid(posambiguity: Any) -> None:
    """Test GPS accuracy with invalid position ambiguity."""
    with pytest.raises(ValueError, match="APRS position ambiguity must be 0-4"):
        device_tracker.gps_accuracy(TEST_COORDS_NULL_ISLAND, posambiguity)


async def test_setup(hass: HomeAssistant, mock_aprs_is: MockAprsIs) -> None:
    """Test the platform connects with the configured login and filter."""
    await _async_setup(hass, CONFIG)

    assert PLATFORM_COMPONENT in hass.config.components
    mock_aprs_is.mock_class.assert_called_once_with(
        TEST_CALLSIGN, passwd=TEST_PASSWORD, host=TEST_HOST, port=14580
    )
    mock_aprs_is.client.set_filter.assert_called_once_with("b/XX0FOO* b/YY0BAR-1")
    mock_aprs_is.client.connect.assert_called_once_with()


async def test_setup_defaults(hass: HomeAssistant, mock_aprs_is: MockAprsIs) -> None:
    """Test the default server and receive-only passcode."""
    await _async_setup(
        hass,
        {
            CONF_PLATFORM: "aprs",
            CONF_USERNAME: TEST_CALLSIGN,
            device_tracker.CONF_CALLSIGNS: "XX0FOO",
        },
    )

    mock_aprs_is.mock_class.assert_called_once_with(
        TEST_CALLSIGN, passwd="-1", host="rotate.aprs2.net", port=14580
    )
    mock_aprs_is.client.set_filter.assert_called_once_with("b/XX0FOO")


@pytest.mark.usefixtures("mock_device_tracker_conf")
@pytest.mark.parametrize(
    ("raw_packet", "entity_id"),
    [
        pytest.param(
            "XX0FOO-1>APRS,TCPIP*:!4903.50N/07201.75W>090/036/A=001234Mobile",
            "device_tracker.xx0foo_1",
            id="uncompressed",
        ),
        pytest.param(
            "XX0FOO-2>APRS,TCPIP*:!4903.5 N/07201.7 W-",
            "device_tracker.xx0foo_2",
            id="ambiguity",
        ),
        pytest.param(
            "YY0BAR-1>APRS,TCPIP*:!/5L!!<*e7>7P[",
            "device_tracker.yy0bar_1",
            id="compressed",
        ),
        pytest.param(
            "CEEWO2-14>APLWS2,qAU,CEEWO2-15:;V4310251 "
            "*121203h5105.72N/00131.89WO085/024/A=033178"
            "!w&,!Clb=3.5m/s calibration 21% 404.40MHz "
            "Type=RS41 batt=2.7V Details on "
            "http://radiosondy.info/",
            "device_tracker.v4310251",
            id="object",
        ),
    ],
)
async def test_position_report(
    hass: HomeAssistant,
    mock_aprs_is: MockAprsIs,
    snapshot: SnapshotAssertion,
    raw_packet: str,
    entity_id: str,
) -> None:
    """Test position reports update device trackers."""
    await _async_setup(hass, CONFIG)

    await mock_aprs_is.async_receive(hass, aprslib.parse(raw_packet))

    assert hass.states.get(entity_id) == snapshot


@pytest.mark.usefixtures("mock_device_tracker_conf")
async def test_position_report_invalid_ambiguity(
    hass: HomeAssistant,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
    snapshot: SnapshotAssertion,
) -> None:
    """Test a report with invalid position ambiguity is used without accuracy."""
    await _async_setup(hass, CONFIG)

    await mock_aprs_is.async_receive(
        hass,
        {
            "format": "uncompressed",
            "from": "XX0FOO-1",
            "latitude": 1.0,
            "longitude": 2.0,
            "posambiguity": 5,
        },
    )

    assert "APRS message contained invalid posambiguity: 5" in caplog.text
    assert hass.states.get("device_tracker.xx0foo_1") == snapshot


@pytest.mark.usefixtures("mock_device_tracker_conf")
async def test_non_position_report(
    hass: HomeAssistant, mock_aprs_is: MockAprsIs
) -> None:
    """Test packets without a position are ignored."""
    await _async_setup(hass, CONFIG)

    await mock_aprs_is.async_receive(
        hass, aprslib.parse("XX0FOO-1>APRS,TCPIP*::YY0BAR-1 :Hello{1")
    )

    assert hass.states.async_entity_ids(DEVICE_TRACKER_DOMAIN) == []


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(AprsConnectionError("Unable to connect."), id="connection"),
        pytest.param(LoginError("Password is incorrect"), id="login"),
    ],
)
async def test_setup_connect_error(
    hass: HomeAssistant,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    """Test a failed connection fails platform setup."""
    mock_aprs_is.client.connect.side_effect = error

    await _async_setup(hass, CONFIG)

    assert PLATFORM_COMPONENT not in hass.config.components
    assert str(error) in caplog.text
    mock_aprs_is.client.consumer.assert_not_called()


async def test_setup_timeout(
    hass: HomeAssistant,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test platform setup fails when the server does not answer in time."""
    mock_aprs_is.connect_allowed.clear()

    await _async_setup(hass, {**CONFIG, CONF_TIMEOUT: 0.01})

    assert PLATFORM_COMPONENT not in hass.config.components
    assert "Timeout waiting for APRS to connect" in caplog.text


async def test_stop(hass: HomeAssistant, mock_aprs_is: MockAprsIs) -> None:
    """Test the connection is closed when Home Assistant stops."""
    await _async_setup(hass, CONFIG)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_aprs_is.client.close.assert_called_once_with()
    await hass.async_add_executor_job(mock_aprs_is.join)
