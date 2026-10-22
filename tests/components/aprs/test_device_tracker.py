"""Test APRS device tracking."""

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry, mock_restore_cache
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.aprs.const import CONF_CALLSIGN, CONF_CALLSIGNS, DOMAIN
from homeassistant.components.aprs.receiver import gps_accuracy, make_filter
from homeassistant.components.device_tracker import DOMAIN as DEVICE_TRACKER_DOMAIN
from homeassistant.const import (
    ATTR_GPS_ACCURACY,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PLATFORM,
    CONF_USERNAME,
    STATE_UNKNOWN,
)
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, State
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import TEST_PASSCODE, TEST_RECEIVER, TEST_SERVER, MockAprsIs

TEST_COORDS_NULL_ISLAND = (0, 0)
TRACKER = "device_tracker.xx0foo_1"
POSITION = "XX0FOO-1>APRS,TCPIP*:!4903.50N/07201.75W>090/036/A=001234Mobile"

YAML_CONFIG = {
    CONF_PLATFORM: DOMAIN,
    CONF_USERNAME: "n0call",
    CONF_PASSWORD: TEST_PASSCODE,
    CONF_HOST: TEST_SERVER,
    CONF_CALLSIGNS: ["xx0foo*", "YY0BAR-1"],
}


async def _async_setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
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
    assert make_filter(callsigns) == expected


@pytest.mark.parametrize(
    ("posambiguity", "accuracy"),
    [(0, 0), (1, 186), (2, 1855), (3, 18553), (4, 111319)],
)
def test_gps_accuracy(posambiguity: int, accuracy: int) -> None:
    """Test GPS accuracy for each APRS position ambiguity level."""
    assert gps_accuracy(TEST_COORDS_NULL_ISLAND, posambiguity) == accuracy


@pytest.mark.parametrize("posambiguity", [5, "not an int", 1.2])
def test_gps_accuracy_invalid(posambiguity: Any) -> None:
    """Test GPS accuracy with invalid position ambiguity."""
    with pytest.raises(ValueError, match="APRS position ambiguity must be 0-4"):
        gps_accuracy(TEST_COORDS_NULL_ISLAND, posambiguity)


