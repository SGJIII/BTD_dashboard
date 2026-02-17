"""Switching-cost-aware rebalance recommendation engine.

Compares the current target portfolio against the previous target to decide
whether switching is worth the estimated cost. Outputs HOLD or SWITCH with
the expected gain and estimated cost in USD.
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class RebalanceDecision:
    """Result of the switching-cost analysis."""
    recommendation: str           # "HOLD" or "SWITCH"
    expected_gain_usd: float      # expected gain over horizon from switching
    estimated_cost_usd: float     # total switching cost estimate
    threshold_usd: float          # cost * multiplier — gain must exceed this
    rationale: str                # human-readable explanation
    changes: list[dict]           # per-position changes


def compute_switching_cost(
    old_positions: list[dict],
    new_positions: list[dict],
) -> float:
    """Estimate the total cost of switching from old to new portfolio.

    Cost per position change = 2 * taker_fee * delta_notional + friction.
    Both opening new and closing old positions incur fees.
    """
    old_map = {p["coin"]: p for p in old_positions}
    new_map = {p["coin"]: p for p in new_positions}
    all_coins = set(old_map.keys()) | set(new_map.keys())

    total_cost = 0.0
    for coin in all_coins:
        old_alloc = old_map.get(coin, {}).get("alloc_notional", 0)
        new_alloc = new_map.get(coin, {}).get("alloc_notional", 0)
        delta = abs(new_alloc - old_alloc)
        if delta < 100:  # ignore tiny changes
            continue
        # Fee: taker on both perp and equity legs
        fee_cost = 2 * config.TAKER_FEE_PCT * delta
        # Friction buffer
        friction = config.REBALANCE_FRICTION_BPS / 10000 * delta
        total_cost += fee_cost + friction

    return total_cost


def compute_expected_gain(
    old_positions: list[dict],
    new_positions: list[dict],
    budget: float,
) -> float:
    """Estimate expected gain from switching over the rebalance horizon.

    Gain = (new_portfolio_yield - old_portfolio_yield) * budget * horizon / 365.
    Yield is the weighted net APR.
    """
    def portfolio_yield(positions: list[dict]) -> float:
        total_alloc = sum(p.get("alloc_notional", 0) for p in positions)
        if total_alloc == 0 or budget == 0:
            return 0.0
        weighted = sum(
            (p.get("net_apr", 0) or p.get("score", 0)) / 100 * p.get("alloc_notional", 0) / budget
            for p in positions
        )
        return weighted * 100  # back to APR %

    old_yield = portfolio_yield(old_positions)
    new_yield = portfolio_yield(new_positions)
    apr_improvement = new_yield - old_yield

    # Convert to USD over horizon
    return (apr_improvement / 100) * budget * config.REBALANCE_HORIZON_DAYS / 365


def evaluate_rebalance(
    old_positions: list[dict],
    new_positions: list[dict],
    budget: float,
) -> RebalanceDecision:
    """Evaluate whether switching from old to new portfolio is worthwhile."""
    cost = compute_switching_cost(old_positions, new_positions)
    gain = compute_expected_gain(old_positions, new_positions, budget)
    threshold = cost * config.REBALANCE_COST_MULTIPLIER

    # Build per-position change list
    old_map = {p["coin"]: p for p in old_positions}
    new_map = {p["coin"]: p for p in new_positions}
    all_coins = sorted(set(old_map.keys()) | set(new_map.keys()))
    changes = []
    for coin in all_coins:
        old_alloc = old_map.get(coin, {}).get("alloc_notional", 0)
        new_alloc = new_map.get(coin, {}).get("alloc_notional", 0)
        delta = new_alloc - old_alloc
        if abs(delta) < 100:
            continue
        ticker = new_map.get(coin, old_map.get(coin, {})).get("ticker", coin)
        changes.append({
            "ticker": ticker,
            "old_alloc": old_alloc,
            "new_alloc": new_alloc,
            "delta": delta,
            "action": "ADD" if old_alloc == 0 else "REMOVE" if new_alloc == 0 else ("INCREASE" if delta > 0 else "DECREASE"),
        })

    if gain > config.REBALANCE_MIN_GAIN_USD and gain >= threshold:
        recommendation = "SWITCH"
        rationale = (
            f"Expected {config.REBALANCE_HORIZON_DAYS}d gain (${gain:,.0f}) "
            f"exceeds {config.REBALANCE_COST_MULTIPLIER}x switching cost (${cost:,.0f}). "
            f"Rebalance is recommended."
        )
    else:
        recommendation = "HOLD"
        rationale = (
            f"Expected {config.REBALANCE_HORIZON_DAYS}d gain (${gain:,.0f}) "
            f"does not exceed {config.REBALANCE_COST_MULTIPLIER}x switching cost (${cost:,.0f}). "
            f"Hold current portfolio."
        )

    return RebalanceDecision(
        recommendation=recommendation,
        expected_gain_usd=round(gain, 2),
        estimated_cost_usd=round(cost, 2),
        threshold_usd=round(threshold, 2),
        rationale=rationale,
        changes=changes,
    )
