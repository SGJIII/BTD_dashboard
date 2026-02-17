"""Arbiter Worker Daemon v2 — multi-asset portfolio builder + scanner + alerts."""

import logging
import signal
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler

import config
import db
from engine import allocator, hyperliquid, scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("arbiter.worker")


def market_refresh_job():
    """60-second job: fetch markets, score candidates, build multi-asset portfolio."""
    try:
        log.info("Market refresh: fetching xyz TradFi markets...")
        universe, ctxs = hyperliquid.fetch_meta_and_asset_ctxs()
        markets = hyperliquid.parse_market_data(universe, ctxs)
        log.info("Fetched %d xyz markets", len(markets))

        # Upsert all market snapshots
        for m in markets:
            db.upsert_market_snapshot(m["ticker"], {
                "coin": m["coin"],
                "mark_px": m["mark_px"],
                "mid_px": m["mid_px"],
                "funding_hourly": m["funding_hourly"],
                "funding_apr": m["funding_apr"],
                "oi": m["oi_base"],
                "oi_usd": m["oi_usd"],
                "volume_24h": m["volume_24h"],
                "max_leverage": m["max_leverage"],
            })

        # Get user budget
        user = db.get_user_inputs()
        budget = user.get("budget", config.DEFAULT_BUDGET)

        # Build candidates: fetch L2 books, compute EMA, forecast, score
        log.info("Building candidates from %d markets with budget $%.0f...", len(markets), budget)
        scan_result = scanner.build_candidates(markets, budget)

        log.info(
            "Scan complete: %d candidates, %d rejected",
            len(scan_result.candidates), len(scan_result.rejected),
        )

        # Build multi-asset portfolio via greedy water-fill
        portfolio = allocator.build_portfolio(scan_result.candidates, budget)

        log.info(
            "Portfolio: %d positions, H=$%.0f, Net APR=%.2f%%, $/day=$%.2f",
            portfolio.num_positions,
            portfolio.total_hedge_notional,
            portfolio.portfolio_net_apr,
            portfolio.portfolio_usd_day,
        )

        # Save portfolio positions to DB
        db.clear_portfolio_positions()
        for pos in portfolio.positions:
            db.upsert_portfolio_position(pos.coin, {
                "ticker": pos.ticker,
                "hedge_symbol": pos.hedge_symbol,
                "rank": pos.rank,
                "alloc_notional": pos.alloc_notional,
                "alloc_pct": pos.alloc_pct,
                "cap_oi": pos.cap_oi,
                "cap_vol": pos.cap_vol,
                "cap_impact": pos.cap_impact,
                "cap_conc": pos.cap_conc,
                "cap_final": pos.cap_final,
                "binding_cap": pos.binding_cap,
                "forecast_apr": pos.forecast_apr,
                "net_apr": pos.net_apr,
                "slippage_drag_apr": pos.slippage_drag_apr,
                "fee_drag_apr": pos.fee_drag_apr,
                "score": pos.score,
                "ema_3d": pos.ema_3d,
                "ema_7d": pos.ema_7d,
                "weekend_mult": pos.weekend_mult,
            })

        # Save rejected markets
        db.clear_rejected_markets()
        for rej in scan_result.rejected:
            db.upsert_rejected_market(rej["coin"], {
                "ticker": rej["ticker"],
                "reason": rej["reason"],
                "instant_apr": rej.get("instant_apr"),
                "forecast_apr": rej.get("forecast_apr"),
                "score": rej.get("score"),
                "cap_final": rej.get("cap_final"),
                "pre_rank": rej.get("pre_rank"),
            })

        # Determine run status
        if portfolio.num_positions > 0:
            run_status = "success"
        elif scan_result.candidates:
            run_status = "partial"  # candidates found but none allocated
        else:
            run_status = "no_candidates"

        # Save portfolio-level aggregates
        db.update_portfolio_targets(
            num_positions=portfolio.num_positions,
            total_hedge_notional=portfolio.total_hedge_notional,
            perp_collateral=portfolio.perp_collateral,
            coinbase_treasury=portfolio.coinbase_treasury,
            coinbase_total=portfolio.coinbase_total,
            emergency=portfolio.emergency,
            portfolio_net_apr=portfolio.portfolio_net_apr,
            portfolio_usd_day=portfolio.portfolio_usd_day,
            health_status="OPTIMIZED" if portfolio.num_positions > 0 else "ACTION",
            run_status=run_status,
            deep_scan_cohort=scan_result.deep_scan_cohort,
        )

        # Log top positions
        for pos in portfolio.positions[:3]:
            log.info(
                "  #%d %s (%s): $%.0f (%.1f%%) | Forecast %.1f%% | Net %.1f%% | Cap: %s",
                pos.rank, pos.ticker, pos.hedge_symbol,
                pos.alloc_notional, pos.alloc_pct,
                pos.forecast_apr, pos.net_apr, pos.binding_cap,
            )

    except Exception:
        log.exception("Market refresh job failed")


def scanner_job():
    """10-minute job: deep EMA refresh + alerts."""
    try:
        log.info("Scanner: running deep EMA refresh...")

        # Re-run the full candidate build (which refreshes all funding history + EMA)
        snapshots = db.get_all_market_snapshots()
        if not snapshots:
            log.warning("No market snapshots for scanner")
            return

        # Convert snapshots to market-like dicts for scanner
        markets = []
        for s in snapshots:
            coin = s.get("coin") or f"{config.HL_TRADFI_DEX}:{s['ticker']}"
            markets.append({
                "coin": coin,
                "ticker": s["ticker"],
                "mark_px": s.get("mark_px") or 0,
                "mid_px": s.get("mid_px") or 0,
                "funding_hourly": s.get("funding_hourly") or 0,
                "funding_apr": s.get("funding_apr") or 0,
                "oi_base": s.get("oi") or 0,
                "oi_usd": s.get("oi_usd") or 0,
                "volume_24h": s.get("volume_24h") or 0,
                "max_leverage": s.get("max_leverage") or 0,
            })

        user = db.get_user_inputs()
        budget = user.get("budget", config.DEFAULT_BUDGET)
        scan_result = scanner.build_candidates(markets, budget)

        log.info(
            "Deep scan: %d candidates, trading_hours=%s",
            len(scan_result.candidates),
            scan_result.is_trading_hours,
        )

    except Exception:
        log.exception("Scanner job failed")


def main():
    log.info("Arbiter Worker v2 starting...")

    db.init_db()
    log.info("Database initialized at %s", config.DB_PATH)

    sched = BackgroundScheduler()

    sched.add_job(
        market_refresh_job, "interval",
        seconds=config.MARKET_REFRESH_INTERVAL,
        id="market_refresh", replace_existing=True, max_instances=1,
    )
    sched.add_job(
        scanner_job, "interval",
        seconds=config.SCANNER_INTERVAL,
        id="scanner", replace_existing=True, max_instances=1,
    )

    sched.start()
    log.info("Scheduler started (refresh %ds, scanner %ds)",
             config.MARKET_REFRESH_INTERVAL, config.SCANNER_INTERVAL)

    # Run first refresh immediately
    market_refresh_job()

    def shutdown(signum, frame):
        log.info("Shutting down...")
        sched.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown(wait=False)
        log.info("Worker stopped.")


if __name__ == "__main__":
    main()
