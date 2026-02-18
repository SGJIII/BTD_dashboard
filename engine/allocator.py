"""Portfolio allocator — greedy water-fill across multiple assets."""

from __future__ import annotations

from dataclasses import dataclass, field

import config


@dataclass
class Position:
    """A single position in the target portfolio."""
    coin: str
    ticker: str
    hedge_symbol: str
    rank: int
    alloc_notional: float           # stock_long = perp_short = this
    alloc_pct: float                # % of total H
    cap_oi: float
    cap_vol: float
    cap_impact: float
    cap_conc: float
    cap_final: float
    binding_cap: str                # which cap was binding
    forecast_apr: float
    net_apr: float
    slippage_drag_apr: float
    fee_drag_apr: float
    score: float
    ema_3d: float
    ema_7d: float
    weekend_mult: float


@dataclass
class Portfolio:
    """The complete target portfolio."""
    positions: list[Position]
    budget: float
    emergency: float
    deployable: float
    h_max: float
    total_hedge_notional: float     # H = sum of allocs
    perp_collateral: float          # 0.35 * H
    coinbase_treasury: float        # DEPLOYABLE - H - perp_collateral
    coinbase_total: float           # EMERGENCY + coinbase_treasury
    portfolio_net_apr: float        # weighted avg
    portfolio_usd_day: float
    num_positions: int


def build_portfolio(candidates: list, budget: float) -> Portfolio:
    """Build multi-asset portfolio using greedy water-fill allocation.

    Args:
        candidates: list of scanner.Candidate objects, pre-sorted by score desc
        budget: total capital (USD)

    Algorithm (waterfall):
        1. Compute emergency, deployable, H_max
        2. Iterate candidates in score order
        3. For each: cap_final = min(cap_oi, cap_vol, cap_impact, cap_conc)
        4. alloc = min(cap_final, remaining)
        5. Skip if alloc < ALLOCATION_DUST_USD ($100)
        6. Continue until MAX_NAMES or budget exhausted
    """
    buckets = config.compute_budget_buckets(budget)
    emergency = buckets["emergency"]
    deployable = buckets["deployable"]
    h_max = buckets["h_max"]
    dust = buckets["min_ticket"]  # ALLOCATION_DUST_USD — noise floor only
    ops_reserve = buckets["ops_reserve"]
    budget = buckets["budget"]

    if h_max <= 0:
        return _empty_portfolio(budget, emergency, deployable, h_max)

    remaining = h_max
    positions = []

    for cand in candidates:
        if len(positions) >= config.MAX_NAMES:
            break
        if remaining <= dust:
            break

        cap_conc = config.MAX_CONCENTRATION * h_max
        cap_final = min(cand.cap_oi, cand.cap_vol, cand.cap_impact, cap_conc)
        alloc = min(cap_final, remaining)

        if alloc < dust:
            continue

        # Determine which cap was binding
        binding = _binding_cap(alloc, cand.cap_oi, cand.cap_vol, cand.cap_impact, cap_conc, remaining)

        positions.append(Position(
            coin=cand.coin,
            ticker=cand.ticker,
            hedge_symbol=cand.hedge_symbol,
            rank=len(positions) + 1,
            alloc_notional=round(alloc, 2),
            alloc_pct=0,  # filled in below
            cap_oi=round(cand.cap_oi, 2),
            cap_vol=round(cand.cap_vol, 2),
            cap_impact=round(cand.cap_impact, 2),
            cap_conc=round(cap_conc, 2),
            cap_final=round(cap_final, 2),
            binding_cap=binding,
            forecast_apr=cand.forecast_apr,
            net_apr=cand.score,  # score = forecast - fee_drag - slip_drag
            slippage_drag_apr=cand.slippage_drag_apr,
            fee_drag_apr=cand.fee_drag_apr,
            score=cand.score,
            ema_3d=cand.ema_3d,
            ema_7d=cand.ema_7d,
            weekend_mult=cand.weekend_mult,
        ))
        remaining -= alloc

    # Compute totals
    h_total = sum(p.alloc_notional for p in positions)
    for p in positions:
        p.alloc_pct = round((p.alloc_notional / h_total * 100) if h_total > 0 else 0, 2)

    perp_collateral = config.COLLATERAL_FRACTION * h_total
    coinbase_treasury = deployable - h_total - perp_collateral
    coinbase_total = emergency + ops_reserve + coinbase_treasury

    # Portfolio net APR: weighted average of position net APRs
    # + Coinbase yield on treasury + emergency
    # - Insurance drag
    if budget > 0 and h_total > 0:
        funding_income = sum(
            (p.net_apr / 100) * (p.alloc_notional / budget) for p in positions
        ) * 100
        coinbase_income = (config.DEFAULT_COINBASE_APR / 100) * (coinbase_total / budget) * 100
        insurance_drag = config.DEFAULT_INSURANCE_BUDGET_PCT
        portfolio_net_apr = funding_income + coinbase_income - insurance_drag
    else:
        portfolio_net_apr = 0

    portfolio_usd_day = (portfolio_net_apr / 100) * budget / 365

    return Portfolio(
        positions=positions,
        budget=round(budget, 2),
        emergency=round(emergency, 2),
        deployable=round(deployable, 2),
        h_max=round(h_max, 2),
        total_hedge_notional=round(h_total, 2),
        perp_collateral=round(perp_collateral, 2),
        coinbase_treasury=round(coinbase_treasury, 2),
        coinbase_total=round(coinbase_total, 2),
        portfolio_net_apr=round(portfolio_net_apr, 2),
        portfolio_usd_day=round(portfolio_usd_day, 2),
        num_positions=len(positions),
    )


def _binding_cap(alloc: float, cap_oi: float, cap_vol: float,
                 cap_impact: float, cap_conc: float, remaining: float) -> str:
    """Determine which cap was the binding constraint."""
    caps = {
        "oi": cap_oi,
        "vol": cap_vol,
        "impact": cap_impact,
        "conc": cap_conc,
        "budget": remaining,
    }
    # The binding cap is the smallest one (that equals the alloc)
    binding = min(caps, key=caps.get)
    return binding


def _empty_portfolio(budget: float, emergency: float, deployable: float, h_max: float) -> Portfolio:
    """Return an empty portfolio when budget is too small."""
    return Portfolio(
        positions=[],
        budget=round(budget, 2),
        emergency=round(emergency, 2),
        deployable=round(deployable, 2),
        h_max=round(h_max, 2),
        total_hedge_notional=0,
        perp_collateral=0,
        coinbase_treasury=round(deployable, 2),
        coinbase_total=round(budget, 2),
        portfolio_net_apr=0,
        portfolio_usd_day=0,
        num_positions=0,
    )
