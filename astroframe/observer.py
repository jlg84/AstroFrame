from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
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


def local_observing_date(now: datetime, tz) -> date:
    """Return the local calendar date whose evening is meant by “Tonight”.

    The Observability planner treats a selected date as local noon on that date
    through local noon on the following date.  Therefore “Tonight” must always
    use the observing site's current *local civil date*, even before noon.
    """
    return now.astimezone(tz).date()


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


@dataclass(frozen=True)
class ObservabilitySummary:
    evening_date: date
    maximum_altitude_deg: float
    peak_time: datetime
    dark_start: datetime | None
    dark_end: datetime | None
    useful_start: datetime | None
    useful_end: datetime | None
    useful_duration_hours: float
    minimum_altitude_deg: float
    moon_illumination_fraction: float
    moon_separation_deg: float
    moon_max_altitude_during_window_deg: float | None

    @property
    def has_astronomical_darkness(self) -> bool:
        return self.dark_start is not None and self.dark_end is not None

    @property
    def has_useful_window(self) -> bool:
        return self.useful_start is not None and self.useful_end is not None

    @property
    def moon_interference(self) -> str:
        if not self.has_useful_window:
            return "—"
        moon_alt = self.moon_max_altitude_during_window_deg
        if moon_alt is None or moon_alt <= 0.0:
            return "None — Moon below horizon"
        illum = self.moon_illumination_fraction
        sep = self.moon_separation_deg
        if illum < 0.25 or sep >= 90.0:
            return "Low"
        if illum < 0.55 or sep >= 60.0:
            return "Moderate"
        return "High"


def _longest_true_interval(mask: np.ndarray) -> tuple[int, int] | None:
    """Return inclusive indices of the longest contiguous True run."""
    indices = np.flatnonzero(mask)
    if not len(indices):
        return None
    best_start = best_end = int(indices[0])
    run_start = run_end = int(indices[0])
    for raw in indices[1:]:
        idx = int(raw)
        if idx == run_end + 1:
            run_end = idx
        else:
            if run_end - run_start > best_end - best_start:
                best_start, best_end = run_start, run_end
            run_start = run_end = idx
    if run_end - run_start > best_end - best_start:
        best_start, best_end = run_start, run_end
    return best_start, best_end


