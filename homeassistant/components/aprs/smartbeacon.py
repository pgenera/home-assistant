"""Decide when a moving station reports, using SmartBeaconing.

SmartBeaconing™ is a trademark of HamHUD.net.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

import geopy.distance

METERS_PER_SECOND_TO_MPH = 3600 / 1609.344

# Average speeds, in mph, that switch between walking and driving. The gap
# keeps a speed near the boundary from switching back and forth.
DRIVING_ABOVE = 8
WALKING_BELOW = 6
AVERAGE_WINDOW = timedelta(minutes=10)


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
    """How fast and which way a station is going."""

    speed: float = 0
    """Mph."""
    course: float | None = None
    """Degrees clockwise from north, when reported."""


STOPPED = Motion()


@dataclass(frozen=True, kw_only=True)
class Profile:
    """SmartBeaconing thresholds.

    Reports go out at the slow rate at or below slow_speed, at fast_rate at
    or above fast_speed, in proportion to speed in between, and after a turn
    of more than min_turn_angle + turn_slope / speed degrees.
    """

    slow_speed: float
    """Mph."""
    fast_speed: float
    """Mph."""
    fast_rate: timedelta
    min_turn_angle: float
    turn_slope: float


# Tracker defaults for vehicles. Walkers turn sharply at a fraction of the
# speed, where a car's turn slope would hide nearly every turn.
DRIVING = Profile(
    slow_speed=5,
    fast_speed=60,
    fast_rate=timedelta(minutes=3),
    min_turn_angle=28,
    turn_slope=255,
)
WALKING = Profile(
    slow_speed=1,
    fast_speed=5,
    fast_rate=timedelta(minutes=5),
    min_turn_angle=30,
    turn_slope=20,
)


def travel_speed(previous: Fix, current: Fix) -> float | None:
    """Return the speed in mph between two fixes.

    Returns None when the fixes are within each other's accuracy, which is
    GPS noise rather than travel.
    """
    seconds = (current.time - previous.time).total_seconds()
    meters: float = geopy.distance.distance(
        (previous.latitude, previous.longitude),
        (current.latitude, current.longitude),
    ).m
    if seconds <= 0 or meters <= max(previous.accuracy, current.accuracy):
        return None
    return meters / seconds * METERS_PER_SECOND_TO_MPH


def beacon_rate(speed: float, profile: Profile, slow_rate: timedelta) -> timedelta:
    """Return how often to report at a speed in mph."""
    if speed <= profile.slow_speed:
        return slow_rate
    if speed >= profile.fast_speed:
        return profile.fast_rate
    return min(profile.fast_rate * (profile.fast_speed / speed), slow_rate)


def turned(reported_course: float | None, current: Motion, profile: Profile) -> bool:
    """Return whether the heading changed enough since the last report."""
    if (
        reported_course is None
        or current.course is None
        or current.speed <= profile.slow_speed
    ):
        return False
    change = abs((current.course - reported_course + 180) % 360 - 180)
    return change > profile.min_turn_angle + profile.turn_slope / current.speed


class SpeedHistory:
    """Recent speeds, to tell walking from driving."""

    def __init__(self) -> None:
        """Initialize the history."""
        self._samples: deque[tuple[datetime, float]] = deque()
        self.profile = WALKING

    def add(self, time: datetime, speed: float) -> None:
        """Record the speed in mph since the last sample, and pick a profile."""
        self._samples.append((time, speed))
        # The oldest sample is kept only as the start of the next one.
        while len(self._samples) > 1 and self._samples[1][0] <= time - AVERAGE_WINDOW:
            self._samples.popleft()
        average = self.average(time)
        if average > DRIVING_ABOVE:
            self.profile = DRIVING
        elif average < WALKING_BELOW:
            self.profile = WALKING

    def average(self, now: datetime) -> float:
        """Return the time-weighted average speed in mph over the window."""
        start = now - AVERAGE_WINDOW
        total = weight = 0.0
        for (previous, _), (time, speed) in pairwise(self._samples):
            if (seconds := (time - max(previous, start)).total_seconds()) > 0:
                total += speed * seconds
                weight += seconds
        # A single sample covers no time yet.
        return total / weight if weight else self._samples[-1][1]
