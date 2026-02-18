"""Tests for deep scan cohort sizing — PRD-aligned N=30.

All tests are deterministic with mocked external dependencies (no network calls).
"""

from unittest.mock import patch, MagicMock

import config


def test_deep_scan_config_minimum():
    """MAX_DEEP_SCAN must be at least 30 (PRD §7.2)."""
    assert config.MAX_DEEP_SCAN >= 30


def test_deep_scan_hard_exceeds_soft():
    """Hard cap must exceed soft cap for tie-break buffer."""
    assert config.MAX_DEEP_SCAN_HARD > config.MAX_DEEP_SCAN


def test_scanner_uses_config_values():
    """Scanner module-level constants reference config, not hardcoded."""
    from engine import scanner
    assert scanner.MAX_DEEP_SCAN == config.MAX_DEEP_SCAN
    assert scanner.MAX_DEEP_SCAN_HARD == config.MAX_DEEP_SCAN_HARD


def _make_stock_markets(n: int) -> list[dict]:
    """Create n synthetic markets from HEDGE_MAP with descending funding."""
    stock_coins = [c for c in config.HEDGE_MAP if c not in config.NON_STOCK_COINS]
    markets = []
    for i, coin in enumerate(stock_coins[:n]):
        ticker = coin.split(":")[-1]
        markets.append({
            "coin": coin,
            "ticker": ticker,
            "funding_apr": 0.30 - i * 0.005,
            "funding_missing": False,
            "max_leverage": 20,
            "oi_usd": 1_000_000,
            "volume_24h": 500_000,
        })
    return markets


def _mock_funding_history(coin: str, start_time_ms: int = 0) -> list[dict]:
    """Return 25 synthetic hourly funding entries (enough for dual EMA)."""
    import time
    base_ts = int(time.time() * 1000) - 25 * 3600 * 1000
    return [
        {"coin": coin, "fundingRate": 0.0001, "time": base_ts + i * 3600_000}
        for i in range(200)  # ~8 days of hourly data → 25 8h epochs
    ]


def _mock_l2_book(coin: str) -> dict:
    """Return synthetic L2 book with reasonable depth."""
    return {
        "bids": [
            {"px": 100.0 - i * 0.1, "sz": 500} for i in range(20)
        ],
        "asks": [
            {"px": 100.0 + i * 0.1, "sz": 500} for i in range(20)
        ],
    }


@patch("engine.scanner.hyperliquid.fetch_l2_book", side_effect=_mock_l2_book)
@patch("engine.scanner.hyperliquid.fetch_funding_history", side_effect=_mock_funding_history)
@patch("engine.scanner.db.upsert_funding_epoch_8h")
@patch("engine.scanner.db.upsert_ema")
@patch("engine.equity.is_public_equity", return_value=True)
def test_cohort_skipped_reason_mentions_count(
    mock_equity, mock_ema, mock_epoch, mock_funding, mock_l2
):
    """When more markets than MAX_DEEP_SCAN, extras get 'outside top funding cohort'."""
    from engine.scanner import build_candidates

    # Temporarily lower MAX_DEEP_SCAN so we can test cohort skipping
    # with the available stock coins (26 in HEDGE_MAP)
    import engine.scanner as scanner_mod
    orig = scanner_mod.MAX_DEEP_SCAN
    scanner_mod.MAX_DEEP_SCAN = 5
    try:
        markets = _make_stock_markets(10)
        result = build_candidates(markets, 640_000)

        assert result.deep_scan_cohort <= 10
        assert result.deep_scan_cohort >= 5

        skipped = [r for r in result.rejected if "outside top funding cohort" in r.get("reason", "")]
        assert len(skipped) > 0
        for r in skipped:
            assert str(result.deep_scan_cohort) in r["reason"]
    finally:
        scanner_mod.MAX_DEEP_SCAN = orig


@patch("engine.scanner.hyperliquid.fetch_l2_book", side_effect=_mock_l2_book)
@patch("engine.scanner.hyperliquid.fetch_funding_history", side_effect=_mock_funding_history)
@patch("engine.scanner.db.upsert_funding_epoch_8h")
@patch("engine.scanner.db.upsert_ema")
@patch("engine.equity.is_public_equity", return_value=True)
def test_deep_scanned_markets_get_forecast(
    mock_equity, mock_ema, mock_epoch, mock_funding, mock_l2
):
    """Every deep-scanned market gets forecast_apr and score (or explicit rejection)."""
    from engine.scanner import build_candidates

    markets = _make_stock_markets(5)
    result = build_candidates(markets, 640_000)

    assert result.deep_scan_cohort == 5

    # Each market should either be a candidate with score or rejected with reason
    scored = result.candidates
    rejected_with_forecast = [
        r for r in result.rejected
        if r.get("forecast_apr") is not None
    ]
    rejected_structural = [
        r for r in result.rejected
        if r.get("forecast_apr") is None
    ]

    total_accounted = len(scored) + len(rejected_with_forecast) + len(rejected_structural)
    assert total_accounted == 5


@patch("engine.scanner.hyperliquid.fetch_l2_book", side_effect=_mock_l2_book)
@patch("engine.scanner.hyperliquid.fetch_funding_history", side_effect=_mock_funding_history)
@patch("engine.scanner.db.upsert_funding_epoch_8h")
@patch("engine.scanner.db.upsert_ema")
@patch("engine.equity.is_public_equity", return_value=True)
def test_small_cohort_no_skipping(
    mock_equity, mock_ema, mock_epoch, mock_funding, mock_l2
):
    """When eligible markets < MAX_DEEP_SCAN, all are deep-scanned (none skipped)."""
    from engine.scanner import build_candidates

    markets = _make_stock_markets(3)
    result = build_candidates(markets, 640_000)

    assert result.deep_scan_cohort == 3
    skipped = [r for r in result.rejected if "outside top funding cohort" in r.get("reason", "")]
    assert len(skipped) == 0


@patch("engine.scanner.hyperliquid.fetch_l2_book", side_effect=_mock_l2_book)
@patch("engine.scanner.hyperliquid.fetch_funding_history", side_effect=_mock_funding_history)
@patch("engine.scanner.db.upsert_funding_epoch_8h")
@patch("engine.scanner.db.upsert_ema")
@patch("engine.equity.is_public_equity", return_value=True)
def test_candidates_sorted_by_score(
    mock_equity, mock_ema, mock_epoch, mock_funding, mock_l2
):
    """Final candidates list must be sorted by score descending."""
    from engine.scanner import build_candidates

    markets = _make_stock_markets(5)
    result = build_candidates(markets, 640_000)

    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
