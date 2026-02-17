"""Tests for budget computation and low-budget no-allocation path."""

import config
from engine.allocator import build_portfolio


def test_budget_buckets_standard():
    """Standard budget produces correct reserves."""
    b = config.compute_budget_buckets(640_000)
    assert b["budget"] == 640_000
    assert b["emergency"] == max(50_000, 0.08 * 640_000)  # 51200
    assert b["ops_reserve"] == 5_000
    assert b["deployable"] == 640_000 - b["emergency"] - b["ops_reserve"]
    assert b["h_max"] == b["deployable"] / (1 + config.COLLATERAL_FRACTION)
    assert b["min_ticket"] == max(config.MIN_TICKET_USD, 0.02 * 640_000)


def test_budget_buckets_small():
    """Small budget: emergency takes floor of 50k."""
    b = config.compute_budget_buckets(100_000)
    assert b["emergency"] == 50_000  # floor dominates
    assert b["deployable"] >= 0
    assert b["h_max"] >= 0


def test_budget_buckets_zero():
    """Zero budget: everything is zero, no crash."""
    b = config.compute_budget_buckets(0)
    assert b["budget"] == 0
    assert b["emergency"] == 0
    assert b["deployable"] == 0
    assert b["h_max"] == 0


def test_budget_buckets_negative():
    """Negative budget treated as zero."""
    b = config.compute_budget_buckets(-1000)
    assert b["budget"] == 0


def test_budget_identity():
    """E + OPS + DEPLOYABLE = B (budget identity)."""
    for budget in [100_000, 300_000, 640_000, 1_000_000]:
        b = config.compute_budget_buckets(budget)
        total = b["emergency"] + b["ops_reserve"] + b["deployable"]
        assert abs(total - b["budget"]) < 0.01, f"Identity failed for B={budget}"


def test_low_budget_no_allocation():
    """Budget too small for min_ticket produces zero positions."""
    # Budget so small that H_max < min_ticket
    portfolio = build_portfolio([], 10_000)
    assert portfolio.num_positions == 0
    assert portfolio.total_hedge_notional == 0
    assert portfolio.positions == []


def test_hmax_minticket_boundary():
    """At the boundary, verify min_ticket is enforced."""
    b = config.compute_budget_buckets(640_000)
    # min_ticket should be max(15000, 0.02 * 640000) = 12800 -> 15000
    assert b["min_ticket"] == 15_000


def test_emergency_floor_vs_pct():
    """Emergency = max(50000, 0.08*B). Floor wins for B < 625k, pct wins above."""
    # B = 500k: 0.08 * 500k = 40k < 50k, so emergency = 50k
    b = config.compute_budget_buckets(500_000)
    assert b["emergency"] == 50_000

    # B = 700k: 0.08 * 700k = 56k > 50k, so emergency = 56k
    b = config.compute_budget_buckets(700_000)
    assert b["emergency"] == 56_000
