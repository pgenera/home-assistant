"""Fixtures for APRS tests."""

from collections.abc import Generator
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aprslib
from aprslib import ConnectionError as AprsConnectionError
import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.components.aprs.const import CONF_CALLSIGN, CONF_CALLSIGNS, DOMAIN
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

TEST_PASSCODE = str(aprslib.passcode("N0CALL"))
TEST_SERVER = "aprs.example.com"

TEST_RECEIVER = "N0CALL"

RECEIVER_DATA = {
    CONF_CALLSIGN: TEST_RECEIVER,
    CONF_PASSWORD: "-1",
    CONF_HOST: TEST_SERVER,
}
RECEIVER_OPTIONS = {CONF_CALLSIGNS: ["XX0FOO*", "YY0BAR-1"]}


class MockAprsIs:
    """Stand-in for the receiver's aprslib.IS connection.

    consumer() blocks like the real one until the socket is shut down or the
    server drops the connection, so the receiver thread runs as it does
    against a real server.
    """

    def __init__(self, mock_class: MagicMock) -> None:
        """Initialize the mock."""
        self.mock_class = mock_class
        self.client = client = mock_class.return_value
        self._callback: Any = None
        self._error: Exception = AprsConnectionError("connection dropped")
        self._consuming = threading.Event()
        self._wake = threading.Event()
        client.consumer.side_effect = self._consumer
        client.sock = MagicMock()
        client.sock.shutdown.side_effect = lambda how: self._wake.set()

    def _consumer(self, callback: Any, **kwargs: Any) -> None:
        self._callback = callback
        self._consuming.set()
        self._wake.wait()
        self._wake.clear()
        self._consuming.clear()
        error, self._error = self._error, AprsConnectionError("connection dropped")
        raise error

    async def async_wait_consuming(self, hass: HomeAssistant) -> None:
        """Wait until the receiver thread is reading packets."""
        assert await hass.async_add_executor_job(self._consuming.wait, 5)

    async def async_receive(self, hass: HomeAssistant, raw_packet: str) -> None:
        """Deliver a packet from the receiver thread's side."""
        await self.async_receive_parsed(hass, aprslib.parse(raw_packet))

    async def async_receive_parsed(
        self, hass: HomeAssistant, packet: dict[str, Any]
    ) -> None:
        """Deliver an already parsed packet."""
        await self.async_wait_consuming(hass)
        await hass.async_add_executor_job(self._callback, packet)
        await hass.async_block_till_done()

    def set_read_error(self, error: Exception) -> None:
        """Make consumer() raise error when it next returns."""
        self._error = error

    def drop_connection(self, error: Exception | None = None) -> None:
        """Make the server drop the connection, or consumer() raise error."""
        if error is not None:
            self._error = error
        # Cleared here too, so waiting for consuming waits for the next read.
        self._consuming.clear()
        self._wake.set()


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "homeassistant.components.aprs.async_setup_entry", return_value=None
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_unload_entry() -> Generator[AsyncMock]:
    """Override async_unload_entry, which needs a real setup."""
    with patch(
        "homeassistant.components.aprs.async_unload_entry", return_value=None
    ) as mock_unload_entry:
        yield mock_unload_entry


@pytest.fixture
def mock_client_is() -> Generator[MagicMock]:
    """Mock the APRS-IS connections used to check logins."""
    with patch("homeassistant.components.aprs.client.IS", autospec=True) as mock_is:
        yield mock_is.return_value


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a receiver config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=TEST_RECEIVER,
        unique_id=TEST_RECEIVER,
        data=RECEIVER_DATA,
        options=RECEIVER_OPTIONS,
    )


@pytest.fixture
def mock_aprs_is() -> Generator[MockAprsIs]:
    """Mock the APRS-IS connection used by receivers."""
    with patch("homeassistant.components.aprs.receiver.IS", autospec=True) as mock_is:
        # Stopping Home Assistant shuts the receiver thread down.
        yield MockAprsIs(mock_is)
