"""Config flow for APRS-IS receivers."""

from collections.abc import Mapping
import logging
import re
from typing import Any, override

import aprslib
from aprslib import ConnectionError as AprsConnectionError, LoginError
import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import AprsIsClient, passcode_refused
from .const import (
    CONF_CALLSIGN,
    CONF_CALLSIGNS,
    DEFAULT_HOST,
    DOMAIN,
    RECEIVE_ONLY_PASSCODE,
)

_LOGGER = logging.getLogger(__name__)

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,6}(-[A-Z0-9]{1,2})?$")

CALLSIGN_FILTER_RE = re.compile(r"^[A-Z0-9-]{1,9}\*?$")

PASSCODE_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CALLSIGN): TextSelector(),
        probatio.Optional(probatio.Secret(CONF_PASSWORD)): PASSCODE_SELECTOR,
        probatio.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(),
        probatio.Required(CONF_CALLSIGNS): TextSelector(
            TextSelectorConfig(multiple=True)
        ),
    }
)
OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CALLSIGNS): TextSelector(
            TextSelectorConfig(multiple=True)
        ),
    }
)
STEP_REAUTH_SCHEMA = probatio.Schema(
    {probatio.Optional(probatio.Secret(CONF_PASSWORD)): PASSCODE_SELECTOR}
)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate the passcode was refused."""


async def validate_login(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """Check the passcode, if any, and that the server accepts it."""
    if data[CONF_PASSWORD] != RECEIVE_ONLY_PASSCODE:
        try:
            valid = int(data[CONF_PASSWORD]) == aprslib.passcode(data[CONF_CALLSIGN])
        except ValueError as err:
            raise InvalidAuth from err
        if not valid:
            raise InvalidAuth
    client = AprsIsClient(data[CONF_CALLSIGN], data[CONF_PASSWORD], data[CONF_HOST])
    try:
        await hass.async_add_executor_job(client.check_login)
    except LoginError as err:
        if passcode_refused(err):
            raise InvalidAuth from err
        raise CannotConnect from err
    except AprsConnectionError as err:
        raise CannotConnect from err


def normalize_callsigns(user_input: dict[str, Any]) -> dict[str, str]:
    """Upper-case the tracked callsigns, returning errors if any is invalid."""
    callsigns = [sign.strip().upper() for sign in user_input[CONF_CALLSIGNS]]
    user_input[CONF_CALLSIGNS] = [sign for sign in callsigns if sign]
    if not user_input[CONF_CALLSIGNS] or not all(
        CALLSIGN_FILTER_RE.match(sign) for sign in user_input[CONF_CALLSIGNS]
    ):
        return {CONF_CALLSIGNS: "invalid_callsigns"}
    return {}


async def _async_login_errors(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> dict[str, str]:
    try:
        await validate_login(hass, data)
    except CannotConnect:
        return {"base": "cannot_connect"}
    except InvalidAuth:
        return {"base": "invalid_auth"}
    except Exception:
        _LOGGER.exception("Unexpected exception")
        return {"base": "unknown"}
    return {}


class AprsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up an APRS-IS receiver."""

    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> AprsOptionsFlow:
        """Get the options flow."""
        return AprsOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the login and the callsigns to track."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_PASSWORD: RECEIVE_ONLY_PASSCODE,
                **user_input,
                CONF_CALLSIGN: user_input[CONF_CALLSIGN].strip().upper(),
                CONF_HOST: user_input[CONF_HOST].strip(),
            }
            if not CALLSIGN_RE.match(data[CONF_CALLSIGN]):
                errors[CONF_CALLSIGN] = "invalid_callsign"
            else:
                errors = normalize_callsigns(data)
            if not errors:
                await self.async_set_unique_id(data[CONF_CALLSIGN])
                self._abort_if_unique_id_configured()
                errors = await _async_login_errors(self.hass, data)
            if not errors:
                return self._async_create_receiver(data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Import a YAML device tracker."""
        data = {
            **import_data,
            CONF_CALLSIGN: import_data[CONF_CALLSIGN].strip().upper(),
        }
        await self.async_set_unique_id(data[CONF_CALLSIGN])
        self._abort_if_unique_id_configured()
        if not CALLSIGN_RE.match(data[CONF_CALLSIGN]):
            return self.async_abort(reason="invalid_callsign")
        if errors := normalize_callsigns(data):
            return self.async_abort(reason=errors[CONF_CALLSIGNS])
        if errors := await _async_login_errors(self.hass, data):
            return self.async_abort(reason=errors["base"])
        return self._async_create_receiver(data)

    def _async_create_receiver(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=data[CONF_CALLSIGN],
            data={
                CONF_CALLSIGN: data[CONF_CALLSIGN],
                CONF_PASSWORD: data[CONF_PASSWORD],
                CONF_HOST: data[CONF_HOST],
            },
            options={CONF_CALLSIGNS: data[CONF_CALLSIGNS]},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a refused passcode."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new passcode, or none to log in receive-only."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            passcode = {CONF_PASSWORD: RECEIVE_ONLY_PASSCODE, **user_input}
            errors = await _async_login_errors(
                self.hass, {**reauth_entry.data, **passcode}
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=passcode
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={CONF_CALLSIGN: reauth_entry.data[CONF_CALLSIGN]},
        )


class AprsOptionsFlow(OptionsFlowWithReload):
    """Change which callsigns a receiver tracks."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the tracked callsigns."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = normalize_callsigns(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )
