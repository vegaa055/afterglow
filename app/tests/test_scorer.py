"""
app/tests/test_scorer.py
------------------------
Unit tests for the AfterglowScore algorithm.

Pure math tests only — no network calls, no filesystem I/O,
no external API dependencies. Fast and CI-safe.

Run:
    pytest app/tests/ -v
"""

import pytest
from scorer import AfterglowScorer, ScoreResult, GRADES


# ── Fixtures & scenario helpers ───────────────────────────────

@pytest.fixture
def scorer():
    return AfterglowScorer()


def ideal() -> dict:
    """Near-optimal atmospheric conditions — should score Vivid or Epic."""
    return {
        "cloud_cover_low":        40.0,   # Gaussian peak
        "cloud_cover_mid":        45.0,   # Gaussian peak
        "cloud_cover_high":       25.0,   # Gaussian peak
        "aerosol_optical_depth":   0.30,  # linear boost zone
        "relative_humidity_2m":   40.0,   # below penalty threshold
        "visibility":          40_000.0,  # well above penalty threshold
        "precipitation":           0.0,
        "solar_elevation":         -4.5,  # optimal twilight window
    }


def overcast() -> dict:
    return {**ideal(), "cloud_cover_high": 95.0, "cloud_cover_low": 95.0}


def rainy() -> dict:
    return {**ideal(), "precipitation": 5.0}


def smoky() -> dict:
    return {**ideal(), "aerosol_optical_depth": 0.9, "visibility": 3_000.0}


def clear_desert() -> dict:
    """Cloudless sky — minimal scatter, low score despite clean air."""
    return {
        "cloud_cover_low":         2.0,
        "cloud_cover_mid":         3.0,
        "cloud_cover_high":        1.0,
        "aerosol_optical_depth":   0.05,
        "relative_humidity_2m":   20.0,
        "visibility":          60_000.0,
        "precipitation":           0.0,
        "solar_elevation":         -4.5,
    }


# ── ScoreResult shape ─────────────────────────────────────────

class TestScoreResultShape:
    def test_returns_score_result(self, scorer):
        assert isinstance(scorer.score(ideal()), ScoreResult)

    def test_score_in_range(self, scorer):
        for scenario in [ideal(), overcast(), rainy(), smoky(), clear_desert()]:
            r = scorer.score(scenario)
            assert 0 <= r.score <= 100, f"Score {r.score} out of range for {scenario}"

    def test_grade_is_valid(self, scorer):
        valid = {g[1] for g in GRADES}
        for scenario in [ideal(), overcast(), rainy()]:
            assert scorer.score(scenario).grade in valid

    def test_breakdown_keys(self, scorer):
        r = scorer.score(ideal())
        assert set(r.breakdown.keys()) == {"low_cloud", "mid_cloud", "high_cloud", "aod"}

    def test_penalty_keys(self, scorer):
        r = scorer.score(ideal())
        assert set(r.penalties.keys()) == {
            "humidity", "visibility", "precipitation", "overcast_ceiling"
        }

    def test_flags_is_list(self, scorer):
        assert isinstance(scorer.score(ideal()).flags, list)

    def test_score_is_integer(self, scorer):
        assert isinstance(scorer.score(ideal()).score, int)


# ── Score ordering ────────────────────────────────────────────

class TestScoreOrdering:
    def test_ideal_beats_overcast(self, scorer):
        assert scorer.score(ideal()).score > scorer.score(overcast()).score

    def test_ideal_beats_rainy(self, scorer):
        assert scorer.score(ideal()).score > scorer.score(rainy()).score

    def test_ideal_beats_smoky(self, scorer):
        assert scorer.score(ideal()).score > scorer.score(smoky()).score

    def test_ideal_beats_clear_desert(self, scorer):
        assert scorer.score(ideal()).score > scorer.score(clear_desert()).score

    def test_ideal_is_vivid_or_epic(self, scorer):
        r = scorer.score(ideal())
        assert r.score >= 61, f"Expected Vivid/Epic, got {r.grade} ({r.score})"

    def test_rainy_is_fair_or_worse(self, scorer):
        r = scorer.score(rainy())
        assert r.score <= 60, f"Rain should suppress score below Good, got {r.score}"


# ── Gaussian cloud curves ─────────────────────────────────────

