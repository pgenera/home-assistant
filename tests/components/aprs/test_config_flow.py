"""Test the APRS config flow."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import (
    CONF_CALLSIGN,
    CONF_CALLSIGNS,
    CONF_COMMENT,
    CONF_LOCATION_ENTITY,
    CONF_POSITION_AMBIGUITY,
    CONF_SYMBOL,
    CONF_WEATHER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import (
    RECEIVER_DATA,
    RECEIVER_OPTIONS,
    STATION_DATA,
    STATION_LOGIN,
    TEST_PASSCODE,
    TEST_RECEIVER,
    TEST_SERVER,
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
RECEIVER_INPUT = {
    CONF_CALLSIGN: TEST_RECEIVER,
    CONF_HOST: TEST_SERVER,
    CONF_CALLSIGNS: ["XX0FOO*", "YY0BAR-1"],
}
IMPORT_DATA = {
    CONF_CALLSIGN: "n0call",
    CONF_PASSWORD: "-1",
    CONF_HOST: TEST_SERVER,
    CONF_CALLSIGNS: ["xx0foo*", "YY0BAR-1"],
}


async def _async_start(hass: HomeAssistant, next_step: str) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": next_step}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == next_step
    return result["flow_id"]


async def _async_start_station_step(hass: HomeAssistant) -> str:
    flow_id = await _async_start(hass, "station_login")
    result = await hass.config_entries.flow.async_configure(flow_id, STATION_LOGIN)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station"
    return flow_id


async def test_station_flow(hass: HomeAssistant, mock_client_is: MagicMock) -> None:
    """Test creating a weather station."""
    flow_id = await _async_start(hass, "station_login")

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**STATION_LOGIN, CONF_CALLSIGN: f" {TEST_STATION.lower()} "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station"
    mock_client_is.connect.assert_called_once_with()
    mock_client_is.close.assert_called_once_with()

    result = await hass.config_entries.flow.async_configure(flow_id, WEATHER_OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_STATION
    assert result["data"] == STATION_DATA
    assert result["options"] == WEATHER_OPTIONS
    assert result["result"].unique_id == TEST_STATION


@pytest.mark.usefixtures("mock_client_is")
async def test_station_flow_position_only(hass: HomeAssistant) -> None:
    """Test creating a station that only reports a position."""
    flow_id = await _async_start_station_step(hass)

    # Number selectors submit floats.
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**POSITION_OPTIONS, CONF_POSITION_AMBIGUITY: 1.0}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == POSITION_OPTIONS
    assert type(result["options"][CONF_POSITION_AMBIGUITY]) is int
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
            {CONF_PASSWORD: "-1"}, None, {"base": "invalid_auth"}, id="receive_only"
        ),
        pytest.param(
            {CONF_PASSWORD: "letters"},
            None,
            {"base": "invalid_auth"},
            id="non_numeric_passcode",
        ),
        pytest.param(
            {},
            LoginError("Password is incorrect"),
            {"base": "invalid_auth"},
            id="refused",
        ),
        pytest.param(
            {},
            LoginError("Failed to login"),
            {"base": "cannot_connect"},
            id="login_failed",
        ),
        pytest.param(
            {},
            AprsConnectionError("no banner from server"),
            {"base": "cannot_connect"},
            id="cannot_connect",
        ),
        pytest.param(
            {},
            IndexError("string index out of range"),
            {"base": "cannot_connect"},
            id="no_banner",
        ),
        pytest.param({}, RuntimeError, {"base": "unknown"}, id="unknown"),
    ],
)
async def test_station_flow_errors(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    user_input: dict[str, str],
    side_effect: Exception | None,
    errors: dict[str, str],
) -> None:
    """Test station login errors and recovering from them."""
    mock_client_is.connect.side_effect = side_effect
    flow_id = await _async_start(hass, "station_login")

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**STATION_LOGIN, **user_input}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(flow_id, STATION_LOGIN)
    assert result["step_id"] == "station"
    result = await hass.config_entries.flow.async_configure(flow_id, WEATHER_OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == TEST_STATION


@pytest.mark.usefixtures("mock_client_is")
async def test_station_flow_already_configured(
    hass: HomeAssistant, mock_station_entry: MockConfigEntry
) -> None:
    """Test a callsign can only be set up once."""
    mock_station_entry.add_to_hass(hass)
    flow_id = await _async_start(hass, "station_login")

    result = await hass.config_entries.flow.async_configure(flow_id, STATION_LOGIN)

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
            {CONF_SYMBOL: "a_"}, {CONF_SYMBOL: "invalid_symbol"}, id="lowercase_overlay"
        ),
        pytest.param(
            {CONF_SYMBOL: "/|"}, {CONF_SYMBOL: "invalid_symbol"}, id="reserved_code"
        ),
        pytest.param(
            {CONF_SYMBOL: "/-"},
            {CONF_SYMBOL: "weather_symbol_required"},
            id="weather_without_wx_symbol",
        ),
        pytest.param(
            {CONF_COMMENT: "Home\r\nN0CALL-13>APRS:!fake"},
            {CONF_COMMENT: "invalid_comment"},
            id="comment_line_break",
        ),
        pytest.param(
            {CONF_COMMENT: "a|b"}, {CONF_COMMENT: "invalid_comment"}, id="comment_pipe"
        ),
        pytest.param(
            {CONF_COMMENT: "h50 hello"},
            {CONF_COMMENT: "invalid_comment"},
            id="comment_like_weather",
        ),
        pytest.param(
            {CONF_COMMENT: "c10 test"},
            {CONF_COMMENT: "invalid_comment"},
            id="comment_like_wind",
        ),
        pytest.param(
            {CONF_SYMBOL: "/-", CONF_WEATHER: {}, CONF_COMMENT: "090/036 QTH"},
            {CONF_COMMENT: "invalid_comment"},
            id="comment_like_course_speed",
        ),
        pytest.param(
            {CONF_COMMENT: "x" * 37},
            {CONF_COMMENT: "comment_too_long"},
            id="comment_too_long_weather",
        ),
        pytest.param(
            {CONF_SYMBOL: "/-", CONF_WEATHER: {}, CONF_COMMENT: "x" * 44},
            {CONF_COMMENT: "comment_too_long"},
            id="comment_too_long_fixed",
        ),
    ],
)
@pytest.mark.usefixtures("mock_client_is")
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
    assert result["result"].unique_id == TEST_STATION


@pytest.mark.parametrize(
    ("user_input", "passcode"),
    [
        pytest.param({}, "-1", id="receive_only"),
        pytest.param({CONF_PASSWORD: TEST_PASSCODE}, TEST_PASSCODE, id="verified"),
    ],
)
async def test_receiver_flow(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    user_input: dict[str, str],
    passcode: str,
) -> None:
    """Test creating a receiver, with or without a passcode."""
    flow_id = await _async_start(hass, "receiver")

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            **RECEIVER_INPUT,
            **user_input,
            CONF_CALLSIGN: "n0call",
            CONF_CALLSIGNS: [" xx0foo* ", "", "YY0BAR-1"],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TEST_RECEIVER
    assert result["data"] == {**RECEIVER_DATA, CONF_PASSWORD: passcode}
    assert result["options"] == RECEIVER_OPTIONS
    assert result["result"].unique_id == TEST_RECEIVER
    mock_client_is.connect.assert_called_once_with()


@pytest.mark.parametrize(
    ("user_input", "side_effect", "errors"),
    [
        pytest.param(
            {CONF_CALLSIGN: "N0CALL-123"},
            None,
            {CONF_CALLSIGN: "invalid_callsign"},
            id="invalid_callsign",
        ),
        pytest.param(
            {CONF_CALLSIGNS: [""]},
            None,
            {CONF_CALLSIGNS: "invalid_callsigns"},
            id="no_callsigns",
        ),
        pytest.param(
            {CONF_CALLSIGNS: ["XX0*FOO"]},
            None,
            {CONF_CALLSIGNS: "invalid_callsigns"},
            id="bad_wildcard",
        ),
        pytest.param(
            {CONF_PASSWORD: "12345"}, None, {"base": "invalid_auth"}, id="passcode"
        ),
        pytest.param(
            {},
            AprsConnectionError("no banner from server"),
            {"base": "cannot_connect"},
            id="cannot_connect",
        ),
    ],
)
async def test_receiver_flow_errors(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    user_input: dict[str, Any],
    side_effect: Exception | None,
    errors: dict[str, str],
) -> None:
    """Test receiver errors and recovering from them."""
    mock_client_is.connect.side_effect = side_effect
    flow_id = await _async_start(hass, "receiver")

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**RECEIVER_INPUT, **user_input}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(flow_id, RECEIVER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == TEST_RECEIVER


@pytest.mark.usefixtures("mock_client_is")
async def test_receiver_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a login callsign can only be set up once."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _async_start(hass, "receiver")

    result = await hass.config_entries.flow.async_configure(flow_id, RECEIVER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_client_is")
async def test_import(hass: HomeAssistant) -> None:
    """Test importing a YAML device tracker."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=IMPORT_DATA
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == RECEIVER_DATA
    assert result["options"] == RECEIVER_OPTIONS
    assert result["result"].unique_id == TEST_RECEIVER


