"""
afterglow/app/solar.py
----------------------
SolarCalculator — thin, opinionated wrapper around the `astral` library.

Provides all solar event times and sun-position data needed by the
forecaster in a single structured output. All times are returned as
timezone-aware datetimes. All angles are in degrees.

Usage:
    from solar import SolarCalculator

    calc = SolarCalculator(lat=31.5457, lon=-110.3019, timezone="America/Phoenix")
    events = calc.events()          # today
    events = calc.events(date)      # specific date
    series = calc.week_events()     # next 7 days

    print(events.sunset)            # datetime
    print(events.golden_hour_end)   # datetime
    print(events.solar_elevation_at(events.sunset))  # float (degrees)

Install:
    pip install astral==3.2
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from astral import LocationInfo, Observer
from astral.sun import (
    SunDirection,
    blue_hour,
    dawn,
    dusk,
    elevation,
    golden_hour,
    sunrise,
    sunset,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SolarEvents:
    """
    All solar event times and geometry for a single date at a given location.
    All datetimes are timezone-aware (location's local timezone unless stated).
    """
    date:               date

    # --- Core twilight sequence (chronological for sunset; reverse for sunrise) ---
    astronomical_dawn:  Optional[datetime]   # sun at -18°
    nautical_dawn:      Optional[datetime]   # sun at -12°
    civil_dawn:         Optional[datetime]   # sun at  -6°
    blue_hour_start:    Optional[datetime]   # sun at  -6° (dawn blue hour start)
    blue_hour_end:      Optional[datetime]   # sun at  -4° (dawn blue hour end)
    golden_hour_start:  Optional[datetime]   # sun at  -4° (PhotoPills convention)
    sunrise:            Optional[datetime]   # sun at   0° (apparent)
    golden_hour_end:    Optional[datetime]   # sun at  +6°

    solar_noon:         Optional[datetime]

    golden_hour_eve_start: Optional[datetime]  # sun at +6°  (evening)
    sunset:             Optional[datetime]     # sun at   0°
    golden_hour_eve_end: Optional[datetime]    # sun at  -4°
    blue_hour_eve_start: Optional[datetime]    # sun at  -4°
    blue_hour_eve_end:   Optional[datetime]    # sun at  -6°
    civil_dusk:         Optional[datetime]     # sun at  -6°
    nautical_dusk:      Optional[datetime]     # sun at -12°
    astronomical_dusk:  Optional[datetime]     # sun at -18°

    # Pre-computed elevations at the key scoring moments
    elevation_at_sunset:  Optional[float] = None   # typically ~0°
    elevation_at_golden_eve_end: Optional[float] = None  # typically ~ -4°

    # Observer metadata
    latitude:  float = 0.0
    longitude: float = 0.0
    timezone:  str   = "UTC"

    # Derived windows (duration in minutes)
    @property
    def golden_hour_eve_duration_min(self) -> Optional[float]:
        if self.golden_hour_eve_start and self.golden_hour_eve_end:
            delta = self.golden_hour_eve_end - self.golden_hour_eve_start
            return abs(delta.total_seconds() / 60)
        return None

    @property
    def blue_hour_eve_duration_min(self) -> Optional[float]:
        if self.blue_hour_eve_start and self.blue_hour_eve_end:
            delta = self.blue_hour_eve_end - self.blue_hour_eve_start
            return abs(delta.total_seconds() / 60)
        return None

    @property
    def afterglow_window(self) -> Optional[tuple[datetime, datetime]]:
        """
        The scoring window: from golden hour start (eve) through end of blue hour.
        This is the ~45-minute span the app forecasts for.
        """
        if self.golden_hour_eve_start and self.blue_hour_eve_end:
            return (self.golden_hour_eve_start, self.blue_hour_eve_end)
        return None

    def to_dict(self) -> dict:
        """Serialise to a JSON-safe dict (ISO strings for datetimes)."""
        def _fmt(v):
            if isinstance(v, datetime):
                return v.isoformat()
            return v

        return {
            "date":                    self.date.isoformat(),
            "astronomical_dawn":       _fmt(self.astronomical_dawn),
            "nautical_dawn":           _fmt(self.nautical_dawn),
            "civil_dawn":              _fmt(self.civil_dawn),
            "blue_hour_start":         _fmt(self.blue_hour_start),
            "blue_hour_end":           _fmt(self.blue_hour_end),
            "golden_hour_start":       _fmt(self.golden_hour_start),
            "sunrise":                 _fmt(self.sunrise),
            "golden_hour_end":         _fmt(self.golden_hour_end),
            "solar_noon":              _fmt(self.solar_noon),
            "golden_hour_eve_start":   _fmt(self.golden_hour_eve_start),
            "sunset":                  _fmt(self.sunset),
            "golden_hour_eve_end":     _fmt(self.golden_hour_eve_end),
            "blue_hour_eve_start":     _fmt(self.blue_hour_eve_start),
            "blue_hour_eve_end":       _fmt(self.blue_hour_eve_end),
            "civil_dusk":              _fmt(self.civil_dusk),
            "nautical_dusk":           _fmt(self.nautical_dusk),
            "astronomical_dusk":       _fmt(self.astronomical_dusk),
            "elevation_at_sunset":     self.elevation_at_sunset,
            "elevation_at_golden_eve_end": self.elevation_at_golden_eve_end,
            "golden_hour_eve_duration_min": self.golden_hour_eve_duration_min,
            "blue_hour_eve_duration_min":   self.blue_hour_eve_duration_min,
            "afterglow_window":        (
                [_fmt(self.afterglow_window[0]), _fmt(self.afterglow_window[1])]
                if self.afterglow_window else None
            ),
            "latitude":  self.latitude,
            "longitude": self.longitude,
            "timezone":  self.timezone,
        }


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

class SolarCalculator:
    """
    Wraps astral 3.2 to produce SolarEvents for any date at a fixed location.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees (+N / -S).
    lon : float
        Longitude in decimal degrees (+E / -W).
    timezone : str
        IANA timezone string, e.g. "America/Phoenix", "UTC".
    observer_elevation : float
        Observer height above sea level in metres.
        Affects how early the sun clears the geometric horizon.
        Sierra Vista sits at ~1,400 m — use 1400.0 for best accuracy there.
    """

    def __init__(
        self,
        lat: float,
        lon: float,
        timezone: str = "UTC",
        observer_elevation: float = 0.0,
    ):
        self.lat = lat
        self.lon = lon
        self.timezone = timezone
        self.observer_elevation = observer_elevation

        self._tz = ZoneInfo(timezone)
        self._observer = Observer(
            latitude=lat,
            longitude=lon,
            elevation=observer_elevation,
        )
        # LocationInfo is only needed for the .Location wrapper API;
        # we use the lower-level astral.sun.* functions directly for
        # explicit control over what gets returned.
        self._location = LocationInfo(
            name="location",
            region="",
            timezone=timezone,
            latitude=lat,
            longitude=lon,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def events(self, for_date: Optional[date] = None) -> SolarEvents:
        """Calculate all solar events for the given date (default: today)."""
        d = for_date or date.today()
        tz = self._tz
        obs = self._observer

        def _safe(fn, *args, **kwargs):
            """Call an astral function; return None if sun never rises/sets."""
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        # --- Dawn sequence ---
        a_dawn     = _safe(dawn, obs, date=d, depression=18, tzinfo=tz)
        n_dawn     = _safe(dawn, obs, date=d, depression=12, tzinfo=tz)
        c_dawn     = _safe(dawn, obs, date=d, depression=6,  tzinfo=tz)
        bh_rise    = _safe(blue_hour, obs, date=d, direction=SunDirection.RISING, tzinfo=tz)
        gh_rise    = _safe(golden_hour, obs, date=d, direction=SunDirection.RISING, tzinfo=tz)
        sr         = _safe(sunrise, obs, date=d, tzinfo=tz)

        # --- Noon ---
        from astral.sun import noon as solar_noon_fn
        s_noon     = _safe(solar_noon_fn, obs, date=d, tzinfo=tz)

        # --- Dusk sequence ---
        ss         = _safe(sunset, obs, date=d, tzinfo=tz)
        gh_set     = _safe(golden_hour, obs, date=d, direction=SunDirection.SETTING, tzinfo=tz)
        bh_set     = _safe(blue_hour, obs, date=d, direction=SunDirection.SETTING, tzinfo=tz)
        c_dusk     = _safe(dusk, obs, date=d, depression=6,  tzinfo=tz)
        n_dusk     = _safe(dusk, obs, date=d, depression=12, tzinfo=tz)
        a_dusk     = _safe(dusk, obs, date=d, depression=18, tzinfo=tz)

        # --- Elevation samples at key scoring moments ---
        elev_sunset = None
        elev_gh_end = None
        if ss:
            elev_sunset = self.solar_elevation_at(ss)
        if gh_set:
            # gh_set is a (start, end) tuple from astral
            gh_set_end = gh_set[1] if isinstance(gh_set, tuple) else None
            if gh_set_end:
                elev_gh_end = self.solar_elevation_at(gh_set_end)

        # Unpack golden/blue hour tuples
        gh_rise_start = gh_rise[0] if isinstance(gh_rise, tuple) else gh_rise
        gh_rise_end   = gh_rise[1] if isinstance(gh_rise, tuple) else None
        gh_set_start  = gh_set[0]  if isinstance(gh_set,  tuple) else gh_set
        gh_set_end_dt = gh_set[1]  if isinstance(gh_set,  tuple) else None

        bh_rise_start = bh_rise[0] if isinstance(bh_rise, tuple) else bh_rise
        bh_rise_end   = bh_rise[1] if isinstance(bh_rise, tuple) else None
        bh_set_start  = bh_set[0]  if isinstance(bh_set,  tuple) else bh_set
        bh_set_end_dt = bh_set[1]  if isinstance(bh_set,  tuple) else None

        return SolarEvents(
            date=d,

            astronomical_dawn=a_dawn,
            nautical_dawn=n_dawn,
            civil_dawn=c_dawn,
            blue_hour_start=bh_rise_start,
            blue_hour_end=bh_rise_end,
            golden_hour_start=gh_rise_start,
            sunrise=sr,
            golden_hour_end=gh_rise_end,

            solar_noon=s_noon,

            golden_hour_eve_start=gh_set_start,
            sunset=ss,
            golden_hour_eve_end=gh_set_end_dt,
            blue_hour_eve_start=bh_set_start,
            blue_hour_eve_end=bh_set_end_dt,
            civil_dusk=c_dusk,
            nautical_dusk=n_dusk,
            astronomical_dusk=a_dusk,

            elevation_at_sunset=elev_sunset,
            elevation_at_golden_eve_end=elev_gh_end,

            latitude=self.lat,
            longitude=self.lon,
            timezone=self.timezone,
        )

    def week_events(self, start_date: Optional[date] = None) -> list[SolarEvents]:
        """Return SolarEvents for 7 consecutive days starting at start_date (default today)."""
        start = start_date or date.today()
        return [self.events(start + timedelta(days=i)) for i in range(7)]

    def solar_elevation_at(self, dt: datetime) -> float:
        """
        Return the solar elevation angle in degrees at a specific datetime.
        Negative = below horizon, positive = above horizon.
        Accounts for atmospheric refraction.
        """
        return elevation(self._observer, dateandtime=dt, with_refraction=True)

    def scoring_elevation(self, for_date: Optional[date] = None) -> float:
        """
        Return the solar elevation at the midpoint of the evening golden hour —
        the single elevation value passed to AfterglowScorer.
        Falls back to the elevation at sunset if golden hour can't be computed.
        """
        d = for_date or date.today()
        ev = self.events(d)
        if ev.golden_hour_eve_start and ev.golden_hour_eve_end:
            midpoint = ev.golden_hour_eve_start + (
                ev.golden_hour_eve_end - ev.golden_hour_eve_start
            ) / 2
            return self.solar_elevation_at(midpoint)
        if ev.sunset:
            return self.solar_elevation_at(ev.sunset)
        return 0.0

    # ------------------------------------------------------------------
    # Convenience factories
    # ------------------------------------------------------------------

    @classmethod
    def for_sierra_vista(cls) -> "SolarCalculator":
        """Pre-configured for Sierra Vista, AZ — useful for local testing."""
        return cls(
            lat=31.5457,
            lon=-110.3019,
            timezone="America/Phoenix",
            observer_elevation=1400.0,  # ~4,600 ft elevation
        )

    @classmethod
    def for_tucson(cls) -> "SolarCalculator":
        return cls(
            lat=32.2226,
            lon=-110.9747,
            timezone="America/Phoenix",
            observer_elevation=728.0,
        )


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    calc = SolarCalculator.for_sierra_vista()
    ev   = calc.events()
    elev = calc.scoring_elevation()

    print(f"\nSolar events for Sierra Vista, AZ — {ev.date}")
    print(f"{'─'*52}")

    pairs = [
        ("Astronomical dawn", ev.astronomical_dawn),
        ("Nautical dawn",     ev.nautical_dawn),
        ("Civil dawn",        ev.civil_dawn),
        ("Blue hour (rise)",  ev.blue_hour_start),
        ("Golden hour start (rise)", ev.golden_hour_start),
        ("Sunrise",           ev.sunrise),
        ("Solar noon",        ev.solar_noon),
        ("Golden hour start (eve)", ev.golden_hour_eve_start),
        ("Sunset",            ev.sunset),
        ("Golden hour end (eve)",   ev.golden_hour_eve_end),
        ("Blue hour start (eve)",   ev.blue_hour_eve_start),
        ("Blue hour end (eve)",     ev.blue_hour_eve_end),
        ("Civil dusk",        ev.civil_dusk),
        ("Nautical dusk",     ev.nautical_dusk),
        ("Astronomical dusk", ev.astronomical_dusk),
    ]
    for label, dt in pairs:
        val = dt.strftime("%H:%M:%S %Z") if dt else "N/A (polar)"
        print(f"  {label:<35} {val}")

    print(f"\n  Golden hour (eve) duration  : {ev.golden_hour_eve_duration_min:.1f} min")
    print(f"  Blue hour (eve) duration    : {ev.blue_hour_eve_duration_min:.1f} min")
    print(f"  Scoring elevation           : {elev:.2f}°")

    win = ev.afterglow_window
    if win:
        print(f"  Afterglow window            : {win[0].strftime('%H:%M')} – {win[1].strftime('%H:%M %Z')}")
