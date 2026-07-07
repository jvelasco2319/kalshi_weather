from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import requests

from kalshi_weather.llm.json_guard import (
    parse_llm_json,
    safe_fallback_decision,
    validate_trade_decision_json,
)
from kalshi_weather.llm.prompts import load_system_prompt
from kalshi_weather.llm.schemas import (
    DEFAULT_LLM_MODEL,
    LLM_PROVIDER_OLLAMA,
    LLM_TRADE_DECISION_SCHEMA,
    LLMRawResponse,
    LLMTradeDecision,
)

DEFAULT_OLLAMA_HOST = "http://localhost:11434"


class OllamaLLMProvider:
    provider_name = LLM_PROVIDER_OLLAMA

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 60,
        max_retries: int = 2,
        temperature: float = 0.0,
        api_key: str | None = None,
        session: requests.Session | None = None,
        fallback_action: str = "WAIT",
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        self.model = (
            model
            or os.getenv("KALSHI_LLM_MODEL")
            or os.getenv("OLLAMA_MODEL")
            or DEFAULT_LLM_MODEL
        )
        self.timeout_seconds = int(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.temperature = float(temperature)
        self.api_key = api_key if api_key is not None else os.getenv("OLLAMA_API_KEY")
        self.session = session or requests.Session()
        self.fallback_action = fallback_action.upper()

    def chat(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        response_schema: dict[str, Any] | None = None,
    ) -> LLMRawResponse:
        request_id = uuid.uuid4().hex
        endpoint = f"{self.host}/api/chat"
        start = time.perf_counter()
        payload = self._chat_payload(system_prompt, user_payload, response_schema)
        last_error: str | None = None
        format_attempts: list[dict[str, Any]] = [payload]
        if "format" in payload:
            fallback_payload = dict(payload)
            fallback_payload.pop("format", None)
            format_attempts.append(fallback_payload)

        for body in format_attempts:
            for _attempt in range(self.max_retries + 1):
                try:
                    response = self.session.request(
                        "POST",
                        endpoint,
                        json=body,
                        headers=self._headers(),
                        timeout=self.timeout_seconds,
                    )
                    if hasattr(response, "raise_for_status"):
                        response.raise_for_status()
                    response_payload = response.json()
                    raw_text = _extract_ollama_text(response_payload)
                    latency_ms = round((time.perf_counter() - start) * 1000)
                    try:
                        parsed_json = parse_llm_json(raw_text)
                    except Exception as exc:  # noqa: BLE001
                        return LLMRawResponse(
                            provider=self.provider_name,
                            model=self.model,
                            request_id=request_id,
                            raw_text=raw_text,
                            parsed_json=None,
                            latency_ms=latency_ms,
                            success=False,
                            error=f"invalid LLM JSON: {exc}",
                        )
                    return LLMRawResponse(
                        provider=self.provider_name,
                        model=self.model,
                        request_id=request_id,
                        raw_text=raw_text,
                        parsed_json=parsed_json,
                        latency_ms=latency_ms,
                        success=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    continue

        latency_ms = round((time.perf_counter() - start) * 1000)
        return LLMRawResponse(
            provider=self.provider_name,
            model=self.model,
            request_id=request_id,
            raw_text="",
            parsed_json=None,
            latency_ms=latency_ms,
            success=False,
            error=last_error or "Ollama request failed",
        )

    def advise_trade(self, trade_snapshot: dict[str, Any]) -> LLMTradeDecision:
        decision, _raw = self.advise_trade_with_response(trade_snapshot)
        return decision

    def advise_trade_with_response(
        self,
        trade_snapshot: dict[str, Any],
    ) -> tuple[LLMTradeDecision, LLMRawResponse]:
        raw = self.chat(load_system_prompt(), trade_snapshot, response_schema=LLM_TRADE_DECISION_SCHEMA)
        if not raw.success or raw.parsed_json is None:
            hard_veto_flag = "invalid_llm_json" if raw.raw_text else "llm_provider_unavailable"
            return (
                safe_fallback_decision(
                    f"Ollama advisor unavailable: {raw.error or 'no valid JSON response'}",
                    fallback_input=trade_snapshot,
                    fallback_action=self.fallback_action,
                    hard_veto_flag=hard_veto_flag,
                ),
                raw,
            )
        try:
            return validate_trade_decision_json(raw.parsed_json), raw
        except Exception as exc:  # noqa: BLE001
            return (
                safe_fallback_decision(
                    f"invalid LLM trade decision: {exc}",
                    fallback_input=trade_snapshot,
                    fallback_action=self.fallback_action,
                ),
                raw,
            )

    def _chat_payload(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        response_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, default=str, sort_keys=True),
                },
            ],
            "stream": False,
            "options": {"temperature": self.temperature},
        }
        payload["format"] = response_schema or "json"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _extract_ollama_text(response_payload: dict[str, Any]) -> str:
    message = response_payload.get("message")
    if isinstance(message, dict) and message.get("content") is not None:
        return str(message["content"])
    if response_payload.get("response") is not None:
        return str(response_payload["response"])
    return json.dumps(response_payload, default=str)
