"""Template safety test for the real repo.

Codex should adapt this test to scan newly added files. The purpose is to catch
accidental live order-placement code. This is not a complete security boundary.
"""

from pathlib import Path

SUSPICIOUS_STRINGS = [
    "/portfolio/orders",
    "create_order(",
    "submit_order(",
    "place_order(",
    "real_order",
]


def test_overlay_contains_no_live_order_placement_strings():
    root = Path(__file__).resolve().parents[1] / "src"
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))
    assert "/portfolio/orders" not in text
