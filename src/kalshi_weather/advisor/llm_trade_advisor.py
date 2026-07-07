from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from kalshi_weather.advisor.decision_schema import (
    AdvisorDecision,
    AdvisorInput,
    advisor_decision_from_json,
    coerce_invalid_decision_to_safe_block,
)
from kalshi_weather.advisor.trade_quality import TradeQualityResult, score_trade_quality

ADVISOR_MODE_OFF = "off"
ADVISOR_MODE_RULE_BASED = "rule_based"
ADVISOR_MODE_PROMPT_ONLY = "prompt_only"
ADVISOR_MODE_LLM_JSON = "llm_json"
ADVISOR_MODES = {
    ADVISOR_MODE_OFF,
    ADVISOR_MODE_RULE_BASED,
    ADVISOR_MODE_PROMPT_ONLY,
    ADVISOR_MODE_LLM_JSON,
}


class TradeAdvisor(ABC):
    provider_name = "base"

    @abstractmethod
    def decide(self, advisor_input: AdvisorInput) -> AdvisorDecision:
        raise NotImplementedError


class RuleBasedAdvisor(TradeAdvisor):
    provider_name = "rule_based"

    def decide(self, advisor_input: AdvisorInput) -> AdvisorDecision:
        quality = score_trade_quality(advisor_input)
        position = advisor_input.position_state
        candidate = advisor_input.candidate_trade
        config = advisor_input.configuration
        if position.get("has_open_position"):
            return _open_position_decision(advisor_input, quality)
        if quality.hard_veto_flags:
            return _decision(
                advisor_input,
                "BLOCK",
                "none",
                quality,
                "Hard risk flags block the entry.",
                hard_veto_flags=quality.hard_veto_flags,
                risk_flags=quality.risk_flags,
            )
        required_seen = int(config.get("required_signal_seen_count") or 2)
        seen_count = int(candidate.get("signal_seen_count") or 0)
        if seen_count < required_seen:
            return _decision(
                advisor_input,
                "WAIT",
                "none",
                quality,
                f"Signal has appeared {seen_count} time(s); required confirmation is {required_seen}.",
                risk_flags=quality.risk_flags + ["signal_not_persistent"],
                recheck_minutes=1,
            )
        min_score = int(config.get("min_score_for_buy") or config.get("advisor_min_score") or 75)
        if quality.score >= min_score:
            side = str(candidate.get("side") or "NONE").upper()
            action = "BUY_YES" if side == "YES" else "BUY_NO" if side == "NO" else "BLOCK"
            return _decision(
                advisor_input,
                action,
                str(config.get("strategy_mode") or advisor_input.strategy_mode or "microtrade"),
                quality,
                "Confirmed edge with acceptable liquidity and no active hard veto.",
                size_multiplier=_size_multiplier(quality.score),
                confidence=_confidence(quality.score),
                risk_flags=quality.risk_flags,
                recheck_minutes=1,
            )
        if quality.score >= int(config.get("min_score_for_small_trade") or 60):
            return _decision(
                advisor_input,
                "WAIT",
                "none",
                quality,
                "Trade quality is acceptable but below the normal buy threshold.",
                risk_flags=quality.risk_flags + ["below_normal_buy_threshold"],
                recheck_minutes=1,
            )
        return _decision(
            advisor_input,
            "WAIT",
            "none",
            quality,
            "Trade quality is too weak for a confirmed entry.",
            risk_flags=quality.risk_flags,
            recheck_minutes=3,
        )


