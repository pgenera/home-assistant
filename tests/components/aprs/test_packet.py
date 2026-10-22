"""Test APRS packet encoding."""

from datetime import UTC, datetime

import aprslib
import pytest

from homeassistant.components.aprs.packet import (
    WeatherData,
    build_report,
    format_course_speed,
    format_position,
    format_weather,
)


@pytest.mark.parametrize(
    ("latitude", "longitude", "symbol", "ambiguity", "expected"),
    [
        pytest.param(42.562956, -70.86915, "/_", 0, "4233.78N/07052.15W_", id="nw"),
        pytest.param(-33.8568, 151.2153, "/-", 0, "3351.41S/15112.92E-", id="se"),
        pytest.param(0, 0, "\\k", 0, "0000.00N\\00000.00Ek", id="null_island"),
        pytest.param(49.999999, -0.0000001, "/>", 0, "5000.00N/00000.00W>", id="carry"),
        pytest.param(42.562956, -70.86915, "/-", 1, "4233.8 N/07052.1 W-", id="amb1"),
        pytest.param(42.562956, -70.86915, "/-", 2, "4234.  N/07052.  W-", id="amb2"),
        pytest.param(42.562956, -70.86915, "/-", 3, "423 .  N/0705 .  W-", id="amb3"),
        pytest.param(42.562956, -70.86915, "/-", 4, "43  .  N/071  .  W-", id="amb4"),
        pytest.param(89.6, -179.9999, "/-", 4, "89  .  N/179  .  W-", id="amb4_pole"),
        pytest.param(-89.999999, 179.999999, "/-", 0, "8959.99S/17959.99E-", id="pole"),
    ],
)
def test_format_position(
    latitude: float, longitude: float, symbol: str, ambiguity: int, expected: str
) -> None:
    """Test uncompressed positions, hemispheres and ambiguity."""
    assert format_position(latitude, longitude, symbol, ambiguity) == expected


@pytest.mark.parametrize(
    ("weather", "expected"),
    [
        pytest.param(WeatherData(), ".../...g...t...", id="empty"),
        pytest.param(
            WeatherData(
                wind_direction=96,
                wind_speed=8.28,
                wind_gust=11.41,
                temperature=50.18,
                rain_1h=0.01,
                rain_24h=0.5,
                rain_since_midnight=1.234,
                humidity=76,
                pressure=1023.7,
                luminosity=156.59,
            ),
            "096/008g011t050r001p050P123h76b10237L157",
            id="full",
        ),
        pytest.param(
            WeatherData(temperature=-4.6, humidity=100, luminosity=1234),
            ".../...g...t-05h00l234",
            id="negative_saturated_bright",
        ),
        pytest.param(
            WeatherData(
                wind_direction=400,
                wind_speed=-1,
                temperature=-150,
                rain_1h=12,
                humidity=0,
                pressure=12000,
                luminosity=5000,
            ),
            "040/000g...t-99r999h01b99999l999",
            id="clamped",
        ),
        pytest.param(
            WeatherData(wind_direction=0.2, wind_speed=3),
            "360/003g...t...",
            id="north_wind",
        ),
    ],
)
def test_format_weather(weather: WeatherData, expected: str) -> None:
    """Test weather fields, missing values and out of range values."""
    assert format_weather(weather) == expected


def test_build_report() -> None:
    """Test a full weather report decodes back to what was sent."""
    packet = build_report(
        "N0CALL-13",
        datetime(2026, 10, 21, 9, 5, tzinfo=UTC),
        format_position(42.562956, -70.86915, "/_"),
        WeatherData(wind_direction=96, wind_speed=8, temperature=50, humidity=76),
        "Home Assistant",
    )

    assert packet == (
        "N0CALL-13>APZHAS,TCPIP*:/210905z4233.78N/07052.15W_096/008g...t050h76"
        "Home Assistant"
    )
    parsed = aprslib.parse(packet)
    assert parsed["symbol"] == "_"
    assert not parsed["messagecapable"]
    # aprslib reads the wind of a weather report with position as course/speed.
    assert parsed["course"] == 96
    assert parsed["weather"] == {
        "temperature": pytest.approx((50 - 32) / 1.8),
        "humidity": 76,
    }


def test_build_report_cleans_comment() -> None:
    """Test characters APRS does not allow are left out of the comment."""
    packet = build_report(
        "N0CALL-9",
        datetime(2026, 10, 21, 23, 59, tzinfo=UTC),
        format_position(42.562956, -70.86915, "/-"),
        None,
        "QTH\r\nN0CALL-9>APRS:|~fake",
    )

    assert packet == (
        "N0CALL-9>APZHAS,TCPIP*:/212359z4233.78N/07052.15W-QTHN0CALL-9>APRS:fake"
    )


def test_build_report_position_only() -> None:
    """Test a report without weather data."""
    packet = build_report(
        "N0CALL-9",
        datetime(2026, 10, 21, 23, 59, tzinfo=UTC),
        format_position(42.562956, -70.86915, "/>"),
        None,
        "",
    )

    assert packet == "N0CALL-9>APZHAS,TCPIP*:/212359z4233.78N/07052.15W>"
    parsed = aprslib.parse(packet)
    assert parsed["latitude"] == pytest.approx(42.563, abs=1e-3)
    assert parsed["longitude"] == pytest.approx(-70.86917, abs=1e-4)


@pytest.mark.parametrize(
    ("course", "speed", "expected"),
    [
        pytest.param(88.4, 36.2, "088/036", id="example"),
        pytest.param(0.2, 0, "360/000", id="north"),
        pytest.param(359.6, 1200, "360/999", id="clamped"),
    ],
)
def test_format_course_speed(course: float, speed: float, expected: str) -> None:
    """Test the CSE/SPD extension, with north as 360 since 000 is unknown."""
    assert format_course_speed(course, speed) == expected


def test_build_report_course_speed() -> None:
    """Test a moving station's report decodes with its course and speed."""
    packet = build_report(
        "N0CALL-9",
        datetime(2026, 10, 21, 17, 5, tzinfo=UTC),
        format_position(42.362, -71.0867, "/>"),
        None,
        "Mobile",
        format_course_speed(88, 36),
    )

    assert packet == "N0CALL-9>APZHAS,TCPIP*:/211705z4221.72N/07105.20W>088/036Mobile"
    parsed = aprslib.parse(packet)
    assert parsed["course"] == 88
    assert parsed["speed"] == pytest.approx(36 * 1.852)
    assert parsed["comment"] == "Mobile"
