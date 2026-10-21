"""Fixtures for APRS tests."""

from collections.abc import Generator
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aprslib
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import (
    CONF_CALLSIGN,
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
    DOMAIN,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

TEST_STATION = "N0CALL-13"
TEST_PASSCODE = str(aprslib.passcode("N0CALL"))
TEST_SERVER = "aprs.example.com"

STATION_DATA = {
    CONF_CALLSIGN: TEST_STATION,
    CONF_PASSWORD: TEST_PASSCODE,
    CONF_HOST: TEST_SERVER,
}
WEATHER_ENTITIES = {
    CONF_TEMPERATURE: "sensor.temperature",
    CONF_HUMIDITY: "sensor.humidity",
    CONF_PRESSURE: "sensor.pressure",
    CONF_WIND_DIRECTION: "sensor.wind_direction",
    CONF_WIND_SPEED: "sensor.wind_speed",
    CONF_WIND_GUST: "sensor.wind_gust",
    CONF_RAIN_1H: "sensor.rain_1h",
    CONF_RAIN_24H: "sensor.rain_24h",
    CONF_RAIN_SINCE_MIDNIGHT: "sensor.rain_today",
    CONF_LUMINOSITY: "sensor.irradiance",
}
WEATHER_OPTIONS = {
    CONF_LOCATION_ENTITY: "zone.home",
    CONF_SYMBOL: "/_",
    CONF_POSITION_AMBIGUITY: 0,
    CONF_WEATHER: WEATHER_ENTITIES,
}


class MockAprsIs:
    """Stand-in for an aprslib.IS connection.

    consumer() blocks like the real one until close() is called, so the
    listener thread runs exactly as it does against a real server.
    """

    def __init__(self, mock_class: MagicMock) -> None:
        """Initialize the mock."""
        self.mock_class = mock_class
        self.client = client = mock_class.return_value
        self.connect_allowed = threading.Event()
        self.connect_allowed.set()
        self._callback: Any = None
        self._consuming = threading.Event()
        self._closed = threading.Event()
        self._threads: list[threading.Thread] = []
        client.connect.side_effect = self._connect
        client.consumer.side_effect = self._consumer
        client.close.side_effect = self._closed.set

    def _connect(self) -> None:
        self._threads.append(threading.current_thread())
        self.connect_allowed.wait()

    def _consumer(self, callback: Any, **kwargs: Any) -> None:
        self._callback = callback
        self._consuming.set()
        self._closed.wait()
        # A closed socket makes the real consumer raise OSError.
        raise OSError("Socket closed")

    async def async_receive(self, hass: HomeAssistant, packet: dict[str, Any]) -> None:
        """Deliver a packet from the listener thread's side."""
        assert await hass.async_add_executor_job(self._consuming.wait, 5)
        await hass.async_add_executor_job(self._callback, packet)
        await hass.async_block_till_done()

    def join(self) -> None:
        """Wait for listener threads to exit."""
        for thread in self._threads:
            thread.join(5)
            assert not thread.is_alive()

    def shutdown(self) -> None:
        """Release and reap all listener threads."""
        self.connect_allowed.set()
        self._closed.set()
        self.join()


@pytest.fixture
def mock_aprs_is() -> Generator[MockAprsIs]:
    """Mock the APRS-IS connection used by the device tracker."""
    with patch(
        "homeassistant.components.aprs.device_tracker.aprslib.IS", autospec=True
    ) as mock_is:
        aprs_is = MockAprsIs(mock_is)
        yield aprs_is
        aprs_is.shutdown()


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.aprs.async_setup_entry", return_value=None
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def options() -> dict[str, Any]:
    """Return the station options."""
    return WEATHER_OPTIONS


@pytest.fixture
def mock_config_entry(options: dict[str, Any]) -> MockConfigEntry:
    """Return a station config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TEST_STATION,
        unique_id=TEST_STATION,
        data=STATION_DATA,
        options=options,
    )


@pytest.fixture
def mock_publisher_is() -> Generator[MagicMock]:
    """Mock the APRS-IS connections used to send reports."""
    with patch(
        "homeassistant.components.aprs.publisher.aprslib.IS", autospec=True
    ) as mock_is:
        yield mock_is.return_value
