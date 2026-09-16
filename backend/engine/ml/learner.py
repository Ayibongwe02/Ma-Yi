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
- Automatic response: pause learning when overfitting ("overfeeding"), force
  exploration / more aggressive retrain when underfitting, adaptive
  hyperparameters (shallower / stronger regularisation on overfit).
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
        return float(os.environ.get("ML_OVERFIT_GAP", "0.18"))
    except ValueError:
        return 0.18

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
            n_estimators=80, max_depth=3, learning_rate=0.08, subsample=0.85, random_state=42
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
        self._load()

    def _load(self) -> None:
        feature_version = None
        if META_PATH.exists():
            try:
                meta = json.loads(META_PATH.read_text())
                feature_version = meta.get("feature_version")
                self.n_train = meta.get("n_train", 0)
                self.n_pos = meta.get("n_pos", 0)
                self.n_neg = meta.get("n_neg", 0)
                self.last_fit = meta.get("last_fit")
                self.train_accuracy = meta.get("train_accuracy")
                self.feature_importance = meta.get("feature_importance", {})
                self._pattern_hit_rates = meta.get("pattern_hit_rates", {})
                self._regime_hit_rates = meta.get("regime_hit_rates", {})
                self.walk_forward = meta.get("walk_forward", {})
                self.learning_paused = bool(meta.get("learning_paused", False))
                self.explore_mode = bool(meta.get("explore_mode", False))
                self._hyper_mode = str(meta.get("hyper_mode") or "normal")
                self.health_state = str(meta.get("health_state") or "insufficient_data")
                self.health_detail = meta.get("health_detail") or {}
            except Exception:
                pass
        if MODEL_PATH.exists() and feature_version == FEATURE_VERSION:
            try:
                self.model = joblib.load(MODEL_PATH)
            except Exception:
                self.model = None
        # else: stale model from an older feature schema — leave self.model
        # None so predict() falls back cleanly until the next retrain.
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

    def _get_hyperparams(self) -> dict[str, Any]:
        """Adaptive hyperparameters driven by current health / hyper_mode."""
        mode = self._hyper_mode
        if mode == "regularized":
            # Overfitting response: shallower, slower, more subsample noise
            return dict(n_estimators=60, max_depth=2, learning_rate=0.05, subsample=0.70, random_state=42)
        if mode == "aggressive":
            # Underfitting response: slightly deeper / faster learning
            return dict(n_estimators=100, max_depth=4, learning_rate=0.10, subsample=0.90, random_state=42)
        return dict(n_estimators=80, max_depth=3, learning_rate=0.08, subsample=0.85, random_state=42)

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
            action = "pause_and_regularize"
            recommendation = (
                f"Train accuracy ({train_acc:.0%}) is {gap:.0%} higher than "
                f"walk-forward ({wf_acc:.0%}). Pausing auto-retrain and switching "
                "to stronger regularisation until more diverse out-of-sample data arrives."
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

        if state == "overfitting":
            self.learning_paused = True
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
        rows = get_signals(fired_only=True, limit=50_000)
        # Merge durable bootstrap / pre-train pool if present (engine/ml/bootstrap_labels.jsonl).
        # These rows are generated offline so the model has enough non-repeating
        # history to avoid overfitting when live closed trades are still scarce.
        bootstrap_path = ROOT / "engine" / "ml" / "bootstrap_labels.jsonl"
        if bootstrap_path.exists():
            try:
                with open(bootstrap_path, "r", encoding="utf-8") as bf:
                    for line in bf:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        X, y, times = [], [], []
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
            pat = str(r.get("pattern") or "unknown")
            pattern_stats.setdefault(pat, []).append(label)
            regime = str((r.get("meta") or {}).get("regime") or "unknown")
            regime_stats.setdefault(regime, []).append(label)
        if len(X) < min_samples:
            return {"ok": False, "reason": f"Need \u2265{min_samples} closed trades, have {len(X)}", "n_samples": len(X)}

        X_arr = np.array(X, dtype=float)
        y_arr = np.array(y, dtype=int)

        # Walk-forward needs chronological order; the final production model
        # is fit on everything (order doesn't matter for a non-sequential
        # classifier), so we sort a *copy* just for validation.
        order = np.argsort(times)
        wf = _walk_forward(X_arr[order], y_arr[order])

        from sklearn.ensemble import GradientBoostingClassifier
        hp = self._get_hyperparams()
        clf = GradientBoostingClassifier(**hp)
        clf.fit(X_arr, y_arr)
        with self._lock:
            self.model = clf
            self.n_train = len(y)
            self.n_pos = int(y_arr.sum())
            self.n_neg = int(len(y_arr) - y_arr.sum())
            self.last_fit = datetime.now(timezone.utc).isoformat()
            self.train_accuracy = round(float(clf.score(X_arr, y_arr)), 3)
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
            "n_wins": self.n_pos,
            "n_losses": self.n_neg,
            "train_accuracy": self.train_accuracy,
            "walk_forward": self.walk_forward,
            "feature_importance": self.feature_importance,
            "pattern_hit_rates": self._pattern_hit_rates,
            "regime_hit_rates": self._regime_hit_rates,
            "health": health,
            "hyper_mode": self._hyper_mode,
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
            result = {
                "prob_win": round(prob_win, 3),
                "adjusted_score": round(direction * mag, 1),
                "anomaly": anomaly,
                "note": note,
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
        """Count fired signals with win/loss outcomes (training pool size).

        Includes the durable bootstrap pre-train pool when present so health
        and auto-retrain thresholds reflect the true sample size available
        to fit_from_logs.
        """
        try:
            from delivery.logger import get_signals
            rows = get_signals(fired_only=True, limit=50_000)
            n = sum(
                1
                for r in rows
                if str(r.get("outcome") or "").lower() in ("win", "loss")
            )
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
                            n += 1
            return n
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

        Health manager may pause auto-retrain when overfitting is detected.
        Manual / force always bypasses the pause.
        """
        if not force and not _auto_retrain_enabled() and reason != "manual":
            return {
                "ok": False,
                "skipped": True,
                "reason": "auto-retrain disabled (ML_AUTO_RETRAIN=false)",
                "trigger": reason,
            }

        # Health pause: stop automatic learning while model is overfeeding
        if not force and reason != "manual" and self.learning_paused:
            return {
                "ok": False,
                "skipped": True,
                "reason": (
                    f"learning paused by health manager (state={self.health_state}). "
                    "Wait for more diverse labels or force a manual retrain."
                ),
                "trigger": reason,
                "health_state": self.health_state,
            }

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
        return max(4, int(os.environ.get("ML_MIN_SAMPLES", "12")))
    except ValueError:
        return 12


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
