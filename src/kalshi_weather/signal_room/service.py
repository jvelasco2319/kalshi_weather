from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from kalshi_weather.model.lax_high_temp import current_lax_market_date
from kalshi_weather.signal_room.api_models import (
    CaptureHealth,
    DecisionState,
    EventState,
    EventSummary,
    GateState,
    HealthResponse,
    MarketRow,
    ModelSlot,
    ReadinessState,
    RiskSnapshot,
    SignalRoomSnapshot,
    SignalRoomTimelinePoint,
    StrategyState,
)
from kalshi_weather.signal_room.evaluation import evaluate_validation_snapshot
from kalshi_weather.signal_room.repository import SignalRoomReadRepository
from kalshi_weather.strategy_current.config import StrategyConfig, load_strategy_config
from kalshi_weather.strategy_current.reason_codes import NO_TRADE_CAPTURE_INCOMPLETE
from kalshi_weather.strategy_current.registry import (
    CANONICAL_MODEL_KEYS,
    canonicalize_model_key,
    strategy_model_by_key,
)

NO_TRADE_PROBABILITY_UNCALIBRATED = "NO_TRADE_PROBABILITY_UNCALIBRATED"
NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE = "NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE"
NO_RECORDER_SNAPSHOT_FOR_TARGET = "NO_RECORDER_SNAPSHOT_FOR_TARGET"

MODEL_COLORS = {
    "ecmwf_ifs": "#5B8FF9",
    "gfs013": "#F6BD16",
    "gfs_seamless": "#E8684A",
    "nam": "#6AA84F",
    "nbm": "#C66DD4",
}
MODEL_LABELS = {
    "ecmwf_ifs": "ECMWF IFS",
    "gfs013": "GFS 0.13",
    "gfs_seamless": "GFS Seamless",
    "nam": "NAM",
    "nbm": "NBM",
}


