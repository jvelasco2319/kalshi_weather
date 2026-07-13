from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from kalshi_weather.config import load_settings
from kalshi_weather.signal_room.api_models import SignalRoomSnapshot
from kalshi_weather.signal_room.explainability import (
    canonical_explainability_snapshot,
    evaluation_index_item,
)
from kalshi_weather.signal_room.repository import SignalRoomReadRepository
from kalshi_weather.signal_room.service import SignalRoomService

PACKAGE_DIR = Path(__file__).parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(
    *,
    sqlite_path: str | Path | None = None,
    sample_fixture_path: str | Path | None = None,
    poll_seconds: int = 2,
    mode: str = "live",
    target_date: date | None = None,
) -> FastAPI:
    settings = load_settings()
    db_path = sqlite_path if sqlite_path is not None else settings.sqlite_path
    service = SignalRoomService(repository=SignalRoomReadRepository(db_path))
    sample_snapshots = _load_sample_snapshots(sample_fixture_path)

    app = FastAPI(title="KLAX Signal Room", version="1")
    app.state.signal_room_service = service
    app.state.signal_room_sample_snapshots = sample_snapshots
    app.state.signal_room_poll_seconds = poll_seconds
    app.state.signal_room_mode = mode
    app.state.signal_room_target_date = target_date
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATE_DIR)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "mode": mode,
                "poll_seconds": poll_seconds,
                "target_date": target_date.isoformat() if target_date else "",
                "sample_mode": bool(sample_snapshots),
            },
        )

    @app.get("/strategy/probability-lab", response_class=HTMLResponse)
    def probability_lab_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "probability_lab.html",
            {
                "mode": mode,
                "poll_seconds": poll_seconds,
                "target_date": target_date.isoformat() if target_date else "",
                "sample_mode": bool(sample_snapshots),
            },
        )

    @app.get("/api/v1/signal-room/health")
    def health() -> dict[str, Any]:
        return service.health().model_dump(mode="json")

    @app.get("/api/v1/signal-room/events")
    def events() -> list[dict[str, Any]]:
        if sample_snapshots:
            seen: set[str] = set()
            sample_events: list[dict[str, Any]] = []
            for snap in sample_snapshots:
                event = dict(snap["event"])
                if str(event["ticker"]) in seen:
                    continue
                seen.add(str(event["ticker"]))
                sample_events.append(event)
            return sample_events
        if target_date is not None:
            return [
                {
                    "ticker": f"{service.config.series}-{target_date:%y%b%d}".upper(),
                    "target_date": target_date.isoformat(),
                    "station": service.config.station,
                    "status": "open",
                }
            ]
        return [event.model_dump(mode="json") for event in service.list_events()]

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/v1/signal-room/events/{event_ticker}/snapshot")
    def snapshot(
        event_ticker: str,
        response: Response,
        as_of: datetime | None = Query(None),
        target: date | None = Query(None),
        if_none_match: str | None = Header(None, alias="If-None-Match"),
    ) -> Response:
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=target or target_date,
            as_of=as_of,
        )
        etag = f'"{snap.revision}"'
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return JSONResponse(snap.model_dump(mode="json"), headers={"ETag": etag})

    @app.get("/api/v1/signal-room/events/{event_ticker}/timeline")
    def timeline(
        event_ticker: str,
        start: datetime | None = Query(None),
        end: datetime | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
        target: date | None = Query(None),
    ) -> list[dict[str, Any]]:
        if sample_snapshots:
            filtered = sample_snapshots
            if start is not None:
                start_utc = _ensure_aware_utc(start)
                filtered = [
                    snap
                    for snap in filtered
                    if _iso_dt(str(snap["decision"]["evaluated_at"])) >= start_utc
                ]
            if end is not None:
                end_utc = _ensure_aware_utc(end)
                filtered = [
                    snap
                    for snap in filtered
                    if _iso_dt(str(snap["decision"]["evaluated_at"])) <= end_utc
                ]
            points = [
                {
                    "evaluated_at": snap["decision"]["evaluated_at"],
                    "observed_high_f": snap["risk"].get("observed_high_f"),
                    "model_states": {
                        model["model_key"]: model.get("state_f") for model in snap["models"]
                    },
                    "decision_status": snap["decision"]["status"],
                    "reason_code": snap["decision"]["reason_code"],
                    "focus_ticker": snap["decision"].get("focus_ticker"),
                    "market_price": snap["decision"].get("executable_price"),
                    "revision": snap["revision"],
                    "source_ids": [],
                }
                for snap in filtered[:limit]
            ]
            return points
        selected_date = target or target_date
        if selected_date is None:
            selected_date = service.list_events()[0].target_date
        return [
            point.model_dump(mode="json")
            for point in service.timeline(
                target_date=selected_date,
                start=start,
                end=end,
                limit=limit,
            )
        ]

    @app.get("/api/v1/signal-room/events/{event_ticker}/capture-health")
    def capture_health(event_ticker: str, target: date | None = Query(None)) -> dict[str, Any]:
        return service.capture_health(event_ticker, target_date=target or target_date).model_dump(mode="json")

    @app.get("/api/v1/signal-room/events/{event_ticker}/probability-lab")
    def probability_lab(
        event_ticker: str,
        as_of: datetime | None = Query(None),
        target: date | None = Query(None),
    ) -> dict[str, Any]:
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=target or target_date,
            as_of=as_of,
        )
        return snap.probability_lab

    @app.get("/api/v1/signal-room/events/{event_ticker}/explainability")
    def explainability(
        event_ticker: str,
        as_of: datetime | None = Query(None),
        target: date | None = Query(None),
    ) -> dict[str, Any]:
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=target or target_date,
            as_of=as_of,
        )
        return snap.explainability

    @app.get("/api/strategy/current/events/{event_ticker}/explainability/latest")
    def strategy_explainability_latest(
        event_ticker: str,
        target: date | None = Query(None),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            as_of=None,
        )
        return canonical_explainability_snapshot(
            snap,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )

    @app.get("/api/strategy/current/events/{event_ticker}/weighting/latest")
    def strategy_weighting_latest(
        event_ticker: str,
        target: date | None = Query(None),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            as_of=None,
        )
        return _weighting_api_snapshot(snap)

    @app.get("/api/strategy/current/events/{event_ticker}/probability-lab/latest")
    def strategy_probability_lab_latest(
        event_ticker: str,
        target: date | None = Query(None),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _snapshot_for_request(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            as_of=None,
        )
        return _probability_lab_bundle(
            snap,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )

    @app.get("/api/strategy/current/events/{event_ticker}/explainability")
    def strategy_explainability_by_id(
        event_ticker: str,
        evaluation_id: str = Query(...),
        target: date | None = Query(None),
        limit: int = Query(500, ge=1, le=500),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _find_snapshot_by_evaluation_id(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            evaluation_id=evaluation_id,
            limit=limit,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )
        if snap is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return canonical_explainability_snapshot(
            snap,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )

    @app.get("/api/strategy/current/events/{event_ticker}/weighting")
    def strategy_weighting_by_id(
        event_ticker: str,
        evaluation_id: str = Query(...),
        target: date | None = Query(None),
        limit: int = Query(500, ge=1, le=500),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _find_snapshot_by_evaluation_id(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            evaluation_id=evaluation_id,
            limit=limit,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )
        if snap is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return _weighting_api_snapshot(snap)

    @app.get("/api/strategy/current/events/{event_ticker}/probability-lab")
    def strategy_probability_lab_by_id(
        event_ticker: str,
        evaluation_id: str = Query(...),
        target: date | None = Query(None),
        limit: int = Query(500, ge=1, le=500),
    ) -> dict[str, Any]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        snap = _find_snapshot_by_evaluation_id(
            service=service,
            sample_snapshots=sample_snapshots,
            event_ticker=event_ticker,
            target=selected_target,
            evaluation_id=evaluation_id,
            limit=limit,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )
        if snap is None:
            raise HTTPException(status_code=404, detail="evaluation not found")
        return _probability_lab_bundle(
            snap,
            replay_mode=mode == "replay" or bool(sample_snapshots),
        )

    @app.get("/api/strategy/current/events/{event_ticker}/weighting/history")
    def strategy_weighting_history(
        event_ticker: str,
        target: date | None = Query(None),
        limit: int = Query(100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        if sample_snapshots:
            snapshots = [
                SignalRoomSnapshot.model_validate(item)
                for item in sample_snapshots[-limit:]
            ]
        else:
            history_target = selected_target or service.list_events()[0].target_date
            rows = service.repository.stage_weight_evaluation_history(
                target_date=history_target,
                weighting_revision=service.stage_weight_config.weighting_revision,
                limit=limit,
            )
            if rows:
                return [_weighting_api_row(row) for row in rows]
            snapshots = [
                service.latest_snapshot(
                    event_ticker=event_ticker,
                    target_date=selected_target,
                )
            ]
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for snap in snapshots:
            row = _weighting_api_snapshot(snap)
            evaluation_id = str(row["evaluation_id"])
            if evaluation_id in seen:
                continue
            seen.add(evaluation_id)
            output.append(row)
        return output

    @app.get("/api/strategy/current/events/{event_ticker}/evaluations")
    def strategy_evaluations(
        event_ticker: str,
        target: date | None = Query(None),
        limit: int = Query(500, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        selected_target = target or target_date or _target_from_event_ticker(event_ticker)
        if sample_snapshots:
            items = []
            for item in sample_snapshots[-limit:]:
                snap = SignalRoomSnapshot.model_validate(item)
                items.append(evaluation_index_item(snap))
            return items
        rows = service.repository.stage_weight_evaluation_history(
            target_date=selected_target,
            weighting_revision=service.stage_weight_config.weighting_revision,
            limit=limit,
        )
        if rows:
            return [_evaluation_index_from_weight_row(row, event_ticker) for row in rows]
        snap = service.latest_snapshot(
            event_ticker=event_ticker,
            target_date=selected_target,
        )
        return [evaluation_index_item(snap)]

    return app


def _snapshot_for_request(
    *,
    service: SignalRoomService,
    sample_snapshots: list[dict[str, Any]],
    event_ticker: str,
    target: date | None,
    as_of: datetime | None,
) -> SignalRoomSnapshot:
    if sample_snapshots:
        if as_of is None:
            return SignalRoomSnapshot.model_validate(sample_snapshots[-1])
        cutoff = _ensure_aware_utc(as_of)
        eligible = [
            snap
            for snap in sample_snapshots
            if _iso_dt(str(snap["decision"]["evaluated_at"])) <= cutoff
        ]
        return SignalRoomSnapshot.model_validate((eligible or sample_snapshots[:1])[-1])
    return service.latest_snapshot(event_ticker=event_ticker, target_date=target, as_of=as_of)


def _weighting_api_snapshot(snapshot: SignalRoomSnapshot) -> dict[str, Any]:
    lab = snapshot.probability_lab or {}
    return {
        "evaluation_id": str(lab.get("evaluation_id") or snapshot.revision),
        "evaluated_at": snapshot.decision.evaluated_at.isoformat(),
        "target_date": snapshot.event.target_date.isoformat(),
        "weighting": lab.get("weighting") or {},
        "weighting_modes": lab.get("weighting_modes") or {},
        "equation_trace": lab.get("equation_trace") or {},
        "order_submission_reachable": snapshot.strategy.order_submission_reachable,
    }


def _probability_lab_bundle(
    snapshot: SignalRoomSnapshot,
    *,
    replay_mode: bool,
) -> dict[str, Any]:
    return {
        "explainability": canonical_explainability_snapshot(
            snapshot,
            replay_mode=replay_mode,
        ),
        "weighting": _weighting_api_snapshot(snapshot),
    }


def _weighting_api_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("evaluation_payload") or {}
    return {
        "evaluation_id": str(row["evaluation_id"]),
        "evaluated_at": str(row["evaluated_at_utc"]),
        "target_date": str(row["target_date_local"]),
        "weighting": row.get("weight_snapshot") or {},
        "weighting_modes": payload.get("mode_outputs") or {},
        "equation_trace": payload.get("equation_trace") or {},
        "order_submission_reachable": False,
    }


def _evaluation_index_from_weight_row(
    row: dict[str, Any],
    event_ticker: str,
) -> dict[str, Any]:
    weighting = row.get("weight_snapshot") or {}
    payload = row.get("evaluation_payload") or {}
    index = payload.get("index") if isinstance(payload.get("index"), dict) else {}
    mode_outputs = payload.get("mode_outputs") or {}
    primary = mode_outputs.get(str(row.get("primary_mode"))) or {}
    counterfactuals = weighting.get("counterfactuals") or []
    primary_counterfactual = next(
        (item for item in counterfactuals if item.get("isPrimary")),
        {},
    )
    selected_ticker = (
        index.get("selectedMarketTicker")
        or primary.get("selected_market_ticker")
        or primary_counterfactual.get("selectedMarketTicker")
    )
    selected_side = (
        index.get("selectedSide")
        or primary.get("selected_side")
        or primary_counterfactual.get("selectedSide")
    )
    blocked = str(row.get("readiness_status")) == "BLOCKED"
    audit = weighting.get("_audit") or {}
    return {
        "evaluationId": str(row["evaluation_id"]),
        "evaluatedAt": str(row["evaluated_at_utc"]),
        "analysisState": index.get("analysisState")
        or ("DATA_BLOCKED" if blocked else "ANALYSIS_READY"),
        "executionState": index.get("executionState")
        or ("BLOCKED" if blocked else "SHADOW_CANDIDATE" if selected_ticker else "NO_TRADE"),
        "finalReasonCode": index.get("finalReasonCode")
        or audit.get("blockedReasonCode")
        or ("PERSISTED_SHADOW_CANDIDATE" if selected_ticker else "PERSISTED_NO_TRADE"),
        "eventTicker": event_ticker,
        "selectedMarketTicker": selected_ticker,
        "selectedSide": selected_side,
    }


def _load_sample_snapshots(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else payload
    if not isinstance(snapshots, list):
        raise ValueError("sample fixture must contain a snapshots list")
    return [dict(item) for item in snapshots]


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _target_from_event_ticker(event_ticker: str) -> date | None:
    try:
        raw = event_ticker.rsplit("-", 1)[1]
        return datetime.strptime(raw, "%y%b%d").date()
    except (IndexError, ValueError):
        return None


def _find_snapshot_by_evaluation_id(
    *,
    service: SignalRoomService,
    sample_snapshots: list[dict[str, Any]],
    event_ticker: str,
    target: date | None,
    evaluation_id: str,
    limit: int,
    replay_mode: bool,
) -> SignalRoomSnapshot | None:
    if sample_snapshots:
        for item in reversed(sample_snapshots[-limit:]):
            snap = SignalRoomSnapshot.model_validate(item)
            canonical = canonical_explainability_snapshot(snap, replay_mode=replay_mode)
            if canonical["evaluationId"] == evaluation_id:
                return snap
        return None
    if target is None:
        latest = service.latest_snapshot(event_ticker=event_ticker, target_date=None)
        canonical = canonical_explainability_snapshot(latest, replay_mode=replay_mode)
        return latest if canonical["evaluationId"] == evaluation_id else None
    persisted = service.repository.stage_weight_evaluation(evaluation_id)
    if (
        persisted is not None
        and persisted.get("target_date_local") == target.isoformat()
        and persisted.get("weighting_revision")
        == service.stage_weight_config.weighting_revision
    ):
        snap = service.latest_snapshot(
            event_ticker=event_ticker,
            target_date=target,
            as_of=_iso_dt(str(persisted["evaluated_at_utc"])),
        )
        canonical = canonical_explainability_snapshot(snap, replay_mode=replay_mode)
        if canonical["evaluationId"] == evaluation_id:
            return snap
    points = service.timeline(target_date=target, limit=limit)
    for point in reversed(points):
        snap = service.latest_snapshot(
            event_ticker=event_ticker,
            target_date=target,
            as_of=point.evaluated_at,
        )
        canonical = canonical_explainability_snapshot(snap, replay_mode=replay_mode)
        if canonical["evaluationId"] == evaluation_id:
            return snap
    latest = service.latest_snapshot(event_ticker=event_ticker, target_date=target)
    canonical = canonical_explainability_snapshot(latest, replay_mode=replay_mode)
    return latest if canonical["evaluationId"] == evaluation_id else None
