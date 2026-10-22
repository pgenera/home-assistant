"""The APRS component."""

from aprslib import ConnectionError as AprsConnectionError, LoginError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.device_registry import AnyDeviceEntry

from .client import passcode_refused
from .const import CONF_CALLSIGN, CONF_CALLSIGNS, DOMAIN
from .receiver import AprsReceiver

PLATFORMS = [Platform.DEVICE_TRACKER]

type AprsConfigEntry = ConfigEntry[AprsReceiver]


async def async_setup_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Set up an APRS-IS receiver from a config entry."""
    receiver = AprsReceiver(
        hass,
        entry.data[CONF_CALLSIGN],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_HOST],
        entry.options[CONF_CALLSIGNS],
    )
    try:
        await hass.async_add_executor_job(receiver.connect)
    except LoginError as err:
        if passcode_refused(err):
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
                translation_placeholders={CONF_CALLSIGN: entry.data[CONF_CALLSIGN]},
            ) from err
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={CONF_HOST: entry.data[CONF_HOST]},
        ) from err
    except AprsConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={CONF_HOST: entry.data[CONF_HOST]},
        ) from err
    entry.runtime_data = receiver
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    receiver.start()

    async def _async_stop(_event: Event) -> None:
        await hass.async_add_executor_job(receiver.stop)

    # Config entries are not unloaded on shutdown, so close the feed here.
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )


async def async_unload_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    await hass.async_add_executor_job(entry.runtime_data.stop)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AprsConfigEntry, device: AnyDeviceEntry
) -> bool:
    """Allow removing a station; it comes back if it is heard again."""
    return True