def observability_for_date(
    target: SkyCoord,
    profile: ObserverProfile,
    evening_date: date,
    *,
    sample_minutes: int = 5,
) -> ObservabilitySummary:
    """Calculate darkness, target altitude and Moon geometry for one local night.

    ``evening_date`` names the local calendar date on which the observing night
    begins.  A noon-to-noon window is used so southern-summer darkness and
    targets that cross midnight are handled without special cases.
    """
    tz = profile.timezone
    start = datetime.combine(
        evening_date,
        datetime.min.time().replace(hour=12),
        tzinfo=tz,
    )
    end = start + timedelta(hours=24)
    step = timedelta(minutes=max(1, int(sample_minutes)))
    count = int((end - start).total_seconds() // step.total_seconds()) + 1
    datetimes = [start + step * i for i in range(count)]
    times = Time(datetimes)
    frame = AltAz(obstime=times, location=profile.earth_location)

    target_alt = np.asarray(target.icrs.transform_to(frame).alt.deg, dtype=float)
    sun_alt = np.asarray(get_sun(times).transform_to(frame).alt.deg, dtype=float)
    # Use a topocentric Moon only for altitude.  For angular separations and
    # phase use the geocentric GCRS Moon, matching get_sun().  Mixing the two
    # frames inside SkyCoord.separation() triggers Astropy's
    # NonRotationTransformationWarning and can introduce a small, unnecessary
    # observer-parallax dependency into the reported angle.
    moon_topocentric = get_body("moon", times, location=profile.earth_location)
    moon_alt = np.asarray(moon_topocentric.transform_to(frame).alt.deg, dtype=float)
    moon_geocentric = get_body("moon", times)

    peak_index = int(np.nanargmax(target_alt))
    maximum = float(target_alt[peak_index])
    peak_time = datetimes[peak_index]

    dark_mask = sun_alt <= -18.0
    dark_interval = _longest_true_interval(dark_mask)
    dark_start = dark_end = None
    if dark_interval is not None:
        dark_start = datetimes[dark_interval[0]]
        dark_end = datetimes[dark_interval[1]]

    useful_mask = dark_mask & (target_alt >= profile.minimum_altitude_deg)
    useful_interval = _longest_true_interval(useful_mask)
    useful_start = useful_end = None
    useful_duration_hours = 0.0
    moon_max_alt = None
    sample_index = peak_index
    if useful_interval is not None:
        a, b = useful_interval
        useful_start = datetimes[a]
        useful_end = datetimes[b]
        # Canonical duration is the elapsed time between the displayed window
        # endpoints.  Search qualification and every UI surface use this same
        # value, avoiding boundary cases where 03:10–05:00 could be labelled
        # as 2.0 h in one place and 1.9 h in another.
        useful_duration_hours = max(
            0.0,
            (useful_end - useful_start).total_seconds() / 3600.0,
        )
        moon_max_alt = float(np.nanmax(moon_alt[a : b + 1]))
        sample_index = (a + b) // 2
    elif dark_interval is not None:
        sample_index = (dark_interval[0] + dark_interval[1]) // 2

    # Illuminated fraction from Sun-Moon elongation: 0 at new Moon, 1 at full.
    moon_sample = moon_geocentric[sample_index]
    sun_sample = get_sun(times[sample_index])
    elongation = float(sun_sample.separation(moon_sample).deg)
    illumination = float((1.0 - np.cos(np.deg2rad(elongation))) / 2.0)
    separation = float(target.icrs.separation(moon_sample.icrs).deg)

    return ObservabilitySummary(
        evening_date=evening_date,
        maximum_altitude_deg=maximum,
        peak_time=peak_time,
        dark_start=dark_start,
        dark_end=dark_end,
        useful_start=useful_start,
        useful_end=useful_end,
        useful_duration_hours=useful_duration_hours,
        minimum_altitude_deg=profile.minimum_altitude_deg,
        moon_illumination_fraction=illumination,
        moon_separation_deg=separation,
        moon_max_altitude_during_window_deg=moon_max_alt,
    )


@dataclass(frozen=True)
class GoodNightCandidate:
    evening_date: date
    summary: ObservabilitySummary
    score: float


def good_night_score(summary: ObservabilitySummary) -> float:
    """Simple planning score: dark duration first, then altitude and Moon penalty."""
    if not summary.has_useful_window:
        return -1e9
    score = summary.useful_duration_hours * 100.0
    score += min(summary.maximum_altitude_deg, 90.0)
    interference = summary.moon_interference
    if interference.startswith("High"):
        score -= 90.0
    elif interference.startswith("Moderate"):
        score -= 40.0
    elif interference.startswith("Low"):
        score -= 10.0
    return score


def find_good_nights(
    target: SkyCoord,
    profile: ObserverProfile,
    start_date: date,
    *,
    search_days: int = 45,
    limit: int = 3,
    sample_minutes: int = 10,
    progress_callback: Callable[[int, int, date], None] | None = None,
) -> list[GoodNightCandidate]:
    """Find useful future imaging nights, favouring duration, altitude and low Moon.

    The first result is the earliest genuinely good night (>=2 dark hours and
    not High Moon interference). Remaining results are the strongest later
    alternatives in the search horizon. If no night reaches that threshold,
    the strongest available nights are returned instead.
    """
    candidates: list[GoodNightCandidate] = []
    total_days = max(1, int(search_days))
    for offset in range(total_days):
        d = start_date + timedelta(days=offset)
        if progress_callback is not None:
            progress_callback(offset + 1, total_days, d)
        summary = observability_for_date(
            target, profile, d, sample_minutes=sample_minutes
        )
        if summary.has_useful_window:
            candidates.append(
                GoodNightCandidate(d, summary, good_night_score(summary))
            )

    if not candidates:
        return []

    genuinely_good = [
        c for c in candidates
        if c.summary.useful_duration_hours >= 2.0
        and not c.summary.moon_interference.startswith("High")
    ]
    if genuinely_good:
        first = min(genuinely_good, key=lambda c: c.evening_date)
        later = [c for c in genuinely_good if c.evening_date > first.evening_date]
        later.sort(key=lambda c: (-c.score, c.evening_date))
        return [first] + later[: max(0, limit - 1)]

    candidates.sort(key=lambda c: (-c.score, c.evening_date))
    return candidates[:limit]


@dataclass(frozen=True)
class SeasonalGoodNightResult:
    candidates: tuple[GoodNightCandidate, ...]
    extended_beyond_near_term: bool
    searched_through_date: date
    geometry_return_date: date | None = None


def find_next_good_nights_seasonal(
    target: SkyCoord,
    profile: ObserverProfile,
    start_date: date,
    *,
    near_term_days: int = 45,
    max_days: int = 365,
    limit: int = 3,
    detailed_sample_minutes: int = 10,
    coarse_step_days: int = 7,
    coarse_sample_minutes: int = 20,
    progress_callback: Callable[[str, int, int, date], None] | None = None,
) -> SeasonalGoodNightResult:
    """Find the next genuinely useful night, extending into the next season if needed.

    The first ``near_term_days`` are checked night-by-night.  If none qualify,
    AstroFrame performs a cheap weekly geometry scan out to ``max_days`` to
    locate when the target becomes seasonally viable again, then returns to a
    detailed night-by-night search around that boundary.  This avoids doing a
    full expensive calculation for every night of a year while still finding
    seasonal targets such as spring galaxies from the southern hemisphere.
    """
    near_term_days = max(1, int(near_term_days))
    max_days = max(near_term_days, int(max_days))
    limit = max(1, int(limit))

    def emit(stage: str, current: int, total: int, d: date) -> None:
        if progress_callback is not None:
            progress_callback(stage, current, total, d)

    # Phase 1: detailed near-term search.
    near_candidates: list[GoodNightCandidate] = []
    for offset in range(near_term_days):
        d = start_date + timedelta(days=offset)
        emit("near", offset + 1, near_term_days, d)
        summary = observability_for_date(
            target, profile, d, sample_minutes=detailed_sample_minutes
        )
        if summary.has_useful_window:
            near_candidates.append(
                GoodNightCandidate(d, summary, good_night_score(summary))
            )

    near_good = [
        c for c in near_candidates
        if c.summary.useful_duration_hours >= 2.0
        and not c.summary.moon_interference.startswith("High")
    ]
    if near_good:
        first = min(near_good, key=lambda c: c.evening_date)
        later = [c for c in near_good if c.evening_date > first.evening_date]
        later.sort(key=lambda c: (-c.score, c.evening_date))
        chosen = tuple([first] + later[: max(0, limit - 1)])
        return SeasonalGoodNightResult(
            candidates=chosen,
            extended_beyond_near_term=False,
            searched_through_date=chosen[-1].evening_date if chosen else start_date,
        )

    # Phase 2: coarse weekly scan for the next season. Ignore Moon here: we
    # only need to locate where darkness + altitude geometry becomes viable.
    coarse_start_offset = near_term_days
    coarse_offsets = list(range(coarse_start_offset, max_days, max(1, coarse_step_days)))
    geometry_hit: tuple[int, date] | None = None
    for idx, offset in enumerate(coarse_offsets):
        d = start_date + timedelta(days=offset)
        emit("coarse", idx + 1, len(coarse_offsets), d)
        summary = observability_for_date(
            target, profile, d, sample_minutes=coarse_sample_minutes
        )
        if summary.useful_duration_hours >= 2.0:
            geometry_hit = (offset, d)
            break

    if geometry_hit is None:
        return SeasonalGoodNightResult(
            candidates=tuple(),
            extended_beyond_near_term=True,
            searched_through_date=start_date + timedelta(days=max_days - 1),
            geometry_return_date=None,
        )

    hit_offset, hit_date = geometry_hit

    # Phase 3: search individual nights around the return boundary. Start ten
    # days before the weekly hit (but never re-search the near-term block), and
    # cover 45 days so Moon phase cannot hide the returning season.
    detailed_start_offset = max(near_term_days, hit_offset - 10)
    detailed_days = min(45, max_days - detailed_start_offset)
    seasonal_candidates: list[GoodNightCandidate] = []
    for i in range(detailed_days):
        offset = detailed_start_offset + i
        d = start_date + timedelta(days=offset)
        emit("detail", i + 1, detailed_days, d)
        summary = observability_for_date(
            target, profile, d, sample_minutes=detailed_sample_minutes
        )
        if summary.has_useful_window:
            seasonal_candidates.append(
                GoodNightCandidate(d, summary, good_night_score(summary))
            )

    seasonal_good = [
        c for c in seasonal_candidates
        if c.summary.useful_duration_hours >= 2.0
        and not c.summary.moon_interference.startswith("High")
    ]
    if seasonal_good:
        first = min(seasonal_good, key=lambda c: c.evening_date)
        later = [c for c in seasonal_good if c.evening_date > first.evening_date]
        later.sort(key=lambda c: (-c.score, c.evening_date))
        chosen = tuple([first] + later[: max(0, limit - 1)])
    else:
        # Geometry is back but Moon may be awkward throughout the refinement
        # window. Return the strongest useful nights rather than claiming the
        # target is impossible.
        seasonal_candidates.sort(key=lambda c: (-c.score, c.evening_date))
        chosen = tuple(seasonal_candidates[:limit])

    searched_through = (
        start_date + timedelta(days=detailed_start_offset + max(0, detailed_days - 1))
    )
    return SeasonalGoodNightResult(
        candidates=chosen,
        extended_beyond_near_term=True,
        searched_through_date=searched_through,
        geometry_return_date=hit_date,
    )


@dataclass(frozen=True)
class ObservingSeasonResult:
    """Season-level visibility derived from darkness + altitude geometry only."""
    selected_date: date
    classification: str
    maximum_possible_altitude_deg: float
    minimum_altitude_deg: float
    current_useful_duration_hours: float
    season_start: date | None = None
    season_end: date | None = None
    prime_start: date | None = None
    prime_end: date | None = None
    longest_useful_duration_hours: float = 0.0
    longest_date: date | None = None
    next_season_start: date | None = None
    next_season_end: date | None = None


def theoretical_maximum_altitude_deg(target: SkyCoord, profile: ObserverProfile) -> float:
    """Upper-culmination altitude for the target from the observing latitude."""
    dec = float(target.icrs.dec.deg)
    lat = float(profile.latitude_deg)
    return 90.0 - abs(lat - dec)


def _season_geometry_for_dates(
    target: SkyCoord,
    profile: ObserverProfile,
    dates: list[date],
    *,
    sample_minutes: int = 15,
    progress_callback: Callable[[str, int, int, date], None] | None = None,
) -> dict[date, tuple[float, float]]:
    """Return useful-dark duration and nightly max altitude for each local night.

    Moon is deliberately omitted.  Dates are evaluated in month-sized vector
    batches so a year-scale season scan remains practical while preserving
    exact night-by-night boundaries.
    """
    tz = profile.timezone
    step_minutes = max(5, int(sample_minutes))
    samples_per_night = int((24 * 60) // step_minutes) + 1
    results: dict[date, tuple[float, float]] = {}
    total = len(dates)
    batch_size = 28
    for batch_start in range(0, total, batch_size):
        batch_dates = dates[batch_start: batch_start + batch_size]
        all_datetimes: list[datetime] = []
        for evening_date in batch_dates:
            start = datetime.combine(
                evening_date,
                datetime.min.time().replace(hour=12),
                tzinfo=tz,
            )
            all_datetimes.extend(
                start + timedelta(minutes=step_minutes * i)
                for i in range(samples_per_night)
            )
        times = Time(all_datetimes)
        frame = AltAz(obstime=times, location=profile.earth_location)
        target_alt = np.asarray(target.icrs.transform_to(frame).alt.deg, dtype=float)
        sun_alt = np.asarray(get_sun(times).transform_to(frame).alt.deg, dtype=float)
        for local_idx, evening_date in enumerate(batch_dates):
            a = local_idx * samples_per_night
            b = a + samples_per_night
            night_target = target_alt[a:b]
            night_sun = sun_alt[a:b]
            useful = (night_sun <= -18.0) & (night_target >= profile.minimum_altitude_deg)
            interval = _longest_true_interval(useful)
            duration = 0.0
            if interval is not None:
                ia, ib = interval
                duration = max(0.0, (ib - ia) * step_minutes / 60.0)
            results[evening_date] = (duration, float(np.nanmax(night_target)))
        if progress_callback is not None:
            completed = min(total, batch_start + len(batch_dates))
            progress_callback("season", completed, total, batch_dates[-1])
    return results

def _true_date_runs(dates: list[date], flags: list[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start_idx: int | None = None
    for i, flag in enumerate(flags):
        if flag and start_idx is None:
            start_idx = i
        if start_idx is not None and (not flag or i == len(flags) - 1):
            end_idx = i if flag and i == len(flags) - 1 else i - 1
            runs.append((start_idx, end_idx))
            start_idx = None
    return runs


def _close_short_false_gaps(flags: list[bool], max_gap_days: int = 14) -> list[bool]:
    """Join short threshold gaps so one astronomical season is not fragmented.

    Season boundaries are a human-scale concept.  A handful of nights that fall
    just below the qualification threshold should not split an otherwise
    continuous season into several tiny runs.  Long gaps (for example, a target
    lost in summer twilight) are preserved.
    """
    if not flags:
        return []
    out = list(flags)
    n = len(out)
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        a = i
        while i < n and not out[i]:
            i += 1
        b = i - 1
        gap_len = b - a + 1
        left_true = a > 0 and out[a - 1]
        right_true = i < n and out[i]
        if left_true and right_true and gap_len <= max_gap_days:
            for j in range(a, b + 1):
                out[j] = True
    return out


def _previous_year_date(d: date) -> date:
    """Return the same civil date in the previous year where possible."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def _season_run_from_circular_flags(
    dates: list[date], flags: list[bool]
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Return (current_run, next_run) for a one-year circular season curve.

    ``dates[0]`` is the selected night.  If the selected night lies in a season
    that began before the scan boundary, the current run is represented with a
    negative start index; its start date can be recovered from the corresponding
    end-of-array date shifted back one year.
    """
    if not flags or not any(flags):
        return None, None
    n = len(flags)
    runs = _true_date_runs(dates, flags)
    if flags[0]:
        # Forward extent from the selected night.
        end = 0
        while end + 1 < n and flags[end + 1]:
            end += 1
        # Backward extent is represented by the qualifying tail of the annual
        # circular curve.
        tail_start = n
        j = n - 1
        while j >= 0 and flags[j]:
            tail_start = j
            j -= 1
        if tail_start < n and tail_start > end:
            start = tail_start - n
        else:
            start = 0
        return (start, end), None

    for run in runs:
        if run[0] > 0:
            return None, run
    return None, None

def analyse_observing_season(
    target: SkyCoord,
    profile: ObserverProfile,
    selected_date: date,
    *,
    qualifying_hours: float = 2.0,
    sample_minutes: int = 15,
    progress_callback: Callable[[str, int, int, date], None] | None = None,
) -> ObservingSeasonResult:
    """Describe the target's annual astronomical observing season.

    The scan covers one complete 366-night annual cycle beginning on the
    selected night and treats that curve circularly.  A qualifying seasonal
    night has at least ``qualifying_hours`` of
    astronomical darkness while the field is above the configured minimum
    altitude.  Moon phase is intentionally excluded from season boundaries.
    """
    max_alt = theoretical_maximum_altitude_deg(target, profile)
    min_alt = float(profile.minimum_altitude_deg)
    if max_alt <= 0.0:
        return ObservingSeasonResult(
            selected_date=selected_date,
            classification="NOT_VISIBLE",
            maximum_possible_altitude_deg=max_alt,
            minimum_altitude_deg=min_alt,
            current_useful_duration_hours=0.0,
        )
    if max_alt < min_alt:
        return ObservingSeasonResult(
            selected_date=selected_date,
            classification="TOO_LOW",
            maximum_possible_altitude_deg=max_alt,
            minimum_altitude_deg=min_alt,
            current_useful_duration_hours=0.0,
        )

    # Analyse exactly one civil-year cycle, excluding the duplicate endpoint.
    # This is 365 nights normally and 366 when the interval crosses 29 February.
    # Treat the resulting curve as circular only for joining a season across the
    # scan boundary; do not manufacture an 18-month pseudo-season.
    try:
        cycle_end = selected_date.replace(year=selected_date.year + 1)
    except ValueError:  # selected 29 February
        cycle_end = selected_date.replace(year=selected_date.year + 1, day=28)
    cycle_days = max(1, (cycle_end - selected_date).days)
    dates = [selected_date + timedelta(days=i) for i in range(cycle_days)]
    geometry = _season_geometry_for_dates(
        target,
        profile,
        dates,
        sample_minutes=sample_minutes,
        progress_callback=progress_callback,
    )
    durations = [geometry[d][0] for d in dates]
    raw_qualifying = [h >= qualifying_hours for h in durations]
    current_duration = durations[0]

    # A far-southern field can genuinely offer >= qualifying_hours on every
    # astronomical night of the year.  That is not a wraparound "season"; it is
    # a year-round target with a narrower prime period.  Detect it before any
    # gap-closing/circular topology logic.
    year_round = bool(raw_qualifying) and all(raw_qualifying)
    qualifying = _close_short_false_gaps(raw_qualifying, max_gap_days=14)

    containing, next_run = _season_run_from_circular_flags(dates, qualifying)
    chosen = (0, len(dates) - 1) if year_round else (containing or next_run)
    if chosen is None:
        return ObservingSeasonResult(
            selected_date=selected_date,
            classification="NOT_PRACTICAL",
            maximum_possible_altitude_deg=max_alt,
            minimum_altitude_deg=min_alt,
            current_useful_duration_hours=current_duration,
        )

    n = len(dates)
    a, b = chosen

    def concrete_date(idx: int) -> date:
        if idx >= 0:
            return dates[idx]
        return _previous_year_date(dates[idx % n])

    run_indices = list(range(a, b + 1))
    run_dates = [concrete_date(i) for i in run_indices]
    run_durations = [durations[i % n] for i in run_indices]

    max_duration = max(run_durations) if run_durations else 0.0
    max_local_idx = run_durations.index(max_duration) if run_durations else 0
    longest_date = run_dates[max_local_idx] if run_dates else None

    # Prime season is the broad near-maximum plateau.  Join short dips so the
    # user sees a useful period rather than a handful of mathematically special
    # nights.
    prime_threshold = max(qualifying_hours, max_duration * 0.80)
    prime_flags = _close_short_false_gaps(
        [h >= prime_threshold for h in run_durations], max_gap_days=7
    )
    prime_runs = _true_date_runs(run_dates, prime_flags)
    if prime_runs:
        pa, pb = max(prime_runs, key=lambda r: r[1] - r[0])
        prime_start = run_dates[pa]
        prime_end = run_dates[pb]
    else:
        prime_start = prime_end = longest_date

    season_start = run_dates[0]
    season_end = run_dates[-1]

    if year_round:
        # Preserve the useful "improving / prime / declining" nuance while
        # explicitly identifying targets that never fall below the annual
        # qualification threshold.
        if prime_start is not None and prime_end is not None and prime_start <= selected_date <= prime_end:
            classification = "YEAR_ROUND_PRIME"
        else:
            after = durations[min(14, n - 1)]
            delta = after - current_duration
            if delta > 0.25:
                classification = "YEAR_ROUND_IMPROVING"
            elif delta < -0.25:
                classification = "YEAR_ROUND_DECLINING"
            else:
                classification = "YEAR_ROUND"
    elif containing is None:
        classification = "OUT_OF_SEASON"
    else:
        if prime_start is not None and prime_end is not None and prime_start <= selected_date <= prime_end:
            classification = "PRIME"
        else:
            # Use the annual curve itself to say whether conditions are moving
            # toward or away from the seasonal maximum.  A fortnight is long
            # enough to avoid reacting to five-minute sampling noise.
            after = durations[min(14, n - 1)]
            delta = after - current_duration
            if delta > 0.25:
                classification = "IMPROVING"
            elif delta < -0.25:
                classification = "DECLINING"
            else:
                classification = "IN_SEASON"

    return ObservingSeasonResult(
        selected_date=selected_date,
        classification=classification,
        maximum_possible_altitude_deg=max_alt,
        minimum_altitude_deg=min_alt,
        current_useful_duration_hours=current_duration,
        season_start=None if year_round else season_start,
        season_end=None if year_round else season_end,
        prime_start=prime_start,
        prime_end=prime_end,
        longest_useful_duration_hours=max_duration,
        longest_date=longest_date,
        next_season_start=season_start if containing is None else None,
        next_season_end=season_end if containing is None else None,
    )
