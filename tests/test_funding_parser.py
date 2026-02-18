"""Tests for funding parser: missing vs zero vs positive vs negative."""

from engine.hyperliquid import _parse_funding, parse_market_data


def test_parse_funding_missing_none():
    """None funding → missing."""
    val, missing = _parse_funding(None)
    assert val is None
    assert missing is True


def test_parse_funding_missing_empty():
    """Empty string funding → missing."""
    val, missing = _parse_funding("")
    assert val is None
    assert missing is True


def test_parse_funding_missing_nonnumeric():
    """Non-numeric string → missing."""
    val, missing = _parse_funding("N/A")
    assert val is None
    assert missing is True


def test_parse_funding_zero():
    """Explicit 0 → not missing, value=0."""
    val, missing = _parse_funding(0)
    assert val == 0.0
    assert missing is False


def test_parse_funding_zero_string():
    """String '0' → not missing, value=0."""
    val, missing = _parse_funding("0")
    assert val == 0.0
    assert missing is False


def test_parse_funding_positive():
    """Positive float → not missing."""
    val, missing = _parse_funding(0.001)
    assert val == 0.001
    assert missing is False


def test_parse_funding_negative():
    """Negative float → not missing."""
    val, missing = _parse_funding(-0.0005)
    assert val == -0.0005
    assert missing is False


def _make_pair(funding_val):
    """Helper: create minimal universe/ctx pair with given funding."""
    meta = {"name": "xyz:TEST", "maxLeverage": 20}
    ctx = {"markPx": "100", "midPx": "100", "openInterest": "10",
           "dayNtlVlm": "5000"}
    if funding_val is not None:
        ctx["funding"] = funding_val
    # else: key absent entirely
    return meta, ctx


def test_parse_market_missing_funding():
    """Missing funding key → funding_missing=True, funding_apr=None."""
    meta, ctx = _make_pair(None)
    ctx.pop("funding", None)  # ensure key not present at all
    markets = parse_market_data([meta], [ctx])
    assert len(markets) == 1
    m = markets[0]
    assert m["funding_missing"] is True
    assert m["funding_apr"] is None
    assert m["funding_hourly"] is None


def test_parse_market_zero_funding():
    """Explicit zero funding → funding_missing=False, funding_apr=0."""
    meta, ctx = _make_pair("0")
    markets = parse_market_data([meta], [ctx])
    m = markets[0]
    assert m["funding_missing"] is False
    assert m["funding_apr"] == 0.0
    assert m["funding_hourly"] == 0.0


def test_parse_market_positive_funding():
    """Positive funding → funding_missing=False, valid APR."""
    meta, ctx = _make_pair("0.001")
    markets = parse_market_data([meta], [ctx])
    m = markets[0]
    assert m["funding_missing"] is False
    assert m["funding_apr"] == 0.001 * 24 * 365
    assert m["funding_hourly"] == 0.001


def test_scanner_rejects_missing_funding():
    """Scanner pre-filter should reject missing funding with structured code."""
    from engine.scanner import build_candidates
    # Use TSLA which has a hedge mapping — funding is missing
    market = {
        "coin": "xyz:TSLA",
        "ticker": "TSLA",
        "funding_apr": None,
        "funding_missing": True,
        "max_leverage": 20,
        "oi_usd": 1_000_000,
        "volume_24h": 500_000,
    }
    result = build_candidates([market], 640_000)
    # Should be rejected with missing_live_funding
    missing_rej = [r for r in result.rejected if r["reason"] == "missing_live_funding"]
    assert len(missing_rej) == 1
    assert missing_rej[0]["ticker"] == "TSLA"
    assert missing_rej[0]["instant_apr"] is None
