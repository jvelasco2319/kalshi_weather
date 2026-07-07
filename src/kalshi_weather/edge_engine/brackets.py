from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Optional

from .types import Bracket, canonicalize_label


def parse_bracket_label(label: str) -> Bracket:
    """Parse canonical Kalshi high-temp bracket labels.

    Supported examples: ``<66``, ``66-67``, ``> 73``. For high-temperature
    range markets the final value is typically an integer Fahrenheit value, so
    ``<66`` is treated as values up to 65 and ``> 73`` as values 74 and higher.
    """
    canon = canonicalize_label(label)
    if canon.startswith("<"):
        threshold = float(canon[1:].strip())
        return Bracket(label=canon, upper_f=threshold - 1.0)
    if canon.startswith(">"):
        threshold = float(canon[1:].strip())
        return Bracket(label=canon, lower_f=threshold + 1.0)
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)", canon)
    if not m:
        raise ValueError(f"Cannot parse bracket label: {label!r}")
    low = float(m.group(1))
    high = float(m.group(2))
    if high < low:
        raise ValueError(f"Invalid bracket range: {label!r}")
    return Bracket(label=canon, lower_f=low, upper_f=high)


def parse_brackets(labels: Iterable[str]) -> Mapping[str, Bracket]:
    brackets = [parse_bracket_label(label) for label in labels]
    return {b.label: b for b in brackets}


def determine_final_bracket(temp_f: float, brackets: Iterable[Bracket]) -> Optional[str]:
    for bracket in brackets:
        if bracket.contains(temp_f):
            return bracket.label
    return None


def default_high_temp_brackets(labels: Iterable[str]) -> List[Bracket]:
    """Return parsed brackets in the provided market/display order."""
    return [parse_bracket_label(label) for label in labels]
