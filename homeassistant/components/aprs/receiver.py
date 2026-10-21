"""Receive station positions from APRS-IS."""

from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import logging
import socket
import threading
from typing import Any

from aprslib import IS, ConnectionError as AprsConnectionError, LoginError
import geopy.distance

from homeassistant.const import ATTR_LATITUDE, ATTR_LONGITUDE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import FILTER_PORT, RECONNECT_DELAY, WEATHER_SYMBOL_CODE

_LOGGER = logging.getLogger(__name__)

ATTR_ALTITUDE = "altitude"
ATTR_COMMENT = "comment"
ATTR_COURSE = "course"
ATTR_FORMAT = "format"
ATTR_FROM = "from"
ATTR_OBJECT_NAME = "object_name"
ATTR_POS_AMBIGUITY = "posambiguity"
ATTR_SPEED = "speed"
ATTR_SYMBOL = "symbol"
ATTR_WEATHER = "weather"

MSG_FORMATS = ["compressed", "uncompressed", "mic-e", "object"]
EXTRA_ATTRIBUTES = (ATTR_ALTITUDE, ATTR_COMMENT, ATTR_COURSE, ATTR_SPEED)


@dataclass(frozen=True, kw_only=True)
class AprsStation:
    """A position heard from a station or object."""

    name: str
    latitude: float
    longitude: float
    gps_accuracy: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)


def make_filter(callsigns: list[str]) -> str:
    """Make a server-side filter from a list of callsigns."""
    return " ".join(f"b/{sign.upper()}" for sign in callsigns)


def gps_accuracy(gps: tuple[float, float], posambiguity: int) -> int:
    """Calculate the GPS accuracy based on APRS posambiguity."""

    pos_a_map = {0: 0, 1: 1 / 600, 2: 1 / 60, 3: 1 / 6, 4: 1}
    if posambiguity in pos_a_map:
        degrees = pos_a_map[posambiguity]

        gps2 = (gps[0], gps[1] + degrees)
        dist_m: float = geopy.distance.distance(gps, gps2).m

        accuracy = round(dist_m)
    else:
        message = f"APRS position ambiguity must be 0-4, not '{posambiguity}'."
        raise ValueError(message)

    return accuracy


def parse_station(msg: dict[str, Any]) -> AprsStation | None:
    """Return the position in a parsed packet, or None if it has none.

    Weather station reports are skipped: they are fixed stations, not
    something to track.
    """
    if (
        msg[ATTR_FORMAT] not in MSG_FORMATS
        or msg.get(ATTR_SYMBOL) == WEATHER_SYMBOL_CODE
        or ATTR_WEATHER in msg
    ):
        return None
    name = msg[ATTR_OBJECT_NAME] if msg[ATTR_FORMAT] == "object" else msg[ATTR_FROM]
    lat = msg[ATTR_LATITUDE]
    lon = msg[ATTR_LONGITUDE]
    accuracy = 0
    if ATTR_POS_AMBIGUITY in msg:
        try:
            accuracy = gps_accuracy((lat, lon), msg[ATTR_POS_AMBIGUITY])
        except ValueError:
            _LOGGER.warning(
                "APRS message contained invalid posambiguity: %s",
                msg[ATTR_POS_AMBIGUITY],
            )
    return AprsStation(
        name=name.strip(),
        latitude=lat,
        longitude=lon,
        gps_accuracy=accuracy,
        attributes={attr: msg[attr] for attr in EXTRA_ATTRIBUTES if attr in msg},
    )


class AprsReceiver:
    """A filtered APRS-IS feed, read on its own thread."""

    def __init__(
        self,
        hass: HomeAssistant,
        callsign: str,
        passcode: str,
        host: str,
        callsigns: list[str],
    ) -> None:
        """Initialize the receiver."""
        self.hass = hass
        self.callsign = callsign
        self._host = host
        self._ais = IS(callsign, passwd=passcode, host=host, port=FILTER_PORT)
        self._ais.set_filter(make_filter(callsigns))
        self._stopping = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"APRS-IS receiver {callsign}", daemon=True
        )
        self._listeners: list[Callable[[AprsStation], None]] = []

    def connect(self) -> None:
        """Log in to the server, raising if it fails."""
        self._ais.connect()

    def start(self) -> None:
        """Start reading packets."""
        self._thread.start()

    def stop(self) -> None:
        """Stop reading packets and wait for the thread to exit."""
        self._stopping.set()
        # Closing alone would not wake the thread blocked in select().
        if (sock := self._ais.sock) is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
        self._thread.join()
        self._ais.close()

    @callback
    def async_add_listener(
        self, update_callback: Callable[[AprsStation], None]
    ) -> CALLBACK_TYPE:
        """Call update_callback with each position heard."""
        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(update_callback)

        return remove_listener

    def _run(self) -> None:
        connected = True
        while not self._stopping.is_set():
            if not connected:
                try:
                    self._ais.connect()
                except AprsConnectionError, LoginError:
                    self._stopping.wait(RECONNECT_DELAY)
                    continue
                _LOGGER.info("Reconnected to APRS-IS server %s", self._host)
                connected = True
            try:
                self._ais.consumer(self._receive, immortal=False)
            except AprsConnectionError:
                if self._stopping.is_set():
                    return
                _LOGGER.warning(
                    "Lost connection to APRS-IS server %s, reconnecting", self._host
                )
                connected = False

    def _receive(self, msg: dict[str, Any]) -> None:
        _LOGGER.debug("APRS message received: %s", msg)
        if (station := parse_station(msg)) is not None:
            self.hass.loop.call_soon_threadsafe(self._async_dispatch, station)

    @callback
    def _async_dispatch(self, station: AprsStation) -> None:
        for update_callback in list(self._listeners):
            update_callback(station)
