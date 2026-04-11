"""
afterglow/app/scorer.py
-----------------------
AfterglowScore — composite sunset/sunrise vividness scorer.

Computes a 0–100 vividness score from atmospheric forecast variables,
graded into human-readable tiers. Designed to be called with a dict
of Open-Meteo hourly values at the target solar event time.

Usage:
    from scorer import AfterglowScorer

    scorer = AfterglowScorer()
    result = scorer.score({
        "cloud_cover_low":          38.0,   # %
        "cloud_cover_mid":          45.0,   # %
        "cloud_cover_high":         10.0,   # %
        "aerosol_optical_depth":     0.18,  # dimensionless
        "relative_humidity_2m":     52.0,   # %
        "visibility":            18000.0,   # metres
        "precipitation":             0.0,   # mm
        "solar_elevation":          -4.5,   # degrees (negative = below horizon)
    })
    print(result.score)       # 74
    print(result.grade)       # "Vivid"
    print(result.breakdown)   # dict of per-factor contributions
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Grading thresholds
# ---------------------------------------------------------------------------

GRADES: list[tuple[int, str, str]] = [
    (81, "Epic",   "🔥 Extraordinary — drop everything."),
    (61, "Vivid",  "✨ Strong color expected."),
    (41, "Good",   "🌤 Worth watching."),
    (21, "Fair",   "🌥 Modest colour possible."),
    (0,  "Poor",   "☁ Unlikely to be noteworthy."),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    score: int                          # 0–100
    grade: str                          # e.g. "Vivid"
    description: str                    # human-readable summary
    breakdown: dict[str, float]         # per-factor raw contributions
    weights: dict[str, float]           # weights used
    penalties: dict[str, float]         # multiplicative penalty values (0–1)
    raw_inputs: dict[str, float]        # original inputs for traceability
    flags: list[str] = field(default_factory=list)   # any warning flags


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class AfterglowScorer:
    """
    Computes a vividness score for a sunset or sunrise afterglow event.

    Each atmospheric factor is mapped to a 0–100 sub-score via a Gaussian
    or linear curve tuned to the physical literature on afterglow quality.
    Sub-scores are combined via a weighted sum, then multiplied by
    cumulative penalty factors for degrading conditions.

    Weights and curve parameters are fully configurable so you can tune
    against real-world photo records later without changing calling code.
    """

    # Default weights — must sum to 1.0
    DEFAULT_WEIGHTS: dict[str, float] = {
        "low_cloud":  0.35,   # Low cloud scatter is the dominant variable
        "mid_cloud":  0.30,   # Altocumulus/altostratus catch the best colour
        "aod":        0.20,   # Aerosol optical depth — Rayleigh/Mie scattering
        "high_cloud": 0.15,   # Cirrus/cirrostratus — softer contribution
    }

    # Gaussian bell curve params: (optimal_value, sigma)
    # Score = 100 * exp(-((x - mu)^2) / (2 * sigma^2))
    CLOUD_CURVES: dict[str, tuple[float, float]] = {
        "low_cloud":  (40.0, 20.0),   # peaks at 40% low cloud
        "mid_cloud":  (45.0, 22.0),   # peaks at 45% mid cloud
        "high_cloud": (25.0, 18.0),   # peaks at 25% high cloud (cirrus)
    }

    # AOD: linear boost up to 0.35, then plateau, then heavy penalty above 0.5
    AOD_BOOST_CAP:     float = 0.35
    AOD_PENALTY_START: float = 0.50
    AOD_BOOST_SCALE:   float = 285.0   # score per unit AOD in linear zone
    AOD_PENALTY_SCALE: float = 350.0   # score loss per unit AOD above threshold

    # Penalty thresholds
    HUMIDITY_PENALTY_START: float = 70.0   # % RH above which haze builds
    HUMIDITY_PENALTY_MAX:   float = 95.0
    HUMIDITY_PENALTY_WEIGHT: float = 0.30  # max 30% suppression at 95% RH

    VISIBILITY_PENALTY_START: float = 15_000.0   # metres
    VISIBILITY_PENALTY_MAX:   float = 2_000.0
    VISIBILITY_PENALTY_WEIGHT: float = 0.25

    PRECIP_PENALTY_THRESHOLD: float = 0.1   # mm — any meaningful precip = hard penalty
    PRECIP_PENALTY_WEIGHT:    float = 0.50

    HIGH_CLOUD_CEILING_PENALTY: float = 80.0   # % — solid overcast kills everything
    HIGH_CLOUD_CEILING_WEIGHT:  float = 0.40

    # Solar elevation bonus: optimal window -3° to -6° below horizon
    SOLAR_ELEV_OPTIMAL_LOW:  float = -6.0
    SOLAR_ELEV_OPTIMAL_HIGH: float = -3.0
    SOLAR_ELEV_BONUS_MAX:    float = 0.10   # up to +10% multiplier in optimal window

    def __init__(self, weights: Optional[dict[str, float]] = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self._validate_weights()

    def _validate_weights(self) -> None:
        total = sum(self.weights.values())
        if not math.isclose(total, 1.0, abs_tol=0.01):
            raise ValueError(f"Weights must sum to 1.0, got {total:.3f}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, data: dict[str, float]) -> ScoreResult:
        """
        Score a single observation dict. Keys:
            cloud_cover_low       (%) 0–100
            cloud_cover_mid       (%) 0–100
            cloud_cover_high      (%) 0–100
            aerosol_optical_depth     0–2 (typical range 0.0–0.8)
            relative_humidity_2m  (%) 0–100
            visibility            (m) 0–80000
            precipitation         (mm) 0–...
            solar_elevation       (°) negative below horizon

        All keys are optional; missing keys use conservative defaults.
        """
        d = self._apply_defaults(data)
        flags: list[str] = []

        # --- Sub-scores (0–100 each) ---
        low_score  = self._gaussian(d["cloud_cover_low"],  *self.CLOUD_CURVES["low_cloud"])
        mid_score  = self._gaussian(d["cloud_cover_mid"],  *self.CLOUD_CURVES["mid_cloud"])
        high_score = self._gaussian(d["cloud_cover_high"], *self.CLOUD_CURVES["high_cloud"])
        aod_score  = self._aod_score(d["aerosol_optical_depth"])

        breakdown = {
            "low_cloud":  round(low_score  * self.weights["low_cloud"],  2),
            "mid_cloud":  round(mid_score  * self.weights["mid_cloud"],  2),
            "high_cloud": round(high_score * self.weights["high_cloud"], 2),
            "aod":        round(aod_score  * self.weights["aod"],        2),
        }
        weighted_sum = sum(breakdown.values())

        # --- Penalty factors ---
        p_humidity   = self._humidity_penalty(d["relative_humidity_2m"])
        p_visibility = self._visibility_penalty(d["visibility"])
        p_precip     = self._precip_penalty(d["precipitation"])
        p_overcast   = self._high_cloud_ceiling_penalty(d["cloud_cover_high"])

        penalties = {
            "humidity":   round(p_humidity,   4),
            "visibility": round(p_visibility, 4),
            "precipitation": round(p_precip,  4),
            "overcast_ceiling": round(p_overcast, 4),
        }
        total_penalty = p_humidity * p_visibility * p_precip * p_overcast

        # --- Solar elevation bonus ---
        solar_mult = self._solar_elevation_multiplier(d.get("solar_elevation"))

        raw_float = weighted_sum * total_penalty * solar_mult
        final_score = int(round(min(100.0, max(0.0, raw_float))))

        # --- Flags ---
        if d["precipitation"] >= self.PRECIP_PENALTY_THRESHOLD:
            flags.append("precipitation_present")
        if d["relative_humidity_2m"] > 85:
            flags.append("high_humidity")
        if d["visibility"] < 5000:
            flags.append("low_visibility")
        if d["aerosol_optical_depth"] > 0.5:
            flags.append("heavy_aerosol")
        if d["cloud_cover_high"] > self.HIGH_CLOUD_CEILING_PENALTY:
            flags.append("overcast_ceiling")

        grade, description = self._grade(final_score)

        return ScoreResult(
            score=final_score,
            grade=grade,
            description=description,
            breakdown=breakdown,
            weights=self.weights.copy(),
            penalties=penalties,
            raw_inputs=d,
            flags=flags,
        )

    def score_series(self, records: list[dict[str, float]]) -> list[ScoreResult]:
        """Score a list of hourly/daily observation dicts."""
        return [self.score(r) for r in records]

    # ------------------------------------------------------------------
    # Curve functions
    # ------------------------------------------------------------------

    @staticmethod
    def _gaussian(x: float, mu: float, sigma: float) -> float:
        """Bell curve centred on mu with width sigma. Returns 0–100."""
        return 100.0 * math.exp(-((x - mu) ** 2) / (2 * sigma ** 2))

    def _aod_score(self, aod: float) -> float:
        """
        AOD scoring:
          0 → 0.35 : linear boost (more aerosol = more scattering = richer colour)
          0.35 → 0.5: plateau
          > 0.5    : penalty (thick smoke/dust washes out colour and blocks sun)
        """
        if aod <= self.AOD_BOOST_CAP:
            return min(100.0, aod * self.AOD_BOOST_SCALE)
        elif aod <= self.AOD_PENALTY_START:
            return min(100.0, self.AOD_BOOST_CAP * self.AOD_BOOST_SCALE)  # plateau
        else:
            plateau = self.AOD_BOOST_CAP * self.AOD_BOOST_SCALE
            return max(0.0, plateau - (aod - self.AOD_PENALTY_START) * self.AOD_PENALTY_SCALE)

    # ------------------------------------------------------------------
    # Penalty functions — each returns a multiplier in (0, 1]
    # ------------------------------------------------------------------

    def _humidity_penalty(self, rh: float) -> float:
        if rh <= self.HUMIDITY_PENALTY_START:
            return 1.0
        ratio = min(1.0, (rh - self.HUMIDITY_PENALTY_START) /
                    (self.HUMIDITY_PENALTY_MAX - self.HUMIDITY_PENALTY_START))
        return 1.0 - ratio * self.HUMIDITY_PENALTY_WEIGHT

    def _visibility_penalty(self, vis: float) -> float:
        if vis >= self.VISIBILITY_PENALTY_START:
            return 1.0
        ratio = min(1.0, (self.VISIBILITY_PENALTY_START - vis) /
                    (self.VISIBILITY_PENALTY_START - self.VISIBILITY_PENALTY_MAX))
        return 1.0 - ratio * self.VISIBILITY_PENALTY_WEIGHT

    def _precip_penalty(self, precip: float) -> float:
        if precip < self.PRECIP_PENALTY_THRESHOLD:
            return 1.0
        # Soft: light drizzle might still produce colour, hard penalty for real rain
        return max(1.0 - self.PRECIP_PENALTY_WEIGHT,
                   1.0 - self.PRECIP_PENALTY_WEIGHT * min(1.0, precip / 2.0))

    def _high_cloud_ceiling_penalty(self, high_cloud: float) -> float:
        """Solid high cloud overcast blocks the colour entirely."""
        if high_cloud <= self.HIGH_CLOUD_CEILING_PENALTY:
            return 1.0
        ratio = min(1.0, (high_cloud - self.HIGH_CLOUD_CEILING_PENALTY) /
                    (100.0 - self.HIGH_CLOUD_CEILING_PENALTY))
        return 1.0 - ratio * self.HIGH_CLOUD_CEILING_WEIGHT

    def _solar_elevation_multiplier(self, elev: Optional[float]) -> float:
        """
        Slight boost when the sun is in the optimal twilight window
        (-3° to -6° below the horizon) — the moment of longest atmospheric
        path length for the most saturated colour.
        """
        if elev is None:
            return 1.0
        if self.SOLAR_ELEV_OPTIMAL_LOW <= elev <= self.SOLAR_ELEV_OPTIMAL_HIGH:
            return 1.0 + self.SOLAR_ELEV_BONUS_MAX
        return 1.0

    # ------------------------------------------------------------------
    # Grading
    # ------------------------------------------------------------------

    @staticmethod
    def _grade(score: int) -> tuple[str, str]:
        for threshold, grade, description in GRADES:
            if score >= threshold:
                return grade, description
        return GRADES[-1][1], GRADES[-1][2]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_defaults(data: dict[str, float]) -> dict[str, float]:
        """Fill missing keys with conservative (penalising) defaults."""
        defaults = {
            "cloud_cover_low":       0.0,
            "cloud_cover_mid":       0.0,
            "cloud_cover_high":      0.0,
            "aerosol_optical_depth": 0.1,
            "relative_humidity_2m":  60.0,
            "visibility":            20_000.0,
            "precipitation":         0.0,
            "solar_elevation":       None,
        }
        return {**defaults, **data}


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    scorer = AfterglowScorer()

    scenarios = [
        ("Sierra Vista golden evening", {
            "cloud_cover_low": 35, "cloud_cover_mid": 50, "cloud_cover_high": 15,
            "aerosol_optical_depth": 0.22, "relative_humidity_2m": 38,
            "visibility": 40000, "precipitation": 0.0, "solar_elevation": -4.2,
        }),
        ("Monsoon aftermath", {
            "cloud_cover_low": 70, "cloud_cover_mid": 60, "cloud_cover_high": 30,
            "aerosol_optical_depth": 0.35, "relative_humidity_2m": 85,
            "visibility": 8000, "precipitation": 0.5, "solar_elevation": -3.8,
        }),
        ("Clear cold morning", {
            "cloud_cover_low": 5, "cloud_cover_mid": 8, "cloud_cover_high": 2,
            "aerosol_optical_depth": 0.08, "relative_humidity_2m": 45,
            "visibility": 60000, "precipitation": 0.0, "solar_elevation": -5.1,
        }),
        ("Wildfire smoke event", {
            "cloud_cover_low": 20, "cloud_cover_mid": 30, "cloud_cover_high": 10,
            "aerosol_optical_depth": 0.72, "relative_humidity_2m": 25,
            "visibility": 5000, "precipitation": 0.0, "solar_elevation": -4.0,
        }),
    ]

    for label, data in scenarios:
        r = scorer.score(data)
        print(f"\n{'─'*54}")
        print(f"  {label}")
        print(f"  Score : {r.score:>3}  │  Grade: {r.grade}")
        print(f"  {r.description}")
        print(f"  Breakdown : {r.breakdown}")
        print(f"  Penalties : {r.penalties}")
        if r.flags:
            print(f"  Flags     : {', '.join(r.flags)}")
