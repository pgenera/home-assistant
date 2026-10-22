"""Diagnostics support for APRS."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import AprsConfigEntry
from .receiver import AprsReceiver

TO_REDACT = {CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AprsConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime_data = entry.runtime_data
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": dict(entry.options),
        **(
            {"connected": runtime_data.connected}
            if isinstance(runtime_data, AprsReceiver)
            else {"last_report": runtime_data.last_report}
        ),
    }
