from __future__ import annotations


def bracket_type(lower_f: int | None, upper_f: int | None) -> str:
    if lower_f is None:
        return "below"
    if upper_f is None:
        return "above"
    return "range"


def settled_yes(
    official_high_f: float,
    lower_f: int | None,
    upper_f: int | None,
) -> int:
    """Return 1 when the official high settles inside the integer bracket."""
    if lower_f is not None and official_high_f < lower_f:
        return 0
    if upper_f is not None and official_high_f > upper_f:
        return 0
    return 1
