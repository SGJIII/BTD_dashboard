"""Tests for switching-cost HOLD vs SWITCH logic."""

from engine.rebalance import (
    RebalanceDecision,
    compute_expected_gain,
    compute_switching_cost,
    evaluate_rebalance,
)


def _make_position(coin, ticker, alloc, net_apr=10.0, score=10.0):
    return {
        "coin": coin, "ticker": ticker, "alloc_notional": alloc,
        "net_apr": net_apr, "score": score,
    }


def test_no_change_hold():
    """Identical portfolios should recommend HOLD with near-zero gain."""
    old = [_make_position("xyz:TSLA", "TSLA", 50000)]
    new = [_make_position("xyz:TSLA", "TSLA", 50000)]
    decision = evaluate_rebalance(old, new, 640_000)
    assert decision.recommendation == "HOLD"
    assert abs(decision.expected_gain_usd) < 1.0
    assert decision.estimated_cost_usd < 1.0


def test_significant_change_switch():
    """Major allocation shift with big APR improvement should recommend SWITCH."""
    old = [_make_position("xyz:INTC", "INTC", 50000, net_apr=5.0, score=5.0)]
    new = [_make_position("xyz:TSLA", "TSLA", 80000, net_apr=30.0, score=30.0)]
    decision = evaluate_rebalance(old, new, 640_000)
    # With 25 APR point improvement across $80k, gain should be meaningful
    assert decision.expected_gain_usd > 0
    assert decision.recommendation == "SWITCH"


def test_trivial_change_hold():
    """Below-threshold delta ($50 < $100 cutoff) treated as no change."""
    old = [_make_position("xyz:TSLA", "TSLA", 50000, net_apr=10.0)]
    new = [_make_position("xyz:TSLA", "TSLA", 50050, net_apr=10.001)]
    decision = evaluate_rebalance(old, new, 640_000)
    assert decision.recommendation == "HOLD"


def test_switching_cost_calculation():
    """Switching cost increases with larger deltas."""
    old = [_make_position("xyz:TSLA", "TSLA", 50000)]
    new = [_make_position("xyz:TSLA", "TSLA", 80000)]
    cost_small = compute_switching_cost(old, new)

    old_big = [_make_position("xyz:TSLA", "TSLA", 50000)]
    new_big = [_make_position("xyz:NVDA", "NVDA", 80000)]
    cost_big = compute_switching_cost(old_big, new_big)

    assert cost_big > cost_small  # completely different positions cost more


def test_expected_gain_improvement():
    """Expected gain should be positive when new portfolio has higher APR."""
    old = [_make_position("xyz:INTC", "INTC", 50000, net_apr=5.0)]
    new = [_make_position("xyz:TSLA", "TSLA", 50000, net_apr=15.0)]
    gain = compute_expected_gain(old, new, 640_000)
    assert gain > 0


def test_expected_gain_regression():
    """Expected gain should be negative when new portfolio has lower APR."""
    old = [_make_position("xyz:TSLA", "TSLA", 50000, net_apr=15.0)]
    new = [_make_position("xyz:INTC", "INTC", 50000, net_apr=5.0)]
    gain = compute_expected_gain(old, new, 640_000)
    assert gain < 0


def test_empty_portfolios():
    """Empty old portfolio (first run) should not crash."""
    new = [_make_position("xyz:TSLA", "TSLA", 50000)]
    decision = evaluate_rebalance([], new, 640_000)
    assert decision.recommendation in ("HOLD", "SWITCH")
    assert isinstance(decision.rationale, str)


def test_decision_has_changes():
    """Decision should list per-position changes."""
    old = [_make_position("xyz:INTC", "INTC", 50000)]
    new = [_make_position("xyz:TSLA", "TSLA", 80000)]
    decision = evaluate_rebalance(old, new, 640_000)
    assert len(decision.changes) >= 1
    actions = {c["action"] for c in decision.changes}
    assert "ADD" in actions or "REMOVE" in actions or "INCREASE" in actions
