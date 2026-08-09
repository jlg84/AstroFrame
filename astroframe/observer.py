from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers
import astropy.units as u

# Visibility calculations must remain usable offline.
iers.conf.auto_download = False
iers.conf.auto_max_age = None


@dataclass(frozen=True)
class ObserverProfile:
    profile_name: str = "Home"
    location_name: str = ""
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    elevation_m: float = 0.0
    timezone_name: str = ""
    bortle_class: int = 0
    minimum_altitude_deg: float = 30.0

    @property
    def is_configured(self) -> bool:
        return bool(self.location_name.strip())

    @property
    def timezone(self):
        if self.timezone_name.strip():
            try:
                return ZoneInfo(self.timezone_name.strip())
            except ZoneInfoNotFoundError:
                pass
        return datetime.now().astimezone().tzinfo or timezone.utc

    @property
    def earth_location(self) -> EarthLocation:
        return EarthLocation(
            lat=self.latitude_deg * u.deg,
            lon=self.longitude_deg * u.deg,
            height=self.elevation_m * u.m,
        )


@dataclass(frozen=True)
class VisibilitySummary:
    maximum_altitude_deg: float
    peak_time: datetime
    visible_start: datetime | None
    visible_end: datetime | None
    minimum_altitude_deg: float

    @property
    def has_useful_window(self) -> bool:
        return self.visible_start is not None and self.visible_end is not None


def tonight_bounds(now: datetime, tz) -> tuple[datetime, datetime]:
    """Return the observing night containing/nearest the supplied local time."""
    local_now = now.astimezone(tz)
    if local_now.hour < 12:
        evening_date = (local_now - timedelta(days=1)).date()
    else:
        evening_date = local_now.date()

    start = datetime.combine(
        evening_date,
        datetime.min.time().replace(hour=18),
        tzinfo=tz,
    )
    end = start + timedelta(hours=12)
    return start, end


def visibility_for_tonight(
    target: SkyCoord,
    profile: ObserverProfile,
    *,
    now: datetime | None = None,
    sample_minutes: int = 10,
) -> VisibilitySummary:
    """Calculate a simple, offline visibility summary for the current night."""
    tz = profile.timezone
    now = now or datetime.now(tz)
    start, end = tonight_bounds(now, tz)

    steps = int((end - start).total_seconds() // (sample_minutes * 60)) + 1
    datetimes = [start + timedelta(minutes=sample_minutes * i) for i in range(steps)]
    times = Time(datetimes)

    frame = AltAz(obstime=times, location=profile.earth_location)
    altitudes = np.asarray(target.icrs.transform_to(frame).alt.deg, dtype=float)

    peak_index = int(np.nanargmax(altitudes))
    maximum = float(altitudes[peak_index])
    peak_time = datetimes[peak_index]

    useful = altitudes >= profile.minimum_altitude_deg
    visible_start = None
    visible_end = None
    indices = np.flatnonzero(useful)
    if len(indices):
        visible_start = datetimes[int(indices[0])]
        visible_end = datetimes[int(indices[-1])]

    return VisibilitySummary(
        maximum_altitude_deg=maximum,
        peak_time=peak_time,
        visible_start=visible_start,
        visible_end=visible_end,
        minimum_altitude_deg=profile.minimum_altitude_deg,
    )
