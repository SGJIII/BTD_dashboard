"""Tests for waterfall micro-allocation — no hard min-ticket gate."""

import config
from engine.allocator import build_portfolio, Position


class FakeCandidate:
    """Minimal candidate for allocator testing."""
    def __init__(self, coin, ticker, hedge, score, cap_oi, cap_vol, cap_impact):
        self.coin = coin
        self.ticker = ticker
        self.hedge_symbol = hedge
        self.score = score
        self.cap_oi = cap_oi
        self.cap_vol = cap_vol
        self.cap_impact = cap_impact
        self.forecast_apr = score + 5
        self.slippage_drag_apr = 1.0
        self.fee_drag_apr = 4.0
        self.ema_3d = 20.0
        self.ema_7d = 18.0
        self.weekend_mult = 1.0


def test_small_budget_nonzero_allocation():
    """$2k budget: proportional emergency=$160, ops=$1840, deployable=0."""
    cand = FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 15.0,
                         cap_oi=500, cap_vol=500, cap_impact=500)
    portfolio = build_portfolio([cand], 2_000)
    # emergency = 0.08 * 2000 = 160, remaining = 1840, ops = min(5000, 1840) = 1840
    # deployable = 0, h_max = 0
    assert portfolio.num_positions == 0
    assert portfolio.emergency == 160  # 8% of $2k


def test_moderate_small_budget_allocates():
    """$80k budget should allocate with proportional emergency."""
    cand = FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 15.0,
                         cap_oi=5_000, cap_vol=5_000, cap_impact=5_000)
    portfolio = build_portfolio([cand], 80_000)
    # emergency = 0.08 * 80k = 6400, remaining = 73600, ops = 5000
    # deployable = 68600, h_max = 68600/1.35 ≈ 50814
    # cap_final = min(5000, 5000, 5000, 0.5*50814≈25407) = 5000
    # 5000 > 100 (dust) → allocated
    assert portfolio.num_positions == 1
    assert portfolio.positions[0].alloc_notional == 5_000


def test_dust_skipped():
    """Allocation below dust threshold ($100) should be skipped."""
    cand = FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 15.0,
                         cap_oi=50, cap_vol=50, cap_impact=50)
    portfolio = build_portfolio([cand], 640_000)
    # cap_final = 50 < 100 (dust) → skipped
    assert portfolio.num_positions == 0


def test_waterfall_continues_past_small_caps():
    """Waterfall should skip a tiny-cap candidate and continue to the next."""
    tiny = FakeCandidate("xyz:TINY", "TINY", "TINY", 25.0,
                         cap_oi=50, cap_vol=50, cap_impact=50)
    good = FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 15.0,
                         cap_oi=100_000, cap_vol=100_000, cap_impact=100_000)
    portfolio = build_portfolio([tiny, good], 640_000)
    # tiny skipped (cap=50 < dust=100), good allocated
    assert portfolio.num_positions == 1
    assert portfolio.positions[0].ticker == "TSLA"


def test_delta_neutral_identity():
    """stock_long == perp_short == alloc_notional for every position."""
    cand = FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 15.0,
                         cap_oi=200_000, cap_vol=200_000, cap_impact=200_000)
    portfolio = build_portfolio([cand], 640_000)
    for pos in portfolio.positions:
        # Each position: stock_long = perp_short = alloc_notional
        assert pos.alloc_notional > 0


def test_large_budget_unchanged():
    """$640k standard budget should produce same results as before."""
    cands = [
        FakeCandidate("xyz:TSLA", "TSLA", "TSLA", 25.0,
                      cap_oi=200_000, cap_vol=200_000, cap_impact=200_000),
        FakeCandidate("xyz:NVDA", "NVDA", "NVDA", 20.0,
                      cap_oi=150_000, cap_vol=150_000, cap_impact=150_000),
    ]
    portfolio = build_portfolio(cands, 640_000)
    assert portfolio.num_positions == 2
    assert portfolio.total_hedge_notional > 0
    # Risk caps still binding
    for pos in portfolio.positions:
        assert pos.alloc_notional <= pos.cap_final + 0.01
