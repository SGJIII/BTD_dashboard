"""Tests for hedge mapping, coin normalization, and rejection taxonomy."""

import config


def test_sndk_in_hedge_map():
    """SNDK should be present in HEDGE_MAP."""
    assert "xyz:SNDK" in config.HEDGE_MAP
    assert config.HEDGE_MAP["xyz:SNDK"] == "SNDK"


def test_normalize_coin_basic():
    """Normalize should uppercase symbol and preserve prefix."""
    assert config.normalize_coin("xyz:sndk") == "xyz:SNDK"
    assert config.normalize_coin("xyz:TSLA") == "xyz:TSLA"


def test_normalize_coin_whitespace():
    """Normalize should strip whitespace."""
    assert config.normalize_coin(" xyz:sndk ") == "xyz:SNDK"
    assert config.normalize_coin("  xyz:TSLA  ") == "xyz:TSLA"


def test_normalize_coin_mixed_case():
    """Normalize should handle mixed case."""
    assert config.normalize_coin("XYZ:tsla") == "xyz:TSLA"
    assert config.normalize_coin("Xyz:Sndk") == "xyz:SNDK"


def test_normalize_coin_no_prefix():
    """Normalize should handle bare symbols."""
    assert config.normalize_coin("tsla") == "TSLA"
    assert config.normalize_coin(" sndk ") == "SNDK"


def test_rejection_reason_codes():
    """Rejection reasons should use structured taxonomy codes."""
    from engine.scanner import build_candidates

    # Create a fake market with no hedge mapping
    markets = [{
        "coin": "xyz:FAKECOIN",
        "ticker": "FAKECOIN",
        "mark_px": 100,
        "mid_px": 100,
        "funding_hourly": 0.001,
        "funding_apr": 0.10,
        "oi_base": 1000,
        "oi_usd": 100000,
        "volume_24h": 500000,
        "max_leverage": 20,
    }]
    result = build_candidates(markets, 640_000)
    fakecoin_rej = [r for r in result.rejected if r["coin"] == "xyz:FAKECOIN"]
    assert len(fakecoin_rej) == 1
    assert fakecoin_rej[0]["reason"] == "missing_hedge_mapping"


def test_non_stock_rejection_code():
    """Non-stock markets should use structured reason code."""
    from engine.scanner import build_candidates

    # GOLD is in HEDGE_MAP but in NON_STOCK_COINS
    markets = [{
        "coin": "xyz:GOLD",
        "ticker": "GOLD",
        "mark_px": 2000,
        "mid_px": 2000,
        "funding_hourly": 0.001,
        "funding_apr": 0.10,
        "oi_base": 1000,
        "oi_usd": 100000,
        "volume_24h": 500000,
        "max_leverage": 20,
    }]
    result = build_candidates(markets, 640_000)
    gold_rej = [r for r in result.rejected if r["coin"] == "xyz:GOLD"]
    assert len(gold_rej) == 1
    assert gold_rej[0]["reason"] == "non_stock_market_excluded"


def test_normalized_coin_matches_hedge_map():
    """Normalization should make coin lookup succeed for valid entries."""
    raw = " xyz:sndk "
    normalized = config.normalize_coin(raw)
    assert normalized in config.HEDGE_MAP
