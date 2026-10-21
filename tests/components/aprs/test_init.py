"""Test setting up APRS-IS stations."""

from unittest.mock import MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_publisher_is: MagicMock,
) -> None:
    """Test a station is set up after a login check and unloads."""
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


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
    mock_config_entry: MockConfigEntry,
    mock_publisher_is: MagicMock,
    error: Exception,
    state: ConfigEntryState,
) -> None:
    """Test login failures during setup."""
    mock_publisher_is.connect.side_effect = error
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is state


async def test_setup_invalid_auth_starts_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_publisher_is: MagicMock,
) -> None:
    """Test a refused passcode asks the user for a new one."""
    mock_publisher_is.connect.side_effect = LoginError("Password is incorrect")
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
