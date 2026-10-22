"""Encode APRS position and weather reports."""

from dataclasses import dataclass
from datetime import datetime
import re

from .const import TOCALL

# Comments are printable ASCII except | and ~, which APRS-IS and Mic-E reserve.
_INVALID_COMMENT_CHARS = re.compile(r"[^\x20-\x7b\x7d]")
# Leading text a parser would read as a data extension such as course/speed.
_EXTENSION_LIKE = re.compile(r"\d{3}/\d{3}")
COMMENT_MAX_LENGTH = 43
COMMENT_MAX_LENGTH_AFTER_EXTENSION = 36


def valid_comment(comment: str) -> bool:
    """Return whether a comment can be sent as is."""
    return not (
        _INVALID_COMMENT_CHARS.search(comment) or _EXTENSION_LIKE.match(comment)
    )


@dataclass(frozen=True, kw_only=True)
class WeatherData:
    """Weather values in APRS units."""

    wind_direction: float | None = None
    """Degrees."""
    wind_speed: float | None = None
    """Sustained wind in mph."""
    wind_gust: float | None = None
    """Peak wind in mph."""
    temperature: float | None = None
    """Degrees Fahrenheit."""
    rain_1h: float | None = None
    """Inches."""
    rain_24h: float | None = None
    """Inches."""
    rain_since_midnight: float | None = None
    """Inches."""
    humidity: float | None = None
    """Percent."""
    pressure: float | None = None
    """Sea level pressure in hPa."""
    luminosity: float | None = None
    """W/m²."""


def _clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, round(value)))


# Hundredths of a minute kept at each ambiguity level: 0.01', 0.1', 1', 10', 1°.
AMBIGUITY_STEPS = (1, 10, 100, 1000, 6000)


def _ambiguate(coordinate: str, ambiguity: int) -> str:
    """Blank out the trailing minute digits, skipping the decimal point."""
    chars = list(coordinate)
    blanked = 0
    for index in range(len(chars) - 1, -1, -1):
        if blanked == ambiguity:
            break
        if chars[index] != ".":
            chars[index] = " "
            blanked += 1
    return "".join(chars)


def _format_coordinate(
    value: float, degree_digits: int, hemispheres: str, ambiguity: int
) -> str:
    # APRS 1.2 defines ambiguity as a precision, not a truncation, so round
    # to the kept digits, but not up to 90° or 180°, which parsers reject.
    step = AMBIGUITY_STEPS[ambiguity]
    limit = (90 if degree_digits == 2 else 180) * 6000 - step
    hundredths = min(round(abs(value) * 6000 / step) * step, limit)
    degrees, minutes = divmod(hundredths, 6000)
    text = f"{degrees:0{degree_digits}d}{minutes // 100:02d}.{minutes % 100:02d}"
    return _ambiguate(text, ambiguity) + hemispheres[value < 0]


def format_position(
    latitude: float, longitude: float, symbol: str, ambiguity: int = 0
) -> str:
    """Format an uncompressed position with its two character symbol."""
    lat = _format_coordinate(latitude, 2, "NS", ambiguity)
    lon = _format_coordinate(longitude, 3, "EW", ambiguity)
    return f"{lat}{symbol[0]}{lon}{symbol[1]}"


def format_weather(weather: WeatherData) -> str:
    """Format the weather data that follows a weather station symbol."""
    # Wind, gust and temperature are mandatory; unknown values are dots.
    parts = [
        # As with a course, 000 would mean unknown, so north is 360.
        "..."
        if weather.wind_direction is None
        else f"{(round(weather.wind_direction) % 360 or 360):03d}",
        "/",
        "..."
        if weather.wind_speed is None
        else f"{_clamp(weather.wind_speed, 0, 999):03d}",
        "g..."
        if weather.wind_gust is None
        else f"g{_clamp(weather.wind_gust, 0, 999):03d}",
        "t..."
        if weather.temperature is None
        else f"t{_clamp(weather.temperature, -99, 999):03d}",
    ]
    for prefix, rain in (
        ("r", weather.rain_1h),
        ("p", weather.rain_24h),
        ("P", weather.rain_since_midnight),
    ):
        if rain is not None:
            parts.append(f"{prefix}{_clamp(rain * 100, 0, 999):03d}")
    if weather.humidity is not None:
        parts.append(f"h{_clamp(weather.humidity, 1, 100) % 100:02d}")
    if weather.pressure is not None:
        parts.append(f"b{_clamp(weather.pressure * 10, 0, 99999):05d}")
    if weather.luminosity is not None:
        luminosity = _clamp(weather.luminosity, 0, 1999)
        parts.append(
            f"L{luminosity:03d}" if luminosity < 1000 else f"l{luminosity - 1000:03d}"
        )
    return "".join(parts)


def format_course_speed(course: float, speed: float) -> str:
    """Format the CSE/SPD extension for a course in degrees and speed in knots."""
    # Course 000 means unknown, so north is sent as 360.
    return f"{(_clamp(course, 0, 360) or 360):03d}/{_clamp(speed, 0, 999):03d}"


def build_report(
    callsign: str,
    timestamp: datetime,
    position: str,
    weather: WeatherData | None,
    comment: str,
    course_speed: str = "",
) -> str:
    """Build a timestamped position report, with weather data if given.

    The / data type marks a station without APRS messaging. course_speed is
    only used without weather data, where that extension carries the wind.
    """
    body = f"/{timestamp:%d%H%M}z{position}"
    body += course_speed if weather is None else format_weather(weather)
    # A line break would end the packet and start another under our login.
    comment = _INVALID_COMMENT_CHARS.sub("", comment)
    return f"{callsign}>{TOCALL},TCPIP*:{body}{comment}"
