"""Test the APRS config flow."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import (
    CONF_CALLSIGN,
    CONF_COMMENT,
    CONF_LOCATION_ENTITY,
    CONF_POSITION_AMBIGUITY,
    CONF_SYMBOL,
    CONF_WEATHER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import (
    STATION_DATA,
    TEST_PASSCODE,
    TEST_STATION,
    WEATHER_ENTITIES,
    WEATHER_OPTIONS,
)

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

POSITION_OPTIONS = {
    CONF_LOCATION_ENTITY: "device_tracker.car",
    CONF_SYMBOL: "/>",
    CONF_COMMENT: "On the road",
    CONF_POSITION_AMBIGUITY: 1,
    CONF_WEATHER: {},
}


async def _async_start_station_step(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], STATION_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station"
    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, mock_publisher_is: MagicMock) -> None:
    """Test creating a weather station."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**STATION_DATA, CONF_CALLSIGN: f" {TEST_STATION.lower()} "},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station"
    mock_publisher_is.connect.assert_called_once_with()
    mock_publisher_is.close.assert_called_once_with()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEATHER_OPTIONS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_STATION
    assert result["data"] == STATION_DATA
    assert result["options"] == WEATHER_OPTIONS
    assert result["result"].unique_id == TEST_STATION


@pytest.mark.usefixtures("mock_publisher_is")
async def test_user_flow_position_only(hass: HomeAssistant) -> None:
    """Test creating a station that only reports a position."""
    flow_id = await _async_start_station_step(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, POSITION_OPTIONS)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == POSITION_OPTIONS
    assert result["result"].unique_id == TEST_STATION


@pytest.mark.parametrize(
    ("user_input", "side_effect", "errors"),
    [
        pytest.param(
            {CONF_CALLSIGN: "N0-CALL-13"},
            None,
            {CONF_CALLSIGN: "invalid_callsign"},
            id="invalid_callsign",
        ),
        pytest.param(
            {CONF_PASSWORD: "12345"}, None, {"base": "invalid_auth"}, id="passcode"
        ),
        pytest.param(
            {CONF_PASSWORD: "letters"},
            None,
            {"base": "invalid_auth"},
            id="non_numeric_passcode",
        ),
        pytest.param({}, LoginError("refused"), {"base": "invalid_auth"}, id="refused"),
        pytest.param(
            {},
            AprsConnectionError("no banner from server"),
            {"base": "cannot_connect"},
            id="cannot_connect",
        ),
        pytest.param({}, RuntimeError, {"base": "unknown"}, id="unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_publisher_is: MagicMock,
    user_input: dict[str, str],
    side_effect: Exception | None,
    errors: dict[str, str],
) -> None:
    """Test login errors and recovering from them."""
    mock_publisher_is.connect.side_effect = side_effect
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**STATION_DATA, **user_input}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    mock_publisher_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], STATION_DATA
    )
    assert result["step_id"] == "station"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], WEATHER_OPTIONS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_publisher_is")
async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a callsign can only be set up once."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], STATION_DATA
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("station_input", "errors"),
    [
        pytest.param(
            {CONF_SYMBOL: "_"}, {CONF_SYMBOL: "invalid_symbol"}, id="too_short"
        ),
        pytest.param(
            {CONF_SYMBOL: "#_"}, {CONF_SYMBOL: "invalid_symbol"}, id="bad_table"
        ),
        pytest.param(
            {CONF_SYMBOL: "/-"},
            {CONF_SYMBOL: "weather_symbol_required"},
            id="weather_without_wx_symbol",
        ),
    ],
)
@pytest.mark.usefixtures("mock_publisher_is")
async def test_station_errors(
    hass: HomeAssistant, station_input: dict[str, Any], errors: dict[str, str]
) -> None:
    """Test invalid station options and recovering from them."""
    flow_id = await _async_start_station_step(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**WEATHER_OPTIONS, **station_input}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    result = await hass.config_entries.flow.async_configure(flow_id, WEATHER_OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_publisher_is: MagicMock,
) -> None:
    """Test updating a refused passcode, recovering from errors."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**STATION_DATA, CONF_PASSWORD: "1"}
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_CALLSIGN] == TEST_STATION

    mock_publisher_is.connect.side_effect = LoginError("refused")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSCODE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_publisher_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSCODE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == STATION_DATA


async def test_options_flow(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test changing station options, recovering from errors."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**POSITION_OPTIONS, CONF_WEATHER: WEATHER_ENTITIES}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_SYMBOL: "weather_symbol_required"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], POSITION_OPTIONS
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == POSITION_OPTIONS
    assert len(mock_setup_entry.mock_calls) == 2