@pytest.mark.parametrize(
    ("import_data", "side_effect", "reason"),
    [
        pytest.param(
            {CONF_CALLSIGN: "N0-CALL"}, None, "invalid_callsign", id="callsign"
        ),
        pytest.param(
            {CONF_CALLSIGNS: ["XX0*FOO"]}, None, "invalid_callsigns", id="callsigns"
        ),
        pytest.param({CONF_PASSWORD: "12345"}, None, "invalid_auth", id="invalid_auth"),
        pytest.param(
            {},
            AprsConnectionError("no banner from server"),
            "cannot_connect",
            id="cannot_connect",
        ),
    ],
)
async def test_import_errors(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    import_data: dict[str, Any],
    side_effect: Exception | None,
    reason: str,
) -> None:
    """Test an import that cannot log in aborts."""
    mock_client_is.connect.side_effect = side_effect

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data={**IMPORT_DATA, **import_data}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("mock_client_is")
async def test_import_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test the YAML is not imported twice."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_IMPORT}, data=IMPORT_DATA
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_client_is: MagicMock,
) -> None:
    """Test updating a refused passcode, recovering from errors."""
    mock_station_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_station_entry, data={**STATION_DATA, CONF_PASSWORD: "1"}
    )
    result = await mock_station_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_CALLSIGN] == TEST_STATION

    mock_client_is.connect.side_effect = LoginError("Password is incorrect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSCODE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: TEST_PASSCODE}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_station_entry.data == STATION_DATA


@pytest.mark.usefixtures("mock_client_is")
async def test_reauth_receiver_receive_only(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a receiver can fall back to logging in receive-only."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**RECEIVER_DATA, CONF_PASSWORD: "1"}
    )
    result = await mock_config_entry.start_reauth_flow(hass)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == RECEIVER_DATA


async def test_options_flow_station(
    hass: HomeAssistant,
    mock_station_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test changing station options, recovering from errors."""
    mock_station_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_station_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_station_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station"

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
    assert mock_station_entry.options == POSITION_OPTIONS
    assert len(mock_setup_entry.mock_calls) == 2


@pytest.mark.usefixtures("mock_unload_entry")
async def test_options_flow_receiver(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_setup_entry: AsyncMock,
) -> None:
    """Test changing the tracked callsigns, recovering from errors."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "receiver"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CALLSIGNS: ["bad callsign"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CALLSIGNS: "invalid_callsigns"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CALLSIGNS: ["zz0baz-7"]}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {CONF_CALLSIGNS: ["ZZ0BAZ-7"]}
    assert len(mock_setup_entry.mock_calls) == 2


@pytest.mark.parametrize(
    ("entry_fixture", "user_input", "data"),
    [
        pytest.param(
            "mock_config_entry",
            {CONF_HOST: " aprs2.example.net "},
            {**RECEIVER_DATA, CONF_HOST: "aprs2.example.net"},
            id="receiver_keep_passcode",
        ),
        pytest.param(
            "mock_station_entry",
            {CONF_HOST: "aprs2.example.net"},
            {**STATION_DATA, CONF_HOST: "aprs2.example.net"},
            id="station_keep_passcode",
        ),
        pytest.param(
            "mock_station_entry",
            {CONF_HOST: "aprs2.example.net", CONF_PASSWORD: ""},
            {**STATION_DATA, CONF_HOST: "aprs2.example.net"},
            id="station_empty_passcode",
        ),
        pytest.param(
            "mock_station_entry",
            {CONF_HOST: "aprs2.example.net", CONF_PASSWORD: TEST_PASSCODE},
            {**STATION_DATA, CONF_HOST: "aprs2.example.net"},
            id="station_passcode",
        ),
    ],
)
@pytest.mark.usefixtures("mock_unload_entry")
async def test_reconfigure(
    hass: HomeAssistant,
    request: pytest.FixtureRequest,
    mock_client_is: MagicMock,
    entry_fixture: str,
    user_input: dict[str, str],
    data: dict[str, str],
) -> None:
    """Test changing the server and passcode, recovering from errors."""
    entry: MockConfigEntry = request.getfixturevalue(entry_fixture)
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**user_input, CONF_PASSWORD: "12345"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client_is.connect.side_effect = AprsConnectionError("no banner from server")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == data


@pytest.mark.parametrize(
    ("entry_fixture", "step", "user_input"),
    [
        pytest.param(
            "mock_config_entry",
            "station_login",
            {**STATION_LOGIN, CONF_CALLSIGN: TEST_RECEIVER},
            id="station_after_receiver",
        ),
        pytest.param(
            "mock_station_entry",
            "receiver",
            {**RECEIVER_INPUT, CONF_CALLSIGN: TEST_STATION},
            id="receiver_after_station",
        ),
    ],
)
@pytest.mark.usefixtures("mock_client_is")
async def test_callsign_in_use(
    hass: HomeAssistant,
    request: pytest.FixtureRequest,
    entry_fixture: str,
    step: str,
    user_input: dict[str, Any],
) -> None:
    """Test a callsign used by the other kind of entry is refused."""
    request.getfixturevalue(entry_fixture).add_to_hass(hass)
    flow_id = await _async_start(hass, step)

    result = await hass.config_entries.flow.async_configure(flow_id, user_input)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "callsign_in_use"
