from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol


class TraderLLMClient(Protocol):
    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        """Return raw LLM text. The caller parses and validates JSON."""
        ...


class DryRunTraderLLMClient:
    """No-key mode. Always returns HOLD."""

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        return json.dumps(
            {
                "schema_version": "1.0",
                "action": "HOLD",
                "selected_candidate_id": None,
                "contract_ticker": None,
                "bracket": None,
                "side": None,
                "limit_price_cents": None,
                "max_contracts": 0,
                "estimated_edge_cents": 0.0,
                "confidence": "low",
                "time_horizon": "no_trade",
                "trader_thesis": "Dry-run mode. No LLM call made.",
                "why_this_trade": "No trade executed in dry-run mode.",
                "why_not_most_likely_bracket": "Dry-run mode does not choose a bracket.",
                "why_not_other_side": "Dry-run mode does not choose a side.",
                "exit_plan": {
                    "take_profit_cents": None,
                    "stop_loss_cents": None,
                    "close_if_edge_below_cents": None,
                    "close_if_model_probability_below": None,
                    "max_hold_minutes": None,
                    "invalidate_if": "Dry-run only.",
                },
                "risk_notes": "Fake-money only.",
                "no_trade_reason": "dry-run mode",
            }
        )


class StaticTraderLLMClient:
    """Return a fixed response. Useful for tests."""

    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        return self.response


class MockTraderLLMClient:
    """Deterministic fake trader for tests and no-key local demos.

    Selects the eligible BUY candidate with the highest fee-adjusted edge.
    If no eligible BUY exists, returns HOLD.
    """

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        candidates = user_payload.get("candidate_trades", [])
        buy_candidates = [
            c
            for c in candidates
            if c.get("eligible") is True and c.get("action") == "BUY" and c.get("entry_price_cents") is not None
        ]
        if not buy_candidates:
            return DryRunTraderLLMClient().complete(system_prompt, user_payload)

        best = max(buy_candidates, key=lambda c: float(c.get("fee_adjusted_edge_cents") or 0.0))
        max_contracts = int(best.get("max_contracts") or 0)
        chosen_contracts = max(1, min(max_contracts, 10)) if max_contracts > 0 else 0
        response = {
            "schema_version": "1.0",
            "action": "PLACE_FAKE_LIMIT_BUY",
            "selected_candidate_id": best["candidate_id"],
            "contract_ticker": best["contract_ticker"],
            "bracket": best["bracket_label"],
            "side": best["side"],
            "limit_price_cents": best["entry_price_cents"],
            "max_contracts": chosen_contracts,
            "estimated_edge_cents": best["fee_adjusted_edge_cents"],
            "confidence": "medium",
            "time_horizon": "scalp",
            "trader_thesis": "Mock trader selected the highest fee-adjusted edge candidate.",
            "why_this_trade": "This candidate has the best positive fee-adjusted edge among eligible buy candidates.",
            "why_not_most_likely_bracket": "The mock ranks by edge, not by most likely final bracket.",
            "why_not_other_side": "The other side had lower fee-adjusted edge or was ineligible.",
            "exit_plan": {
                "take_profit_cents": min(99, int(best["entry_price_cents"]) + 5),
                "stop_loss_cents": max(1, int(best["entry_price_cents"]) - 5),
                "close_if_edge_below_cents": 1.0,
                "close_if_model_probability_below": None,
                "max_hold_minutes": 45,
                "invalidate_if": "Updated model distribution removes the edge or observed weather moves against the thesis.",
            },
            "risk_notes": "Fake-money only; size capped by candidate max_contracts and mock limit.",
            "no_trade_reason": None,
        }
        return json.dumps(response)


class OllamaTraderLLMClient:
    """Local Ollama-backed trader client.

    If Ollama is unavailable, this returns a safe HOLD decision so paper trading
    does not proceed on missing or malformed provider output.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 60,
        temperature: float = 0.0,
    ) -> None:
        self.host = (host or os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self.model = model or os.getenv("KALSHI_LLM_MODEL") or os.getenv("OLLAMA_MODEL") or "gpt-oss:120b"
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self._resolved_model: str | None = None

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        model = self._resolve_model()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2, sort_keys=True)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature},
        }
        try:
            response = _post_json(f"{self.host}/api/chat", payload, timeout_seconds=self.timeout_seconds)
            content = ((response.get("message") or {}).get("content") or "").strip()
            return content or _hold_json("ollama returned an empty response")
        except Exception as exc:  # noqa: BLE001
            return _hold_json(f"ollama unavailable: {exc}")

    def _resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        try:
            available = _fetch_ollama_model_names(self.host, timeout_seconds=min(self.timeout_seconds, 10))
            self._resolved_model = resolve_ollama_model_name(self.model, available)
        except Exception:  # noqa: BLE001
            self._resolved_model = self.model
        return self._resolved_model


class OpenAITraderLLMClient:
    """OpenAI-compatible chat-completions trader client.

    This is opt-in and requires OPENAI_API_KEY. Missing configuration becomes
    HOLD rather than a trade.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        temperature: float = 0.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> str:
        if not self.api_key:
            return _hold_json("openai provider not configured: OPENAI_API_KEY missing")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, indent=2, sort_keys=True)},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            response = _post_json(
                f"{self.base_url}/chat/completions",
                payload,
                timeout_seconds=self.timeout_seconds,
                headers=headers,
            )
            content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return content or _hold_json("openai returned an empty response")
        except Exception as exc:  # noqa: BLE001
            return _hold_json(f"openai unavailable: {exc}")


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc


def _get_json(
    url: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc


def _fetch_ollama_model_names(host: str, *, timeout_seconds: int) -> set[str]:
    payload = _get_json(f"{host}/api/tags", timeout_seconds=timeout_seconds)
    return {str(model.get("name")) for model in payload.get("models", []) if model.get("name")}


def resolve_ollama_model_name(requested: str, available: set[str]) -> str:
    """Resolve common local/cloud Ollama aliases without making commands brittle."""
    if requested in available:
        return requested

    aliases = {
        "gpt-oss:120b": ["gpt-oss:120b-cloud"],
        "gpt-oss:120b-cloud": ["gpt-oss:120b"],
    }
    candidates = aliases.get(requested, [])
    if not requested.endswith("-cloud"):
        candidates.append(f"{requested}-cloud")
    else:
        candidates.append(requested.removesuffix("-cloud"))

    for candidate in candidates:
        if candidate in available:
            return candidate
    return requested


def _hold_json(reason: str) -> str:
    payload = json.loads(DryRunTraderLLMClient().complete("", {}))
    payload["trader_thesis"] = "Provider fallback HOLD."
    payload["no_trade_reason"] = reason
    payload["risk_notes"] = "Fake-money only; provider failure falls back to HOLD."
    return json.dumps(payload)
