"""
Tests for engine/ml/learner.py — Stage 3 feature expansion + walk-forward
validation. Uses isolated temp paths for MODEL_PATH/META_PATH so this never
touches the real model.joblib / meta.json the running desk relies on.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "delivery"))

from engine.ml import learner as ml  # noqa: E402


def _isolate(tmpdir: Path):
    ml.MODEL_PATH = tmpdir / "model.joblib"
    ml.META_PATH = tmpdir / "meta.json"


def _signal(pattern="tweezer_bottom", score=72.0, outcome="win", bar_time="2026-09-01T10:00:00+00:00",
            regime="with_long_trend", sentiment=None, pair="EURUSD=X"):
    return {
        "pair": pair, "final_score": score, "raw_score": score, "direction": 1 if score >= 0 else -1,
        "trend": 1, "sr_score": 0.7, "volatility": "normal", "rr": 1.5, "bar_time": bar_time,
        "pattern": pattern, "outcome": outcome, "fired": True,
        "sentiment_score": sentiment, "meta": {"regime": regime},
    }


def test_feature_vector_length_matches_keys_plus_pattern_id():
    vec = ml._feature_vector(_signal())
    assert len(vec) == len(ml.FEATURE_KEYS) + 1


def test_feature_vector_defaults_are_neutral_when_fields_missing():
    vec = ml._feature_vector({"final_score": 40.0})
    # sentiment_score index and all regime one-hots should be 0 when absent
    idx = {k: i for i, k in enumerate(ml.FEATURE_KEYS)}
    assert vec[idx["sentiment_score"]] == 0.0
    assert vec[idx["regime_with_trend"]] == 0.0
    assert vec[idx["regime_counter_early"]] == 0.0
    assert vec[idx["regime_counter_ignored"]] == 0.0
    assert vec[idx["regime_no_trend"]] == 0.0


def test_feature_vector_regime_one_hot_and_sentiment_scaling():
    vec = ml._feature_vector(_signal(regime="counter_trend_early", sentiment=50.0))
    idx = {k: i for i, k in enumerate(ml.FEATURE_KEYS)}
    assert vec[idx["regime_counter_early"]] == 1.0
    assert vec[idx["regime_with_trend"]] == 0.0
    assert vec[idx["sentiment_score"]] == 0.5  # scaled /100


def test_walk_forward_unavailable_below_min_samples():
    X = np.zeros((5, 3))
    y = np.array([1, 0, 1, 0, 1])
    result = ml._walk_forward(X, y)
    assert result["available"] is False
    assert "Need" in result["reason"]


def test_walk_forward_runs_on_separable_synthetic_data():
    # Build a chronological, perfectly separable toy dataset so walk-forward
    # has real signal to find (score >= 0.5 -> win) — sanity-checks the
    # plumbing (TimeSeriesSplit + GBClassifier + metrics), not model skill.
    rng = np.random.default_rng(7)
    n = 40
    scores = rng.uniform(0, 1, size=n)
    X = scores.reshape(-1, 1)
    y = (scores >= 0.5).astype(int)
    result = ml._walk_forward(X, y, n_splits=4)
    assert result["available"] is True
    assert result["n_tested"] > 0
    assert 0.0 <= result["accuracy"] <= 1.0
    assert result["auc"] is None or 0.0 <= result["auc"] <= 1.0


def test_stale_model_schema_is_discarded_on_load():
    tmpdir = Path(tempfile.mkdtemp())
    try:
        _isolate(tmpdir)
        import json
        ml.META_PATH.write_text(json.dumps({"feature_version": ml.FEATURE_VERSION - 1}))
        ml.MODEL_PATH.write_text("not actually a model")  # would crash joblib.load if loaded
        fresh = ml.MLLearner()
        assert fresh.model is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_fit_from_logs_reports_walk_forward_and_regime_hit_rates(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    try:
        _isolate(tmpdir)
        rows = []
        regimes = ["with_long_trend", "counter_trend_early", "no_long_trend"]
        for i in range(20):
            outcome = "win" if i % 3 != 0 else "loss"
            rows.append(_signal(
                score=60 + i, outcome=outcome, regime=regimes[i % 3],
                bar_time=f"2026-08-{(i % 28) + 1:02d}T10:00:00+00:00",
            ))

        import delivery.logger as logger_mod  # noqa: E402
        monkeypatch.setattr(logger_mod, "get_signals", lambda **kwargs: rows)
        # Isolate from the durable bootstrap pool so this test asserts on the
        # 20 rows it actually supplied. Without this, fit_from_logs mixes in up
        # to ML_BOOTSTRAP_FLOOR (default 40) bootstrap rows and n_samples is 60.
        monkeypatch.setenv("ML_BOOTSTRAP_FLOOR", "0")
        monkeypatch.setenv("ML_BOOTSTRAP_MAX_RATIO", "0")

        fresh = ml.MLLearner()
        result = fresh.fit_from_logs(min_samples=10)
        assert result["ok"] is True
        assert result["n_samples"] == 20
        assert result["n_live"] == 20
        assert result["n_bootstrap"] == 0
        assert "walk_forward" in result
        assert set(result["regime_hit_rates"].keys()) == set(regimes)
        assert fresh.model is not None
        # reload from disk and confirm it round-trips (feature_version matches)
        reloaded = ml.MLLearner()
        assert reloaded.model is not None
        assert reloaded.walk_forward.get("available") in (True, False)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_model_discarded_when_sklearn_version_differs():
    """A pickle from another sklearn build must not be loaded silently."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        _isolate(tmpdir)
        learner = ml.MLLearner()
        learner.model = object()
        learner.n_train = 12
        learner._save()
        assert ml.MODEL_PATH.exists()

        # Same version -> loads
        assert ml.MLLearner().model is not None

        # Different version -> discarded, with a reason the API can surface
        real = ml._SKLEARN_VERSION
        try:
            ml._SKLEARN_VERSION = "0.0.0-not-a-real-version"
            fresh = ml.MLLearner()
            assert fresh.model is None
            assert "discarded" in (fresh.model_discard_reason or "")
        finally:
            ml._SKLEARN_VERSION = real
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_learning_paused_is_never_restored_from_meta():
    """The removed auto-block must not come back via a stale meta.json."""
    tmpdir = Path(tempfile.mkdtemp())
    try:
        _isolate(tmpdir)
        ml.META_PATH.write_text(json.dumps({
            "feature_version": ml.FEATURE_VERSION,
            "sklearn_version": ml._SKLEARN_VERSION,
            "learning_paused": True,
            "n_train": 5,
        }))
        assert ml.MLLearner().learning_paused is False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    import inspect

    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        sig = inspect.signature(t)
        if "monkeypatch" in sig.parameters:
            # tiny stand-in so this file runs without pytest installed
            class _MonkeyPatch:
                def __init__(self):
                    self._undo = []

                def setattr(self, obj, name, value):
                    self._undo.append((obj, name, getattr(obj, name)))
                    setattr(obj, name, value)

                def undo(self):
                    for obj, name, old in self._undo:
                        setattr(obj, name, old)

            mp = _MonkeyPatch()
            try:
                t(mp)
            finally:
                mp.undo()
        else:
            t()
        passed += 1
        print(f"  ok: {t.__name__}")
    print(f"\n{passed}/{len(tests)} ML learner tests passed.")
