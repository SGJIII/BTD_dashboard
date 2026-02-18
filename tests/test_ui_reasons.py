"""Tests for UI rejection reason code handling."""

# Import the reason label mapping directly — test that it covers all scanner codes
import importlib
import sys


def _get_ui_reason_labels() -> dict:
    """Import _REASON_LABELS from ui module without running Streamlit."""
    # We can't import ui.py directly (it calls st.set_page_config at import).
    # Instead, parse it and extract the dict.
    import ast
    with open("ui.py") as f:
        source = f.read()
    # Find the _REASON_LABELS assignment
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_REASON_LABELS":
                    return ast.literal_eval(node.value)
    raise RuntimeError("_REASON_LABELS not found in ui.py")


def test_reason_labels_cover_structured_codes():
    """All structured reason codes from scanner should have UI labels."""
    labels = _get_ui_reason_labels()
    required_codes = [
        "missing_hedge_mapping",
        "non_stock_market_excluded",
        "not_in_public_directories",
        "insufficient_history",
        "negative funding forecast",
        "negative/zero instantaneous funding",
        "no funding history",
    ]
    for code in required_codes:
        assert code in labels, f"Missing UI label for reason code: {code}"
        assert isinstance(labels[code], str)
        assert len(labels[code]) > 0


def test_reason_labels_are_human_readable():
    """Labels should not be raw codes (no underscores)."""
    labels = _get_ui_reason_labels()
    for code, label in labels.items():
        assert "_" not in label, f"Label for '{code}' looks like a raw code: '{label}'"


def test_no_legacy_substring_matching_in_diagnostics():
    """UI diagnostics should use exact reason code matching, not substring contains."""
    with open("ui.py") as f:
        source = f.read()
    # The old pattern was: "not in public directories" in r.get("reason", "")
    # This should no longer appear
    assert '"not in public directories" in' not in source, \
        "UI still uses legacy substring matching for public directory rejections"
    assert '"no hedge mapping" in' not in source, \
        "UI still uses legacy substring matching for hedge mapping rejections"


def test_diagnostics_uses_structured_lookup():
    """UI diagnostics should use reason_counts dict with exact code keys."""
    with open("ui.py") as f:
        source = f.read()
    # Should use .get("not_in_public_directories") not substring match
    assert 'reason_counts.get("not_in_public_directories"' in source
    assert 'reason_counts.get("insufficient_history"' in source
    assert 'reason_counts.get("missing_hedge_mapping"' in source
