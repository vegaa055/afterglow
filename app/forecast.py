"""
afterglow/app/forecast.py
-------------------------
AfterglowFetcher — fetches and merges weather + air quality data from
Open-Meteo, then bundles it with solar event times from SolarCalculator
into ready-to-score dicts for AfterglowScorer.

Two Open-Meteo endpoints are used:
  - /v1/forecast      → cloud cover (low/mid/high), humidity, visibility,
                        precipitation (weather.open-meteo.com)
  - /v1/air-quality   → aerosol_optical_depth, dust, pm10, pm2_5
                        (air-quality-api.open-meteo.com)

Both are free, no API key required.

Usage:
    from forecast import AfterglowFetcher
    from solar import SolarCalculator
    from scorer import AfterglowScorer

    calc    = SolarCalculator(lat=31.5457, lon=-110.3019, timezone="America/Phoenix")
    fetcher = AfterglowFetcher(lat=31.5457, lon=-110.3019, timezone="America/Phoenix")
    scorer  = AfterglowScorer()

    daily   = fetcher.fetch_week()          # list[DayForecast] for next 7 days
    for day in daily:
        result = scorer.score(day.sunset_scorer_dict(calc))
        print(day.date, result.grade, result.score)

Install:
    pip install requests==2.31 openmeteo-requests==1.2 requests-cache==1.1 retry-requests==2.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import requests_cache
from retry_requests import retry

from solar import SolarCalculator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Open-Meteo endpoint URLs
# ---------------------------------------------------------------------------

WEATHER_URL     = "https://api.open-meteo.com/v1/forecast"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Hourly variables to request from each endpoint
WEATHER_HOURLY_VARS = [
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "relative_humidity_2m",
    "visibility",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
]

AIR_QUALITY_HOURLY_VARS = [
    "aerosol_optical_depth",
    "dust",
    "pm10",
    "pm2_5",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HourlySlice:
    """Merged weather + air quality values for a single hour."""
    time:                   datetime

    # From /v1/forecast
    cloud_cover_low:        float   = 0.0   # %
    cloud_cover_mid:        float   = 0.0   # %
    cloud_cover_high:       float   = 0.0   # %
    relative_humidity_2m:   float   = 60.0  # %
    visibility:             float   = 20_000.0  # m
    precipitation:          float   = 0.0   # mm
    precipitation_probability: float = 0.0  # %
    weather_code:           int     = 0
    wind_speed_10m:         float   = 0.0   # km/h
    wind_direction_10m:     float   = 0.0   # degrees

    # From /v1/air-quality
    aerosol_optical_depth:  float   = 0.1
    dust:                   float   = 0.0   # μg/m³
    pm10:                   float   = 0.0   # μg/m³
    pm2_5:                  float   = 0.0   # μg/m³

    def to_scorer_dict(self, solar_elevation: Optional[float] = None) -> dict:
        """Convert to the flat dict expected by AfterglowScorer.score()."""
        d = {
            "cloud_cover_low":       self.cloud_cover_low,
            "cloud_cover_mid":       self.cloud_cover_mid,
            "cloud_cover_high":      self.cloud_cover_high,
            "aerosol_optical_depth": self.aerosol_optical_depth,
            "relative_humidity_2m":  self.relative_humidity_2m,
            "visibility":            self.visibility,
            "precipitation":         self.precipitation,
        }
        if solar_elevation is not None:
            d["solar_elevation"] = solar_elevation
        return d


@dataclass
class DayForecast:
    """All hourly slices for a single calendar date."""
    date:   date
    hours:  list[HourlySlice] = field(default_factory=list)

    # Timezone-aware solar events (populated by AfterglowFetcher)
    timezone: str = "UTC"

    def slice_at(self, dt: datetime) -> Optional[HourlySlice]:
        """Return the hourly slice closest to a given datetime."""
        if not self.hours:
            return None
        return min(self.hours, key=lambda h: abs((h.time - dt).total_seconds()))

    def slices_in_window(
        self, start: datetime, end: datetime
    ) -> list[HourlySlice]:
        """Return all slices whose time falls within [start, end]."""
        return [h for h in self.hours if start <= h.time <= end]

    def sunset_scorer_dict(self, calc: SolarCalculator) -> dict:
        """
        Build the scorer input dict for the sunset afterglow window.
        Averages all hourly slices within the golden-hour-to-blue-hour window
        rather than using a single snapshot — more stable than the instant value.
        """
        ev = calc.events(self.date)
        solar_elev = calc.scoring_elevation(self.date)

        if ev.afterglow_window:
            window_start, window_end = ev.afterglow_window
            slices = self.slices_in_window(window_start, window_end)
        else:
            slices = []

        # Fallback: nearest hour to sunset
        if not slices and ev.sunset:
            s = self.slice_at(ev.sunset)
            slices = [s] if s else []

        if not slices:
            logger.warning("No hourly slices found for %s sunset window", self.date)
            return {"solar_elevation": solar_elev}

        return _average_slices(slices, solar_elev)

    def sunrise_scorer_dict(self, calc: SolarCalculator) -> dict:
        """
        Build the scorer input dict for the sunrise afterglow window.
        Uses the hour around golden_hour_end (morning).
        """
        ev = calc.events(self.date)
        solar_elev = calc.scoring_elevation(self.date)

        target = ev.golden_hour_end or ev.sunrise
        if target:
            # ±30 min window around golden hour end
            slices = self.slices_in_window(
                target - timedelta(minutes=30),
                target + timedelta(minutes=30),
            )

        if not slices and target:
            s = self.slice_at(target)
            slices = [s] if s else []

        if not slices:
            return {"solar_elevation": solar_elev}

        return _average_slices(slices, solar_elev)


# ---------------------------------------------------------------------------
# Averaging helper
# ---------------------------------------------------------------------------

def _average_slices(
    slices: list[HourlySlice], solar_elev: Optional[float]
) -> dict:
    """Average the numeric scoring fields across a list of HourlySlices."""
    n = len(slices)
    averaged = {
        "cloud_cover_low":       sum(s.cloud_cover_low        for s in slices) / n,
        "cloud_cover_mid":       sum(s.cloud_cover_mid        for s in slices) / n,
        "cloud_cover_high":      sum(s.cloud_cover_high       for s in slices) / n,
        "aerosol_optical_depth": sum(s.aerosol_optical_depth  for s in slices) / n,
        "relative_humidity_2m":  sum(s.relative_humidity_2m   for s in slices) / n,
        "visibility":            sum(s.visibility             for s in slices) / n,
        "precipitation":         sum(s.precipitation          for s in slices) / n,
    }
    if solar_elev is not None:
        averaged["solar_elevation"] = solar_elev
    return averaged


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class AfterglowFetcher:
    """
    Fetches weather + air quality forecasts from Open-Meteo and
    assembles DayForecast objects ready for scoring.

    Uses requests-cache so repeated calls within `cache_expire_minutes`
    don't hit the network — important for the APScheduler refresh loop.

    Parameters
    ----------
    lat : float
    lon : float
    timezone : str
        IANA timezone string, e.g. "America/Phoenix".
    forecast_days : int
        How many days ahead to fetch (max 16 for weather, 5 for air quality).
    cache_expire_minutes : int
        SQLite cache TTL. Set to 0 to disable caching.
    """

    def __init__(
        self,
        lat: float,
        lon: float,
        timezone: str = "UTC",
        forecast_days: int = 7,
        cache_expire_minutes: int = 60,
    ):
        self.lat           = lat
        self.lon           = lon
        self.timezone      = timezone
        self.forecast_days = min(forecast_days, 7)   # air quality caps at 5, weather at 16
        self._tz           = ZoneInfo(timezone)

        # Set up a cached + retry-wrapped session
        if cache_expire_minutes > 0:
            cache_session = requests_cache.CachedSession(
                ".afterglow_cache",
                expire_after=timedelta(minutes=cache_expire_minutes),
            )
            self._session = retry(cache_session, retries=3, backoff_factor=0.5)
        else:
            self._session = retry(requests.Session(), retries=3, backoff_factor=0.5)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_week(self) -> list[DayForecast]:
        """
        Fetch weather + air quality for the next `forecast_days` days,
        merge them, and return a list of DayForecast objects.
        """
        weather_raw  = self._fetch_weather()
        aq_raw       = self._fetch_air_quality()
        return self._merge(weather_raw, aq_raw)

    def fetch_day(self, for_date: Optional[date] = None) -> Optional[DayForecast]:
        """Return the DayForecast for a single date (default: today)."""
        d = for_date or date.today()
        week = self.fetch_week()
        for day in week:
            if day.date == d:
                return day
        return None

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_weather(self) -> dict:
        params = {
            "latitude":     self.lat,
            "longitude":    self.lon,
            "hourly":       ",".join(WEATHER_HOURLY_VARS),
            "timezone":     self.timezone,
            "forecast_days": self.forecast_days,
            "wind_speed_unit": "kmh",
            "timeformat":   "iso8601",
        }
        resp = self._session.get(WEATHER_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Weather fetch: %s (%s rows)", data.get("timezone"), len(data["hourly"]["time"]))
        return data

    def _fetch_air_quality(self) -> dict:
        params = {
            "latitude":  self.lat,
            "longitude": self.lon,
            "hourly":    ",".join(AIR_QUALITY_HOURLY_VARS),
            "timezone":  self.timezone,
            "timeformat": "iso8601",
            "forecast_days": min(self.forecast_days, 5),  # AQ API max
        }
        resp = self._session.get(AIR_QUALITY_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.debug("Air quality fetch: %s rows", len(data["hourly"]["time"]))
        return data

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def _merge(self, weather: dict, aq: dict) -> list[DayForecast]:
        """
        Build a time-indexed dict from both API responses, then group by date.
        Open-Meteo returns ISO 8601 time strings — we parse them as
        timezone-aware datetimes so slices_in_window() comparisons work.
        """
        # Parse weather
        wh = weather["hourly"]
        weather_by_time: dict[str, dict] = {}
        for i, ts in enumerate(wh["time"]):
            dt = self._parse_ts(ts)
            weather_by_time[ts] = {
                "time":                      dt,
                "cloud_cover_low":           self._safe(wh, "cloud_cover_low",           i, 0.0),
                "cloud_cover_mid":           self._safe(wh, "cloud_cover_mid",           i, 0.0),
                "cloud_cover_high":          self._safe(wh, "cloud_cover_high",          i, 0.0),
                "relative_humidity_2m":      self._safe(wh, "relative_humidity_2m",      i, 60.0),
                "visibility":                self._safe(wh, "visibility",                i, 20_000.0),
                "precipitation":             self._safe(wh, "precipitation",             i, 0.0),
                "precipitation_probability": self._safe(wh, "precipitation_probability", i, 0.0),
                "weather_code":              int(self._safe(wh, "weather_code",          i, 0)),
                "wind_speed_10m":            self._safe(wh, "wind_speed_10m",            i, 0.0),
                "wind_direction_10m":        self._safe(wh, "wind_direction_10m",        i, 0.0),
            }

        # Parse air quality and merge into weather_by_time
        aqh = aq["hourly"]
        for i, ts in enumerate(aqh["time"]):
            if ts in weather_by_time:
                weather_by_time[ts]["aerosol_optical_depth"] = self._safe(aqh, "aerosol_optical_depth", i, 0.1)
                weather_by_time[ts]["dust"]  = self._safe(aqh, "dust",   i, 0.0)
                weather_by_time[ts]["pm10"]  = self._safe(aqh, "pm10",   i, 0.0)
                weather_by_time[ts]["pm2_5"] = self._safe(aqh, "pm2_5",  i, 0.0)

        # Group by date
        days_map: dict[date, DayForecast] = {}
        for row in weather_by_time.values():
            d = row["time"].date()
            if d not in days_map:
                days_map[d] = DayForecast(date=d, timezone=self.timezone)
            days_map[d].hours.append(
                HourlySlice(
                    time=row["time"],
                    cloud_cover_low=row["cloud_cover_low"],
                    cloud_cover_mid=row["cloud_cover_mid"],
                    cloud_cover_high=row["cloud_cover_high"],
                    relative_humidity_2m=row["relative_humidity_2m"],
                    visibility=row["visibility"],
                    precipitation=row["precipitation"],
                    precipitation_probability=row["precipitation_probability"],
                    weather_code=row["weather_code"],
                    wind_speed_10m=row["wind_speed_10m"],
                    wind_direction_10m=row["wind_direction_10m"],
                    aerosol_optical_depth=row.get("aerosol_optical_depth", 0.1),
                    dust=row.get("dust", 0.0),
                    pm10=row.get("pm10", 0.0),
                    pm2_5=row.get("pm2_5", 0.0),
                )
            )

        # Sort hours within each day, sort days chronologically
        for day in days_map.values():
            day.hours.sort(key=lambda h: h.time)

        return sorted(days_map.values(), key=lambda d: d.date)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _parse_ts(self, ts: str) -> datetime:
        """
        Parse an Open-Meteo ISO 8601 timestamp (e.g. "2026-04-07T18:00")
        into a timezone-aware datetime in the location's timezone.
        Open-Meteo returns naive local time strings when timezone is set,
        so we attach the ZoneInfo manually.
        """
        naive = datetime.fromisoformat(ts)
        return naive.replace(tzinfo=self._tz)

    @staticmethod
    def _safe(hourly: dict, key: str, idx: int, default: float) -> float:
        """Index into an hourly values list, substituting None with a default."""
        values = hourly.get(key, [])
        if idx >= len(values):
            return default
        val = values[idx]
        return float(val) if val is not None else default

    # ------------------------------------------------------------------
    # Convenience factories
    # ------------------------------------------------------------------

    @classmethod
    def for_sierra_vista(cls, **kwargs) -> "AfterglowFetcher":
        return cls(lat=31.5457, lon=-110.3019, timezone="America/Phoenix", **kwargs)

    @classmethod
    def for_tucson(cls, **kwargs) -> "AfterglowFetcher":
        return cls(lat=32.2226, lon=-110.9747, timezone="America/Phoenix", **kwargs)


# ---------------------------------------------------------------------------
# CLI smoke-test — runs a real fetch and prints the full week
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    from scorer import AfterglowScorer

    calc    = SolarCalculator.for_sierra_vista()
    fetcher = AfterglowFetcher.for_sierra_vista()
    scorer  = AfterglowScorer()

    print("Fetching 7-day forecast for Sierra Vista, AZ...\n")
    week = fetcher.fetch_week()

    print(f"{'Date':<12} {'Sunset':<8} {'Score':>5} {'Grade':<8} {'Flags'}")
    print("─" * 58)
    for day in week:
        ev = calc.events(day.date)
        sunset_str = ev.sunset.strftime("%H:%M") if ev.sunset else "N/A"
        sd = day.sunset_scorer_dict(calc)
        result = scorer.score(sd)
        flags_str = ", ".join(result.flags) if result.flags else "—"
        print(f"{str(day.date):<12} {sunset_str:<8} {result.score:>5}  {result.grade:<8} {flags_str}")

    print("\nFull breakdown for today:")
    today_day = week[0] if week else None
    if today_day:
        sd = today_day.sunset_scorer_dict(calc)
        result = scorer.score(sd)
        print(f"  Score    : {result.score} ({result.grade})")
        print(f"  {result.description}")
        print(f"  Breakdown: {result.breakdown}")
        print(f"  Penalties: {result.penalties}")
        print(f"  Inputs   : {json.dumps({k: round(v, 3) for k, v in sd.items() if k != 'solar_elevation'}, indent=4)}")
