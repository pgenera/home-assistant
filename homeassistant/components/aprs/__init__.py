"""The APRS component."""

from collections.abc import Callable
from functools import partial

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

from .client import AprsIsClient, passcode_refused
from .const import (
    CONF_CALLSIGN,
    CONF_CALLSIGNS,
    CONF_ENTRY_TYPE,
    DOMAIN,
    ENTRY_TYPE_RECEIVER,
)
from .publisher import AprsPublisher
from .receiver import AprsReceiver

RECEIVER_PLATFORMS = [Platform.DEVICE_TRACKER]
STATION_PLATFORMS = [Platform.SENSOR]

type AprsConfigEntry = ConfigEntry[AprsPublisher | AprsReceiver]


def _is_receiver(entry: AprsConfigEntry) -> bool:
    return bool(entry.data[CONF_ENTRY_TYPE] == ENTRY_TYPE_RECEIVER)


async def _async_connect(
    hass: HomeAssistant, entry: AprsConfigEntry, connect: Callable[[], None]
) -> None:
    try:
        await hass.async_add_executor_job(connect)
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


async def async_setup_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Set up an APRS-IS station or receiver from a config entry."""
    if _is_receiver(entry):
        receiver = AprsReceiver(
            hass,
            entry.data[CONF_CALLSIGN],
            entry.data[CONF_PASSWORD],
            entry.data[CONF_HOST],
            entry.options[CONF_CALLSIGNS],
        )
        await _async_connect(hass, entry, receiver.connect)
        entry.runtime_data = receiver
        await hass.config_entries.async_forward_entry_setups(entry, RECEIVER_PLATFORMS)
        receiver.start()

        async def _async_stop(_event: Event) -> None:
            await hass.async_add_executor_job(receiver.stop)

        # Config entries are not unloaded on shutdown, so close the feed here.
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
        )
        return

    client = AprsIsClient(
        entry.data[CONF_CALLSIGN], entry.data[CONF_PASSWORD], entry.data[CONF_HOST]
    )
    await _async_connect(hass, entry, client.check_login)
    publisher = AprsPublisher(
        hass, client, entry.options, partial(entry.async_start_reauth, hass)
    )
    entry.runtime_data = publisher
    await hass.config_entries.async_forward_entry_setups(entry, STATION_PLATFORMS)
    entry.async_on_unload(publisher.async_start())


async def async_unload_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Unload a config entry."""
    if not _is_receiver(entry):
        await hass.config_entries.async_unload_platforms_or_raise(
            entry, STATION_PLATFORMS
        )
        return
    await hass.config_entries.async_unload_platforms_or_raise(entry, RECEIVER_PLATFORMS)
    receiver = entry.runtime_data
    assert isinstance(receiver, AprsReceiver)
    await hass.async_add_executor_job(receiver.stop)


async def async_migrate_entry(hass: HomeAssistant, entry: AprsConfigEntry) -> None:
    """Migrate an older config entry."""
    if entry.minor_version < 2:
        # Every entry was a receiver before stations were added.
        hass.config_entries.async_update_entry(
            entry,
            data={CONF_ENTRY_TYPE: ENTRY_TYPE_RECEIVER, **entry.data},
            minor_version=2,
        )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AprsConfigEntry, device: AnyDeviceEntry
) -> bool:
    """Allow removing a heard station; it comes back if it is heard again."""
    return _is_receiver(entry)