class TestGaussianCurves:
    def test_low_cloud_peaks_near_40(self, scorer):
        scores = {pct: scorer.score({**ideal(), "cloud_cover_low": float(pct)}).score
                  for pct in [0, 20, 40, 60, 80, 100]}
        peak = max(scores, key=scores.get)
        assert 20 <= peak <= 60, f"Low cloud peak at {peak}%, expected ~40%"

    def test_mid_cloud_peaks_near_45(self, scorer):
        scores = {pct: scorer.score({**ideal(), "cloud_cover_mid": float(pct)}).score
                  for pct in [0, 20, 45, 70, 100]}
        peak = max(scores, key=scores.get)
        assert 20 <= peak <= 70, f"Mid cloud peak at {peak}%, expected ~45%"

    def test_zero_clouds_scores_low(self, scorer):
        # Zero out clouds AND aerosol — a genuinely clear, clean sky has
        # nothing to scatter light and should score Poor.
        # Keeping AOD=0.3 from ideal() would contribute ~17pts on its own.
        d = {
            **ideal(),
            "cloud_cover_low":       0.0,
            "cloud_cover_mid":       0.0,
            "cloud_cover_high":      0.0,
            "aerosol_optical_depth": 0.0,
        }
        assert scorer.score(d).score < 20, "Clear, clean sky should score Poor"

    def test_full_cloud_cover_scores_low(self, scorer):
        d = {**ideal(), "cloud_cover_low": 100.0, "cloud_cover_mid": 100.0}
        assert scorer.score(d).score < scorer.score(ideal()).score


# ── AOD scoring ───────────────────────────────────────────────

class TestAODScoring:
    def test_moderate_aod_scores_high(self, scorer):
        assert scorer._aod_score(0.30) >= 80

    def test_zero_aod_scores_zero(self, scorer):
        assert scorer._aod_score(0.0) == 0.0

    def test_heavy_aod_penalised(self, scorer):
        assert scorer._aod_score(0.80) < scorer._aod_score(0.35)

    def test_aod_never_exceeds_100(self, scorer):
        for aod in [0.0, 0.1, 0.35, 0.5, 0.8, 1.2]:
            assert scorer._aod_score(aod) <= 100.0

    def test_aod_plateau(self, scorer):
        # Scores between 0.35 and 0.50 should be roughly equal (plateau)
        s1 = scorer._aod_score(0.35)
        s2 = scorer._aod_score(0.45)
        assert abs(s1 - s2) < 5.0, "AOD plateau should be flat between 0.35–0.50"


# ── Penalty multipliers ───────────────────────────────────────

class TestPenalties:
    def test_humidity_no_penalty_below_threshold(self, scorer):
        assert scorer._humidity_penalty(60.0) == 1.0

    def test_humidity_penalty_above_threshold(self, scorer):
        p = scorer._humidity_penalty(90.0)
        assert 0.7 <= p < 1.0

    def test_visibility_no_penalty_above_threshold(self, scorer):
        assert scorer._visibility_penalty(20_000.0) == 1.0

    def test_visibility_penalty_low(self, scorer):
        assert scorer._visibility_penalty(1_000.0) < 1.0

    def test_precip_no_penalty_dry(self, scorer):
        assert scorer._precip_penalty(0.0) == 1.0

    def test_precip_penalty_rain(self, scorer):
        assert scorer._precip_penalty(2.0) < 1.0

    def test_overcast_no_penalty_below_threshold(self, scorer):
        assert scorer._high_cloud_ceiling_penalty(70.0) == 1.0

    def test_overcast_penalty_above_threshold(self, scorer):
        assert scorer._high_cloud_ceiling_penalty(95.0) < 1.0

    @pytest.mark.parametrize("rh", [0, 50, 80, 100])
    def test_humidity_penalty_in_range(self, scorer, rh):
        assert 0.0 <= scorer._humidity_penalty(float(rh)) <= 1.0

    @pytest.mark.parametrize("vis", [500, 5000, 20000, 60000])
    def test_visibility_penalty_in_range(self, scorer, vis):
        assert 0.0 <= scorer._visibility_penalty(float(vis)) <= 1.0

    @pytest.mark.parametrize("pr", [0, 0.5, 2, 10])
    def test_precip_penalty_in_range(self, scorer, pr):
        assert 0.0 <= scorer._precip_penalty(float(pr)) <= 1.0


# ── Solar elevation bonus ─────────────────────────────────────

