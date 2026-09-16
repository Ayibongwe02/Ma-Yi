"""
Stage 3 (sentiment) / Stage 3.5 (COT) — Confidence blending.

Combines the pattern/context engine's score (engine/context.py) with:
  - the live retail sentiment score (data/sentiment.py), contrarian, and
  - the institutional COT (Commitment of Traders) score (data/cot.py),
    directional (not contrarian) — large speculators' own positioning lean.

Retail-vs-institutional divergence is the point: when the retail crowd is
heavily long (a strongly bearish contrarian sentiment_score) and large
speculators are heavily short (a strongly bearish cot_score too), that
agreement is a stronger signal than either input alone — see
`retail_institutional_divergence()` / `combine_signals()` below. When they
disagree (retail crowded one way, institutions leaning the *other* way from
what the contrarian read implies), the combined conviction is dialed back
rather than boosted.

Important asymmetry: both sentiment and COT are effectively *live-only*
overlays in this pipeline. IG's Client Sentiment API gives only the current
long/short split (no history at all), and while CFTC COT does have a full
weekly history, this codebase only wires it into the newest (live) bar —
same as sentiment — so a historical replay/backtest never has today's
positioning painted onto old bars that never saw it. See delivery/runner.py:
only live_once() calls this.
"""

from __future__ import annotations

import os

DEFAULT_SENTIMENT_WEIGHT = 0.2
DEFAULT_COT_WEIGHT = 0.15
# How much extra conviction (+bonus) or how much conviction gets stripped
# out (-bonus, i.e. multiplied by (1 - bonus)) when retail sentiment and
# institutional COT positioning agree vs. disagree in direction.
DEFAULT_DIVERGENCE_BONUS = 0.20
# Both sentiment_score and cot_score must be at least this extreme (on the
# shared -100..+100 scale) before a divergence read counts for anything —
# two nearly-neutral readings "agreeing" isn't a meaningful confluence.
DEFAULT_DIVERGENCE_MIN_MAGNITUDE = 30.0


def sentiment_weight() -> float:
    """Read SENTIMENT_WEIGHT from env (0..1), defaulting to a modest nudge
    rather than letting sentiment dominate the pattern/context score."""
    raw = os.environ.get("SENTIMENT_WEIGHT", str(DEFAULT_SENTIMENT_WEIGHT))
    try:
        weight = float(raw)
    except ValueError:
        return DEFAULT_SENTIMENT_WEIGHT
    return max(0.0, min(1.0, weight))


def blend_with_sentiment(
    pattern_score: float,
    sentiment_score: float | None,
    weight: float | None = None,
) -> float:
    """
    Blend a pattern/context score with a contrarian sentiment score.

    pattern_score: the engine's final score for this bar (-100..+100).
    sentiment_score: from data/sentiment.py's sentiment_to_score(), or None
        if sentiment wasn't available (missing creds, API error, etc). When
        None, the pattern_score passes through unchanged — sentiment should
        never be a hard requirement for a signal to fire.
    weight: fraction of the blend given to sentiment, 0..1. Defaults to
        SENTIMENT_WEIGHT from env (see sentiment_weight()).

    Returns the blended score, clamped to +/-100.
    """
    if sentiment_score is None:
        return pattern_score

    w = sentiment_weight() if weight is None else max(0.0, min(1.0, weight))
    blended = pattern_score * (1 - w) + sentiment_score * w
    return round(max(-100.0, min(100.0, blended)), 1)


def cot_weight() -> float:
    """Read COT_WEIGHT from env (0..1), defaulting to a modest nudge — kept
    a bit lighter than sentiment's default since COT is weekly-refreshed
    and always a few days stale by nature, not live."""
    raw = os.environ.get("COT_WEIGHT", str(DEFAULT_COT_WEIGHT))
    try:
        weight = float(raw)
    except ValueError:
        return DEFAULT_COT_WEIGHT
    return max(0.0, min(1.0, weight))


