"""
Tests for engine/confidence.py — the pattern/sentiment score blender.
"""

import os

from confidence import (
    blend_with_sentiment,
    blend_with_cot,
    combine_signals,
    retail_institutional_divergence,
    sentiment_weight,
    cot_weight,
    divergence_bonus,
    DEFAULT_SENTIMENT_WEIGHT,
    DEFAULT_COT_WEIGHT,
    DEFAULT_DIVERGENCE_BONUS,
)


def test_none_sentiment_passes_through():
    assert blend_with_sentiment(72.0, None) == 72.0
    assert blend_with_sentiment(-55.0, None) == -55.0


def test_blend_moves_toward_sentiment():
    # Pattern says strongly bullish (80), sentiment says bearish (-40).
    # With default weight 0.2, result should sit between them, closer to 80.
    blended = blend_with_sentiment(80.0, -40.0, weight=0.2)
    assert -40.0 < blended < 80.0
    assert blended == round(80.0 * 0.8 + (-40.0) * 0.2, 1)


def test_weight_zero_ignores_sentiment():
    assert blend_with_sentiment(50.0, -100.0, weight=0.0) == 50.0


def test_weight_one_uses_only_sentiment():
    assert blend_with_sentiment(50.0, -100.0, weight=1.0) == -100.0


def test_result_is_clamped_to_100():
    blended = blend_with_sentiment(95.0, 95.0, weight=0.5)
    assert -100.0 <= blended <= 100.0


def test_sentiment_weight_env_default():
    os.environ.pop("SENTIMENT_WEIGHT", None)
    assert sentiment_weight() == DEFAULT_SENTIMENT_WEIGHT


def test_sentiment_weight_env_override():
    os.environ["SENTIMENT_WEIGHT"] = "0.5"
    try:
        assert sentiment_weight() == 0.5
    finally:
        os.environ.pop("SENTIMENT_WEIGHT", None)


def test_sentiment_weight_env_invalid_falls_back():
    os.environ["SENTIMENT_WEIGHT"] = "not-a-number"
    try:
        assert sentiment_weight() == DEFAULT_SENTIMENT_WEIGHT
    finally:
        os.environ.pop("SENTIMENT_WEIGHT", None)


def test_none_cot_passes_through():
    assert blend_with_cot(72.0, None) == 72.0
    assert blend_with_cot(-55.0, None) == -55.0


def test_cot_blend_moves_toward_cot():
    blended = blend_with_cot(80.0, -40.0, weight=0.2)
    assert -40.0 < blended < 80.0
    assert blended == round(80.0 * 0.8 + (-40.0) * 0.2, 1)


def test_cot_weight_env_default():
    os.environ.pop("COT_WEIGHT", None)
    assert cot_weight() == DEFAULT_COT_WEIGHT


def test_cot_weight_env_override():
    os.environ["COT_WEIGHT"] = "0.4"
    try:
        assert cot_weight() == 0.4
    finally:
        os.environ.pop("COT_WEIGHT", None)


def test_divergence_requires_both_scores():
    assert retail_institutional_divergence(None, 50.0) == 0
    assert retail_institutional_divergence(50.0, None) == 0


def test_divergence_requires_magnitude():
    # Both positive but weak -> no call.
    assert retail_institutional_divergence(10.0, 10.0) == 0


def test_divergence_confirmed_when_same_sign_and_strong():
    # Retail heavily short (contrarian score strongly bullish, +60) AND
    # large specs themselves net long (+60) -> institutions agree with fade.
    assert retail_institutional_divergence(60.0, 60.0) == 1
    assert retail_institutional_divergence(-60.0, -60.0) == 1


def test_divergence_contradicted_when_opposite_sign_and_strong():
    assert retail_institutional_divergence(60.0, -60.0) == -1


def test_divergence_bonus_env_default():
    os.environ.pop("COT_DIVERGENCE_BONUS", None)
    assert divergence_bonus() == DEFAULT_DIVERGENCE_BONUS


def test_combine_signals_matches_sentiment_only_when_cot_none():
    a = blend_with_sentiment(70.0, -30.0, weight=0.2)
    b = combine_signals(70.0, sentiment_score=-30.0, cot_score=None)
    assert a == b


def test_combine_signals_pass_through_with_no_overlays():
    assert combine_signals(42.0) == 42.0


def test_combine_signals_amplifies_on_confirmed_divergence():
    # Strong retail-vs-institutional agreement should push |score| higher
    # than blending sentiment+COT alone would, but still clamped to 100.
    base = blend_with_cot(
        blend_with_sentiment(50.0, 70.0, weight=0.2), 70.0, weight=0.15
    )
    boosted = combine_signals(50.0, sentiment_score=70.0, cot_score=70.0)
    assert abs(boosted) >= abs(base)
    assert -100.0 <= boosted <= 100.0


def test_combine_signals_dampens_on_contradicted_divergence():
    base = blend_with_cot(
        blend_with_sentiment(50.0, 70.0, weight=0.2), -70.0, weight=0.15
    )
    dampened = combine_signals(50.0, sentiment_score=70.0, cot_score=-70.0)
    assert abs(dampened) <= abs(base)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        passed += 1
        print(f"  ok: {t.__name__}")
    print(f"\n{passed}/{len(tests)} confidence tests passed.")
