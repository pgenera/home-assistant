"""Short-lived APRS-IS connections."""

from aprslib import IS, ConnectionError as AprsConnectionError, LoginError

from .const import FILTER_PORT


class AprsIsClient:
    """Log in to APRS-IS, or send a packet, without staying connected."""

    def __init__(self, callsign: str, passcode: str, host: str) -> None:
        """Initialize the client."""
        self.callsign = callsign
        self._passcode = passcode
        self._host = host

    def _connect(self) -> IS:
        ais = IS(
            self.callsign, passwd=self._passcode, host=self._host, port=FILTER_PORT
        )
        connect(ais)
        return ais

    def check_login(self) -> None:
        """Log in and disconnect, raising if the server refuses us."""
        self._connect().close()

    def send(self, packet: str) -> None:
        """Send one packet.

        A connection per report means we never hold a socket whose server
        keepalives nobody reads.
        """
        ais = self._connect()
        try:
            ais.sendall(packet)
        finally:
            ais.close()


def connect(ais: IS) -> None:
    """Connect and log in, raising aprslib's ConnectionError if it fails."""
    try:
        ais.connect()
    except IndexError as err:
        # aprslib reads the server's banner without checking it got one.
        ais.close()
        raise AprsConnectionError("no banner from server") from err


def passcode_refused(err: LoginError) -> bool:
    """Return whether a failed login means the passcode was refused.

    aprslib raises LoginError for any failed login, such as a timeout waiting
    for the server's reply.
    """
    return str(err) == "Password is incorrect"
