from datetime import datetime
from zoneinfo import ZoneInfo

from astropy.coordinates import SkyCoord

from astroframe.observer import ObserverProfile, tonight_bounds, visibility_for_tonight


def test_tonight_bounds_before_noon_uses_previous_evening():
    tz = ZoneInfo("Pacific/Auckland")
    now = datetime(2026, 8, 7, 6, 0, tzinfo=tz)
    start, end = tonight_bounds(now, tz)
    assert start.day == 6
    assert start.hour == 18
    assert end.day == 7
    assert end.hour == 6


def test_visibility_returns_a_peak():
    profile = ObserverProfile(
        location_name="Test",
        latitude_deg=-45.0,
        longitude_deg=170.0,
        timezone_name="Pacific/Auckland",
        minimum_altitude_deg=30.0,
    )
    target = SkyCoord(ra=275.0, dec=-30.0, unit=("deg", "deg"))
    now = datetime(2026, 8, 7, 20, 0, tzinfo=ZoneInfo("Pacific/Auckland"))
    summary = visibility_for_tonight(target, profile, now=now, sample_minutes=30)
    assert -90.0 <= summary.maximum_altitude_deg <= 90.0
