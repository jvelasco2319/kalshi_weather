"""Freeze the V5A strategy, then perform exactly one 28-day holdout evaluation.

The protected Kalshi settlement fields are not opened until the development
campaign has emitted a terminal summary and an immutable strategy freeze.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from v5a.development_search import evaluate, _inputs


CURRENT = Path("runs/v5a_current_campaign.json")
UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
EVENT_RULES = Path("data/manifests/v5a_event_rules_outcome_blind.json")
STRATEGY_FREEZE = Path("data/manifests/v5a_strategy_freeze.json")
CONSUMPTION = Path("data/manifests/v5a_holdout_consumption.json")
LABELS = Path("data/protected/v5a/holdout_labels.json")


class HoldoutError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise HoldoutError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise HoldoutError(f"{field} mismatch")


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if _load(path) != dict(value):
            raise HoldoutError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def _campaign_paths(root: Path) -> tuple[str, Path, Path, Path]:
    pointer = _load(root / CURRENT)
    campaign_id = str(pointer["campaign_id"])
    state_path = root / str(pointer["state_path"])
    run_dir = state_path.parent
    return (
        campaign_id,
        state_path,
        run_dir / "development-leader.json",
        run_dir / "development-summary.json",
    )


def freeze_strategy(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    campaign_id, state_path, leader_path, summary_path = _campaign_paths(workspace)
    for path in (state_path, leader_path, summary_path):
        if not path.is_file():
            raise HoldoutError("development campaign has not produced terminal artifacts")
    state, leader, summary = map(_load, (state_path, leader_path, summary_path))
    _verify(state)
    _verify(leader, "result_sha256")
    _verify(summary)
    if state.get("status") not in {
        "DEVELOPMENT_TARGET_FOUND", "DEVELOPMENT_BUDGET_EXHAUSTED",
    }:
        raise HoldoutError("development campaign is not terminal")
    if (
        summary.get("leader_result_sha256") != leader.get("result_sha256")
        or state.get("development_leader_result_sha256") != leader.get("result_sha256")
        or state.get("development_summary_self_sha256") != summary.get("self_sha256")
        or summary.get("holdout_labels_opened") is not False
        or state.get("holdout_labels_opened") is not False
    ):
        raise HoldoutError("terminal development bindings differ")
    body = {
        "schema_version": "klax-v5a-strategy-freeze-v1",
        "status": "STRATEGY_FROZEN_BEFORE_HOLDOUT_OPEN",
        "campaign_id": campaign_id,
        "frozen_at_utc": _now(),
        "development_state": {
            "path": state_path.relative_to(workspace).as_posix(),
            "sha256": _file_hash(state_path),
            "self_sha256": state["self_sha256"],
        },
        "development_leader": {
            "path": leader_path.relative_to(workspace).as_posix(),
            "sha256": _file_hash(leader_path),
            "result_sha256": leader["result_sha256"],
        },
        "development_summary": {
            "path": summary_path.relative_to(workspace).as_posix(),
            "sha256": _file_hash(summary_path),
            "self_sha256": summary["self_sha256"],
        },
        "parameters": leader["parameters"],
        "development_metrics": summary["leader_metrics"],
        "holdout_date_count": 28,
        "holdout_evaluations_authorized": 1,
        "holdout_labels_opened": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    path = workspace / STRATEGY_FREEZE
    if path.exists():
        existing = _load(path)
        _verify(existing)
        # Timestamps are intentionally not recomputed after the first freeze.
        comparable = dict(body)
        comparable["frozen_at_utc"] = existing.get("frozen_at_utc")
        comparable["self_sha256"] = _canonical_hash(comparable)
        if existing != comparable:
            raise HoldoutError("immutable strategy freeze differs")
        return existing
    _write_immutable(path, body)
    return body


def _markets(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    market = payload.get("market")
    if isinstance(market, Mapping):
        found.append(market)
    nested = payload.get("markets")
    if isinstance(nested, list):
        found.extend(row for row in nested if isinstance(row, Mapping))
    event = payload.get("event")
    if isinstance(event, Mapping):
        event_markets = event.get("markets")
        if isinstance(event_markets, list):
            found.extend(row for row in event_markets if isinstance(row, Mapping))
    return found


def _integer_temperature(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or not -100 <= number <= 150:
        return None
    return int(number)


def _extract_temperature(markets: list[Mapping[str, Any]]) -> int:
    values = {
        parsed
        for market in markets
        for parsed in (_integer_temperature(market.get("expiration_value")),)
        if parsed is not None
    }
    if len(values) != 1:
        raise HoldoutError("protected market records lack one exact settlement temperature")
    return next(iter(values))


def _open_holdout_labels(
    workspace: Path, universe: Mapping[str, Any], strategy: Mapping[str, Any],
) -> dict[str, Any]:
    rules_path = workspace / EVENT_RULES
    rules = _load(rules_path)
    _verify(rules)
    holdout_dates = set(universe["split"]["holdout_dates"])
    expected_tickers: dict[str, set[str]] = {day: set() for day in holdout_dates}
    for row in universe["records"]:
        if row["partition"] == "holdout" and row["contract_side"] == "YES":
            expected_tickers[row["climate_date"]].add(row["market_ticker"])
    raw_by_date: dict[str, list[Mapping[str, Any]]] = {day: [] for day in holdout_dates}
    for source in rules["raw_sources"]:
        day = source.get("climate_date")
        if day in raw_by_date:
            raw_by_date[day].append(source)
    labels, opened = [], []
    for day in sorted(holdout_dates):
        collected: dict[str, Mapping[str, Any]] = {}
        for source in raw_by_date[day]:
            path = workspace / str(source["path"])
            if not path.is_file() or _file_hash(path) != source.get("sha256"):
                raise HoldoutError("protected raw source binding differs")
            payload = _load(path)
            for market in _markets(payload):
                ticker = str(market.get("ticker", ""))
                if ticker in expected_tickers[day]:
                    collected[ticker] = market
            opened.append({
                "path": source["path"], "sha256": source["sha256"],
                "climate_date": day,
            })
        if set(collected) != expected_tickers[day]:
            raise HoldoutError(f"protected settlement markets incomplete: {day}")
        labels.append({
            "climate_date": day,
            "reported_high_f": _extract_temperature(list(collected.values())),
            "source": "Kalshi quarantined historical settlement fields",
            "market_tickers": sorted(collected),
        })
    body = {
        "schema_version": "klax-v5a-protected-holdout-labels-v1",
        "status": "OPENED_AFTER_IMMUTABLE_STRATEGY_FREEZE",
        "strategy_freeze_self_sha256": strategy["self_sha256"],
        "event_rules": {
            "path": EVENT_RULES.as_posix(), "sha256": _file_hash(rules_path),
            "self_sha256": rules["self_sha256"],
        },
        "holdout_date_count": len(labels),
        "labels": labels,
        "opened_raw_sources": opened,
        "holdout_labels_opened": True,
        "protected_confirmation_labels_read": True,
        "network_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def run(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    strategy = freeze_strategy(workspace)
    campaign_id, _, _, _ = _campaign_paths(workspace)
    final_path = workspace / "runs/campaigns_v5a" / campaign_id / "holdout-evaluation.json"
    if final_path.exists():
        final = _load(final_path)
        _verify(final)
        return final
    universe, predictions, _ = _inputs(workspace)
    if len(universe["split"]["holdout_dates"]) != 28:
        raise HoldoutError("holdout split differs")
    claim = {
        "schema_version": "klax-v5a-holdout-consumption-v1",
        "status": "CLAIMED_FOR_EXACT_FROZEN_STRATEGY",
        "campaign_id": campaign_id,
        "strategy_freeze_self_sha256": strategy["self_sha256"],
        "holdout_evaluations_consumed": 1,
        "alternative_strategy_authorized": False,
        "claimed_at_utc": _now(),
        "actual_orders_placed": False,
    }
    claim["self_sha256"] = _canonical_hash(claim)
    claim_path = workspace / CONSUMPTION
    if claim_path.exists():
        existing = _load(claim_path)
        _verify(existing)
        if existing.get("strategy_freeze_self_sha256") != strategy["self_sha256"]:
            raise HoldoutError("holdout was claimed by a different strategy")
    else:
        _write_immutable(claim_path, claim)
    labels_path = workspace / LABELS
    if labels_path.exists():
        protected = _load(labels_path)
        _verify(protected)
        if protected.get("strategy_freeze_self_sha256") != strategy["self_sha256"]:
            raise HoldoutError("protected labels bind a different strategy")
    else:
        protected = _open_holdout_labels(workspace, universe, strategy)
        _write_immutable(labels_path, protected)
    label_map = {
        row["climate_date"]: int(row["reported_high_f"])
        for row in protected["labels"]
    }
    result = evaluate(
        universe, predictions, label_map, strategy["parameters"],
        include_trades=True, partition="holdout",
    )
    trades = result.get("trades", [])
    grade_metrics = {}
    for grade in ("A", "B_PLUS", "B"):
        selected = [row for row in trades if row["execution_evidence_grade"] == grade]
        outlay = sum(row["entry_outlay_dollars"] for row in selected)
        profit = sum(row["net_profit_dollars"] for row in selected)
        grade_metrics[grade] = {
            "selected_trades": len(selected),
            "aggregate_realized_net_return": profit / outlay if outlay else None,
            "total_net_profit_dollars": profit,
            "total_entry_outlay_dollars": outlay,
        }
    relaxed_target = bool(
        result["selected_days"] >= 20
        and result["aggregate_realized_net_return"] >= 0.10
        and result["mean_expected_net_return"] >= 0.10
        and result["positive_fold_count"] >= 4
        and result["worst_nonempty_fold_return"] >= -0.10
    )
    conclusion = (
        "RELAXED_TEN_PERCENT_TARGET_CONFIRMED_ON_ONE_SHOT_HOLDOUT"
        if relaxed_target
        else "POSITIVE_HOLDOUT_RETURN_BELOW_FULL_TARGET"
        if result["aggregate_realized_net_return"] > 0
        else "NO_POSITIVE_HOLDOUT_EDGE"
    )
    if result["selected_days"] < 10:
        conclusion = "INSUFFICIENT_HOLDOUT_TRADES_FOR_A_DECISIVE_CONCLUSION"
    final = {
        "schema_version": "klax-v5a-one-shot-holdout-evaluation-v1",
        "status": "COMPLETE",
        "campaign_id": campaign_id,
        "scientific_conclusion": conclusion,
        "relaxed_ten_percent_target_confirmed": relaxed_target,
        "strategy_freeze": {
            "path": STRATEGY_FREEZE.as_posix(),
            "sha256": _file_hash(workspace / STRATEGY_FREEZE),
            "self_sha256": strategy["self_sha256"],
        },
        "holdout_labels": {
            "path": LABELS.as_posix(), "sha256": _file_hash(labels_path),
            "self_sha256": protected["self_sha256"],
        },
        "holdout_result": result,
        "execution_grade_metrics": grade_metrics,
        "holdout_evaluations_consumed": 1,
        "development_reopened_after_holdout": False,
        "protected_confirmation_labels_read": True,
        "network_used": False,
        "actual_orders_placed": False,
    }
    final["self_sha256"] = _canonical_hash(final)
    _write_immutable(final_path, final)
    return final


def status(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    if not (workspace / CURRENT).is_file():
        return {"status": "NO_CAMPAIGN"}
    campaign_id, _, _, _ = _campaign_paths(workspace)
    final_path = workspace / "runs/campaigns_v5a" / campaign_id / "holdout-evaluation.json"
    return {
        "campaign_id": campaign_id,
        "strategy_frozen": (workspace / STRATEGY_FREEZE).is_file(),
        "holdout_consumed": (workspace / CONSUMPTION).is_file(),
        "holdout_complete": final_path.is_file(),
        "actual_orders_placed": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run", "status"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    if args.action == "freeze":
        value = freeze_strategy(args.project_root)
    elif args.action == "run":
        value = run(args.project_root)
    else:
        value = status(args.project_root)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