class SignalRoomService:
    def __init__(
        self,
        *,
        repository: SignalRoomReadRepository,
        config: StrategyConfig | None = None,
    ) -> None:
        self.repository = repository
        self.config = config or load_strategy_config()

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="healthy" if self.repository.database_present else "not_ready",
            generated_at=_now(),
            database_present=self.repository.database_present,
            strategy_id=self.config.strategy_id,
            mode="shadow",
        )

    def list_events(self) -> list[EventSummary]:
        rows = self.repository.list_events()
        validation_rows = self.repository.list_validation_events()
        if not rows and not validation_rows:
            target = current_lax_market_date()
            return [
                EventSummary(
                    ticker=_event_ticker(self.config.series, target),
                    target_date=target,
                    station=self.config.station,
                    status="open",
                )
            ]
        by_date: dict[date, EventSummary] = {}
        for row in rows:
            target = date.fromisoformat(str(row["target_date_local"]))
            by_date[target] = EventSummary(
                ticker=_event_ticker(self.config.series, target),
                target_date=target,
                station=self.config.station,
                status="open",
            )
        for row in validation_rows:
            target = date.fromisoformat(str(row["target_date"]))
            by_date.setdefault(
                target,
                EventSummary(
                    ticker=_event_ticker(str(row.get("series") or self.config.series), target),
                    target_date=target,
                    station=str(row.get("station") or self.config.station),
                    status="open",
                ),
            )
        return [by_date[target] for target in sorted(by_date, reverse=True)]

    def latest_snapshot(
        self,
        *,
        event_ticker: str | None = None,
        target_date: date | None = None,
        as_of: datetime | None = None,
    ) -> SignalRoomSnapshot:
        target = target_date or current_lax_market_date()
        generated_at = _now()
        latest_decision = self.repository.latest_decision(target, as_of=as_of)
        manifest = self.repository.latest_capture_manifest(target)
        model_rows = self.repository.model_state_rows(target_date=target, as_of=as_of)
        validation_snapshot = self.repository.latest_validation_snapshot(target, as_of=as_of)
        if latest_decision is None and not model_rows and validation_snapshot is not None:
            return self._validation_snapshot(
                validation_snapshot,
                event_ticker=event_ticker,
            )
        models = _model_slots(model_rows)
        gates = _gates(manifest, model_rows)
        latest_validation_target = _latest_validation_target(self.repository.list_validation_events())
        missing_requested_validation = (
            latest_decision is None
            and not model_rows
            and validation_snapshot is None
            and latest_validation_target is not None
        )
        if missing_requested_validation:
            gates.insert(
                0,
                GateState(
                    code=NO_RECORDER_SNAPSHOT_FOR_TARGET,
                    label="Recorder snapshot",
                    severity="block",
                    detail=(
                        f"No validation recorder snapshot exists for {target.isoformat()}. "
                        f"Latest journal target is {latest_validation_target.isoformat()}."
                    ),
                ),
            )
        readiness = _readiness(gates, model_rows, manifest)
        decision = _decision_state(latest_decision, generated_at)
        if missing_requested_validation:
            decision = DecisionState(
                evaluated_at=generated_at,
                status="DATA_INCOMPLETE",
                reason_code=NO_RECORDER_SNAPSHOT_FOR_TARGET,
                reason_text=(
                    f"No recorder snapshot exists for {target.isoformat()} in the selected journal. "
                    f"Start the recorder for {target.isoformat()} or point the dashboard at the journal it is writing."
                ),
            )
        event = EventState(
            ticker=event_ticker or _event_ticker(self.config.series, target),
            target_date=target,
            station=self.config.station,
            status="open",
        )
        revision = _revision(
            {
                "target_date": target.isoformat(),
                "strategy_id": self.config.strategy_id,
                "config_hash": self.config.config_hash,
                "latest_decision": latest_decision,
                "manifest": manifest,
                "model_rows": model_rows,
            }
        )
        return SignalRoomSnapshot(
            revision=revision,
            generated_at=generated_at,
            event=event,
            strategy=self._strategy_state(),
            decision=decision,
            risk=RiskSnapshot(active_roi_hurdle=0.15),
            models=models,
            gates=gates,
            capture_health=_capture_health_payload("strategy_current", gates),
            readiness=readiness,
            market=[],
            banner=(
                f"No validation recorder snapshot found for {target.isoformat()}; "
                f"latest journal target is {latest_validation_target.isoformat()}."
                if missing_requested_validation and latest_validation_target is not None
                else None if self.repository.database_present else "No persisted current-strategy decisions found yet."
            ),
        )

    def timeline(
        self,
        *,
        target_date: date,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[SignalRoomTimelinePoint]:
        rows = self.repository.timeline(target_date=target_date, start=start, end=end, limit=limit)
        points: list[SignalRoomTimelinePoint] = []
        if not rows:
            return [
                _validation_timeline_point(row)
                for row in self.repository.validation_timeline(
                    target_date=target_date,
                    start=start,
                    end=end,
                    limit=limit,
                )
            ]
        for row in rows:
            source_ids = _json_list(row.get("source_ids_json"))
            payload = {
                "decision_id": row.get("decision_id"),
                "evaluated_at": row.get("evaluated_at_utc"),
                "reason_code": row.get("reason_code"),
                "source_ids": source_ids,
            }
            points.append(
                SignalRoomTimelinePoint(
                    evaluated_at=_parse_dt(str(row["evaluated_at_utc"])),
                    observed_high_f=None,
                    model_states={key: None for key in CANONICAL_MODEL_KEYS},
                    decision_status="NO_TRADE",
                    reason_code=str(row.get("reason_code") or NO_TRADE_CAPTURE_INCOMPLETE),
                    revision=_revision(payload),
                    source_ids=source_ids,
                )
            )
        return points

    def capture_health(self, event_ticker: str, target_date: date | None = None) -> CaptureHealth:
        target = target_date or current_lax_market_date()
        manifest = self.repository.latest_capture_manifest(target)
        if manifest is None:
            validation_snapshot = self.repository.latest_validation_snapshot(target)
            if validation_snapshot is not None:
                snapshot_id = int(validation_snapshot["id"])
                model_rows = self.repository.validation_model_rows(snapshot_id)
                market_rows = self.repository.validation_market_rows(snapshot_id)
                observation_rows = self.repository.validation_observation_rows(snapshot_id)
                models = _validation_model_slots(
                    model_rows,
                    _parse_dt(str(validation_snapshot["captured_utc"])),
                )
                evaluation = evaluate_validation_snapshot(
                    snapshot_row=validation_snapshot,
                    model_rows=model_rows,
                    market_rows=market_rows,
                    observation_rows=observation_rows,
                    model_slots=models,
                    config=self.config,
                )
                gates = _with_validation_source_gate(evaluation.gates)
                status = "invalid" if any(gate.severity == "block" for gate in gates) else "warning"
                return CaptureHealth(
                    event_ticker=event_ticker,
                    status=status,
                    generated_at=_now(),
                    details=gates,
                )
        gates = _gates(manifest, [])
        status = "not_ready" if manifest is None else ("healthy" if manifest.get("status") == "complete" else "invalid")
        return CaptureHealth(
            event_ticker=event_ticker,
            status=status,
            generated_at=_now(),
            details=gates,
        )

    def _strategy_state(self) -> StrategyState:
        return StrategyState(
            strategy_id=self.config.strategy_id,
            mode="shadow",
            live_trading_enabled=self.config.live_trading_enabled,
            canary_enabled=self.config.canary_enabled,
            taker_enabled=self.config.taker_enabled,
            order_submission_reachable=self.config.order_submission_reachable,
            code_revision=_code_revision(),
            config_hash=self.config.config_hash,
        )

    def _validation_snapshot(
        self,
        snapshot_row: dict[str, Any],
        *,
        event_ticker: str | None,
    ) -> SignalRoomSnapshot:
        snapshot_id = int(snapshot_row["id"])
        payload = snapshot_row.get("payload") or {}
        target = date.fromisoformat(str(snapshot_row["target_date"]))
        captured_at = _parse_dt(str(snapshot_row["captured_utc"]))
        model_rows = self.repository.validation_model_rows(snapshot_id)
        market_rows = self.repository.validation_market_rows(snapshot_id)
        observation_rows = self.repository.validation_observation_rows(snapshot_id)
        event = EventState(
            ticker=event_ticker or _validation_event_ticker(payload, market_rows, self.config.series, target),
            target_date=target,
            relative_day=_relative_day(target),
            station=str(snapshot_row.get("station") or self.config.station),
            status=_validation_event_status(market_rows),
        )
        models = _validation_model_slots(model_rows, captured_at)
        evaluation = evaluate_validation_snapshot(
            snapshot_row=snapshot_row,
            model_rows=model_rows,
            market_rows=market_rows,
            observation_rows=observation_rows,
            model_slots=models,
            config=self.config,
        )
        gates = _with_validation_source_gate(evaluation.gates)
        revision = _revision(
            {
                "source": "validation_journal",
                "snapshot_id": snapshot_id,
                "captured_utc": snapshot_row.get("captured_utc"),
                "target_date": snapshot_row.get("target_date"),
                "evaluation_id": evaluation.probability_lab.get("evaluation_id"),
            }
        )
        return SignalRoomSnapshot(
            revision=revision,
            generated_at=_now(),
            event=event,
            strategy=self._strategy_state(),
            decision=evaluation.decision,
            risk=evaluation.risk,
            models=models,
            gates=gates,
            capture_health=_capture_health_payload("validation_journal", gates),
            readiness=_validation_readiness(gates, models, market_rows),
            market=evaluation.market,
            probability_lab=evaluation.probability_lab,
            explainability=evaluation.explainability,
            banner=(
                "Live recorder snapshot loaded from validation journal. "
                "Probabilities and economics are quote-based shadow outputs; order submission is disabled."
            ),
        )


def _model_slots(model_rows: list[dict[str, Any]]) -> list[ModelSlot]:
    latest_by_model: dict[str, dict[str, Any]] = {}
    for row in model_rows:
        latest_by_model.setdefault(str(row.get("model_key")), row)
    slots: list[ModelSlot] = []
    for index, model_key in enumerate(CANONICAL_MODEL_KEYS, start=1):
        model = strategy_model_by_key(model_key)
        row = latest_by_model.get(model_key)
        slots.append(
            ModelSlot(
                model_key=model_key,
                label=MODEL_LABELS[model_key],
                display_order=index,
                color=MODEL_COLORS[model_key],
                state_f=_float_or_none(row, "raw_live_state_f"),
                remaining_window_max_f=_float_or_none(row, "future_max_f"),
                observed_floor_f=_float_or_none(row, "observed_max_f"),
                prior_weight=str(model.prior_weight),
                effective_weight=_str_or_none(row, "reliability_weight"),
                maturity_completed_dates=_int_or_none(row, "residual_history_count"),
                maturity_required_dates=10 if model_key == "nbm" else 30,
                maturity_status="provisional"
                if model_key == "nbm"
                else ("mature" if row is not None else "excluded"),
                feed_status="missing" if row is None else "healthy",
                strict_as_of_valid=None if row is None else True,
                status_detail="No persisted current-strategy model state" if row is None else None,
            )
        )
    return slots


def _gates(manifest: dict[str, Any] | None, model_rows: list[dict[str, Any]]) -> list[GateState]:
    gates = [
        GateState(
            code="ORDER_PATH_DISABLED",
            label="Live order path",
            severity="pass",
            detail="Dashboard is read-only and order submission is disabled.",
        )
    ]
    if manifest is None:
        gates.append(
            GateState(
                code=NO_TRADE_CAPTURE_INCOMPLETE,
                label="Capture completeness",
                severity="block",
                detail="No complete current-strategy capture manifest is available.",
            )
        )
    else:
        gates.append(
            GateState(
                code="CAPTURE_MANIFEST_STATUS",
                label="Capture completeness",
                severity="pass" if manifest.get("status") == "complete" else "block",
                detail=f"Latest manifest status is {manifest.get('status')}.",
            )
        )
    if len({row.get("model_key") for row in model_rows}) < 4:
        gates.append(
            GateState(
                code="NO_TRADE_TOO_FEW_MODELS",
                label="Five-model availability",
                severity="block",
                detail="Fewer than four tradable current-strategy model states are persisted.",
            )
        )
    return sorted(gates, key=lambda gate: {"block": 0, "warning": 1, "info": 2, "pass": 3}[gate.severity])


def _readiness(
    gates: list[GateState],
    model_rows: list[dict[str, Any]],
    manifest: dict[str, Any] | None,
) -> ReadinessState:
    feed_keys = {str(row.get("model_key")) for row in model_rows}
    family_count = len({strategy_model_by_key(key).family for key in feed_keys if key in CANONICAL_MODEL_KEYS})
    return ReadinessState(
        tradable_feed_count=len(feed_keys),
        required_tradable_feed_count=4,
        independent_family_count=family_count,
        required_independent_family_count=3,
        nbm_completed_dates=0,
        nbm_next_maturity_threshold=10,
        orderbook_sequence_valid=bool(manifest and manifest.get("book_sequence_valid")),
        orderbook_depth_available=False,
        fee_schedule_verified=False,
        settlement_rules_verified=False,
        capture_health_status="invalid" if any(gate.severity == "block" for gate in gates) else "healthy",
    )


def _decision_state(row: dict[str, Any] | None, generated_at: datetime) -> DecisionState:
    if row is None:
        return DecisionState(
            evaluated_at=generated_at,
            status="DATA_INCOMPLETE",
            reason_code=NO_TRADE_CAPTURE_INCOMPLETE,
            reason_text="No persisted current-strategy decision is available yet.",
        )
    return DecisionState(
        evaluated_at=_parse_dt(str(row.get("evaluated_at_utc"))),
        status="NO_TRADE" if str(row.get("reason_code", "")).startswith("NO_TRADE") else "SHADOW_ONLY",
        reason_code=str(row.get("reason_code") or NO_TRADE_CAPTURE_INCOMPLETE),
        reason_text="Persisted current-strategy decision.",
    )


def _validation_model_slots(model_rows: list[dict[str, Any]], captured_at: datetime) -> list[ModelSlot]:
    rows_by_model: dict[str, dict[str, Any]] = {}
    for row in model_rows:
        try:
            key = canonicalize_model_key(str(row.get("model_key")))
        except ValueError:
            continue
        current = rows_by_model.get(key)
        if current is None or _validation_row_rank(row) > _validation_row_rank(current):
            rows_by_model[key] = row

    slots: list[ModelSlot] = []
    for index, model_key in enumerate(CANONICAL_MODEL_KEYS, start=1):
        model = strategy_model_by_key(model_key)
        row = rows_by_model.get(model_key)
        status = str(row.get("fetch_status")) if row is not None else "missing"
        healthy = status == "ok"
        slots.append(
            ModelSlot(
                model_key=model_key,
                label=MODEL_LABELS[model_key],
                display_order=index,
                color=MODEL_COLORS[model_key],
                state_f=_float_or_none(row, "estimated_high_f"),
                remaining_window_max_f=_float_or_none(row, "estimated_high_f"),
                observed_floor_f=None,
                mapped_bracket=_display_bracket(_str_or_none(row, "estimated_bracket")),
                prior_weight=str(model.prior_weight),
                effective_weight=None,
                maturity_completed_dates=0,
                maturity_required_dates=10 if model_key == "nbm" else 30,
                maturity_status="provisional"
                if model_key == "nbm"
                else ("mature" if healthy else "excluded"),
                source_available_at=captured_at if row is not None else None,
                received_at=captured_at if row is not None else None,
                age_seconds=max(0, int((_now() - captured_at).total_seconds()))
                if row is not None
                else None,
                strict_as_of_valid=True if row is not None else None,
                feed_status=_validation_feed_status(status),
                status_detail=_validation_status_detail(row),
            )
        )
    return slots


def _validation_gates(
    *,
    model_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> list[GateState]:
    canonical_ok = {
        canonicalize_model_key(str(row.get("model_key")))
        for row in model_rows
        if _is_strategy_model_key(str(row.get("model_key"))) and row.get("fetch_status") == "ok"
    }
    gates = [
        GateState(
            code="ORDER_PATH_DISABLED",
            label="Live order path",
            severity="pass",
            detail="Dashboard is read-only and order submission is disabled.",
        ),
        GateState(
            code="VALIDATION_JOURNAL_SOURCE",
            label="Recorder data source",
            severity="info",
            detail="Displaying the existing validation journal written by record-weather-market-loop.",
        ),
        GateState(
            code=NO_TRADE_PROBABILITY_UNCALIBRATED,
            label="Probability calibration",
            severity="block",
            detail=(
                "Live model and market data are visible, but calibrated current-strategy "
                "probabilities/economics are not persisted for this snapshot."
            ),
        ),
        GateState(
            code=NO_TRADE_EXECUTABLE_BOOK_UNAVAILABLE,
            label="Executable book",
            severity="block",
            detail=(
                "Validation recorder uses observe-only REST/top-of-book data; no sequence-valid "
                "ten-level executable book is available."
            ),
        ),
    ]
    if len(canonical_ok) >= 4:
        gates.append(
            GateState(
                code="FIVE_MODEL_RECORDER_VALUES_AVAILABLE",
                label="Five-model display",
                severity="pass",
                detail=f"{len(canonical_ok)} canonical current-strategy model estimates are available.",
            )
        )
    else:
        gates.append(
            GateState(
                code="NO_TRADE_TOO_FEW_MODELS",
                label="Five-model display",
                severity="warning",
                detail=f"{len(canonical_ok)} canonical current-strategy model estimates are available.",
            )
        )
    if market_rows:
        gates.append(
            GateState(
                code="MARKET_QUOTES_AVAILABLE",
                label="Market quotes",
                severity="info",
                detail=f"{len(market_rows)} validation market rows are available.",
            )
        )
    else:
        gates.append(
            GateState(
                code="NO_MARKET_AVAILABLE",
                label="Market quotes",
                severity="warning",
                detail="No validation market rows are available for this snapshot.",
            )
        )
    return sorted(gates, key=lambda gate: {"block": 0, "warning": 1, "info": 2, "pass": 3}[gate.severity])


def _validation_readiness(
    gates: list[GateState],
    models: list[ModelSlot],
    market_rows: list[dict[str, Any]],
) -> ReadinessState:
    healthy_keys = {model.model_key for model in models if model.feed_status == "healthy"}
    family_count = len({strategy_model_by_key(key).family for key in healthy_keys})
    settlement_verified = any(gate.code == "SETTLEMENT_RULES_VERIFIED" for gate in gates)
    has_blocks = any(gate.severity == "block" for gate in gates)
    has_warnings = any(gate.severity == "warning" for gate in gates)
    return ReadinessState(
        tradable_feed_count=len(healthy_keys),
        required_tradable_feed_count=4,
        independent_family_count=family_count,
        required_independent_family_count=3,
        nbm_completed_dates=0,
        nbm_next_maturity_threshold=10,
        orderbook_sequence_valid=False,
        orderbook_depth_available=False,
        fee_schedule_verified=bool(market_rows),
        settlement_rules_verified=settlement_verified,
        capture_health_status="invalid" if has_blocks else "warning" if has_warnings else "healthy",
    )


def _validation_decision(captured_at: datetime, leader: MarketRow | None) -> DecisionState:
    return DecisionState(
        evaluated_at=captured_at,
        status="DATA_INCOMPLETE",
        reason_code=NO_TRADE_PROBABILITY_UNCALIBRATED,
        reason_text=(
            "Recorder data is live, but calibrated current-strategy probabilities and "
            "shadow economics are not available, so no candidate can be emitted."
        ),
        focus_ticker=leader.ticker if leader else None,
        focus_bracket=leader.bracket if leader else None,
        focus_side="YES" if leader else None,
        executable_price=leader.yes_ask if leader else None,
        p_mean=None,
        p_safe=None,
        required_probability=None,
        modeled_net_roi=None,
        max_acceptable_price=None,
        proposed_quantity=None,
    )


def _validation_risk(
    models: list[ModelSlot],
    observation_rows: list[dict[str, Any]],
    leader: MarketRow | None,
) -> RiskSnapshot:
    values = [model.state_f for model in models if model.state_f is not None]
    spread = max(values) - min(values) if len(values) >= 2 else None
    observation = _latest_observation(observation_rows)
    return RiskSnapshot(
        model_spread_f=round(spread, 2) if spread is not None else None,
        active_roi_hurdle=0.15,
        adjusted_probability_hurdle=None,
        observed_high_f=_float_or_none(observation, "high_so_far_f"),
        market_leader_bracket=leader.bracket if leader else None,
        risk_multiplier=None,
        target_date_exposure_pct="0",
        daily_loss_pct="0",
    )


def _validation_market_rows(rows: list[dict[str, Any]]) -> list[MarketRow]:
    market: list[MarketRow] = []
    for row in rows:
        market.append(
            MarketRow(
                ticker=str(row.get("ticker") or ""),
                bracket=_display_bracket(str(row.get("bracket_label") or "unknown")),
                yes_bid=_dollars_from_cents(row.get("yes_bid_cents")),
                yes_ask=_dollars_from_cents(row.get("yes_ask_cents")),
                no_bid=_dollars_from_cents(row.get("no_bid_cents")),
                no_ask=_dollars_from_cents(row.get("no_ask_cents")),
                p_mean_yes=None,
                p_safe_yes=None,
                p_safe_no=None,
                required_probability_yes=None,
                modeled_net_roi_yes=None,
                max_acceptable_yes_price=None,
                model_point_support_count=None,
                eligible=False,
                candidate=False,
                status_code=NO_TRADE_PROBABILITY_UNCALIBRATED,
                settled_outcome=None,
            )
        )
    leader = _market_leader(market)
    if leader is not None:
        for row in market:
            if row.ticker == leader.ticker:
                row.candidate = True
    return market


def _validation_timeline_point(row: dict[str, Any]) -> SignalRoomTimelinePoint:
    payload = row.get("payload") or {}
    model_states = {key: None for key in CANONICAL_MODEL_KEYS}
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        key_raw = str(item.get("model_key"))
        if not _is_strategy_model_key(key_raw):
            continue
        key = canonicalize_model_key(key_raw)
        if model_states[key] is None or item.get("fetch_status") == "ok":
            value = item.get("estimated_high_f")
            model_states[key] = float(value) if value is not None else None
    observation = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
    healthy_count = sum(1 for value in model_states.values() if value is not None)
    return SignalRoomTimelinePoint(
        evaluated_at=_parse_dt(str(row["captured_utc"])),
        observed_high_f=_float_or_none(observation, "high_so_far_f"),
        model_states=model_states,
        decision_status="SHADOW_ONLY" if healthy_count >= 4 else "DATA_INCOMPLETE",
        reason_code="SHADOW_QUOTE_EVALUATED" if healthy_count >= 4 else NO_TRADE_PROBABILITY_UNCALIBRATED,
        focus_ticker=None,
        market_price=None,
        revision=_revision(
            {
                "source": "validation_journal",
                "snapshot_id": row.get("id"),
                "captured_utc": row.get("captured_utc"),
            }
        ),
        source_ids=[f"validation_snapshot:{row.get('id')}"],
    )


def _validation_event_ticker(
    payload: dict[str, Any],
    market_rows: list[dict[str, Any]],
    default_series: str,
    target: date,
) -> str:
    for row in market_rows:
        raw = _json_object(row.get("raw_json"))
        event_ticker = ((raw.get("market") or {}).get("event_ticker")) if raw else None
        if event_ticker:
            return str(event_ticker)
    return _event_ticker(str(payload.get("series") or default_series), target)


def _validation_event_status(market_rows: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("market_status") or "").lower() for row in market_rows}
    if "active" in statuses:
        return "open"
    if "settled" in statuses:
        return "settled"
    if "closed" in statuses:
        return "closed"
    return "open"


def _event_ticker(series: str, target: date) -> str:
    return f"{series}-{target:%y%b%d}".upper()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _revision(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except Exception:
        return "uncommitted"


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _float_or_none(row: dict[str, Any] | None, key: str) -> float | None:
    if row is None or row.get(key) is None:
        return None
    return float(row[key])


def _int_or_none(row: dict[str, Any] | None, key: str) -> int | None:
    if row is None or row.get(key) is None:
        return None
    return int(row[key])


def _str_or_none(row: dict[str, Any] | None, key: str) -> str | None:
    if row is None or row.get(key) is None:
        return None
    value = row[key]
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _validation_row_rank(row: dict[str, Any]) -> int:
    status = str(row.get("fetch_status") or "")
    key = str(row.get("model_key") or "")
    return (2 if status == "ok" else 1 if status == "missing" else 0) + (1 if key != "nam_conus" else 0)


def _validation_feed_status(status: str) -> str:
    if status == "ok":
        return "healthy"
    if status == "missing":
        return "missing"
    return "invalid"


def _validation_status_detail(row: dict[str, Any] | None) -> str:
    if row is None:
        return "No validation recorder row for this current-strategy model."
    raw = _json_object(row.get("raw_json"))
    if raw.get("carried_forward"):
        source_time = raw.get("source_captured_utc") or "prior snapshot"
        current_error = raw.get("current_error_message") or "latest fetch unavailable"
        return f"Carried forward successful recorder estimate from {source_time}; latest fetch: {current_error}"
    if row.get("error_message"):
        return str(row["error_message"])
    if row.get("fetch_status") == "ok":
        return "Live validation recorder estimate; included in shadow probability lab."
    return f"Recorder status: {row.get('fetch_status') or 'missing'}"


def _with_validation_source_gate(gates: list[GateState]) -> list[GateState]:
    return sorted(
        [
            GateState(
                code="VALIDATION_JOURNAL_SOURCE",
                label="Recorder data source",
                severity="info",
                detail="Displaying the validation journal written by record-weather-market-loop.",
            ),
            *gates,
        ],
        key=lambda gate: {"block": 0, "warning": 1, "info": 2, "pass": 3}[gate.severity],
    )


def _is_strategy_model_key(value: str) -> bool:
    try:
        canonicalize_model_key(value)
    except ValueError:
        return False
    return True


def _display_bracket(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() == "none":
        return None
    return text if text.endswith(" F") else f"{text} F"


def _dollars_from_cents(value: Any) -> str | None:
    if value is None:
        return None
    dollars = (Decimal(str(value)) / Decimal("100")).quantize(Decimal("0.01"))
    return str(dollars)


def _latest_observation(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    with_high = [row for row in rows if row.get("high_so_far_f") is not None]
    return (with_high or rows)[-1]


def _market_leader(rows: list[MarketRow]) -> MarketRow | None:
    if not rows:
        return None

    def score(row: MarketRow) -> Decimal:
        if row.yes_bid is not None:
            return Decimal(row.yes_bid)
        if row.no_ask is not None:
            return Decimal("1") - Decimal(row.no_ask)
        if row.yes_ask is not None:
            return Decimal(row.yes_ask)
        return Decimal("-1")

    ranked = [row for row in rows if score(row) >= 0]
    return max(ranked, key=score) if ranked else None


def _capture_health_payload(source: str, gates: list[GateState]) -> dict[str, object]:
    return {
        "source": source,
        "status": "invalid" if any(gate.severity == "block" for gate in gates) else "healthy",
        "blocking_codes": [gate.code for gate in gates if gate.severity == "block"],
    }


def _relative_day(target: date) -> str:
    today = current_lax_market_date()
    if target == today:
        return "today"
    if (target - today).days == 1:
        return "tomorrow"
    return "other"


def _latest_validation_target(rows: list[dict[str, Any]]) -> date | None:
    targets: list[date] = []
    for row in rows:
        try:
            targets.append(date.fromisoformat(str(row["target_date"])))
        except (KeyError, ValueError):
            continue
    return max(targets) if targets else None


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in payload] if isinstance(payload, list) else []
