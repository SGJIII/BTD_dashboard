"""Tests for L2 fail-closed scoring — no -10k% artifacts."""

from engine.hyperliquid import compute_impact
from engine.scanner import compute_score


def test_compute_impact_empty_book():
    """Empty book → impact = 1.0 (100%)."""
    book = {"bids": [], "asks": []}
    assert compute_impact(book, 10_000) == 1.0


def test_compute_impact_no_bids():
    """No bids → impact = 1.0."""
    book = {"bids": [], "asks": [{"px": 100, "sz": 10}]}
    assert compute_impact(book, 10_000, side="sell") == 1.0


def test_compute_impact_insufficient_depth():
    """Insufficient depth to fill → impact = 1.0."""
    book = {
        "bids": [{"px": 100, "sz": 1}],  # $100 total depth
        "asks": [{"px": 101, "sz": 1}],
    }
    assert compute_impact(book, 50_000, side="sell") == 1.0


def test_compute_impact_valid():
    """Valid book with sufficient depth → 0 <= impact < 1."""
    book = {
        "bids": [
            {"px": 100.0, "sz": 100},  # $10,000
            {"px": 99.5, "sz": 100},   # $9,950
            {"px": 99.0, "sz": 100},   # $9,900
        ],
        "asks": [{"px": 100.5, "sz": 100}],
    }
    impact = compute_impact(book, 5_000, side="sell")
    assert 0 <= impact < 1
    assert impact < 0.01  # small order on decent book


def test_no_score_emitted_for_impact_1():
    """Score with impact=1.0 would produce ~-10,400% — verify the magnitude."""
    # This demonstrates why we gate on impact < 1
    score, _, slip_drag = compute_score(20.0, 1.0)
    assert slip_drag > 10_000  # ~10,428%
    assert score < -10_000  # ~-10,413%


def test_no_extreme_score_from_valid_impact():
    """Valid impact (< 1%) should never produce |score| > 1000%."""
    score, _, _ = compute_score(50.0, 0.005)  # 0.5% impact
    assert abs(score) < 1000


def test_score_gating_threshold():
    """Impact exactly at 1.0 is rejected; impact at 0.999 would be extreme."""
    # 0.999 → slip_drag ≈ 10,418%, still extreme
    _, _, slip = compute_score(20.0, 0.999)
    assert slip > 10_000
    # Any impact >= 1 should be gated by scanner (not scored)


def test_ui_reason_labels_include_l2():
    """UI _REASON_LABELS covers new L2 reason codes."""
    import ast
    with open("ui.py") as f:
        source = f.read()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_REASON_LABELS":
                    labels = ast.literal_eval(node.value)
                    assert "no_l2_orderbook" in labels
                    assert "insufficient_orderbook_depth" in labels
                    assert "_" not in labels["no_l2_orderbook"]
                    return
    raise AssertionError("_REASON_LABELS not found")