class TestSolarElevationBonus:
    def test_optimal_window_gives_bonus(self, scorer):
        assert scorer._solar_elevation_multiplier(-4.5) > 1.0

    def test_above_horizon_no_bonus(self, scorer):
        assert scorer._solar_elevation_multiplier(10.0) == 1.0

    def test_deep_twilight_no_bonus(self, scorer):
        assert scorer._solar_elevation_multiplier(-15.0) == 1.0

    def test_none_no_bonus(self, scorer):
        assert scorer._solar_elevation_multiplier(None) == 1.0

    def test_bonus_increases_score(self, scorer):
        base = {k: v for k, v in ideal().items() if k != "solar_elevation"}
        without = scorer.score(base).score
        with_opt = scorer.score({**base, "solar_elevation": -4.5}).score
        assert with_opt >= without


# ── Edge cases ────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_dict_valid_score(self, scorer):
        r = scorer.score({})
        assert 0 <= r.score <= 100

    def test_partial_input_valid_score(self, scorer):
        r = scorer.score({"cloud_cover_low": 40.0})
        assert isinstance(r.score, int)

    def test_score_series_length(self, scorer):
        results = scorer.score_series([ideal(), rainy(), clear_desert()])
        assert len(results) == 3

    def test_score_series_all_results(self, scorer):
        results = scorer.score_series([ideal(), rainy()])
        assert all(isinstance(r, ScoreResult) for r in results)

    def test_custom_weights_change_scores(self):
        aod_heavy = AfterglowScorer(weights={
            "low_cloud": 0.10, "mid_cloud": 0.10,
            "high_cloud": 0.05, "aod": 0.75,
        })
        good_aod = {**ideal(), "aerosol_optical_depth": 0.30}
        no_aod   = {**ideal(), "aerosol_optical_depth": 0.0}
        assert aod_heavy.score(good_aod).score > aod_heavy.score(no_aod).score

    def test_invalid_weights_raise_value_error(self):
        with pytest.raises(ValueError):
            AfterglowScorer(weights={
                "low_cloud": 0.5, "mid_cloud": 0.5,
                "high_cloud": 0.5, "aod": 0.5,
            })

    def test_extreme_inputs_no_crash(self, scorer):
        extreme = {
            "cloud_cover_low": 100.0, "cloud_cover_mid": 100.0,
            "cloud_cover_high": 100.0, "aerosol_optical_depth": 2.0,
            "relative_humidity_2m": 100.0, "visibility": 0.0,
            "precipitation": 50.0, "solar_elevation": -90.0,
        }
        r = scorer.score(extreme)
        assert 0 <= r.score <= 100


# ── Flag generation ───────────────────────────────────────────

class TestFlags:
    def test_no_flags_ideal(self, scorer):
        assert scorer.score(ideal()).flags == []

    def test_precipitation_flag(self, scorer):
        assert "precipitation_present" in scorer.score(rainy()).flags

    def test_high_humidity_flag(self, scorer):
        d = {**ideal(), "relative_humidity_2m": 90.0}
        assert "high_humidity" in scorer.score(d).flags

    def test_low_visibility_flag(self, scorer):
        d = {**ideal(), "visibility": 2_000.0}
        assert "low_visibility" in scorer.score(d).flags

    def test_heavy_aerosol_flag(self, scorer):
        d = {**ideal(), "aerosol_optical_depth": 0.6}
        assert "heavy_aerosol" in scorer.score(d).flags

    def test_overcast_ceiling_flag(self, scorer):
        d = {**ideal(), "cloud_cover_high": 90.0}
        assert "overcast_ceiling" in scorer.score(d).flags

    def test_multiple_flags_accumulate(self, scorer):
        d = {**ideal(), "precipitation": 5.0, "relative_humidity_2m": 90.0}
        flags = scorer.score(d).flags
        assert "precipitation_present" in flags
        assert "high_humidity" in flags


# ── Grade thresholds ──────────────────────────────────────────

class TestGradeThresholds:
    @pytest.mark.parametrize("score,expected_grade", [
        (90, "Epic"),
        (81, "Epic"),
        (80, "Vivid"),
        (61, "Vivid"),
        (60, "Good"),
        (41, "Good"),
        (40, "Fair"),
        (21, "Fair"),
        (20, "Poor"),
        (0,  "Poor"),
    ])
    def test_grade_boundaries(self, scorer, score, expected_grade):
        _, grade, _ = next(g for g in GRADES if g[0] <= score)
        assert grade == expected_grade
