"""Opportunity scanner — dual EMA, 8h epoch aggregation, weekend seasonality, scoring.

All markets come from the xyz (TradFi) DEX on Hyperliquid. Funding rates
are hourly; we aggregate to 8h epochs for EMA computation.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone

import config
import db
from engine import hyperliquid

log = logging.getLogger(__name__)

# Deep-scan limits from config (PRD §7.2: N=30 funding focus)
MAX_DEEP_SCAN = config.MAX_DEEP_SCAN
MAX_DEEP_SCAN_HARD = config.MAX_DEEP_SCAN_HARD


@dataclass
class Candidate:
    """A market that passed hard gates and has computed scores/caps."""
    coin: str
    ticker: str
    hedge_symbol: str
    ema_3d: float
    ema_7d: float
    weekend_mult: float
    forecast_apr: float
    score: float
    fee_drag_apr: float
    slippage_drag_apr: float
    cap_oi: float
    cap_vol: float
    cap_impact: float
    oi_usd: float
    volume_24h: float
    max_leverage: int
    mark_px: float


@dataclass
class ScanResult:
    candidates: list[Candidate]
    rejected: list[dict]
    is_trading_hours: bool
    deep_scan_cohort: int = 0


# ── NYSE Trading Hours ──────────────────────────────────────────────────────

def is_nyse_trading_hours() -> bool:
    """Check if current time is within NYSE core hours (9:30-16:00 ET)."""
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:
        return False
    t = now_et.hour * 60 + now_et.minute
    open_t = config.NYSE_OPEN_HOUR * 60 + config.NYSE_OPEN_MINUTE
    close_t = config.NYSE_CLOSE_HOUR * 60 + config.NYSE_CLOSE_MINUTE
    return open_t <= t < close_t


def is_weekend_et() -> bool:
    """Check if current time is weekend in ET."""
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return now_et.weekday() >= 5


# ── 8h Epoch Aggregation ────────────────────────────────────────────────────

def aggregate_to_8h_epochs(hourly_history: list[dict]) -> list[dict]:
    """Group hourly funding entries into 8h buckets.

    Each entry has 'time' (ms timestamp or ISO string) and 'fundingRate' (hourly rate).
    Buckets are 0:00, 8:00, 16:00 UTC. The 8h rate = mean of hourly rates in that bucket.
    Tags each epoch with is_weekend based on ET timezone.
    """
    from zoneinfo import ZoneInfo

    et_tz = ZoneInfo("America/New_York")
    buckets: dict[str, list[float]] = {}

    for entry in hourly_history:
        rate = float(entry.get("fundingRate") or entry.get("funding_rate") or 0)
        ts = entry.get("time") or entry.get("timestamp")

        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        else:
            continue

        # Round down to 8h epoch
        epoch_hour = (dt.hour // 8) * 8
        epoch_dt = dt.replace(hour=epoch_hour, minute=0, second=0, microsecond=0)
        epoch_key = epoch_dt.isoformat()

        if epoch_key not in buckets:
            buckets[epoch_key] = []
        buckets[epoch_key].append(rate)

    # Convert to list of dicts
    epochs = []
    for epoch_ts, rates in sorted(buckets.items()):
        dt = datetime.fromisoformat(epoch_ts)
        dt_et = dt.astimezone(et_tz)
        is_weekend = dt_et.weekday() >= 5

        mean_rate = sum(rates) / len(rates)
        # APR = mean_hourly_rate * 24 * 365 * 100 (as percentage)
        apr = mean_rate * 24 * 365 * 100

        epochs.append({
            "epoch_ts": epoch_ts,
            "rate_8h": mean_rate,
            "apr": apr,
            "is_weekend": is_weekend,
        })

    return epochs


# ── Dual EMA Computation ────────────────────────────────────────────────────

def _ema(values: list[float], alpha: float) -> float:
    """Compute EMA over a list of values (oldest first)."""
    if not values:
        return 0.0
    ema = values[0]
    for v in values[1:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def compute_dual_ema(epochs_8h: list[dict]) -> tuple[float | None, float | None]:
    """Compute 3-day and 7-day EMA from 8h epoch data.

    Returns (ema_3d, ema_7d) as APR percentages, or None if insufficient data.
    Requires at least 9 epochs for 3d EMA, at least 21 epochs for 7d EMA.
    No cold-start shortcuts — both windows must be fully populated.
    """
    apr_values = [e["apr"] for e in epochs_8h]

    if len(apr_values) < config.EMA_3D_EPOCHS:
        return None, None

    # 3-day EMA (9 epochs) — always computed from last 9
    recent_3d = apr_values[-config.EMA_3D_EPOCHS:]
    ema_3d = _ema(recent_3d, config.EMA_3D_ALPHA)

    # 7-day EMA (21 epochs) — requires full window
    if len(apr_values) < config.EMA_7D_EPOCHS:
        return ema_3d, None

    recent_7d = apr_values[-config.EMA_7D_EPOCHS:]
    ema_7d = _ema(recent_7d, config.EMA_7D_ALPHA)

    return ema_3d, ema_7d


# ── Weekend Seasonality ─────────────────────────────────────────────────────

def compute_weekend_seasonality(epochs_8h: list[dict]) -> float:
    """Compute weekend seasonality multiplier from the last 28 days of data.

    Returns ratio of median_weekend / median_weekday APR.
    Default 1.0 if insufficient data.
    """
    recent = epochs_8h[-config.SEASONALITY_LOOKBACK_EPOCHS:]

    weekday_aprs = [e["apr"] for e in recent if not e.get("is_weekend")]
    weekend_aprs = [e["apr"] for e in recent if e.get("is_weekend")]

    if len(weekday_aprs) < 3 or len(weekend_aprs) < 3:
        return 1.0

    median_weekday = statistics.median(weekday_aprs)
    median_weekend = statistics.median(weekend_aprs)

    if median_weekday <= 0:
        return 1.0

    return median_weekend / median_weekday


# ── 72h Forecast ────────────────────────────────────────────────────────────

def forecast_72h_apr(ema_3d: float, ema_7d: float, seasonality: float, is_weekend: bool) -> float:
    """Compute 72-hour forecast APR.

    Weekend: r_hat = 0.55 * ema_7d + 0.45 * ema_3d
    Weekday: r_hat = 0.30 * ema_7d + 0.70 * ema_3d
    Apply seasonality multiplier.
    Returns APR as percentage (e.g. 20.0 = 20%).
    """
    if is_weekend:
        r_hat = config.WEEKEND_W7D * ema_7d + config.WEEKEND_W3D * ema_3d
    else:
        r_hat = config.WEEKDAY_W7D * ema_7d + config.WEEKDAY_W3D * ema_3d

    return r_hat * seasonality


# ── Score Computation ───────────────────────────────────────────────────────

def compute_score(forecast_apr: float, impact_at_alloc: float) -> tuple[float, float, float]:
    """Compute net score for an asset.

    Returns (score, fee_drag_apr, slippage_drag_apr) all as APR percentages.
    """
    fee_drag = 2 * config.TAKER_FEE_PCT * config.REBALANCES_PER_YEAR * 100
    slip_drag = 2 * impact_at_alloc * config.REBALANCES_PER_YEAR * 100
    score = forecast_apr - fee_drag - slip_drag
    return score, fee_drag, slip_drag


# ── Full Scan Pipeline ──────────────────────────────────────────────────────

def build_candidates(markets: list[dict], budget: float) -> ScanResult:
    """Process all markets into scored candidates and rejected markets.

    Optimized pipeline:
    1. Fast pre-filter: hedge mapping, leverage, positive funding
    2. Sort by instantaneous funding, take top N for deep scan
    3. Deep scan: L2 book + funding history + EMA + forecast + score
    """
    from engine.equity import is_public_equity

    candidates = []
    rejected = []
    weekend = is_weekend_et()

    # Estimate initial per-asset allocation for impact calc
    buckets = config.compute_budget_buckets(budget)
    h_max = buckets["h_max"]
    est_alloc = h_max / config.MAX_NAMES

    # ── Phase 1: Fast pre-filter (no API calls) ────────────────────────────
    pre_filtered = []
    for m in markets:
        coin = config.normalize_coin(m["coin"])
        ticker = m["ticker"]

        inst_funding_apr = round((m.get("funding_apr") or 0) * 100, 2)  # as %

        # Hedge mapping check
        hedge_symbol = config.HEDGE_MAP.get(coin)
        if not hedge_symbol:
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": "missing_hedge_mapping",
                "instant_apr": inst_funding_apr,
                "forecast_apr": None, "score": None, "cap_final": None, "pre_rank": None,
            })
            continue

        # Stock-only filter
        if config.STOCK_ONLY_MODE and coin in config.NON_STOCK_COINS:
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": "non_stock_market_excluded",
                "forecast_apr": None, "score": None, "cap_final": None,
            })
            continue

        # Hard gate: maxLeverage >= 10
        max_lev = m.get("max_leverage") or 0
        if max_lev < config.MIN_MAX_LEVERAGE:
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": f"maxLeverage {max_lev} < {config.MIN_MAX_LEVERAGE}",
                "instant_apr": inst_funding_apr,
                "forecast_apr": None, "score": None, "cap_final": None, "pre_rank": None,
            })
            continue

        # Skip negative/zero instantaneous funding (no point deep scanning)
        if (m.get("funding_apr") or 0) <= 0:
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": "negative/zero instantaneous funding",
                "instant_apr": inst_funding_apr,
                "forecast_apr": None, "score": None, "cap_final": None, "pre_rank": None,
            })
            continue

        pre_filtered.append((m, hedge_symbol))

    # Sort by instantaneous funding APR descending (with liquidity tie-breakers).
    pre_filtered.sort(
        key=lambda x: (
            x[0].get("funding_apr") or 0,
            x[0].get("volume_24h") or 0,
            x[0].get("oi_usd") or 0,
        ),
        reverse=True,
    )

    deep_scan_set = pre_filtered[:MAX_DEEP_SCAN]
    if len(pre_filtered) > MAX_DEEP_SCAN:
        cutoff = pre_filtered[MAX_DEEP_SCAN - 1][0].get("funding_apr") or 0
        for m, hedge_symbol in pre_filtered[MAX_DEEP_SCAN:]:
            if len(deep_scan_set) >= MAX_DEEP_SCAN_HARD:
                break
            if (m.get("funding_apr") or 0) < cutoff:
                break
            deep_scan_set.append((m, hedge_symbol))
    skipped = pre_filtered[len(deep_scan_set):]

    # Assign pre-filter rank to skipped items
    for idx, (m, hedge_symbol) in enumerate(skipped):
        pre_rank = len(deep_scan_set) + idx + 1
        rejected.append({
            "coin": m["coin"], "ticker": m["ticker"],
            "reason": f"outside top funding cohort (scanned {len(deep_scan_set)})",
            "instant_apr": round((m.get("funding_apr") or 0) * 100, 2),
            "forecast_apr": None,
            "score": None, "cap_final": None, "pre_rank": pre_rank,
        })

    log.info("Pre-filter: %d passed, %d rejected, deep-scanning top %d",
             len(pre_filtered), len(rejected), len(deep_scan_set))

    # ── Phase 1.5: NASDAQ check (one fetch, cached for all) ────────────────
    # Trigger the NASDAQ cache load once before the loop
    if deep_scan_set:
        try:
            is_public_equity("AAPL")  # prime the cache
        except Exception:
            pass

    # ── Phase 2: Deep scan (API calls per market) ──────────────────────────
    for ds_idx, (m, hedge_symbol) in enumerate(deep_scan_set):
        coin = m["coin"]
        ticker = m["ticker"]
        ds_rank = ds_idx + 1  # 1-based rank within deep-scan cohort
        inst_apr = round((m.get("funding_apr") or 0) * 100, 2)

        # NASDAQ check (uses cached data after first call)
        if not is_public_equity(hedge_symbol):
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": "not_in_public_directories",
                "hedge_symbol": hedge_symbol,
                "instant_apr": inst_apr, "pre_rank": ds_rank,
                "forecast_apr": None, "score": None, "cap_final": None,
            })
            continue

        # Compute soft caps from market data (no API call needed)
        oi_usd = m.get("oi_usd") or 0
        volume_24h = m.get("volume_24h") or 0
        cap_oi = config.OI_CAP_FRACTION * oi_usd
        cap_vol = config.VOLUME_CAP_FRACTION * volume_24h

        # Fetch L2 book for impact cap
        try:
            book = hyperliquid.fetch_l2_book(coin)
            cap_impact = hyperliquid.find_max_notional_for_impact(book, config.MAX_IMPACT_PCT)
        except Exception as e:
            log.warning("L2 book fetch failed for %s: %s", coin, e)
            book = {"bids": [], "asks": []}
            cap_impact = cap_oi  # fallback: use OI cap

        # Fetch funding history → aggregate → dual EMA → forecast
        try:
            history = hyperliquid.fetch_funding_history(coin)
            if not history:
                rejected.append({
                    "coin": coin, "ticker": ticker,
                    "reason": "no funding history",
                    "instant_apr": inst_apr, "pre_rank": ds_rank,
                    "forecast_apr": None, "score": None, "cap_final": None,
                })
                continue

            epochs = aggregate_to_8h_epochs(history)

            # Persist epochs to DB
            for ep in epochs:
                db.upsert_funding_epoch_8h(
                    coin, ep["epoch_ts"], ep["rate_8h"], ep["apr"], ep["is_weekend"]
                )

            ema_3d, ema_7d = compute_dual_ema(epochs)

            # Strict history requirement: both EMAs must be populated
            if ema_3d is None or ema_7d is None:
                rejected.append({
                    "coin": coin, "ticker": ticker,
                    "reason": "insufficient_history",
                    "instant_apr": inst_apr, "pre_rank": ds_rank,
                    "epochs_available": len(epochs),
                    "epochs_required_3d": config.EMA_3D_EPOCHS,
                    "epochs_required_7d": config.EMA_7D_EPOCHS,
                    "forecast_apr": None, "score": None, "cap_final": None,
                })
                continue

            seasonality = compute_weekend_seasonality(epochs)
            forecast = forecast_72h_apr(ema_3d, ema_7d, seasonality, weekend)

            # Save EMA to cache
            db.upsert_ema(coin, ema_3d, ema_7d)

        except Exception as e:
            log.warning("Funding/EMA computation failed for %s: %s", coin, e)
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": f"funding data error: {e}",
                "instant_apr": inst_apr, "pre_rank": ds_rank,
                "forecast_apr": None, "score": None, "cap_final": None,
            })
            continue

        # Compute impact at estimated allocation for score
        preliminary_cap = min(cap_oi, cap_vol, cap_impact)
        est_position = min(preliminary_cap, est_alloc)
        try:
            impact_at_alloc = hyperliquid.compute_impact(book, est_position, side="sell")
        except Exception:
            impact_at_alloc = 0.01  # 1% default

        score, fee_drag, slip_drag = compute_score(forecast, impact_at_alloc)

        # Skip negative-funding assets
        if forecast <= 0:
            rejected.append({
                "coin": coin, "ticker": ticker,
                "reason": "negative funding forecast",
                "instant_apr": inst_apr, "pre_rank": ds_rank,
                "forecast_apr": round(forecast, 2), "score": round(score, 2),
                "cap_final": round(preliminary_cap, 2),
            })
            continue

        candidates.append(Candidate(
            coin=coin,
            ticker=ticker,
            hedge_symbol=hedge_symbol,
            ema_3d=round(ema_3d, 2),
            ema_7d=round(ema_7d, 2),
            weekend_mult=round(seasonality, 4),
            forecast_apr=round(forecast, 2),
            score=round(score, 2),
            fee_drag_apr=round(fee_drag, 2),
            slippage_drag_apr=round(slip_drag, 2),
            cap_oi=round(cap_oi, 2),
            cap_vol=round(cap_vol, 2),
            cap_impact=round(cap_impact, 2),
            oi_usd=oi_usd,
            volume_24h=volume_24h,
            max_leverage=m.get("max_leverage") or 0,
            mark_px=m.get("mark_px") or 0,
        ))

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)

    return ScanResult(
        candidates=candidates,
        rejected=rejected,
        is_trading_hours=is_nyse_trading_hours(),
        deep_scan_cohort=len(deep_scan_set),
    )
