"""Ma-yi ML learning layer — outcome model + anomaly detection + health control.

Stage 3 additions:
- More features: sentiment score, day-of-week, and one-hot pattern "regime"
  (with_long_trend / counter_trend_early / counter_trend_ignored / no_long_trend).
- Walk-forward (chronological, expanding-window) validation alongside the
  naive train-accuracy metric, since train accuracy alone is misleading on
  small sample counts (it can look "trained" while really just memorizing).
- A feature-schema version so an older model.joblib (fit on the old, shorter
  feature vector) is discarded rather than crashing predict() on a shape
  mismatch — the API will just report "not trained" until the next retrain.

Stage 4 — ML Health Manager:
- Dedicated health state machine: insufficient_data | healthy | overfitting |
  underfitting | stale.
- Automatic response: regularise (shallower trees, stronger penalties, higher
  new-label threshold) when overfitting; force exploration / more aggressive
  retrain when underfitting. Learning and scanning are never halted — an
  overfit model needs *more* out-of-sample outcomes, and halting the pipeline
  is the one thing guaranteed to prevent them arriving.
- Health status + recommended action exposed via status() for the UI and API.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_PATH = ROOT / "engine" / "ml" / "model.joblib"
META_PATH = ROOT / "engine" / "ml" / "meta.json"

# Bump this whenever _feature_vector's shape/order changes. A model trained
# under an older version is incompatible and gets discarded on load.
FEATURE_VERSION = 2

try:
    import sklearn

    _SKLEARN_VERSION = str(sklearn.__version__)
except Exception:  # pragma: no cover - sklearn is a hard dependency in practice
    _SKLEARN_VERSION = "unknown"

FEATURE_KEYS = [
    "abs_score", "raw_score", "direction", "trend", "sr_score",
    "vol_spike", "vol_quiet", "vol_normal", "rr", "hour", "day_of_week",
    "is_major", "sentiment_score",
    "regime_with_trend", "regime_counter_early", "regime_counter_ignored", "regime_no_trend",
]
PATTERN_MAP = {
    "tweezer_bottom": 1, "tweezer_top": 2, "engulfing_bull": 3,
    "engulfing_bear": 4, "pin_bar_bull": 5, "pin_bar_bear": 6,
    "inside_bar": 7, "unknown": 0,
}
REGIMES = ("with_long_trend", "counter_trend_early", "counter_trend_ignored", "no_long_trend")

MIN_WALK_FORWARD_SAMPLES = 10

# ── Health thresholds (env-overridable) ──────────────────────────────────────
def _overfit_gap() -> float:
    """Train accuracy − walk-forward accuracy above this → overfitting."""
    try:
        return float(os.environ.get("ML_OVERFIT_GAP", "0.22"))
    except ValueError:
        return 0.22

def _underfit_acc() -> float:
    """Both train and walk-forward below this → underfitting."""
    try:
        return float(os.environ.get("ML_UNDERFIT_ACC", "0.52"))
    except ValueError:
        return 0.52

def _stale_hours() -> float:
    try:
        return float(os.environ.get("ML_STALE_HOURS", "48"))
    except ValueError:
        return 48.0

def _min_diversity_patterns() -> int:
    """Need at least this many distinct patterns with ≥2 samples for healthy diversity."""
    try:
        return max(2, int(os.environ.get("ML_MIN_DIVERSITY_PATTERNS", "3")))
    except ValueError:
        return 3


def _bootstrap_max_ratio() -> float:
    """Max bootstrap rows relative to live closed labels (default 1.0 = 1:1).

    Keeps historical sample data from drowning out fresh live outcomes so the
    model tracks the current regime without discarding bootstrap entirely when
    live data is still thin.
    """
    try:
        return max(0.0, float(os.environ.get("ML_BOOTSTRAP_MAX_RATIO", "1.0")))
    except ValueError:
        return 1.0


def _bootstrap_floor() -> int:
    """Minimum bootstrap rows allowed when live labels are scarce (cold start)."""
    try:
        return max(0, int(os.environ.get("ML_BOOTSTRAP_FLOOR", "40")))
    except ValueError:
        return 40


def _live_label_weight() -> float:
    """Relative weight of live closed labels vs bootstrap (default 3.0).

    Higher → production model tracks recent outcomes more closely, which
    usually narrows the train vs walk-forward gap when regimes shift.
    """
    try:
        return max(1.0, float(os.environ.get("ML_LIVE_LABEL_WEIGHT", "3.0")))
    except ValueError:
        return 3.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _feature_vector(sig: dict) -> list[float]:
    score = _safe_float(sig.get("final_score"))
    raw = _safe_float(sig.get("raw_score"), score)
    direction = int(sig.get("direction") or 0)
    trend = int(sig.get("trend") or 0)
    sr = _safe_float(sig.get("sr_score"), 0.5)
    vol = str(sig.get("volatility") or "normal").lower()
    rr = _safe_float(sig.get("rr"), 1.5)

    hour = 12
    dow = 0.0
    bt = str(sig.get("bar_time") or "")
    try:
        date_part, time_part = (bt.split("T", 1) if "T" in bt else bt.split(" ", 1))
        hour = int(time_part[:2])
        dow = float(datetime.fromisoformat(date_part[:10]).weekday())
    except Exception:
        pass

    pair = str(sig.get("pair") or "")
    is_major = 1.0 if pair in ("EURUSD=X", "GBPUSD=X", "USDJPY=X") else 0.0
    pattern = str(sig.get("pattern") or "unknown").lower()
    pat_id = float(PATTERN_MAP.get(pattern, 0))

    # Sentiment is only ever populated on the live bar it was blended into
    # (see delivery/logger.py) — everywhere else this is 0 (neutral), which
    # is the right default since "no sentiment fetched" isn't a signal.
    sentiment = _safe_float(sig.get("sentiment_score"), 0.0) / 100.0

    meta = sig.get("meta") or {}
    regime = str(meta.get("regime") or "").lower()

    return [
        abs(score), raw, float(direction), float(trend), sr,
        1.0 if vol == "spike" else 0.0,
        1.0 if vol == "quiet" else 0.0,
        1.0 if vol == "normal" else 0.0,
        rr, float(hour), dow, is_major, sentiment,
        1.0 if regime == "with_long_trend" else 0.0,
        1.0 if regime == "counter_trend_early" else 0.0,
        1.0 if regime == "counter_trend_ignored" else 0.0,
        1.0 if regime == "no_long_trend" else 0.0,
        pat_id,
    ]


def _walk_forward(X: np.ndarray, y: np.ndarray, n_splits: int = 4) -> dict[str, Any]:
    """Chronological, expanding-window out-of-sample validation.

    X/y MUST already be sorted oldest-first — each fold trains only on data
    that would actually have been available at the time, unlike the naive
    train_accuracy the model reports elsewhere (which just memorizes).
    """
    n = len(y)
    if n < MIN_WALK_FORWARD_SAMPLES:
        return {
            "available": False,
            "reason": f"Need \u2265{MIN_WALK_FORWARD_SAMPLES} chronological closed trades for walk-forward, have {n}",
        }
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import brier_score_loss, roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    n_splits = max(2, min(n_splits, n // 5))
    tscv = TimeSeriesSplit(n_splits=n_splits)
    preds, actuals, probs, fold_reports = [], [], [], []

    for fold_i, (train_idx, test_idx) in enumerate(tscv.split(X), start=1):
        y_train = y[train_idx]
        if len(set(y_train.tolist())) < 2:
            # Can't fit a classifier on a single-class training slice yet —
            # skip this fold rather than fabricating a result.
            continue
        # Use a fixed moderate regularisation for walk-forward so the
        # diagnostic itself is not contaminated by adaptive hyper-modes.
        clf = GradientBoostingClassifier(
            n_estimators=80,
            max_depth=2,
            learning_rate=0.05,
            subsample=0.70,
            min_samples_leaf=5,
            min_samples_split=10,
            random_state=42,
        )
        clf.fit(X[train_idx], y_train)
        proba = clf.predict_proba(X[test_idx])
        classes = list(clf.classes_)
        idx1 = classes.index(1) if 1 in classes else None
        p_win = proba[:, idx1] if idx1 is not None else np.zeros(len(test_idx))
        pred = (p_win >= 0.5).astype(int)
        y_test = y[test_idx]
        acc = float((pred == y_test).mean())
        fold_reports.append({
            "fold": fold_i, "n_train": int(len(train_idx)), "n_test": int(len(test_idx)),
            "accuracy": round(acc, 3),
        })
        preds.extend(pred.tolist())
        actuals.extend(y_test.tolist())
        probs.extend(p_win.tolist())

    if not preds:
        return {"available": False, "reason": "Not enough class diversity across folds yet — try again after a few more closed trades"}

    preds_arr, actuals_arr, probs_arr = np.array(preds), np.array(actuals), np.array(probs)
    accuracy = float((preds_arr == actuals_arr).mean())
    brier = float(brier_score_loss(actuals_arr, probs_arr))
    auc = None
    if len(set(actuals_arr.tolist())) > 1:
        try:
            auc = round(float(roc_auc_score(actuals_arr, probs_arr)), 3)
        except Exception:
            auc = None
    return {
        "available": True,
        "n_folds": len(fold_reports),
        "n_tested": int(len(preds_arr)),
        "accuracy": round(accuracy, 3),
        "brier_score": round(brier, 3),
        "auc": auc,
        "folds": fold_reports,
    }


class MLLearner:
    def __init__(self):
        self._lock = threading.Lock()
        self.model = None
        self.n_train = 0
        self.n_pos = 0
        self.n_neg = 0
        self.last_fit: Optional[str] = None
        self.train_accuracy: Optional[float] = None
        self.feature_importance: dict[str, float] = {}
        self._pattern_hit_rates: dict[str, dict] = {}
        self._regime_hit_rates: dict[str, dict] = {}
        self.walk_forward: dict[str, Any] = {}
        # Health manager state
        self.health_state: str = "insufficient_data"
        self.health_detail: dict[str, Any] = {}
        self.learning_paused: bool = False
        self.explore_mode: bool = False
        self._hyper_mode: str = "normal"  # normal | regularized | aggressive
        # Set by _load() when a persisted model had to be thrown away.
        self.model_discard_reason: Optional[str] = None
        self._load()

    def _load(self) -> None:
        feature_version = None
        saved_sklearn = None
        if META_PATH.exists():
            try:
                meta = json.loads(META_PATH.read_text())
                feature_version = meta.get("feature_version")
                saved_sklearn = meta.get("sklearn_version")
                self.n_train = meta.get("n_train", 0)
                self.n_pos = meta.get("n_pos", 0)
                self.n_neg = meta.get("n_neg", 0)
                self.last_fit = meta.get("last_fit")
                self.train_accuracy = meta.get("train_accuracy")
                self.feature_importance = meta.get("feature_importance", {})
                self._pattern_hit_rates = meta.get("pattern_hit_rates", {})
                self._regime_hit_rates = meta.get("regime_hit_rates", {})
                self.walk_forward = meta.get("walk_forward", {})
                # learning_paused is deliberately NOT restored. It is a dead
                # field kept only for API compatibility; rehydrating a stale
                # `true` from an old meta.json would resurrect the removed
                # auto-block on the next boot.
                self.learning_paused = False
                self.explore_mode = bool(meta.get("explore_mode", False))
                self._hyper_mode = str(meta.get("hyper_mode") or "normal")
                self.health_state = str(meta.get("health_state") or "insufficient_data")
                self.health_detail = meta.get("health_detail") or {}
            except Exception:
                pass
        # A pickled estimator is only valid under the scikit-learn version that
        # produced it. Loading across versions raises InconsistentVersionWarning
        # and, per sklearn's own docs, can yield invalid results — a warning is
        # far too quiet for a model that decides trades. Treat a version
        # mismatch exactly like a feature-schema mismatch: drop the model and
        # let the next retrain rebuild it.
        # A meta.json with no recorded version predates this check, so the
        # pickle's provenance is unverifiable — discard rather than assume.
        # Costs one retrain on first boot after upgrading; the alternative is
        # trusting a pickle that may have been written by any version.
        version_ok = saved_sklearn == _SKLEARN_VERSION
        if MODEL_PATH.exists() and feature_version == FEATURE_VERSION and version_ok:
            try:
                self.model = joblib.load(MODEL_PATH)
            except Exception:
                self.model = None
        else:
            self.model = None
            if MODEL_PATH.exists() and not version_ok:
                self.model_discard_reason = (
                    f"model.joblib was fit with scikit-learn "
                    f"{saved_sklearn or 'an unrecorded version'}, running "
                    f"{_SKLEARN_VERSION} — discarded, will retrain."
                )
        # Stale model from an older feature schema or sklearn build — self.model
        # stays None so predict() falls back cleanly until the next retrain.
        self._n_at_last_fit = self.n_train
        self._last_auto_fit = None
        self._last_auto_fit_ts = None
        self._last_auto_result = None
        # Re-evaluate health on load so UI is correct even before next fit
        try:
            self._evaluate_health()
        except Exception:
            pass

    def _save(self) -> None:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self.model is not None:
            joblib.dump(self.model, MODEL_PATH)
        META_PATH.write_text(json.dumps({
            "feature_version": FEATURE_VERSION,
            "sklearn_version": _SKLEARN_VERSION,
            "n_train": self.n_train,
            "n_pos": self.n_pos,
            "n_neg": self.n_neg,
            "last_fit": self.last_fit,
            "train_accuracy": self.train_accuracy,
            "feature_importance": self.feature_importance,
            "pattern_hit_rates": self._pattern_hit_rates,
            "regime_hit_rates": self._regime_hit_rates,
            "walk_forward": self.walk_forward,
            "health_state": self.health_state,
            "health_detail": self.health_detail,
            "learning_paused": self.learning_paused,
            "explore_mode": self.explore_mode,
            "hyper_mode": self._hyper_mode,
        }, indent=2))

    def _get_hyperparams(self, force_mode: str | None = None) -> dict[str, Any]:
        """Adaptive hyperparameters driven by current health / hyper_mode.

        Regularized mode is tuned to keep train accuracy closer to walk-forward
        (shallower trees, higher min_samples_leaf, early stopping).
        """
        mode = force_mode or self._hyper_mode
        # Shared early-stop knobs (sklearn ignores these if n is tiny)
        early = dict(
            validation_fraction=0.15,
            n_iter_no_change=12,
            tol=1e-4,
        )
        if mode == "regularized":
            return dict(
                n_estimators=80,
                max_depth=2,
                learning_rate=0.04,
                subsample=0.65,
                min_samples_leaf=6,
                min_samples_split=12,
                random_state=42,
                **early,
            )
        if mode == "aggressive":
            return dict(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.08,
                subsample=0.85,
                min_samples_leaf=3,
                min_samples_split=6,
                random_state=42,
                **early,
            )
        # normal — slightly conservative so train ≈ walk-forward by default
        return dict(
            n_estimators=80,
            max_depth=2,
            learning_rate=0.06,
            subsample=0.75,
            min_samples_leaf=4,
            min_samples_split=8,
            random_state=42,
            **early,
        )

    def _evaluate_health(self) -> dict[str, Any]:
        """Compute health state and apply control actions (pause / explore / hyper).

        Called after every successful fit and on load so the UI always sees
        an up-to-date diagnosis.
        """
        labeled = self.count_labeled()
        train_acc = self.train_accuracy
        wf = self.walk_forward or {}
        wf_acc = wf.get("accuracy") if wf.get("available") else None
        gap = None
        if train_acc is not None and wf_acc is not None:
            gap = round(float(train_acc) - float(wf_acc), 4)

        # Diversity: how many distinct patterns have at least 2 closed trades
        diverse_patterns = sum(
            1 for st in (self._pattern_hit_rates or {}).values() if st.get("n", 0) >= 2
        )

        # Staleness
        hours_since_fit = None
        if self.last_fit:
            try:
                last = datetime.fromisoformat(self.last_fit.replace("Z", "+00:00"))
                hours_since_fit = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
            except Exception:
                hours_since_fit = None

        reasons: list[str] = []
        state = "healthy"
        action = "continue"
        recommendation = "Model looks balanced — keep normal auto-retrain cadence."

        if labeled < _min_samples() or self.model is None:
            state = "insufficient_data"
            action = "collect"
            recommendation = (
                f"Need ≥{_min_samples()} closed trades before reliable learning "
                f"(have {labeled}). Keep labeling outcomes."
            )
            reasons.append("not_enough_labeled")
        elif hours_since_fit is not None and hours_since_fit >= _stale_hours():
            state = "stale"
            action = "force_retrain"
            recommendation = (
                f"Last fit was {hours_since_fit:.0f}h ago. Force a retrain when "
                "new labels appear, or run Scan to generate more closed outcomes."
            )
            reasons.append("stale_model")
        elif gap is not None and gap >= _overfit_gap():
            state = "overfitting"
            action = "regularize"
            recommendation = (
                f"Train accuracy ({train_acc:.0%}) is {gap:.0%} higher than "
                f"walk-forward ({wf_acc:.0%}). Switching to stronger regularisation "
                "and requiring more new labels per refit. Learning keeps running — "
                "fresh out-of-sample outcomes are what closes the gap."
            )
            reasons.append(f"overfit_gap={gap}")
            if diverse_patterns < _min_diversity_patterns():
                reasons.append(f"low_pattern_diversity={diverse_patterns}")
                recommendation += " Pattern diversity is also low — prefer under-represented setups."
        elif (
            train_acc is not None
            and train_acc < _underfit_acc()
            and (wf_acc is None or wf_acc < _underfit_acc())
        ):
            state = "underfitting"
            action = "explore_and_aggressive"
            recommendation = (
                f"Both train ({train_acc:.0%}) and out-of-sample accuracy are weak. "
                "Switching to more aggressive learning and explore mode so the model "
                "sees more varied patterns."
            )
            reasons.append("low_accuracy")
        else:
            # Healthy — clear any previous pause once gap has closed
            if self.learning_paused and (gap is None or gap < _overfit_gap() * 0.7):
                reasons.append("recovered_from_overfit")

        # Apply control actions
        prev_state = self.health_state
        self.health_state = state

        # learning_paused is retained for API compatibility but is never set by
        # the health manager any more. Pausing auto-retrain on an overfit signal
        # was self-defeating: the pause also stopped the model from absorbing
        # the new out-of-sample labels that would have closed the gap. Overfit
        # is now handled purely by regularising hyperparameters and demanding
        # more new labels per refit (see auto_retrain).
        if state == "overfitting":
            self.learning_paused = False
            self.explore_mode = True
            self._hyper_mode = "regularized"
        elif state == "underfitting":
            self.learning_paused = False
            self.explore_mode = True
            self._hyper_mode = "aggressive"
        elif state == "healthy":
            self.learning_paused = False
            self.explore_mode = False
            self._hyper_mode = "normal"
        elif state == "stale":
            # Allow retrain so we can refresh, but stay cautious
            self.learning_paused = False
            self.explore_mode = False
            self._hyper_mode = "normal"
        else:  # insufficient_data
            self.learning_paused = False
            self.explore_mode = False
            self._hyper_mode = "normal"

        detail = {
            "state": state,
            "previous_state": prev_state,
            "action": action,
            "recommendation": recommendation,
            "reasons": reasons,
            "metrics": {
                "labeled": labeled,
                "train_accuracy": train_acc,
                "walk_forward_accuracy": wf_acc,
                "gap": gap,
                "diverse_patterns": diverse_patterns,
                "hours_since_fit": round(hours_since_fit, 1) if hours_since_fit is not None else None,
                "n_wins": self.n_pos,
                "n_losses": self.n_neg,
            },
            "controls": {
                "learning_paused": self.learning_paused,
                "explore_mode": self.explore_mode,
                "hyper_mode": self._hyper_mode,
            },
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.health_detail = detail
        return detail

    def fit_from_logs(self, min_samples: int = 12) -> dict[str, Any]:
        from delivery.logger import get_signals
        live_rows = get_signals(fired_only=True, limit=50_000)
        # Durable bootstrap pool — capped so live/new labels dominate when present.
        bootstrap_rows: list[dict] = []
        bootstrap_path = ROOT / "engine" / "ml" / "bootstrap_labels.jsonl"
        if bootstrap_path.exists():
            try:
                with open(bootstrap_path, "r", encoding="utf-8") as bf:
                    for line in bf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            bootstrap_rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass

        def _closed(rows: list) -> list:
            out = []
            for r in rows:
                outcome = str(r.get("outcome") or "pending").lower()
                if outcome in ("win", "loss"):
                    out.append(r)
            return out

        live_closed = _closed(live_rows)
        boot_closed = _closed(bootstrap_rows)
        n_live = len(live_closed)

        # Cap bootstrap: max(floor, ratio * live). When live is empty use floor only.
        ratio = _bootstrap_max_ratio()
        floor = _bootstrap_floor()
        if n_live <= 0:
            boot_cap = floor
        else:
            boot_cap = max(floor, int(n_live * ratio))
        if boot_cap <= 0:
            # A cap of zero means "train on live labels only". The previous
            # guard (`len(boot_closed) > boot_cap > 0`) was false in this case,
            # so setting ML_BOOTSTRAP_MAX_RATIO=0 / ML_BOOTSTRAP_FLOOR=0 did the
            # opposite of what it says: no cap applied, whole pool loaded.
            boot_closed = []
        elif len(boot_closed) > boot_cap:
            # Stratified subsample: keep pattern diversity, prefer recent-looking rows
            rng = np.random.RandomState(42)
            by_pat: dict[str, list] = {}
            for r in boot_closed:
                by_pat.setdefault(str(r.get("pattern") or "unknown"), []).append(r)
            picked: list = []
            pats = list(by_pat.keys())
            rng.shuffle(pats)
            # Round-robin by pattern until cap
            while len(picked) < boot_cap and by_pat:
                progress = False
                for p in list(pats):
                    bucket = by_pat.get(p) or []
                    if not bucket:
                        by_pat.pop(p, None)
                        continue
                    idx = rng.randint(0, len(bucket))
                    picked.append(bucket.pop(idx))
                    progress = True
                    if len(picked) >= boot_cap:
                        break
                if not progress:
                    break
            boot_closed = picked

        # Tag source so live labels can be up-weighted vs bootstrap
        live_closed = [dict(r, _src="live") for r in live_closed]
        boot_closed = [dict(r, _src="bootstrap") for r in boot_closed]
        rows = live_closed + boot_closed
        X, y, times, sources = [], [], [], []
        pattern_stats: dict[str, list[int]] = {}
        regime_stats: dict[str, list[int]] = {}
        for r in rows:
            outcome = str(r.get("outcome") or "pending").lower()
            if outcome not in ("win", "loss"):
                continue
            X.append(_feature_vector(r))
            label = 1 if outcome == "win" else 0
            y.append(label)
            times.append(str(r.get("bar_time") or r.get("ts") or ""))
            sources.append(str(r.get("_src") or "bootstrap"))
            pat = str(r.get("pattern") or "unknown")
            pattern_stats.setdefault(pat, []).append(label)
            regime = str((r.get("meta") or {}).get("regime") or "unknown")
            regime_stats.setdefault(regime, []).append(label)
        if len(X) < min_samples:
            return {
                "ok": False,
                "reason": f"Need >={min_samples} closed trades, have {len(X)} (live={n_live}, bootstrap={len(boot_closed)})",
                "n_samples": len(X),
                "n_live": n_live,
                "n_bootstrap": len(boot_closed),
            }

        X_arr = np.array(X, dtype=float)
        y_arr = np.array(y, dtype=int)
        sources_arr = np.array(sources)

        # Sample weights: live labels count more; mild recency boost within pool
        live_w = _live_label_weight()
        weights = np.where(sources_arr == "live", live_w, 1.0).astype(float)
        try:
            # Rank times oldest→newest; scale 0.75..1.25
            order_t = np.argsort(times)
            ranks = np.empty(len(times), dtype=float)
            ranks[order_t] = np.linspace(0.75, 1.25, len(times))
            weights *= ranks
        except Exception:
            pass

        # Walk-forward needs chronological order; the final production model
        # is fit on everything (order doesn't matter for a non-sequential
        # classifier), so we sort a *copy* just for validation.
        order = np.argsort(times)
        wf = _walk_forward(X_arr[order], y_arr[order])

        from sklearn.ensemble import GradientBoostingClassifier

        def _fit_clf(mode: str | None = None):
            hp = self._get_hyperparams(force_mode=mode)
            # Early stopping needs enough rows; drop it on tiny pools
            if len(y_arr) < 30:
                hp.pop("validation_fraction", None)
                hp.pop("n_iter_no_change", None)
                hp.pop("tol", None)
            clf_local = GradientBoostingClassifier(**hp)
            try:
                clf_local.fit(X_arr, y_arr, sample_weight=weights)
            except TypeError:
                clf_local.fit(X_arr, y_arr)
            return clf_local, hp

        # First fit with current mode
        clf, used_hp = _fit_clf()
        train_acc = float(clf.score(X_arr, y_arr, sample_weight=weights))
        wf_acc = (wf or {}).get("accuracy") if (wf or {}).get("available") else None
        gap = (train_acc - float(wf_acc)) if wf_acc is not None else None

        # If train still runs ahead of walk-forward, refit in regularized mode
        # so the deployed model matches OOS behaviour more closely.
        try:
            gap_lim = float(os.environ.get("ML_OVERFIT_GAP", "0.22"))
        except ValueError:
            gap_lim = 0.22
        refit_note = None
        if gap is not None and gap >= gap_lim * 0.75 and self._hyper_mode != "regularized":
            clf, used_hp = _fit_clf(mode="regularized")
            self._hyper_mode = "regularized"
            train_acc = float(clf.score(X_arr, y_arr, sample_weight=weights))
            refit_note = f"refit_regularized gap={gap:.3f}"
        with self._lock:
            self.model = clf
            self.n_train = len(y)
            self.n_pos = int(y_arr.sum())
            self.n_neg = int(len(y_arr) - y_arr.sum())
            self.last_fit = datetime.now(timezone.utc).isoformat()
            self.train_accuracy = round(float(train_acc), 3)
            self.walk_forward = wf
            names = FEATURE_KEYS + ["pattern_id"]
            imp = clf.feature_importances_
            self.feature_importance = {
                names[i]: round(float(imp[i]), 4) for i in range(min(len(names), len(imp)))
            }
            self._pattern_hit_rates = {}
            for pat, labels in pattern_stats.items():
                n = len(labels)
                wins = sum(labels)
                self._pattern_hit_rates[pat] = {
                    "n": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else 0.0
                }
            self._regime_hit_rates = {}
            for regime, labels in regime_stats.items():
                n = len(labels)
                wins = sum(labels)
                self._regime_hit_rates[regime] = {
                    "n": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else 0.0
                }
            # Re-evaluate health after every successful fit
            health = self._evaluate_health()
            self._save()
        return {
            "ok": True,
            "n_samples": len(y),
            "n_live": int(n_live),
            "n_bootstrap": int(len(boot_closed)),
            "n_wins": self.n_pos,
            "n_losses": self.n_neg,
            "train_accuracy": self.train_accuracy,
            "walk_forward": self.walk_forward,
            "feature_importance": self.feature_importance,
            "pattern_hit_rates": self._pattern_hit_rates,
            "regime_hit_rates": self._regime_hit_rates,
            "health": health,
            "hyper_mode": self._hyper_mode,
            "refit_note": refit_note,
            "live_label_weight": live_w,
        }

    def predict(self, sig: dict) -> dict[str, Any]:
        base = _safe_float(sig.get("final_score"))
        abs_base = abs(base)
        direction = 1 if base >= 0 else -1
        result = {
            "prob_win": None,
            "adjusted_score": base,
            "anomaly": False,
            "note": "ML model not trained yet",
        }
        if self.model is None:
            return result
        try:
            vec = np.array([_feature_vector(sig)], dtype=float)
            proba = self.model.predict_proba(vec)[0]
            classes = list(self.model.classes_)
            idx = classes.index(1) if 1 in classes else -1
            prob_win = float(proba[idx])
            mag = 20 + prob_win * 75
            anomaly = False
            note = "ML-adjusted"
            if abs_base < 50 and prob_win >= 0.65:
                anomaly = True
                note = "Low engine confidence but historically high hit-rate setup"
                mag = max(mag, 55)
            elif abs_base >= 70 and prob_win < 0.40:
                anomaly = True
                note = "High engine score but historically poor outcomes — caution"
                mag = min(mag, 45)
            # Shrink ML influence when walk-forward lags train (overfit risk)
            # so adjusted scores stay closer to what OOS accuracy supports.
            wf = self.walk_forward or {}
            wf_acc = wf.get("accuracy") if wf.get("available") else None
            train_acc = self.train_accuracy
            reliability = 1.0
            if wf_acc is not None:
                # Map WF accuracy 0.45→0.70 into reliability 0.35→1.0
                reliability = max(0.35, min(1.0, (float(wf_acc) - 0.45) / 0.25))
                if train_acc is not None:
                    gap = float(train_acc) - float(wf_acc)
                    if gap > 0.12:
                        reliability *= max(0.4, 1.0 - (gap - 0.12) * 1.5)
            # Blend ML magnitude with |base| using reliability
            blended_mag = reliability * mag + (1.0 - reliability) * abs_base
            if reliability < 0.85 and not anomaly:
                note = f"ML-adjusted (WF-calibrated {reliability:.0%})"
            result = {
                "prob_win": round(prob_win, 3),
                "adjusted_score": round(direction * blended_mag, 1),
                "anomaly": anomaly,
                "note": note,
                "wf_reliability": round(reliability, 3),
            }
        except Exception as e:
            result["note"] = f"predict error: {e}"
        return result

    def insights(self) -> list[dict]:
        insights = []
        for pat, st in sorted(
            self._pattern_hit_rates.items(),
            key=lambda x: x[1].get("win_rate", 0),
            reverse=True,
        ):
            if st.get("n", 0) >= 5:
                insights.append({
                    "type": "pattern_edge",
                    "pattern": pat,
                    "win_rate": st["win_rate"],
                    "n": st["n"],
                    "message": f"{pat}: {st['win_rate']}% win rate over {st['n']} trades",
                })
        for regime, st in sorted(
            self._regime_hit_rates.items(),
            key=lambda x: x[1].get("win_rate", 0),
            reverse=True,
        ):
            if st.get("n", 0) >= 5:
                insights.append({
                    "type": "regime_edge",
                    "regime": regime,
                    "win_rate": st["win_rate"],
                    "n": st["n"],
                    "message": f"{regime.replace('_', ' ')}: {st['win_rate']}% win rate over {st['n']} trades",
                })
        if self.feature_importance:
            top = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)[:3]
            insights.append({
                "type": "feature_drivers",
                "drivers": top,
                "message": "Top features: " + ", ".join(f"{k} ({v})" for k, v in top),
            })
        if self.walk_forward.get("available"):
            wf = self.walk_forward
            msg = (
                f"Walk-forward (out-of-sample) accuracy: {wf['accuracy'] * 100:.0f}% "
                f"over {wf['n_tested']} held-out trades across {wf['n_folds']} folds."
            )
            if self.train_accuracy is not None and (self.train_accuracy - wf["accuracy"]) > 0.25:
                msg += (
                    f" Training accuracy ({self.train_accuracy * 100:.0f}%) is notably higher — "
                    "likely overfitting on this small sample rather than a real edge."
                )
            insights.append({
                "type": "walk_forward",
                "accuracy": wf["accuracy"],
                "n_tested": wf["n_tested"],
                "auc": wf.get("auc"),
                "message": msg,
            })
        elif self.walk_forward:
            insights.append({
                "type": "walk_forward",
                "available": False,
                "message": self.walk_forward.get("reason", "Walk-forward validation not available yet."),
            })
        return insights

    def status(self) -> dict:
        labeled = self.count_labeled()
        # Keep health current even if no fit has run this process
        try:
            self._evaluate_health()
        except Exception:
            pass
        return {
            "available": True,
            "trained": self.model is not None,
            "sklearn_version": _SKLEARN_VERSION,
            "model_discard_reason": self.model_discard_reason,
            "n_train": self.n_train,
            "n_wins": self.n_pos,
            "n_losses": self.n_neg,
            "last_fit": self.last_fit,
            "train_accuracy": self.train_accuracy,
            "walk_forward": self.walk_forward,
            "feature_importance": self.feature_importance,
            "pattern_hit_rates": self._pattern_hit_rates,
            "regime_hit_rates": self._regime_hit_rates,
            "health": {
                "state": self.health_state,
                "detail": self.health_detail,
                "learning_paused": self.learning_paused,
                "explore_mode": self.explore_mode,
                "hyper_mode": self._hyper_mode,
            },
            "auto_retrain": {
                "enabled": _auto_retrain_enabled(),
                "paused_by_health": self.learning_paused,
                "min_samples": _min_samples(),
                "min_new_labels": _min_new_labels(),
                "cooldown_sec": _cooldown_sec(),
                "labeled_closed": labeled,
                "n_train_at_last_fit": getattr(self, "_n_at_last_fit", self.n_train),
                "last_auto_fit": getattr(self, "_last_auto_fit", None),
                "last_auto_result": getattr(self, "_last_auto_result", None),
            },
        }

    def count_labeled(self) -> int:
        """Effective training pool size after bootstrap cap (matches fit_from_logs)."""
        try:
            from delivery.logger import get_signals
            live = sum(
                1
                for r in get_signals(fired_only=True, limit=50_000)
                if str(r.get("outcome") or "").lower() in ("win", "loss")
            )
            boot = 0
            bootstrap_path = ROOT / "engine" / "ml" / "bootstrap_labels.jsonl"
            if bootstrap_path.exists():
                with open(bootstrap_path, "r", encoding="utf-8") as bf:
                    for line in bf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            r = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if str(r.get("outcome") or "").lower() in ("win", "loss"):
                            boot += 1
            ratio = _bootstrap_max_ratio()
            floor = _bootstrap_floor()
            if live <= 0:
                boot_cap = min(boot, floor)
            else:
                boot_cap = min(boot, max(floor, int(live * ratio)))
            return live + boot_cap
        except Exception:
            return 0

    def maybe_retrain(
        self,
        reason: str = "manual",
        force: bool = False,
        min_samples: Optional[int] = None,
        min_new_labels: Optional[int] = None,
    ) -> dict[str, Any]:
        """Retrain only when there is enough new labeled data (or force=True).

        Used after outcome labels, after Scan (replay simulates outcomes),
        and by the background auto-retrain loop.

        Never gated on health state. When the model is overfitting the health
        manager regularizes hyperparameters and raises the new-label threshold;
        it does not stop retraining.
        """
        if not force and not _auto_retrain_enabled() and reason != "manual":
            return {
                "ok": False,
                "skipped": True,
                "reason": "auto-retrain disabled (ML_AUTO_RETRAIN=false)",
                "trigger": reason,
            }

        # No health-based pause. An overfit model still retrains — it just does
        # so with regularized hyperparameters and a higher new-label threshold
        # (set below). Blocking the refit only froze the model in its overfit
        # state, since nothing could ever update it.

        ms = min_samples if min_samples is not None else _min_samples()
        # When underfitting / explore, accept fewer new labels so we can adapt faster
        if self.explore_mode and self.health_state == "underfitting":
            mn = max(1, (min_new_labels if min_new_labels is not None else _min_new_labels()) // 2)
        else:
            mn = min_new_labels if min_new_labels is not None else _min_new_labels()
            # When recovering from overfit, demand more new labels before refitting
            if self.health_state == "overfitting" or self._hyper_mode == "regularized":
                mn = max(mn, _min_new_labels() + 2)

        labeled = self.count_labeled()
        n_at_fit = int(getattr(self, "_n_at_last_fit", self.n_train) or 0)
        new_labels = max(0, labeled - n_at_fit)

        # Cooldown (skip unless forced)
        now = datetime.now(timezone.utc)
        last_auto = getattr(self, "_last_auto_fit_ts", None)
        if not force and last_auto is not None:
            elapsed = (now - last_auto).total_seconds()
            if elapsed < _cooldown_sec():
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": f"cooldown ({int(_cooldown_sec() - elapsed)}s left)",
                    "trigger": reason,
                    "labeled_closed": labeled,
                    "new_labels": new_labels,
                }

        if labeled < ms:
            result = {
                "ok": False,
                "skipped": True,
                "reason": f"Need ≥{ms} closed trades, have {labeled}",
                "n_samples": labeled,
                "trigger": reason,
            }
            self._last_auto_result = result
            return result

        if not force and self.model is not None and new_labels < mn:
            result = {
                "ok": False,
                "skipped": True,
                "reason": f"Only {new_labels} new labels since last fit (need ≥{mn})",
                "n_samples": labeled,
                "new_labels": new_labels,
                "trigger": reason,
            }
            self._last_auto_result = result
            return result

        result = self.fit_from_logs(min_samples=ms)
        result = dict(result)
        result["trigger"] = reason
        result["new_labels"] = new_labels
        if result.get("ok"):
            self._n_at_last_fit = int(result.get("n_samples") or labeled)
            self._last_auto_fit = datetime.now(timezone.utc).isoformat()
            self._last_auto_fit_ts = now
            self._last_auto_result = result
            self._save_auto_meta()
        else:
            self._last_auto_result = result
        return result

    def _save_auto_meta(self) -> None:
        """Persist auto-retrain counters alongside model meta."""
        try:
            data = {}
            if META_PATH.exists():
                data = json.loads(META_PATH.read_text())
            data["n_at_last_fit"] = getattr(self, "_n_at_last_fit", self.n_train)
            data["last_auto_fit"] = getattr(self, "_last_auto_fit", None)
            META_PATH.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _load_auto_meta(self) -> None:
        try:
            if not META_PATH.exists():
                self._n_at_last_fit = self.n_train
                return
            data = json.loads(META_PATH.read_text())
            self._n_at_last_fit = int(data.get("n_at_last_fit") or self.n_train or 0)
            self._last_auto_fit = data.get("last_auto_fit")
        except Exception:
            self._n_at_last_fit = self.n_train


def _auto_retrain_enabled() -> bool:
    return os.environ.get("ML_AUTO_RETRAIN", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _min_samples() -> int:
    try:
        return max(4, int(os.environ.get("ML_MIN_SAMPLES", "8")))
    except ValueError:
        return 8


def _min_new_labels() -> int:
    try:
        return max(1, int(os.environ.get("ML_MIN_NEW_LABELS", "3")))
    except ValueError:
        return 3


def _cooldown_sec() -> float:
    try:
        return max(5.0, float(os.environ.get("ML_RETRAIN_COOLDOWN_SEC", "60")))
    except ValueError:
        return 60.0


def _interval_sec() -> float:
    try:
        return max(30.0, float(os.environ.get("ML_RETRAIN_INTERVAL_SEC", "300")))
    except ValueError:
        return 300.0


_learner: Optional[MLLearner] = None
_learner_lock = threading.Lock()


def get_learner() -> MLLearner:
    global _learner
    with _learner_lock:
        if _learner is None:
            _learner = MLLearner()
            try:
                _learner._load_auto_meta()
            except Exception:
                pass
        return _learner


def auto_retrain(reason: str = "event", force: bool = False) -> dict[str, Any]:
    """Module-level convenience used by API, runner, and outcome updates."""
    try:
        return get_learner().maybe_retrain(reason=reason, force=force)
    except Exception as e:
        return {"ok": False, "reason": str(e), "trigger": reason}