def divergence_bonus() -> float:
    """Read COT_DIVERGENCE_BONUS from env (>=0)."""
    raw = os.environ.get("COT_DIVERGENCE_BONUS", str(DEFAULT_DIVERGENCE_BONUS))
    try:
        bonus = float(raw)
    except ValueError:
        return DEFAULT_DIVERGENCE_BONUS
    return max(0.0, bonus)


def blend_with_cot(
    score: float,
    cot_score: float | None,
    weight: float | None = None,
) -> float:
    """
    Blend a score (pattern, or pattern-already-blended-with-sentiment) with
    the institutional COT score.

    cot_score: from data/cot.py's fetch_cot_score(), or None if COT wasn't
        available (unmapped instrument, network error, etc). When None, the
        input score passes through unchanged — COT should never be a hard
        requirement for a signal to fire, same as sentiment.

    Returns the blended score, clamped to +/-100.
    """
    if cot_score is None:
        return score

    w = cot_weight() if weight is None else max(0.0, min(1.0, weight))
    blended = score * (1 - w) + cot_score * w
    return round(max(-100.0, min(100.0, blended)), 1)


def retail_institutional_divergence(
    sentiment_score: float | None,
    cot_score: float | None,
    min_magnitude: float = DEFAULT_DIVERGENCE_MIN_MAGNITUDE,
) -> int:
    """
    Compare retail positioning (contrarian sentiment_score) against
    institutional positioning (directional cot_score) and report whether
    they confirm or contradict each other.

    sentiment_score is already contrarian (positive = retail crowd heavily
    SHORT, since that's bullish for a contrarian). cot_score is directional
    (positive = large specs themselves net long / bullish). So when both are
    positive and both are large, it means: retail crowd is heavily short
    *and* large speculators are net long — the classic "smart money agrees
    with the fade" setup. Same logic mirrored for both negative.

    Returns:
        +1  — same sign, both past `min_magnitude`: retail-vs-institutional
              divergence confirmed (institutions agree with the contrarian
              read) — the stronger-signal case this module exists for.
        -1  — opposite sign, both past `min_magnitude`: institutions are
              leaning the same way as the retail crowd, undercutting the
              contrarian read.
         0  — either score is missing, zero, or too weak to call.
    """
    if sentiment_score is None or cot_score is None:
        return 0
    if sentiment_score == 0 or cot_score == 0:
        return 0
    if abs(sentiment_score) < min_magnitude or abs(cot_score) < min_magnitude:
        return 0
    return 1 if (sentiment_score > 0) == (cot_score > 0) else -1


def combine_signals(
    pattern_score: float,
    sentiment_score: float | None = None,
    cot_score: float | None = None,
    sentiment_weight_: float | None = None,
    cot_weight_: float | None = None,
    bonus: float | None = None,
) -> float:
    """
    Full live-overlay blend: pattern/context score + retail sentiment
    (contrarian) + institutional COT positioning, with a confluence bonus
    (or penalty) applied when the two overlays agree (or disagree).

    Backward compatible: with cot_score=None this reduces to exactly
    blend_with_sentiment(); with both None it's a pass-through of
    pattern_score, same as before Stage 3.5.

    Order of operations: blend sentiment in first (unchanged Stage-3
    behaviour), then blend COT into that result, then apply the divergence
    multiplier last so it scales overall conviction rather than
    double-counting either overlay by itself.
    """
    score = blend_with_sentiment(pattern_score, sentiment_score, sentiment_weight_)
    score = blend_with_cot(score, cot_score, cot_weight_)

    divergence = retail_institutional_divergence(sentiment_score, cot_score)
    if divergence != 0:
        b = divergence_bonus() if bonus is None else max(0.0, bonus)
        multiplier = (1 + b) if divergence > 0 else max(0.0, 1 - b)
        score *= multiplier

    return round(max(-100.0, min(100.0, score)), 1)
