"""The APRS component."""

from aprslib import ConnectionError as AprsConnectionError, LoginError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import CONF_CALLSIGN, DOMAIN
from .publisher import AprsIsClient, AprsPublisher

PLATFORMS = [Platform.SENSOR]

type AprsConfigEntry = ConfigEntry[AprsPublisher]


async def async_setup_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Set up an APRS-IS station from a config entry."""
    client = AprsIsClient(
        entry.data[CONF_CALLSIGN], entry.data[CONF_PASSWORD], entry.data[CONF_HOST]
    )
    try:
        await hass.async_add_executor_job(client.check_login)
    except LoginError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="invalid_auth",
            translation_placeholders={CONF_CALLSIGN: client.callsign},
        ) from err
    except AprsConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={CONF_HOST: entry.data[CONF_HOST]},
        ) from err

    publisher = AprsPublisher(hass, client, entry.options)
    entry.runtime_data = publisher
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(publisher.async_start())


async def async_unload_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
