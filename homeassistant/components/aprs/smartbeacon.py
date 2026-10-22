"""Decide when a moving station reports, using SmartBeaconing.

SmartBeaconing™ is a trademark of HamHUD.net.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math

import geopy.distance

from .const import (
    FAST_RATE,
    FAST_SPEED,
    MIN_TURN_ANGLE,
    POSITION_INTERVAL,
    SLOW_SPEED,
    TURN_SLOPE,
)

METERS_PER_SECOND_TO_MPH = 3600 / 1609.344


@dataclass(frozen=True, kw_only=True)
class Fix:
    """A position at a point in time."""

    latitude: float
    longitude: float
    time: datetime
    accuracy: float = 0
    """Meters."""


@dataclass(frozen=True, kw_only=True)
class Motion:
    """Speed and course between two fixes."""

    speed: float = 0
    """Mph."""
    course: float | None = None
    """Degrees clockwise from north, or None when not moving."""


STOPPED = Motion()


def bearing(start: Fix, end: Fix) -> float:
    """Return the initial great circle bearing from start to end in degrees."""
    lat1, lat2 = math.radians(start.latitude), math.radians(end.latitude)
    delta_lon = math.radians(end.longitude - start.longitude)
    x = math.sin(delta_lon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        delta_lon
    )
    return math.degrees(math.atan2(x, y)) % 360


def motion(previous: Fix, current: Fix) -> Motion:
    """Return the motion between two fixes.

    Movement within the fixes' accuracy is GPS noise, not travel.
    """
    seconds = (current.time - previous.time).total_seconds()
    meters = geopy.distance.distance(
        (previous.latitude, previous.longitude),
        (current.latitude, current.longitude),
    ).m
    if seconds <= 0 or meters <= max(previous.accuracy, current.accuracy):
        return STOPPED
    return Motion(
        speed=meters / seconds * METERS_PER_SECOND_TO_MPH,
        course=bearing(previous, current),
    )


def beacon_rate(speed: float) -> timedelta:
    """Return how often to report at a speed in mph."""
    if speed <= SLOW_SPEED:
        return POSITION_INTERVAL
    if speed >= FAST_SPEED:
        return FAST_RATE
    return FAST_RATE * (FAST_SPEED / speed)


def turned(reported_course: float | None, current: Motion) -> bool:
    """Return whether the heading changed enough since the last report."""
    if reported_course is None or current.course is None or current.speed <= SLOW_SPEED:
        return False
    change = abs((current.course - reported_course + 180) % 360 - 180)
    return change > MIN_TURN_ANGLE + TURN_SLOPE / current.speed
