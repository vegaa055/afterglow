"""
afterglow/app/main.py
---------------------
FastAPI application for the Afterglow sunset/sunrise forecasting service.

Routes
------
GET  /                          → HTML dashboard (Jinja2)
GET  /api/forecast              → 7-day scored forecast for a location
GET  /api/forecast/today        → today's sunset + sunrise scores
GET  /api/events                → raw solar event times (no scoring)
GET  /api/score                 → score an arbitrary set of atmospheric inputs
POST /api/score                 → same, JSON body
GET  /health                    → liveness check

Query params shared across forecast routes:
    lat      float   required   decimal degrees (+N)
    lon      float   required   decimal degrees (+E)
    tz       str     required   IANA timezone, e.g. "America/Phoenix"
    elev     float   optional   observer elevation in metres (default 0)

Install:
    pip install fastapi==0.111 uvicorn[standard]==0.29 jinja2==3.1 \
                python-multipart==0.0.9 pydantic==2.7
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Annotated, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from forecast import AfterglowFetcher, DayForecast
from scheduler import start_scheduler, stop_scheduler
from scorer import AfterglowScorer, ScoreResult
from solar import SolarCalculator, SolarEvents

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Afterglow API starting up")
    start_scheduler()
    yield
    logger.info("Afterglow API shutting down")
    stop_scheduler()


app = FastAPI(
    title="Afterglow",
    description="Sunset & sunrise vividness forecasting API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # tighten in production
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Shared scorer instance — stateless, safe to reuse across requests
_scorer = AfterglowScorer()


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class SolarEventTimes(BaseModel):
    date:                       str
    sunrise:                    Optional[str]
    sunset:                     Optional[str]
    golden_hour_eve_start:      Optional[str]
    golden_hour_eve_end:        Optional[str]
    blue_hour_eve_start:        Optional[str]
    blue_hour_eve_end:          Optional[str]
    civil_dusk:                 Optional[str]
    golden_hour_duration_min:   Optional[float]
    blue_hour_duration_min:     Optional[float]
    afterglow_window:           Optional[list[str]]

    @classmethod
    def from_events(cls, ev: SolarEvents) -> "SolarEventTimes":
        def _fmt(dt) -> Optional[str]:
            return dt.isoformat() if dt else None

        return cls(
            date=ev.date.isoformat(),
            sunrise=_fmt(ev.sunrise),
            sunset=_fmt(ev.sunset),
            golden_hour_eve_start=_fmt(ev.golden_hour_eve_start),
            golden_hour_eve_end=_fmt(ev.golden_hour_eve_end),
            blue_hour_eve_start=_fmt(ev.blue_hour_eve_start),
            blue_hour_eve_end=_fmt(ev.blue_hour_eve_end),
            civil_dusk=_fmt(ev.civil_dusk),
            golden_hour_duration_min=ev.golden_hour_eve_duration_min,
            blue_hour_duration_min=ev.blue_hour_eve_duration_min,
            afterglow_window=(
                [ev.afterglow_window[0].isoformat(), ev.afterglow_window[1].isoformat()]
                if ev.afterglow_window else None
            ),
        )


class ScoreSummary(BaseModel):
    score:       int
    grade:       str
    description: str
    breakdown:   dict[str, float]
    penalties:   dict[str, float]
    flags:       list[str]

    @classmethod
    def from_result(cls, r: ScoreResult) -> "ScoreSummary":
        return cls(
            score=r.score,
            grade=r.grade,
            description=r.description,
            breakdown=r.breakdown,
            penalties=r.penalties,
            flags=r.flags,
        )


class DayResponse(BaseModel):
    date:           str
    solar_events:   SolarEventTimes
    sunset_score:   ScoreSummary
    sunrise_score:  ScoreSummary
    raw_inputs_sunset:  dict[str, float]
    raw_inputs_sunrise: dict[str, float]


class ForecastResponse(BaseModel):
    location: dict
    generated_at: str
    days: list[DayResponse]


class TodayResponse(BaseModel):
    location:     dict
    generated_at: str
    date:         str
    solar_events: SolarEventTimes
    sunset:       ScoreSummary
    sunrise:      ScoreSummary


class ScoreRequest(BaseModel):
    """Body for POST /api/score — manual atmospheric input scoring."""
    cloud_cover_low:        float = Field(default=0.0,   ge=0, le=100)
    cloud_cover_mid:        float = Field(default=0.0,   ge=0, le=100)
    cloud_cover_high:       float = Field(default=0.0,   ge=0, le=100)
    aerosol_optical_depth:  float = Field(default=0.1,   ge=0, le=5.0)
    relative_humidity_2m:   float = Field(default=60.0,  ge=0, le=100)
    visibility:             float = Field(default=20000, ge=0, le=80000)
    precipitation:          float = Field(default=0.0,   ge=0)
    solar_elevation:        Optional[float] = Field(default=None, ge=-90, le=90)


# ---------------------------------------------------------------------------
# Shared dependency: build fetcher + solar calc from query params
# ---------------------------------------------------------------------------

def _make_tools(
    lat: float, lon: float, tz: str, elev: float
) -> tuple[AfterglowFetcher, SolarCalculator]:
    try:
        calc    = SolarCalculator(lat=lat, lon=lon, timezone=tz, observer_elevation=elev)
        fetcher = AfterglowFetcher(lat=lat, lon=lon, timezone=tz)
        return fetcher, calc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid location parameters: {exc}")


def _location_meta(lat: float, lon: float, tz: str) -> dict:
    return {"latitude": lat, "longitude": lon, "timezone": tz}


def _score_day(
    day: DayForecast,
    calc: SolarCalculator,
) -> tuple[ScoreSummary, ScoreSummary, dict, dict]:
    """Score both sunset and sunrise for a DayForecast. Returns (sunset, sunrise, raw_ss, raw_sr)."""
    sd_sunset  = day.sunset_scorer_dict(calc)
    sd_sunrise = day.sunrise_scorer_dict(calc)
    r_sunset   = _scorer.score(sd_sunset)
    r_sunrise  = _scorer.score(sd_sunrise)
    return (
        ScoreSummary.from_result(r_sunset),
        ScoreSummary.from_result(r_sunrise),
        sd_sunset,
        sd_sunrise,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def dashboard(request: Request):
    """Serve the main HTML dashboard."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/health", tags=["meta"])
