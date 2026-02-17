"""Tests for stock-only universe enforcement."""

import config


def test_hedge_map_contains_non_stock():
    """HEDGE_MAP should include non-stock entries for test coverage."""
    assert "xyz:GOLD" in config.HEDGE_MAP
    assert "xyz:CL" in config.HEDGE_MAP
    assert "xyz:XYZ100" in config.HEDGE_MAP


def test_non_stock_coins_defined():
    """NON_STOCK_COINS set should exist and contain expected entries."""
    assert hasattr(config, "NON_STOCK_COINS")
    expected = {"xyz:GOLD", "xyz:SILVER", "xyz:CL", "xyz:XYZ100"}
    assert expected.issubset(config.NON_STOCK_COINS)


def test_stock_only_mode_default_on():
    """STOCK_ONLY_MODE should default to True."""
    assert hasattr(config, "STOCK_ONLY_MODE")
    assert config.STOCK_ONLY_MODE is True


def test_equity_coins_not_in_non_stock():
    """Direct equities should NOT be in NON_STOCK_COINS."""
    equity_coins = ["xyz:AAPL", "xyz:TSLA", "xyz:NVDA", "xyz:MSFT", "xyz:META"]
    for coin in equity_coins:
        assert coin not in config.NON_STOCK_COINS, f"{coin} should not be in NON_STOCK_COINS"


def test_non_stock_has_hedge_mapping():
    """Every NON_STOCK_COINS entry should have a HEDGE_MAP entry."""
    for coin in config.NON_STOCK_COINS:
        assert coin in config.HEDGE_MAP, f"{coin} in NON_STOCK_COINS but not in HEDGE_MAP"
