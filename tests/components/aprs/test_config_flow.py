"""Test the APRS config flow."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aprslib import ConnectionError as AprsConnectionError, LoginError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import CONF_CALLSIGN, CONF_CALLSIGNS, DOMAIN
from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import (
    RECEIVER_DATA,
    RECEIVER_OPTIONS,
    TEST_PASSCODE,
    TEST_RECEIVER,
    TEST_SERVER,
)

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

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


async def _async_start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


@pytest.mark.parametrize(
    ("user_input", "passcode"),
    [
        pytest.param({}, "-1", id="receive_only"),
        pytest.param({CONF_PASSWORD: TEST_PASSCODE}, TEST_PASSCODE, id="verified"),
    ],
)
async def test_user_flow(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    user_input: dict[str, str],
    passcode: str,
) -> None:
    """Test creating a receiver, with or without a passcode."""
    flow_id = await _async_start(hass)

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
    mock_client_is.close.assert_called_once_with()


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
async def test_user_flow_errors(
    hass: HomeAssistant,
    mock_client_is: MagicMock,
    user_input: dict[str, Any],
    side_effect: Exception | None,
    errors: dict[str, str],
) -> None:
    """Test errors and recovering from them."""
    mock_client_is.connect.side_effect = side_effect
    flow_id = await _async_start(hass)

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
async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test a login callsign can only be set up once."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await _async_start(hass)

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


@pytest.mark.parametrize(
    ("user_input", "passcode"),
    [
        pytest.param({CONF_PASSWORD: TEST_PASSCODE}, TEST_PASSCODE, id="passcode"),
        pytest.param({}, "-1", id="receive_only"),
    ],
)
@pytest.mark.usefixtures("mock_unload_entry")
async def test_reauth(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    user_input: dict[str, str],
    passcode: str,
) -> None:
    """Test updating a refused passcode, recovering from errors."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**RECEIVER_DATA, CONF_PASSWORD: "1"}
    )
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_CALLSIGN] == TEST_RECEIVER

    mock_client_is.connect.side_effect = LoginError("Password is incorrect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data == {**RECEIVER_DATA, CONF_PASSWORD: passcode}


@pytest.mark.usefixtures("mock_unload_entry")
async def test_options_flow(
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
    assert result["step_id"] == "init"

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
    ("user_input", "passcode"),
    [
        pytest.param({}, TEST_PASSCODE, id="keep_passcode"),
        pytest.param({CONF_PASSWORD: ""}, TEST_PASSCODE, id="empty_passcode"),
        pytest.param({CONF_PASSWORD: "-1"}, "-1", id="receive_only"),
    ],
)
@pytest.mark.usefixtures("mock_unload_entry")
async def test_reconfigure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_client_is: MagicMock,
    user_input: dict[str, str],
    passcode: str,
) -> None:
    """Test changing the server and passcode, recovering from errors."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**RECEIVER_DATA, CONF_PASSWORD: TEST_PASSCODE}
    )
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: TEST_SERVER, CONF_PASSWORD: "12345"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client_is.connect.side_effect = AprsConnectionError("no banner from server")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " aprs2.example.net ", **user_input}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client_is.connect.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: " aprs2.example.net ", **user_input}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {
        **RECEIVER_DATA,
        CONF_HOST: "aprs2.example.net",
        CONF_PASSWORD: passcode,
    }