async def test_setup(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test the receiver logs in with its filter and has no trackers yet."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)

    mock_aprs_is.mock_class.assert_called_once_with(
        TEST_RECEIVER, passwd="-1", host=TEST_SERVER, port=14580
    )
    mock_aprs_is.client.set_filter.assert_called_once_with("b/XX0FOO* b/YY0BAR-1")
    mock_aprs_is.client.consumer.assert_called_once()
    assert hass.states.async_entity_ids(DEVICE_TRACKER_DOMAIN) == []


@pytest.mark.parametrize(
    ("raw_packet", "entity_id"),
    [
        pytest.param(POSITION, TRACKER, id="uncompressed"),
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
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    snapshot: SnapshotAssertion,
    raw_packet: str,
    entity_id: str,
) -> None:
    """Test a station's first position report creates its tracker."""
    await _async_setup(hass, mock_config_entry)

    await mock_aprs_is.async_receive(hass, raw_packet)

    assert hass.states.get(entity_id) == snapshot(name="state")
    assert entity_registry.async_get(entity_id) == snapshot(name="registry")


async def test_position_update(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test later reports move the same tracker."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_receive(hass, POSITION)

    await mock_aprs_is.async_receive(hass, "XX0FOO-1>APRS,TCPIP*:!4904.00N/07202.00W>")

    assert hass.states.async_entity_ids(DEVICE_TRACKER_DOMAIN) == [TRACKER]
    state = hass.states.get(TRACKER)
    assert state.attributes[ATTR_LATITUDE] == pytest.approx(49.0667, abs=1e-4)
    assert "altitude" not in state.attributes


@pytest.mark.parametrize(
    "raw_packet",
    [
        pytest.param(
            "XX0FOO-13>APZHAS,TCPIP*:/211631z4233.78N/07052.15W_176/002g004t052h69",
            id="weather_station",
        ),
        pytest.param("XX0FOO-1>APRS,TCPIP*::YY0BAR-1 :Hello{1", id="message"),
        pytest.param(
            "XX0FOO-1>APRS,TCPIP*:_10090556c220s004g005t077r000p000P000h50b09900",
            id="positionless_weather",
        ),
    ],
)
async def test_packets_not_tracked(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    raw_packet: str,
) -> None:
    """Test weather stations and packets without a position are not tracked."""
    await _async_setup(hass, mock_config_entry)

    await mock_aprs_is.async_receive(hass, raw_packet)

    assert hass.states.async_entity_ids(DEVICE_TRACKER_DOMAIN) == []


async def test_invalid_ambiguity(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a report with invalid position ambiguity is used without accuracy."""
    await _async_setup(hass, mock_config_entry)

    await mock_aprs_is.async_receive_parsed(
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
    assert hass.states.get(TRACKER).attributes[ATTR_GPS_ACCURACY] == 0


@pytest.mark.parametrize(
    ("restored", "expected"),
    [
        pytest.param(
            {
                ATTR_LATITUDE: 49.0583,
                ATTR_LONGITUDE: -72.0292,
                ATTR_GPS_ACCURACY: 186,
                "altitude": 376.1,
                "friendly_name": "XX0FOO-1",
            },
            "not_home",
            id="position",
        ),
        pytest.param({}, STATE_UNKNOWN, id="no_position"),
    ],
)
async def test_restore(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    snapshot: SnapshotAssertion,
    restored: dict[str, Any],
    expected: str,
) -> None:
    """Test known stations come back with their last position after a restart."""
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        DEVICE_TRACKER_DOMAIN,
        DOMAIN,
        f"{TEST_RECEIVER}_XX0FOO-1",
        config_entry=mock_config_entry,
        suggested_object_id="xx0foo_1",
    )
    mock_restore_cache(hass, [State(TRACKER, "not_home", restored)])

    await _async_setup(hass, mock_config_entry)

    state = hass.states.get(TRACKER)
    assert state.state == expected
    assert state.attributes == snapshot

    await mock_aprs_is.async_receive(hass, POSITION)
    assert hass.states.async_entity_ids(DEVICE_TRACKER_DOMAIN) == [TRACKER]


async def test_reconnect(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the receiver reconnects after the server drops it."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)
    mock_aprs_is.client.connect.side_effect = [
        AprsConnectionError("no banner from server"),
        None,
    ]

    with patch("homeassistant.components.aprs.receiver.RECONNECT_DELAY", 0):
        await hass.async_add_executor_job(mock_aprs_is.drop_connection)
        await mock_aprs_is.async_receive(hass, POSITION)

    assert f"Lost connection to APRS-IS server {TEST_SERVER}" in caplog.text
    assert f"Reconnected to APRS-IS server {TEST_SERVER}" in caplog.text
    assert mock_aprs_is.client.connect.call_count == 3
    assert hass.states.get(TRACKER) is not None


async def test_unexpected_error_reconnects(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test an unexpected error while reading reconnects instead of stopping."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)

    with patch("homeassistant.components.aprs.receiver.RECONNECT_DELAY", 0):
        await hass.async_add_executor_job(
            mock_aprs_is.drop_connection, KeyError("latitude")
        )
        await mock_aprs_is.async_receive(hass, POSITION)

    assert f"Unexpected error reading from APRS-IS server {TEST_SERVER}" in caplog.text
    assert mock_aprs_is.client.close.call_count == 1
    assert mock_aprs_is.client.connect.call_count == 2
    assert hass.states.get(TRACKER) is not None


async def test_unload_while_reconnecting(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test unloading during a reconnect does not start reading the new socket."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)
    connecting = threading.Event()
    release = threading.Event()
    shut_down = threading.Event()

    def _slow_connect() -> None:
        connecting.set()
        assert release.wait(5)

    mock_aprs_is.client.connect.side_effect = _slow_connect
    mock_aprs_is.client.sock.shutdown.side_effect = lambda how: shut_down.set()
    with patch("homeassistant.components.aprs.receiver.RECONNECT_DELAY", 0):
        await hass.async_add_executor_job(mock_aprs_is.drop_connection)
        assert await hass.async_add_executor_job(connecting.wait, 5)

    unload = hass.async_create_task(
        hass.config_entries.async_unload(mock_config_entry.entry_id)
    )
    assert await hass.async_add_executor_job(shut_down.wait, 5)
    release.set()
    assert await unload

    assert mock_aprs_is.client.consumer.call_count == 1


@pytest.mark.usefixtures("mock_aprs_is")
async def test_yaml_import(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    mock_client_is: MagicMock,
) -> None:
    """Test a YAML device tracker is imported as a receiver."""
    assert await async_setup_component(
        hass, DEVICE_TRACKER_DOMAIN, {DEVICE_TRACKER_DOMAIN: YAML_CONFIG}
    )
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    assert entries[0].unique_id == TEST_RECEIVER
    assert entries[0].data == {
        CONF_CALLSIGN: TEST_RECEIVER,
        CONF_PASSWORD: TEST_PASSCODE,
        CONF_HOST: TEST_SERVER,
    }
    assert entries[0].options == {CONF_CALLSIGNS: ["XX0FOO*", "YY0BAR-1"]}
    assert issue_registry.async_get_issue(
        HOMEASSISTANT_DOMAIN, f"deprecated_yaml_{DOMAIN}"
    )


@pytest.mark.usefixtures("mock_aprs_is", "mock_client_is")
async def test_yaml_import_already_imported(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the YAML is only imported once but still flagged."""
    mock_config_entry.add_to_hass(hass)

    assert await async_setup_component(
        hass, DEVICE_TRACKER_DOMAIN, {DEVICE_TRACKER_DOMAIN: YAML_CONFIG}
    )
    await hass.async_block_till_done()

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert issue_registry.async_get_issue(
        HOMEASSISTANT_DOMAIN, f"deprecated_yaml_{DOMAIN}"
    )


@pytest.mark.parametrize(
    ("config", "error", "issue_id"),
    [
        pytest.param(
            YAML_CONFIG,
            AprsConnectionError("no banner from server"),
            "yaml_import_cannot_connect_n0call",
            id="cannot_connect",
        ),
        pytest.param(
            YAML_CONFIG,
            LoginError("Password is incorrect"),
            "yaml_import_invalid_auth_n0call",
            id="invalid_auth",
        ),
        pytest.param(
            {**YAML_CONFIG, CONF_CALLSIGNS: ["XX0*FOO"]},
            None,
            "yaml_import_invalid_callsigns_n0call",
            id="invalid_callsigns",
        ),
        pytest.param(
            {**YAML_CONFIG, CONF_USERNAME: "n0-call"},
            None,
            "yaml_import_invalid_callsign_n0-call",
            id="invalid_callsign",
        ),
        pytest.param(
            YAML_CONFIG,
            RuntimeError("boom"),
            "yaml_import_unknown_n0call",
            id="unknown",
        ),
    ],
)
async def test_yaml_import_error(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    mock_client_is: MagicMock,
    config: dict[str, Any],
    error: Exception | None,
    issue_id: str,
) -> None:
    """Test a failed import is reported instead of flagging the YAML."""
    mock_client_is.connect.side_effect = error

    assert await async_setup_component(
        hass, DEVICE_TRACKER_DOMAIN, {DEVICE_TRACKER_DOMAIN: config}
    )
    await hass.async_block_till_done()

    assert hass.config_entries.async_entries(DOMAIN) == []
    assert issue_registry.async_get_issue(DOMAIN, issue_id)
    assert not issue_registry.async_get_issue(
        HOMEASSISTANT_DOMAIN, f"deprecated_yaml_{DOMAIN}"
    )


async def test_reconnect_unexpected_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test an unexpected error while reconnecting is retried."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)
    mock_aprs_is.client.connect.side_effect = [IndexError("string index"), None]

    with patch("homeassistant.components.aprs.receiver.RECONNECT_DELAY", 0):
        await hass.async_add_executor_job(mock_aprs_is.drop_connection)
        await mock_aprs_is.async_receive(hass, POSITION)

    assert mock_aprs_is.client.connect.call_count == 3
    assert hass.states.get(TRACKER) is not None


async def test_unreadable_packet(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a packet that cannot be read is skipped without reconnecting."""
    await _async_setup(hass, mock_config_entry)

    await mock_aprs_is.async_receive_parsed(
        hass, {"format": "uncompressed", "from": "XX0FOO-1"}
    )
    await mock_aprs_is.async_receive(hass, POSITION)

    assert "Could not read APRS packet" in caplog.text
    assert mock_aprs_is.client.connect.call_count == 1
    assert hass.states.get(TRACKER) is not None


async def test_disabled_station(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test a disabled station's tracker is not added when it is heard."""
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        DEVICE_TRACKER_DOMAIN,
        DOMAIN,
        f"{TEST_RECEIVER}_XX0FOO-1",
        config_entry=mock_config_entry,
        suggested_object_id="xx0foo_1",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    await _async_setup(hass, mock_config_entry)

    await mock_aprs_is.async_receive(hass, POSITION)
    await mock_aprs_is.async_receive(hass, POSITION)

    assert hass.states.get(TRACKER) is None


async def test_disabled_while_running(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test a station disabled while running is no longer updated."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_receive(hass, POSITION)

    entity_registry.async_update_entity(
        TRACKER, disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    await mock_aprs_is.async_receive(hass, POSITION)

    assert hass.states.get(TRACKER) is None


async def test_unload_with_read_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test an error from closing the socket on unload is not reported."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)
    mock_aprs_is.set_read_error(OSError("Bad file descriptor"))

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)

    assert "Unexpected error" not in caplog.text


async def test_quick_drop_backs_off(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test a connection dropped straight away is not retried in a tight loop."""
    await _async_setup(hass, mock_config_entry)
    await mock_aprs_is.async_wait_consuming(hass)

    await hass.async_add_executor_job(mock_aprs_is.drop_connection)
    await hass.async_add_executor_job(time.sleep, 0.1)

    assert mock_aprs_is.client.connect.call_count == 1
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
