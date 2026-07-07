from __future__ import annotations

from pathlib import Path

FALLBACK_SYSTEM_PROMPT = """You are the fake-money Kalshi Weather LLM Trade Advisor.

You do not execute trades. You only review one compact candidate trade snapshot.
The weather models remain the source of temperature estimates and probabilities.
Your job is to judge whether the candidate is high quality enough for fake-money
paper execution, then return strict JSON only.

Core rules:
- Edge starts the idea. Confirmation triggers the trade.
- Do not buy on edge alone.
- Prefer WAIT over low-quality trades.
- BLOCK missing exit bid, stale data, cooldown, wide spread, bracket invalidation,
  overexposure, malformed candidates, and any live-trading path.
- Bad outcomes and recent stops reduce future trust.
- Risk rules control size.
- The hard risk validator always has final veto.
- Never recommend real-money execution.
- Output strict JSON only. Do not include markdown.
"""


def load_system_prompt(prompt_path: str | Path | None = None) -> str:
    candidates = []
    if prompt_path:
        candidates.append(Path(prompt_path))
    candidates.extend(
        [
            Path("codex_llm_trade_advisor_package/01_LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md"),
            Path("prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md"),
        ]
    )
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return FALLBACK_SYSTEM_PROMPT

