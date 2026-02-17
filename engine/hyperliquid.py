"""Hyperliquid /info endpoint client — queries the xyz TradFi DEX."""

from __future__ import annotations

import logging

import httpx

import config

log = logging.getLogger(__name__)


def fetch_meta_and_asset_ctxs() -> tuple[list[dict], list[dict]]:
    """Fetch metaAndAssetCtxs for the xyz (TradFi) DEX.

    Returns:
        universe_meta: list of dicts with name, szDecimals, maxLeverage, etc.
        asset_contexts: list of dicts with funding, openInterest, markPx, midPx, etc.
    """
    resp = httpx.post(
        config.HL_INFO_URL,
        json={"type": "metaAndAssetCtxs", "dex": config.HL_TRADFI_DEX},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    universe = data[0]["universe"]
    ctxs = data[1]
    return universe, ctxs


def fetch_funding_history(coin: str, start_time_ms: int = 0) -> list[dict]:
    """Fetch fundingHistory for a given coin (e.g. 'xyz:AAPL').

    Returns list of {coin, fundingRate, premium, time} sorted by time asc.
    """
    payload = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_time_ms,
    }
    resp = httpx.post(config.HL_INFO_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_l2_book(coin: str) -> dict:
    """Fetch L2 orderbook for a coin.

    Returns {"bids": [{px, sz, n}], "asks": [{px, sz, n}]}.
    """
    resp = httpx.post(
        config.HL_INFO_URL,
        json={"type": "l2Book", "coin": coin},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    levels = data.get("levels", [[], []])
    bids = [{"px": float(l["px"]), "sz": float(l["sz"])} for l in levels[0]]
    asks = [{"px": float(l["px"]), "sz": float(l["sz"])} for l in levels[1]]
    return {"bids": bids, "asks": asks}


def compute_impact(book: dict, notional: float, side: str = "sell") -> float:
    """Compute execution slippage for a given notional order.

    Walks the bid side (for sell/short) or ask side (for buy/cover).
    Returns slippage as a fraction (e.g. 0.002 = 0.2%).
    Returns 1.0 (100%) if book is empty or insufficient depth.
    """
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    if not bids or not asks:
        return 1.0

    mid = (bids[0]["px"] + asks[0]["px"]) / 2
    if mid <= 0:
        return 1.0

    # Walk the appropriate side
    levels = bids if side == "sell" else asks
    remaining = notional
    total_cost = 0.0
    total_filled = 0.0

    for level in levels:
        px = level["px"]
        sz = level["sz"]
        level_notional = px * sz

        if remaining <= level_notional:
            # Partial fill at this level
            fill_sz = remaining / px if px > 0 else 0
            total_cost += remaining
            total_filled += fill_sz
            remaining = 0
            break
        else:
            total_cost += level_notional
            total_filled += sz
            remaining -= level_notional

    if total_filled <= 0 or remaining > 0:
        return 1.0  # couldn't fill

    vwap = total_cost / total_filled
    slippage = abs(vwap - mid) / mid
    return slippage


def find_max_notional_for_impact(book: dict, max_impact_pct: float) -> float:
    """Binary search for the largest notional with slippage <= max_impact_pct.

    Returns the max notional in USD. Returns 0 if book is empty.
    """
    bids = book.get("bids", [])
    if not bids:
        return 0.0

    # Total book depth on bid side
    total_depth = sum(l["px"] * l["sz"] for l in bids)
    if total_depth <= 0:
        return 0.0

    lo, hi = 0.0, total_depth
    best = 0.0

    for _ in range(50):  # binary search iterations
        mid_val = (lo + hi) / 2
        if mid_val < 1:
            break
        impact = compute_impact(book, mid_val, side="sell")
        if impact <= max_impact_pct:
            best = mid_val
            lo = mid_val
        else:
            hi = mid_val
        if hi - lo < 100:  # $100 precision
            break

    return round(best, 2)


def parse_market_data(universe: list[dict], ctxs: list[dict]) -> list[dict]:
    """Combine universe metadata + asset contexts into unified market dicts.

    Funding is HOURLY. APR = funding_hourly * 24 * 365.
    OI is in base units; OI_USD = openInterest * markPx.
    Ticker names are xyz-prefixed (e.g. 'xyz:TSLA').
    """
    markets = []
    for meta, ctx in zip(universe, ctxs):
        try:
            name = meta["name"]  # e.g. "xyz:TSLA"
            mark_px = float(ctx.get("markPx") or 0)
            mid_px = float(ctx.get("midPx") or 0)
            funding_hourly = float(ctx.get("funding") or 0)
            oi_base = float(ctx.get("openInterest") or 0)
            volume_24h = float(ctx.get("dayNtlVlm") or 0)
            max_leverage = int(meta.get("maxLeverage") or 1)

            # Funding is hourly — annualize correctly
            funding_apr = funding_hourly * 24 * 365
            # OI in base units → multiply by mark price for USD
            oi_usd = oi_base * mark_px

            # Extract clean ticker (strip "xyz:" prefix for display)
            display_ticker = name.split(":")[-1] if ":" in name else name

            markets.append({
                "coin": name,               # full coin name for API calls: "xyz:TSLA"
                "ticker": display_ticker,    # display name: "TSLA"
                "mark_px": mark_px,
                "mid_px": mid_px,
                "funding_hourly": funding_hourly,
                "funding_apr": funding_apr,  # as decimal (e.g. 0.20 = 20%)
                "oi_base": oi_base,
                "oi_usd": oi_usd,
                "volume_24h": volume_24h,
                "max_leverage": max_leverage,
            })
        except (ValueError, KeyError):
            continue
    return markets
