from __future__ import annotations

from pathlib import Path


def test_no_live_order_code_present() -> None:
    risky_terms = [
        "create_order",
        "place_order",
        "CreateOrder",
        "create-order",
        "requests.post",
        ".post(",
    ]
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in Path("src").rglob("*.py")
    )

    assert not any(term in source_text for term in risky_terms)
