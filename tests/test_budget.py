"""Tests for budget computation and low-budget no-allocation path."""

import config
from engine.allocator import build_portfolio


def test_budget_buckets_standard():
    """Standard budget produces correct reserves (proportional emergency)."""
    b = config.compute_budget_buckets(640_000)
    assert b["budget"] == 640_000
    assert b["emergency"] == 0.08 * 640_000  # 51200
    assert b["ops_reserve"] == 5_000
    assert b["deployable"] == 640_000 - b["emergency"] - b["ops_reserve"]
    assert b["h_max"] == b["deployable"] / (1 + config.COLLATERAL_FRACTION)
    assert b["min_ticket"] == config.ALLOCATION_DUST_USD


def test_budget_25k_proportional():
    """$25k budget: emergency = 8% = $2,000, not absorbed by floor."""
    b = config.compute_budget_buckets(25_000)
    assert b["emergency"] == 2_000  # 0.08 * 25000
    assert b["ops_reserve"] == 5_000
    assert b["deployable"] == 18_000  # 25000 - 2000 - 5000
    assert abs(b["h_max"] - 18_000 / (1 + config.COLLATERAL_FRACTION)) < 0.01
    assert b["h_max"] > 0  # can allocate!


def test_budget_2k_ops_absorbs():
    """$2k budget: emergency=$160, ops absorbs rest, deployable=0."""
    b = config.compute_budget_buckets(2_000)
    assert b["emergency"] == 160  # 0.08 * 2000
    assert b["ops_reserve"] == 1_840  # min(5000, 2000-160) = 1840
    assert b["deployable"] == 0
    assert b["h_max"] == 0


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
    """E + OPS + DEPLOYABLE = B (budget identity) for all budget sizes."""
    for budget in [2_000, 10_000, 25_000, 100_000, 300_000, 640_000, 1_000_000]:
        b = config.compute_budget_buckets(budget)
        total = b["emergency"] + b["ops_reserve"] + b["deployable"]
        assert abs(total - b["budget"]) < 0.01, f"Identity failed for B={budget}"


def test_low_budget_no_allocation():
    """Budget too small for min_ticket produces zero positions."""
    portfolio = build_portfolio([], 10_000)
    assert portfolio.num_positions == 0
    assert portfolio.total_hedge_notional == 0
    assert portfolio.positions == []


def test_dust_threshold():
    """min_ticket is now ALLOCATION_DUST_USD ($100), not the old hard floor."""
    b = config.compute_budget_buckets(640_000)
    assert b["min_ticket"] == config.ALLOCATION_DUST_USD
    b_small = config.compute_budget_buckets(2_000)
    assert b_small["min_ticket"] == config.ALLOCATION_DUST_USD


def test_emergency_strictly_proportional():
    """Emergency is always 8% of budget — no floor, purely proportional."""
    for budget in [10_000, 25_000, 100_000, 500_000, 700_000, 1_000_000]:
        b = config.compute_budget_buckets(budget)
        expected = config.EMERGENCY_PCT * budget
        assert abs(b["emergency"] - expected) < 0.01, (
            f"B={budget}: expected emergency={expected}, got {b['emergency']}"
        )


def test_emergency_floor_deprecated():
    """EMERGENCY_FLOOR exists but is not used in runtime bucket math."""
    assert hasattr(config, "EMERGENCY_FLOOR")
    # Verify small budget does NOT use the floor
    b = config.compute_budget_buckets(25_000)
    assert b["emergency"] != config.EMERGENCY_FLOOR
    assert b["emergency"] == 0.08 * 25_000
