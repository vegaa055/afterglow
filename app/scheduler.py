"""
afterglow/app/scheduler.py
--------------------------
Background scheduler for pre-computing and warming the forecast cache.

On startup the app registers one recurring job:
  - Every 60 minutes: re-fetch Open-Meteo data for all pinned locations
    and pre-score the week, storing results in an in-process cache that
    the API routes read instead of making live network calls.

Pinned locations are defined in WATCHED_LOCATIONS below — add entries
for any coordinates you want to guarantee fast cold-start responses.
The user's dynamic location is always fetched live and benefits from
requests-cache TTL (see forecast.py).

Usage (called by main.py lifespan):
    from scheduler import start_scheduler, stop_scheduler
    start_scheduler()   # on app startup
    stop_scheduler()    # on app shutdown

Install:
    pip install apscheduler==3.10
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from forecast import AfterglowFetcher
from scorer import AfterglowScorer
from solar import SolarCalculator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pinned locations — pre-warmed every hour
# ---------------------------------------------------------------------------

WATCHED_LOCATIONS: list[dict] = [
    {
        "name":     "Sierra Vista, AZ",
        "lat":      31.5457,
        "lon":     -110.3019,
        "timezone": "America/Phoenix",
        "elev":     1400.0,
    },
    {
        "name":     "Tucson, AZ",
        "lat":      32.2226,
        "lon":     -110.9747,
        "timezone": "America/Phoenix",
        "elev":     728.0,
    },
]

# ---------------------------------------------------------------------------
# In-process cache — keyed by (lat, lon, date_iso)
# ---------------------------------------------------------------------------
# This is intentionally a plain dict rather than Redis so the app has
# zero external runtime dependencies in development. Swap the _cache
# read/write for Redis calls when you move to a multi-worker deployment.

_cache: dict[str, dict] = {}
_scorer = AfterglowScorer()
_scheduler: Optional[BackgroundScheduler] = None


def cache_key(lat: float, lon: float, d: str) -> str:
    return f"{lat:.4f}:{lon:.4f}:{d}"


def cache_get(lat: float, lon: float, d: str) -> Optional[dict]:
    return _cache.get(cache_key(lat, lon, d))


def cache_set(lat: float, lon: float, d: str, payload: dict) -> None:
    _cache[cache_key(lat, lon, d)] = payload


def cache_clear() -> None:
    _cache.clear()


# ---------------------------------------------------------------------------
# The refresh job
# ---------------------------------------------------------------------------

def _refresh_location(loc: dict) -> None:
    """Fetch and score a full week for one pinned location, populate _cache."""
    lat      = loc["lat"]
    lon      = loc["lon"]
    tz       = loc["timezone"]
    elev     = loc.get("elev", 0.0)
    name     = loc["name"]

    logger.info("Refreshing forecast cache for %s", name)
    t0 = datetime.utcnow()

    try:
        fetcher = AfterglowFetcher(lat=lat, lon=lon, timezone=tz)
        calc    = SolarCalculator(lat=lat, lon=lon, timezone=tz, observer_elevation=elev)
        week    = fetcher.fetch_week()

        for day in week:
            ev          = calc.events(day.date)
            sd_sunset   = day.sunset_scorer_dict(calc)
            sd_sunrise  = day.sunrise_scorer_dict(calc)
            r_sunset    = _scorer.score(sd_sunset)
            r_sunrise   = _scorer.score(sd_sunrise)

            payload = {
                "date":              day.date.isoformat(),
                "sunset_score":      r_sunset.score,
                "sunset_grade":      r_sunset.grade,
                "sunset_breakdown":  r_sunset.breakdown,
                "sunset_flags":      r_sunset.flags,
                "sunrise_score":     r_sunrise.score,
                "sunrise_grade":     r_sunrise.grade,
                "sunrise_breakdown": r_sunrise.breakdown,
                "sunrise_flags":     r_sunrise.flags,
                "sunset_time":       ev.sunset.isoformat() if ev.sunset else None,
                "sunrise_time":      ev.sunrise.isoformat() if ev.sunrise else None,
                "cached_at":         t0.isoformat() + "Z",
            }
            cache_set(lat, lon, day.date.isoformat(), payload)

        elapsed = (datetime.utcnow() - t0).total_seconds()
        logger.info("Cache refresh for %s complete in %.2fs (%d days)", name, elapsed, len(week))

    except Exception:
        logger.exception("Cache refresh failed for %s", name)


def _refresh_all() -> None:
    """Refresh all pinned locations. Called on startup and then hourly."""
    for loc in WATCHED_LOCATIONS:
        _refresh_location(loc)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    global _scheduler

    if _scheduler and _scheduler.running:
        logger.warning("Scheduler already running — skipping start")
        return

    _scheduler = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    _scheduler.add_job(
        _refresh_all,
        trigger=IntervalTrigger(minutes=60),
        id="forecast_refresh",
        name="Hourly forecast cache refresh",
        replace_existing=True,
        next_run_time=datetime.utcnow(),   # run immediately on startup
    )
    _scheduler.start()
    logger.info("Scheduler started — first refresh queued immediately")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
