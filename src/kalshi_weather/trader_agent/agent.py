from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .decision_schema import DecisionParseError, TraderDecision
from .journal import TraderJournalProtocol
from .llm_client import DryRunTraderLLMClient, TraderLLMClient
from .prompt_builder import TraderPromptBuilder
from .trade_board import build_trade_board
from .trader_types import TraderContext, dataclass_to_dict
from .validator import ValidationResult, validate_decision


@dataclass(frozen=True)
class TraderRunResult:
    context: TraderContext
    raw_llm_output: str | None
    decision: TraderDecision
    validation: ValidationResult
    approved_action: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dataclass_to_dict(self)


class TraderAgent:
    """Orchestrates prompt construction, LLM choice, validation, and journaling."""

    def __init__(
        self,
        *,
        llm_client: TraderLLMClient | None = None,
        prompt_builder: TraderPromptBuilder | None = None,
        journal: TraderJournalProtocol | None = None,
    ) -> None:
        self.llm_client = llm_client or DryRunTraderLLMClient()
        self.prompt_builder = prompt_builder or TraderPromptBuilder()
        self.journal = journal

    def recommend(self, context: TraderContext) -> TraderRunResult:
        if not context.candidate_trades:
            context = build_trade_board(context)

        prompt = self.prompt_builder.build(context)
        raw_output = self.llm_client.complete(prompt.system_prompt, prompt.user_payload)

        try:
            decision = TraderDecision.from_json(raw_output)
            validation = validate_decision(
                decision=decision,
                candidate_trades=context.candidate_trades,
                risk_limits=context.risk_limits,
            )
        except DecisionParseError as exc:
            decision = TraderDecision.hold(f"invalid LLM JSON: {exc}")
            validation = ValidationResult(
                valid=False,
                approved_action=decision.to_dict(),
                rejection_reason=str(exc),
            )

        result = TraderRunResult(
            context=context,
            raw_llm_output=raw_output,
            decision=decision,
            validation=validation,
            approved_action=validation.approved_action,
        )
        if self.journal is not None:
            self.journal.record_run(result.to_dict())
        return result
