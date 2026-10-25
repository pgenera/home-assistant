"""Test SmartBeaconing."""

from datetime import UTC, datetime, timedelta

import pytest

from homeassistant.components.aprs.smartbeacon import (
    DRIVING,
    WALKING,
    Fix,
    Motion,
    Profile,
    SpeedHistory,
    beacon_rate,
    travel_speed,
    turned,
)

START = Fix(latitude=42.5, longitude=-71.0, time=datetime(2026, 10, 21, tzinfo=UTC))
SLOW_RATE = timedelta(minutes=30)


@pytest.mark.parametrize(
    ("speed", "profile", "rate"),
    [
        pytest.param(0, DRIVING, SLOW_RATE, id="stopped"),
        pytest.param(5, DRIVING, SLOW_RATE, id="driving_slow"),
        pytest.param(30, DRIVING, timedelta(minutes=6), id="driving_between"),
        pytest.param(60, DRIVING, timedelta(minutes=3), id="driving_fast"),
        pytest.param(100, DRIVING, timedelta(minutes=3), id="driving_faster"),
        pytest.param(1, WALKING, SLOW_RATE, id="walking_slow"),
        pytest.param(2.5, WALKING, timedelta(minutes=10), id="walking_between"),
        pytest.param(6, WALKING, timedelta(minutes=5), id="walking_fast"),
        pytest.param(1.25, WALKING, timedelta(minutes=20), id="walking_slowly"),
    ],
)
def test_beacon_rate(speed: float, profile: Profile, rate: timedelta) -> None:
    """Test the report rate scales between each profile's slow and fast speeds."""
    assert beacon_rate(speed, profile, SLOW_RATE) == rate


@pytest.mark.parametrize(
    ("reported_course", "current", "profile", "expected"),
    [
        pytest.param(None, Motion(speed=60, course=90), DRIVING, False, id="first"),
        pytest.param(0, Motion(speed=60), DRIVING, False, id="no_course"),
        pytest.param(0, Motion(speed=5, course=90), DRIVING, False, id="too_slow"),
        pytest.param(0, Motion(speed=60, course=30), DRIVING, False, id="curve"),
        pytest.param(0, Motion(speed=60, course=35), DRIVING, True, id="turn"),
        pytest.param(0, Motion(speed=3, course=90), DRIVING, False, id="walk_driving"),
        pytest.param(0, Motion(speed=3, course=40), WALKING, True, id="walk_turn"),
        pytest.param(0, Motion(speed=3, course=35), WALKING, False, id="walk_curve"),
        pytest.param(350, Motion(speed=60, course=10), DRIVING, False, id="wraps"),
        pytest.param(350, Motion(speed=60, course=60), DRIVING, True, id="wraps_turn"),
    ],
)
def test_turned(
    reported_course: float | None, current: Motion, profile: Profile, expected: bool
) -> None:
    """Test turns must exceed a threshold that shrinks with speed."""
    assert turned(reported_course, current, profile) is expected


@pytest.mark.parametrize(
    ("end", "speed"),
    [
        pytest.param(
            Fix(
                latitude=42.5 + 1609.344 / 111_050,
                longitude=-71.0,
                time=START.time + timedelta(minutes=1),
            ),
            60,
            id="mile_a_minute",
        ),
        pytest.param(
            Fix(
                latitude=42.5001,
                longitude=-71.0,
                time=START.time + timedelta(minutes=1),
                accuracy=50,
            ),
            None,
            id="within_accuracy",
        ),
        pytest.param(
            Fix(latitude=42.6, longitude=-71.0, time=START.time),
            None,
            id="no_time",
        ),
    ],
)
def test_travel_speed(end: Fix, speed: float | None) -> None:
    """Test speed between fixes, ignoring movement within their accuracy."""
    assert travel_speed(START, end) == (
        None if speed is None else pytest.approx(speed, rel=1e-2)
    )


def test_speed_history() -> None:
    """Test the profile follows the 10 minute average speed, with a gap."""
    history = SpeedHistory()
    history.add(START.time, 0)
    assert history.profile is WALKING

    history.add(START.time + timedelta(minutes=1), 30)
    assert history.profile is DRIVING

    # A minute at 30 mph and a minute at 3 mph is between the thresholds.
    history.add(START.time + timedelta(minutes=2), 3)
    assert history.average(START.time + timedelta(minutes=2)) == pytest.approx(16.5)
    assert history.profile is DRIVING

    history.add(START.time + timedelta(minutes=12), 3)
    assert history.average(START.time + timedelta(minutes=12)) == pytest.approx(3)
    assert history.profile is WALKING

    # Walking briskly or jogging stays below the driving threshold.
    history.add(START.time + timedelta(minutes=30), 7)
    assert history.profile is WALKING
