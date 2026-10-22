"""Constants for the APRS integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "aprs"

CONF_CALLSIGN: Final = "callsign"
CONF_CALLSIGNS: Final = "callsigns"
CONF_COMMENT: Final = "comment"
CONF_ENTRY_TYPE: Final = "entry_type"
CONF_LOCATION_ENTITY: Final = "location_entity"
CONF_POSITION_AMBIGUITY: Final = "position_ambiguity"
CONF_SYMBOL: Final = "symbol"
CONF_WEATHER: Final = "weather"

CONF_HUMIDITY: Final = "humidity"
CONF_LUMINOSITY: Final = "luminosity"
CONF_PRESSURE: Final = "pressure"
CONF_RAIN_1H: Final = "rain_1h"
CONF_RAIN_24H: Final = "rain_24h"
CONF_RAIN_SINCE_MIDNIGHT: Final = "rain_since_midnight"
CONF_TEMPERATURE: Final = "temperature"
CONF_WIND_DIRECTION: Final = "wind_direction"
CONF_WIND_GUST: Final = "wind_gust"
CONF_WIND_SPEED: Final = "wind_speed"

ENTRY_TYPE_RECEIVER: Final = "receiver"
ENTRY_TYPE_STATION: Final = "station"

DEFAULT_HOST: Final = "rotate.aprs2.net"
DEFAULT_LOCATION_ENTITY: Final = "zone.home"
DEFAULT_SYMBOL: Final = "/_"

FILTER_PORT: Final = 14580

RECEIVE_ONLY_PASSCODE: Final = "-1"

RECONNECT_DELAY: Final = 30

# APZ is the experimental software range of the APRS tocall registry.
TOCALL: Final = "APZHAS"

WEATHER_SYMBOL_CODE: Final = "_"

ZONE_DOMAIN: Final = "zone"

# Weather networks ask for one report every 5-10 minutes; fixed positions
# need far less.
WEATHER_INTERVAL: Final = timedelta(minutes=10)
POSITION_INTERVAL: Final = timedelta(minutes=30)

# SmartBeaconing™ (a trademark of HamHUD.net), with the usual tracker
# defaults: POSITION_INTERVAL at or below SLOW_SPEED, FAST_RATE at or above
# FAST_SPEED, and a report after a turn of more than
# MIN_TURN_ANGLE + TURN_SLOPE / speed degrees.
SLOW_SPEED: Final = 5
FAST_SPEED: Final = 60
FAST_RATE: Final = timedelta(minutes=3)
MIN_TURN_ANGLE: Final = 28
TURN_SLOPE: Final = 255
MIN_TURN_TIME: Final = timedelta(seconds=30)

# Faster than this between two positions, in mph, is a jump rather than travel.
MAX_SPEED: Final = 150
