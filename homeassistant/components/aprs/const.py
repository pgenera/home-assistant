"""Constants for the APRS integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "aprs"

CONF_CALLSIGN: Final = "callsign"
CONF_CALLSIGNS: Final = "callsigns"
CONF_COMMENT: Final = "comment"
CONF_ENTRY_TYPE: Final = "entry_type"
CONF_LOCATION_ENTITY: Final = "location_entity"
CONF_MOTION_ENTITY: Final = "motion_entity"
CONF_POSITION_AMBIGUITY: Final = "position_ambiguity"
CONF_REPORT_INTERVAL: Final = "report_interval"
CONF_SEND_ALTITUDE: Final = "send_altitude"
CONF_SMART_BEACONING: Final = "smart_beaconing"
CONF_SUPPRESS_TEMPLATE: Final = "suppress_template"
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

# Weather networks ask for one report every 5-10 minutes, and no more often;
# fixed positions need far less.
WEATHER_INTERVAL: Final = timedelta(minutes=10)
POSITION_INTERVAL: Final = timedelta(minutes=30)
MIN_WEATHER_INTERVAL: Final = timedelta(minutes=5)

# The least time between reports for a turn or stop.
MIN_TURN_TIME: Final = timedelta(seconds=30)

# Faster than this between two positions, in mph, is a jump rather than travel.
MAX_SPEED: Final = 150