class PromptOnlyAdvisor(TradeAdvisor):
    provider_name = "prompt_only"

    def __init__(self, *, prompt_path: str | Path, log_dir: str | Path) -> None:
        self.prompt_path = Path(prompt_path)
        self.log_dir = Path(log_dir)

    def decide(self, advisor_input: AdvisorInput) -> AdvisorDecision:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        quality = score_trade_quality(advisor_input)
        prompt_text = self.prompt_path.read_text(encoding="utf-8") if self.prompt_path.exists() else ""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        path = self.log_dir / f"prompt_only_{timestamp}.json"
        path.write_text(
            json.dumps(
                {
                    "provider": self.provider_name,
                    "prompt_path": str(self.prompt_path),
                    "prompt": prompt_text,
                    "advisor_input": advisor_input.to_dict(),
                    "expected_output": "Paste this input into the LLM advisor prompt, then return strict JSON.",
                },
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        return _decision(
            advisor_input,
            "WAIT",
            "none",
            quality,
            f"Prompt-only advisor wrote review artifact to {path}.",
            risk_flags=quality.risk_flags + ["prompt_only_no_execution"],
            recheck_minutes=5,
        )


class LLMJsonAdvisor(TradeAdvisor):
    provider_name = "llm_json"

    def __init__(self, *, provider_config: str | Path | None = None) -> None:
        self.provider_config = Path(provider_config) if provider_config else None

    def decide(self, advisor_input: AdvisorInput) -> AdvisorDecision:
        if self.provider_config is None or not self.provider_config.exists():
            return coerce_invalid_decision_to_safe_block(
                {},
                reason="advisor provider unavailable",
                fallback_input=advisor_input,
            )
        try:
            config = json.loads(self.provider_config.read_text(encoding="utf-8"))
            response_path = config.get("response_json_path")
            if not response_path:
                return coerce_invalid_decision_to_safe_block(
                    config,
                    reason="advisor provider unavailable",
                    fallback_input=advisor_input,
                )
            return advisor_decision_from_json(Path(response_path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return coerce_invalid_decision_to_safe_block(
                {},
                reason=f"invalid llm_json advisor output: {exc}",
                fallback_input=advisor_input,
            )


def advisor_for_mode(
    mode: str,
    *,
    prompt_path: str | Path = "prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md",
    log_dir: str | Path = "reports/llm_trade_advisor",
    provider_config: str | Path | None = None,
) -> TradeAdvisor | None:
    normalized = mode.strip().lower()
    if normalized == ADVISOR_MODE_OFF:
        return None
    if normalized == ADVISOR_MODE_RULE_BASED:
        return RuleBasedAdvisor()
    if normalized == ADVISOR_MODE_PROMPT_ONLY:
        return PromptOnlyAdvisor(prompt_path=prompt_path, log_dir=log_dir)
    if normalized == ADVISOR_MODE_LLM_JSON:
        return LLMJsonAdvisor(provider_config=provider_config)
    raise ValueError(f"unknown advisor mode: {mode}")


def safe_decide(advisor: TradeAdvisor, advisor_input: AdvisorInput) -> AdvisorDecision:
    try:
        return advisor.decide(advisor_input)
    except Exception as exc:  # noqa: BLE001
        return coerce_invalid_decision_to_safe_block(
            {},
            reason=f"advisor error: {exc}",
            fallback_input=advisor_input,
        )


def _open_position_decision(advisor_input: AdvisorInput, quality: TradeQualityResult) -> AdvisorDecision:
    position = advisor_input.position_state
    exit_triggers = [
        "stop_loss_triggered",
        "profit_target_triggered",
        "probability_drop_triggered",
        "max_hold_triggered",
        "force_flat_active",
        "weather_invalidated",
    ]
    triggered = [flag for flag in exit_triggers if position.get(flag)]
    if triggered and position.get("current_exit_bid") is not None:
        return _decision(
            advisor_input,
            "SELL",
            "microtrade",
            quality,
            "Existing position exit trigger fired: " + ", ".join(triggered),
            risk_flags=quality.risk_flags + triggered,
            confidence="high",
        )
    if triggered:
        return _decision(
            advisor_input,
            "HOLD",
            "microtrade",
            quality,
            "Exit trigger fired, but no usable exit bid is available.",
            risk_flags=quality.risk_flags + triggered + ["missing_exit_bid"],
            hard_veto_flags=["missing_exit_bid"],
        )
    return _decision(
        advisor_input,
        "HOLD",
        "microtrade",
        quality,
        "Existing position remains within risk limits.",
        risk_flags=quality.risk_flags,
        confidence="medium",
    )


def _decision(
    advisor_input: AdvisorInput,
    action: str,
    trade_type: str,
    quality: TradeQualityResult,
    primary_reason: str,
    *,
    size_multiplier: float = 0.0,
    confidence: str | None = None,
    risk_flags: list[str] | None = None,
    hard_veto_flags: list[str] | None = None,
    recheck_minutes: int = 1,
) -> AdvisorDecision:
    candidate = advisor_input.candidate_trade
    model = advisor_input.model
    side = str(candidate.get("side") or "NONE").upper()
    if action == "BUY_YES":
        side = "YES"
    elif action == "BUY_NO":
        side = "NO"
    elif side not in {"YES", "NO"}:
        side = "NONE"
    return AdvisorDecision(
        decision=action,
        trade_type=trade_type if trade_type in {"microtrade", "long_hold", "scout", "none"} else "none",
        model_key=str(model.get("model_key") or ""),
        market_ticker=str(candidate.get("market_ticker") or ""),
        bracket_label=str(candidate.get("bracket_label") or ""),
        side=side,
        confidence=confidence or _confidence(quality.score),
        trade_quality_score=int(quality.score),
        recommended_size_multiplier=max(0.0, min(1.0, size_multiplier)),
        primary_reason=primary_reason,
        supporting_reasons=[quality.explanation],
        risk_flags=_dedupe(risk_flags or quality.risk_flags),
        hard_veto_flags=_dedupe(hard_veto_flags or quality.hard_veto_flags),
        requires_validator_approval=True,
        should_recheck_after_minutes=max(0, int(recheck_minutes)),
        human_readable_summary=primary_reason,
    )


def _confidence(score: int) -> str:
    if score >= 75:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def _size_multiplier(score: int) -> float:
    if score >= 90:
        return 1.0
    if score >= 75:
        return 0.75
    if score >= 60:
        return 0.4
    return 0.0


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
