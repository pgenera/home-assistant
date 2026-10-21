"""Test SmartBeaconing."""

from datetime import UTC, datetime, timedelta

import pytest

from homeassistant.components.aprs.smartbeacon import (
    STOPPED,
    Fix,
    Motion,
    beacon_rate,
    motion,
    turned,
)

START = Fix(latitude=42.5, longitude=-71.0, time=datetime(2026, 10, 21, tzinfo=UTC))


@pytest.mark.parametrize(
    ("speed", "rate"),
    [
        pytest.param(0, timedelta(minutes=30), id="stopped"),
        pytest.param(5, timedelta(minutes=30), id="slow"),
        pytest.param(30, timedelta(minutes=6), id="between"),
        pytest.param(60, timedelta(minutes=3), id="fast"),
        pytest.param(100, timedelta(minutes=3), id="faster"),
    ],
)
def test_beacon_rate(speed: float, rate: timedelta) -> None:
    """Test the report rate scales between the slow and fast speeds."""
    assert beacon_rate(speed) == rate


@pytest.mark.parametrize(
    ("reported_course", "current", "expected"),
    [
        pytest.param(None, Motion(speed=60, course=90), False, id="first_report"),
        pytest.param(0, Motion(speed=60, course=None), False, id="stopped"),
        pytest.param(0, Motion(speed=5, course=90), False, id="too_slow"),
        pytest.param(0, Motion(speed=60, course=30), False, id="gentle_curve"),
        pytest.param(0, Motion(speed=60, course=35), True, id="turn"),
        pytest.param(0, Motion(speed=10, course=50), False, id="slow_needs_more"),
        pytest.param(350, Motion(speed=60, course=10), False, id="wraps_small"),
        pytest.param(350, Motion(speed=60, course=60), True, id="wraps_turn"),
    ],
)
def test_turned(reported_course: float | None, current: Motion, expected: bool) -> None:
    """Test turns must exceed a threshold that shrinks with speed."""
    assert turned(reported_course, current) is expected


@pytest.mark.parametrize(
    ("end", "speed", "course"),
    [
        pytest.param(
            Fix(
                latitude=42.5 + 1609.344 / 111_050,
                longitude=-71.0,
                time=START.time + timedelta(minutes=1),
            ),
            60,
            0,
            id="north",
        ),
        pytest.param(
            Fix(
                latitude=42.5,
                longitude=-71.0 + 1609.344 / 81_890,
                time=START.time + timedelta(minutes=2),
            ),
            30,
            90,
            id="east",
        ),
    ],
)
def test_motion(end: Fix, speed: float, course: float) -> None:
    """Test speed and course between fixes."""
    result = motion(START, end)

    assert result.speed == pytest.approx(speed, rel=0.01)
    assert result.course == pytest.approx(course, abs=0.5)


@pytest.mark.parametrize(
    "end",
    [
        pytest.param(
            Fix(
                latitude=42.5001,
                longitude=-71.0,
                time=START.time + timedelta(minutes=1),
                accuracy=20,
            ),
            id="within_accuracy",
        ),
        pytest.param(
            Fix(latitude=42.6, longitude=-71.0, time=START.time),
            id="same_time",
        ),
    ],
)
def test_motion_stopped(end: Fix) -> None:
    """Test GPS noise and simultaneous fixes are not movement."""
    assert motion(START, end) is STOPPED
