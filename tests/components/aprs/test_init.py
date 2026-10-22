"""Test setting up APRS-IS stations and receivers."""

import threading
from unittest.mock import MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import WebSocketGenerator

from homeassistant.components.aprs.const import CONF_ENTRY_TYPE, DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from .conftest import RECEIVER_DATA, TEST_STATION, MockAprsIs


def _receiver_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("APRS-IS receiver")
    ]


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a station is set up after a login check and unloads."""
    mock_station_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_station_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_station_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_station_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("error", "state"),
    [
        pytest.param(
            AprsConnectionError("no banner from server"),
            ConfigEntryState.SETUP_RETRY,
            id="cannot_connect",
        ),
        pytest.param(
            LoginError("Password is incorrect"),
            ConfigEntryState.SETUP_ERROR,
            id="invalid_auth",
        ),
    ],
)
async def test_setup_error(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Test login failures during setup."""
    mock_client_is.connect.side_effect = error
    mock_station_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_station_entry.state is state


async def test_setup_invalid_auth_starts_reauth(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test a refused passcode asks the user for a new one."""
    mock_client_is.connect.side_effect = LoginError("Password is incorrect")
    mock_station_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_receiver_setup_and_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test a receiver reads on its own thread until it is unloaded."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await mock_aprs_is.async_wait_consuming(hass)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(_receiver_threads()) == 1

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert _receiver_threads() == []
    mock_aprs_is.client.close.assert_called_once_with()


async def test_receiver_stops_with_home_assistant(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
) -> None:
    """Test the receiver thread exits when Home Assistant stops."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await mock_aprs_is.async_wait_consuming(hass)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert _receiver_threads() == []


@pytest.mark.parametrize(
    ("error", "state"),
    [
        pytest.param(
            AprsConnectionError("no banner from server"),
            ConfigEntryState.SETUP_RETRY,
            id="cannot_connect",
        ),
        pytest.param(
            LoginError("Password is incorrect"),
            ConfigEntryState.SETUP_ERROR,
            id="invalid_auth",
        ),
        pytest.param(
            LoginError("Failed to login"),
            ConfigEntryState.SETUP_RETRY,
            id="login_failed",
        ),
        pytest.param(
            IndexError("string index out of range"),
            ConfigEntryState.SETUP_RETRY,
            id="no_banner",
        ),
    ],
)
async def test_receiver_setup_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Test receiver login failures during setup."""
    mock_aprs_is.client.connect.side_effect = error
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is state
    assert _receiver_threads() == []


async def test_migrate_receiver(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_aprs_is: MockAprsIs
) -> None:
    """Test receivers from before stations existed are migrated."""
    mock_config_entry.add_to_hass(hass)
    data = dict(mock_config_entry.data)
    del data[CONF_ENTRY_TYPE]
    hass.config_entries.async_update_entry(
        mock_config_entry, data=data, minor_version=1
    )

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await mock_aprs_is.async_wait_consuming(hass)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.minor_version == 2
    assert mock_config_entry.data == RECEIVER_DATA


@pytest.mark.usefixtures("mock_client_is")
async def test_station_device_not_removable(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    mock_station_entry: MockConfigEntry,
) -> None:
    """Test a station's own device cannot be removed."""
    assert await async_setup_component(hass, "config", {})
    mock_station_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, TEST_STATION), mock_station_entry.entry_id
    )
    assert device is not None

    client = await hass_ws_client(hass)
    response = await client.remove_device(device.id)

    assert not response["success"]
