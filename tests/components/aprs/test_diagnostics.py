"""Test APRS diagnostics."""

import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .conftest import RECEIVER_DATA, TEST_PASSCODE, MockAprsIs


async def test_receiver_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    mock_aprs_is: MockAprsIs,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the passcode is redacted from the diagnostics."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**RECEIVER_DATA, CONF_PASSWORD: TEST_PASSCODE}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
        == snapshot
    )


@pytest.mark.freeze_time("2026-10-21T09:05:00+00:00")
@pytest.mark.usefixtures("mock_client_is")
async def test_station_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_station_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test station diagnostics include the time of the last report."""
    hass.states.async_set(
        "zone.home", "0", {ATTR_LATITUDE: 42.562956, ATTR_LONGITUDE: -70.86915}
    )
    mock_station_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        await get_diagnostics_for_config_entry(hass, hass_client, mock_station_entry)
        == snapshot
    )