async def health():
    """Liveness probe — returns 200 if the service is running."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat() + "Z"}


@app.get("/api/forecast", response_model=ForecastResponse, tags=["forecast"])
async def get_forecast(
    lat:  Annotated[float, Query(ge=-90,  le=90,   description="Latitude")],
    lon:  Annotated[float, Query(ge=-180, le=180,  description="Longitude")],
    tz:   Annotated[str,   Query(description="IANA timezone, e.g. America/Phoenix")],
    elev: Annotated[float, Query(ge=0,    le=9000, description="Observer elevation (m)")] = 0.0,
):
    """
    Return scored sunset + sunrise forecasts for the next 7 days.

    Scores are computed using the AfterglowScore algorithm:
    cloud cover (low/mid/high), aerosol optical depth, humidity,
    visibility, and precipitation are averaged across the golden-hour
    window then fed into the weighted Gaussian model.
    """
    fetcher, calc = _make_tools(lat, lon, tz, elev)

    try:
        week = fetcher.fetch_week()
    except Exception as exc:
        logger.exception("Forecast fetch failed")
        raise HTTPException(status_code=502, detail=f"Open-Meteo fetch failed: {exc}")

    days_out: list[DayResponse] = []
    for day in week:
        ev = calc.events(day.date)
        ss, sr, raw_ss, raw_sr = _score_day(day, calc)
        days_out.append(DayResponse(
            date=day.date.isoformat(),
            solar_events=SolarEventTimes.from_events(ev),
            sunset_score=ss,
            sunrise_score=sr,
            raw_inputs_sunset=raw_ss,
            raw_inputs_sunrise=raw_sr,
        ))

    return ForecastResponse(
        location=_location_meta(lat, lon, tz),
        generated_at=datetime.utcnow().isoformat() + "Z",
        days=days_out,
    )


@app.get("/api/forecast/today", response_model=TodayResponse, tags=["forecast"])
async def get_today(
    lat:  Annotated[float, Query(ge=-90,  le=90)],
    lon:  Annotated[float, Query(ge=-180, le=180)],
    tz:   Annotated[str,   Query()],
    elev: Annotated[float, Query(ge=0, le=9000)] = 0.0,
):
    """
    Return today's sunset and sunrise scores plus all solar event times.
    Faster than /api/forecast — fetches only today's data window.
    """
    fetcher, calc = _make_tools(lat, lon, tz, elev)

    try:
        today_day = fetcher.fetch_day()
    except Exception as exc:
        logger.exception("Today fetch failed")
        raise HTTPException(status_code=502, detail=f"Open-Meteo fetch failed: {exc}")

    if not today_day:
        raise HTTPException(status_code=404, detail="No forecast data returned for today")

    ev = calc.events()
    ss, sr, _, _ = _score_day(today_day, calc)

    return TodayResponse(
        location=_location_meta(lat, lon, tz),
        generated_at=datetime.utcnow().isoformat() + "Z",
        date=date.today().isoformat(),
        solar_events=SolarEventTimes.from_events(ev),
        sunset=ss,
        sunrise=sr,
    )


@app.get("/api/events", response_model=dict, tags=["solar"])
async def get_solar_events(
    lat: Annotated[float, Query(ge=-90,  le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    tz:  Annotated[str,   Query()],
    elev: Annotated[float, Query(ge=0, le=9000)] = 0.0,
    days: Annotated[int,  Query(ge=1, le=7, description="Number of days")] = 7,
):
    """
    Return raw solar event times (no weather scoring).
    Useful for populating the calendar timeline on the frontend.
    """
    try:
        calc = SolarCalculator(lat=lat, lon=lon, timezone=tz, observer_elevation=elev)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    week_events = calc.week_events()[:days]
    return {
        "location": _location_meta(lat, lon, tz),
        "events": [SolarEventTimes.from_events(ev).model_dump() for ev in week_events],
    }


@app.get("/api/score", tags=["score"])
async def score_manual_get(
    cloud_cover_low:       Annotated[float, Query(ge=0, le=100)] = 0.0,
    cloud_cover_mid:       Annotated[float, Query(ge=0, le=100)] = 0.0,
    cloud_cover_high:      Annotated[float, Query(ge=0, le=100)] = 0.0,
    aerosol_optical_depth: Annotated[float, Query(ge=0, le=5.0)] = 0.1,
    relative_humidity_2m:  Annotated[float, Query(ge=0, le=100)] = 60.0,
    visibility:            Annotated[float, Query(ge=0, le=80000)] = 20000.0,
    precipitation:         Annotated[float, Query(ge=0)] = 0.0,
    solar_elevation:       Annotated[Optional[float], Query(ge=-90, le=90)] = None,
):
    """
    Score an arbitrary set of atmospheric parameters.
    Useful for the frontend tuner and for testing the algorithm directly.

    Example:
        GET /api/score?cloud_cover_low=40&cloud_cover_mid=50&aerosol_optical_depth=0.22
    """
    data = {
        "cloud_cover_low":       cloud_cover_low,
        "cloud_cover_mid":       cloud_cover_mid,
        "cloud_cover_high":      cloud_cover_high,
        "aerosol_optical_depth": aerosol_optical_depth,
        "relative_humidity_2m":  relative_humidity_2m,
        "visibility":            visibility,
        "precipitation":         precipitation,
    }
    if solar_elevation is not None:
        data["solar_elevation"] = solar_elevation

    result = _scorer.score(data)
    return ScoreSummary.from_result(result).model_dump()


@app.post("/api/score", tags=["score"])
async def score_manual_post(body: ScoreRequest):
    """
    Score an arbitrary set of atmospheric parameters (JSON body).

    Accepts the same fields as GET /api/score but as a POST body —
    more ergonomic when called from JavaScript fetch() or curl.
    """
    data = body.model_dump(exclude_none=True)
    result = _scorer.score(data)
    return ScoreSummary.from_result(result).model_dump()


# ---------------------------------------------------------------------------
# Global error handler — always return JSON, never HTML 500 pages
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,        # flip to False in production
        log_level="info",
    )
