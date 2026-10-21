"""Fixtures for APRS tests."""

from collections.abc import Generator
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant


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
