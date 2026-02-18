"""Tests for ranking transparency fields presence."""

from engine.scanner import (
    ScanResult,
    aggregate_to_8h_epochs,
    compute_dual_ema,
    compute_score,
    compute_weekend_seasonality,
    forecast_72h_apr,
)


def test_scan_result_has_deep_scan_cohort():
    """ScanResult dataclass must have deep_scan_cohort field."""
    sr = ScanResult(candidates=[], rejected=[], is_trading_hours=False)
    assert hasattr(sr, "deep_scan_cohort")
    assert sr.deep_scan_cohort == 0


def test_compute_score_returns_triple():
    """compute_score should return (score, fee_drag, slippage_drag)."""
    score, fee_drag, slip_drag = compute_score(20.0, 0.001)
    assert isinstance(score, float)
    assert isinstance(fee_drag, float)
    assert isinstance(slip_drag, float)
    assert fee_drag > 0  # fees should always be positive
    assert slip_drag > 0  # slippage should always be positive
    assert score < 20.0  # score should be less than forecast due to drags


def test_score_ordering():
    """Higher forecast APR with same impact should produce higher score."""
    s1, _, _ = compute_score(30.0, 0.001)
    s2, _, _ = compute_score(20.0, 0.001)
    assert s1 > s2


def test_dual_ema_insufficient_3d():
    """With < 9 epochs, both EMAs should be None (strict history)."""
    epochs = [{"apr": 10.0 + i} for i in range(8)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert ema_3d is None
    assert ema_7d is None


def test_dual_ema_sufficient_3d_insufficient_7d():
    """With 9-20 epochs, ema_3d populated but ema_7d is None."""
    epochs = [{"apr": 10.0 + i} for i in range(15)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert ema_3d is not None
    assert ema_3d > 0
    assert ema_7d is None


def test_dual_ema_warm():
    """With >= 21 epochs, both EMAs should be computed independently."""
    epochs = [{"apr": 10.0 + i * 0.5} for i in range(25)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert ema_3d is not None and ema_3d > 0
    assert ema_7d is not None and ema_7d > 0
    # 3d EMA should be more recent-biased
    assert ema_3d != ema_7d


def test_dual_ema_constant_converges():
    """Constant funding history → EMA converges to that constant APR."""
    epochs = [{"apr": 15.0} for _ in range(30)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert abs(ema_3d - 15.0) < 0.01
    assert abs(ema_7d - 15.0) < 0.01


def test_dual_ema_exact_boundary():
    """Exactly 9 epochs should produce ema_3d but not ema_7d."""
    epochs = [{"apr": 10.0} for _ in range(9)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert ema_3d is not None
    assert ema_7d is None

    # Exactly 21 should produce both
    epochs = [{"apr": 10.0} for _ in range(21)]
    ema_3d, ema_7d = compute_dual_ema(epochs)
    assert ema_3d is not None
    assert ema_7d is not None


def test_weekend_seasonality_insufficient_data():
    """With < 3 weekend epochs, seasonality should default to 1.0."""
    epochs = [{"apr": 10.0, "is_weekend": False} for _ in range(50)]
    mult = compute_weekend_seasonality(epochs)
    assert mult == 1.0


def test_forecast_weekend_vs_weekday():
    """Weekend forecast should weight 7d EMA more heavily."""
    ema_3d, ema_7d = 30.0, 10.0
    weekday = forecast_72h_apr(ema_3d, ema_7d, 1.0, False)
    weekend = forecast_72h_apr(ema_3d, ema_7d, 1.0, True)
    # Weekday: 0.70 * 30 + 0.30 * 10 = 24
    # Weekend: 0.45 * 30 + 0.55 * 10 = 19
    assert abs(weekday - 24.0) < 0.01
    assert abs(weekend - 19.0) < 0.01


def test_8h_epoch_aggregation():
    """Test that hourly entries aggregate into 8h buckets."""
    hourly = [
        {"time": "2025-01-15T00:00:00+00:00", "fundingRate": 0.001},
        {"time": "2025-01-15T01:00:00+00:00", "fundingRate": 0.002},
        {"time": "2025-01-15T08:00:00+00:00", "fundingRate": 0.003},
    ]
    epochs = aggregate_to_8h_epochs(hourly)
    assert len(epochs) == 2  # 00:00 bucket and 08:00 bucket
    # First bucket should have mean of 0.001 and 0.002
    assert abs(epochs[0]["rate_8h"] - 0.0015) < 0.0001
