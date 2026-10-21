"""Config flow for publishing to APRS-IS."""

from collections.abc import Mapping
import logging
import re
from typing import Any, override

import aprslib
from aprslib import ConnectionError as AprsConnectionError, LoginError
import probatio

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CALLSIGN,
    CONF_COMMENT,
    CONF_HUMIDITY,
    CONF_LOCATION_ENTITY,
    CONF_LUMINOSITY,
    CONF_POSITION_AMBIGUITY,
    CONF_PRESSURE,
    CONF_RAIN_1H,
    CONF_RAIN_24H,
    CONF_RAIN_SINCE_MIDNIGHT,
    CONF_SYMBOL,
    CONF_TEMPERATURE,
    CONF_WEATHER,
    CONF_WIND_DIRECTION,
    CONF_WIND_GUST,
    CONF_WIND_SPEED,
    DEFAULT_HOST,
    DEFAULT_LOCATION_ENTITY,
    DEFAULT_SYMBOL,
    DOMAIN,
    WEATHER_SYMBOL_CODE,
)
from .publisher import AprsIsClient

_LOGGER = logging.getLogger(__name__)

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{3,6}(-[A-Z0-9]{1,2})?$")

STEP_USER_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CALLSIGN): TextSelector(),
        probatio.Required(probatio.Secret(CONF_PASSWORD)): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        probatio.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(),
    }
)
STEP_REAUTH_DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(probatio.Secret(CONF_PASSWORD)): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

WEATHER_DEVICE_CLASSES: dict[str, list[str]] = {
    CONF_TEMPERATURE: [SensorDeviceClass.TEMPERATURE],
    CONF_HUMIDITY: [SensorDeviceClass.HUMIDITY],
    CONF_PRESSURE: [
        SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        SensorDeviceClass.PRESSURE,
    ],
    CONF_WIND_DIRECTION: [SensorDeviceClass.WIND_DIRECTION],
    CONF_WIND_SPEED: [SensorDeviceClass.WIND_SPEED],
    CONF_WIND_GUST: [SensorDeviceClass.WIND_SPEED],
    CONF_RAIN_1H: [SensorDeviceClass.PRECIPITATION],
    CONF_RAIN_24H: [SensorDeviceClass.PRECIPITATION],
    CONF_RAIN_SINCE_MIDNIGHT: [SensorDeviceClass.PRECIPITATION],
    CONF_LUMINOSITY: [SensorDeviceClass.IRRADIANCE],
}

STATION_SCHEMA = probatio.Schema(
    {
        probatio.Required(
            CONF_LOCATION_ENTITY, default=DEFAULT_LOCATION_ENTITY
        ): EntitySelector(
            EntitySelectorConfig(domain=["device_tracker", "person", "zone"])
        ),
        probatio.Required(CONF_SYMBOL, default=DEFAULT_SYMBOL): TextSelector(),
        probatio.Optional(CONF_COMMENT): TextSelector(),
        probatio.Required(CONF_POSITION_AMBIGUITY, default=0): NumberSelector(
            NumberSelectorConfig(min=0, max=4, step=1, mode=NumberSelectorMode.BOX)
        ),
        probatio.Required(CONF_WEATHER): section(
            probatio.Schema(
                {
                    probatio.Optional(key): EntitySelector(
                        EntitySelectorConfig(domain="sensor", device_class=classes)
                    )
                    for key, classes in WEATHER_DEVICE_CLASSES.items()
                }
            ),
            {"collapsed": False},
        ),
    }
)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate the passcode was refused."""


async def validate_login(hass: HomeAssistant, data: Mapping[str, Any]) -> None:
    """Check the passcode and that the server accepts it."""
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
        raise InvalidAuth from err
    except AprsConnectionError as err:
        raise CannotConnect from err


def validate_station(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Return errors for station options."""
    symbol: str = user_input[CONF_SYMBOL]
    if len(symbol) != 2 or not (symbol[0] in "/\\" or symbol[0].isalnum()):
        return {CONF_SYMBOL: "invalid_symbol"}
    if any(user_input[CONF_WEATHER].values()) and symbol[1] != WEATHER_SYMBOL_CODE:
        return {CONF_SYMBOL: "weather_symbol_required"}
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
    """Set up an APRS-IS station."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._data: dict[str, Any] = {}

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
        """Ask for the station login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_CALLSIGN] = user_input[CONF_CALLSIGN].strip().upper()
            if not CALLSIGN_RE.match(user_input[CONF_CALLSIGN]):
                errors[CONF_CALLSIGN] = "invalid_callsign"
            else:
                await self.async_set_unique_id(user_input[CONF_CALLSIGN])
                self._abort_if_unique_id_configured()
                errors = await _async_login_errors(self.hass, user_input)
            if not errors:
                self._data = user_input
                return await self.async_step_station()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask what the station reports."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_station(user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._data[CONF_CALLSIGN],
                    data=self._data,
                    options=user_input,
                )
        return self.async_show_form(
            step_id="station",
            data_schema=self.add_suggested_values_to_schema(STATION_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a refused passcode."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new passcode."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await _async_login_errors(
                self.hass, {**reauth_entry.data, **user_input}
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={CONF_CALLSIGN: reauth_entry.data[CONF_CALLSIGN]},
        )


class AprsOptionsFlow(OptionsFlowWithReload):
    """Change what a station reports."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the station options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_station(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                STATION_SCHEMA, user_input or self.config_entry.options
            ),
            errors=errors,
        )
