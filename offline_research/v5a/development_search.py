"""Run the four-colony V5A development search on the frozen 64-day split.

Each deterministic iteration assigns one proposal to probability calibration,
execution evidence, fee/entry economics, or temporal robustness.  Only the
development labels are visible.  The 28-day holdout remains sealed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from statistics import mean
import time
from typing import Any, Mapping

import pandas as pd


UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
PREDICTION_MANIFEST = Path("data/manifests/v5a_frozen_leader_probabilities_outcome_blind.json")
LABELS = Path("data/development/v5a/development_labels.json")
RUN_ROOT = Path("runs/campaigns_v5a")
CURRENT = Path("runs/v5a_current_campaign.json")
STATE_SCHEMA = "klax-v5a-four-colony-development-state-v1"
RESULT_SCHEMA = "klax-v5a-development-candidate-v1"
SUMMARY_SCHEMA = "klax-v5a-development-summary-v1"
WALL_SECONDS = 43_200
COLONIES = (
    "probability_calibration",
    "execution_evidence",
    "fee_and_entry_economics",
    "sample_and_regime_robustness",
)


class DevelopmentSearchError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


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
        raise DevelopmentSearchError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise DevelopmentSearchError(f"{field} mismatch")


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                   allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    pending.replace(path)


def _fee(climate_date: str, price_cents: int) -> Decimal:
    price = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * price * (Decimal(1) - price)
    quantum = Decimal("0.01") if climate_date < "2026-07-07" else Decimal("0.0001")
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _contains(contract: Mapping[str, Any], reported_high_f: int) -> bool:
    strike = contract["strike_type"]
    if strike == "less":
        return reported_high_f < int(contract["cap_strike"])
    if strike == "between":
        return int(contract["floor_strike"]) <= reported_high_f <= int(contract["cap_strike"])
    if strike == "greater":
        return reported_high_f > int(contract["floor_strike"])
    raise DevelopmentSearchError("unsupported strike type")


def _transform(values: list[float], power: float, blend: float) -> list[float]:
    raised = [max(value, 1e-12) ** power for value in values]
    total = sum(raised)
    normalized = [value / total for value in raised]
    uniform = 1.0 / len(values)
    result = [(1.0 - blend) * value + blend * uniform for value in normalized]
    if abs(sum(result) - 1.0) > 1e-10:
        raise DevelopmentSearchError("transformed probability mass differs")
    return result


def _baseline() -> dict[str, Any]:
    return {
        "probability_power": 1.0,
        "uniform_blend": 0.0,
        "probability_haircut": 0.0,
        "allowed_grades": ["A", "B_PLUS", "B"],
        "allowed_sides": ["YES", "NO"],
        "minimum_price_cents": 5,
        "maximum_price_cents": 80,
        "maximum_spread_cents": 5,
        "minimum_expected_net_return": 0.10,
        "additional_adverse_price_cents": 0,
    }


_OPTIONS = {
    "probability_power": [0.60, 0.75, 0.90, 1.0, 1.10, 1.25, 1.40],
    "uniform_blend": [0.0, 0.03, 0.07, 0.12, 0.20],
    "probability_haircut": [0.0, 0.01, 0.02, 0.04, 0.06, 0.08],
    "allowed_grades": [["A"], ["A", "B_PLUS"], ["A", "B_PLUS", "B"]],
    "allowed_sides": [["YES"], ["NO"], ["YES", "NO"]],
    "minimum_price_cents": [1, 5, 10, 15, 20],
    "maximum_price_cents": [40, 60, 80, 95, 99],
    "maximum_spread_cents": [2, 5, 10, 20, 100],
    "minimum_expected_net_return": [0.0, 0.05, 0.10, 0.15, 0.20, 0.30],
    "additional_adverse_price_cents": [0, 1, 2, 3],
}
_COLONY_KEYS = {
    "probability_calibration": (
        "probability_power", "uniform_blend", "probability_haircut",
    ),
    "execution_evidence": (
        "allowed_grades", "allowed_sides", "maximum_spread_cents",
    ),
    "fee_and_entry_economics": (
        "minimum_price_cents", "maximum_price_cents",
        "minimum_expected_net_return", "additional_adverse_price_cents",
    ),
    "sample_and_regime_robustness": (
        "probability_haircut", "allowed_grades", "allowed_sides",
        "minimum_expected_net_return",
    ),
}


def _mutate(incumbent: Mapping[str, Any], iteration: int, colony: str) -> dict[str, Any]:
    result = json.loads(json.dumps(incumbent))
    digest = sha256(f"v5a:{iteration}:{colony}".encode("ascii")).digest()
    keys = _COLONY_KEYS[colony]
    mutation_count = 1 + digest[0] % min(3, len(keys))
    for offset in range(mutation_count):
        key = keys[(digest[1 + offset] + offset) % len(keys)]
        options = _OPTIONS[key]
        result[key] = options[digest[8 + offset] % len(options)]
    if result["minimum_price_cents"] >= result["maximum_price_cents"]:
        result["minimum_price_cents"], result["maximum_price_cents"] = 5, 80
    return result


def _inputs(root: Path) -> tuple[dict[str, Any], pd.DataFrame, dict[str, int]]:
    universe = _load(root / UNIVERSE)
    predictions = _load(root / PREDICTION_MANIFEST)
    labels = _load(root / LABELS)
    for value in (universe, predictions, labels):
        _verify(value)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("holdout_access_authorized") is not False
        or predictions.get("outcomes_read") is not False
        or labels.get("holdout_labels_opened") is not False
        or labels.get("development_date_count") != 64
    ):
        raise DevelopmentSearchError("development input safety differs")
    output = predictions["output"]
    path = root / output["path"]
    if not path.is_file() or _file_hash(path) != output["sha256"]:
        raise DevelopmentSearchError("probability output binding differs")
    frame = pd.read_parquet(path)
    by_date = {row["climate_date"]: int(row["reported_high_f"]) for row in labels["labels"]}
    if set(by_date) != set(universe["split"]["development_dates"]):
        raise DevelopmentSearchError("development label date set differs")
    return universe, frame, by_date


def evaluate(
    universe: Mapping[str, Any], predictions: pd.DataFrame,
    labels: Mapping[str, int], parameters: Mapping[str, Any],
    *, include_trades: bool = False, partition: str = "development",
) -> dict[str, Any]:
    if partition not in {"development", "holdout"}:
        raise DevelopmentSearchError("unsupported evaluation partition")
    rows_by_date: dict[str, list[Mapping[str, Any]]] = {}
    for row in universe["records"]:
        if row["partition"] == partition:
            rows_by_date.setdefault(row["climate_date"], []).append(row)
    probabilities = {
        (str(row.climate_date), str(row.market_ticker)): float(row.yes_probability)
        for row in predictions.itertuples(index=False)
        if str(row.partition) == partition
    }
    trades = []
    brier_rows = []
    scoring_dates = universe["split"][f"{partition}_dates"]
    if set(labels) != set(scoring_dates):
        raise DevelopmentSearchError(f"{partition} label date set differs")
    for climate_date in scoring_dates:
        yes_rows = sorted(
            [row for row in rows_by_date[climate_date] if row["contract_side"] == "YES"],
            key=lambda row: (-10000 if row["floor_strike"] is None else float(row["floor_strike"])),
        )
        base = [probabilities[(climate_date, row["market_ticker"])] for row in yes_rows]
        adjusted = _transform(
            base, float(parameters["probability_power"]),
            float(parameters["uniform_blend"]),
        )
        adjusted_by_ticker = {
            row["market_ticker"]: probability for row, probability in zip(yes_rows, adjusted)
        }
        actual = labels[climate_date]
        winners = [1.0 if _contains(row, actual) else 0.0 for row in yes_rows]
        brier_rows.append(sum((p - y) ** 2 for p, y in zip(adjusted, winners)))
        candidates = []
        for row in rows_by_date[climate_date]:
            grade = row["execution_evidence_grade"]
            if grade not in parameters["allowed_grades"] or row["contract_side"] not in parameters["allowed_sides"]:
                continue
            ask = row["execution_price_cents"]
            if ask is None:
                continue
            price_cents = int(ask) + int(parameters["additional_adverse_price_cents"])
            if not int(parameters["minimum_price_cents"]) <= price_cents <= int(parameters["maximum_price_cents"]):
                continue
            if not 0 < price_cents < 100:
                continue
            bid = row["bid_price_cents"]
            spread = 100 if bid is None else int(ask) - int(bid)
            if spread > int(parameters["maximum_spread_cents"]):
                continue
            yes_probability = adjusted_by_ticker[row["market_ticker"]]
            purchased = yes_probability if row["contract_side"] == "YES" else 1.0 - yes_probability
            purchased = max(0.0, purchased - float(parameters["probability_haircut"]))
            price = Decimal(price_cents) / Decimal(100)
            fee = _fee(climate_date, price_cents)
            outlay = price + fee
            expected_profit = Decimal(str(purchased)) - outlay
            expected_roi = expected_profit / outlay
            if float(expected_roi) < float(parameters["minimum_expected_net_return"]):
                continue
            side_won = _contains(row, actual)
            if row["contract_side"] == "NO":
                side_won = not side_won
            payout = Decimal(1 if side_won else 0)
            profit = payout - outlay
            candidates.append({
                "climate_date": climate_date,
                "market_ticker": row["market_ticker"],
                "contract_side": row["contract_side"],
                "execution_evidence_grade": grade,
                "assumed_fill": row["assumed_fill"],
                "model_probability": purchased,
                "entry_price_cents": price_cents,
                "fee_dollars": float(fee),
                "entry_outlay_dollars": float(outlay),
                "expected_net_return": float(expected_roi),
                "won": side_won,
                "net_profit_dollars": float(profit),
                "realized_net_return": float(profit / outlay),
            })
        if candidates:
            trades.append(max(
                candidates,
                key=lambda row: (
                    row["expected_net_return"],
                    -row["entry_price_cents"],
                    row["market_ticker"], row["contract_side"],
                ),
            ))

    fold_map = {}
    for index, climate_date in enumerate(scoring_dates):
        fold_map[climate_date] = min(4, index * 5 // len(scoring_dates))
    fold_rows = []
    for fold in range(5):
        selected = [row for row in trades if fold_map[row["climate_date"]] == fold]
        outlay = sum(row["entry_outlay_dollars"] for row in selected)
        profit = sum(row["net_profit_dollars"] for row in selected)
        fold_rows.append({
            "fold": fold + 1,
            "selected_days": len(selected),
            "aggregate_realized_net_return": profit / outlay if outlay else -1.0,
        })
    total_outlay = sum(row["entry_outlay_dollars"] for row in trades)
    total_profit = sum(row["net_profit_dollars"] for row in trades)
    realized = total_profit / total_outlay if total_outlay else -1.0
    expected = mean(row["expected_net_return"] for row in trades) if trades else -1.0
    grades = {grade: sum(row["execution_evidence_grade"] == grade for row in trades)
              for grade in ("A", "B_PLUS", "B")}
    quality_weights = {"A": 1.0, "B_PLUS": 0.65, "B": 0.30}
    evidence_quality = (
        mean(quality_weights[row["execution_evidence_grade"]] for row in trades)
        if trades else 0.0
    )
    positive_folds = sum(
        row["selected_days"] > 0 and row["aggregate_realized_net_return"] > 0
        for row in fold_rows
    )
    nonempty_fold_returns = [
        row["aggregate_realized_net_return"] for row in fold_rows
        if row["selected_days"] > 0
    ]
    worst_fold = min(nonempty_fold_returns) if nonempty_fold_returns else -1.0
    robust_positive = bool(
        len(trades) >= 30 and realized > 0 and positive_folds >= 4 and worst_fold >= -0.10
    )
    target_reached = bool(
        robust_positive and realized >= 0.10 and expected >= 0.10
    )
    body = {
        "schema_version": RESULT_SCHEMA,
        "parameters": json.loads(json.dumps(parameters)),
        "selected_trades": len(trades),
        "selected_days": len(trades),
        "mean_expected_net_return": expected,
        "aggregate_realized_net_return": realized,
        "total_net_profit_dollars": total_profit,
        "total_entry_outlay_dollars": total_outlay,
        "multiclass_brier": mean(brier_rows),
        "temporal_folds": fold_rows,
        "positive_fold_count": positive_folds,
        "worst_nonempty_fold_return": worst_fold,
        "execution_grade_counts": grades,
        "evidence_quality_score": evidence_quality,
        "robust_positive": robust_positive,
        "ten_percent_target_reached": target_reached,
        "sample_label": (
            "SUPPORTED_PARTIAL_EVIDENCE" if len(trades) >= 30
            else "PRELIMINARY_PARTIAL_EVIDENCE" if len(trades) >= 10
            else "ANECDOTAL_PARTIAL_EVIDENCE" if trades
            else "NO_PARTIAL_EVIDENCE_SAMPLE"
        ),
        "evaluation_partition": partition,
        "development_only": partition == "development",
        "holdout_labels_opened": partition == "holdout",
        "protected_confirmation_labels_read": partition == "holdout",
        "actual_orders_placed": False,
    }
    if include_trades:
        body["trades"] = trades
    body["result_sha256"] = _canonical_hash(body, "result_sha256")
    return body


def _rank(result: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        bool(result["ten_percent_target_reached"]),
        bool(result["robust_positive"]),
        int(result["selected_days"] >= 30),
        int(result["positive_fold_count"]),
        float(result["worst_nonempty_fold_return"]),
        float(result["aggregate_realized_net_return"]),
        float(result["mean_expected_net_return"]),
        float(result["evidence_quality_score"]),
        -float(result["multiclass_brier"]),
        int(result["selected_days"]),
    )


def _campaign(root: Path, campaign_id: str | None = None) -> tuple[str, Path]:
    if campaign_id is None:
        pointer = _load(root / CURRENT)
        campaign_id = str(pointer["campaign_id"])
    if not campaign_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in campaign_id):
        raise DevelopmentSearchError("campaign id is malformed")
    return campaign_id, root / RUN_ROOT / campaign_id


def register(root: Path | str, campaign_id: str | None = None) -> dict[str, Any]:
    workspace = Path(root).resolve()
    if (workspace / CURRENT).is_file():
        existing = _load(workspace / CURRENT)
        state_path = workspace / str(existing["state_path"])
        if state_path.is_file():
            return _load(state_path)
    identifier = campaign_id or "v5a-offline-" + _now().strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = workspace / RUN_ROOT / identifier
    start = _now()
    universe, probabilities, labels = _inputs(workspace)
    state = {
        "schema_version": STATE_SCHEMA,
        "campaign_id": identifier,
        "status": "REGISTERED",
        "phase": "development",
        "registered_at_utc": _iso(start),
        "analysis_budget_started_at_utc": None,
        "absolute_deadline_utc": None,
        "wall_clock_budget_seconds": WALL_SECONDS,
        "iteration": 0,
        "proposal_count_by_colony": {colony: 0 for colony in COLONIES},
        "incumbent": None,
        "colony_leaders": {colony: None for colony in COLONIES},
        "best_positive": None,
        "target_candidate": None,
        "bindings": {
            "universe": {"path": UNIVERSE.as_posix(), "sha256": _file_hash(workspace / UNIVERSE), "self_sha256": universe["self_sha256"]},
            "probabilities": {"path": PREDICTION_MANIFEST.as_posix(), "sha256": _file_hash(workspace / PREDICTION_MANIFEST), "self_sha256": _load(workspace / PREDICTION_MANIFEST)["self_sha256"]},
            "development_labels": {"path": LABELS.as_posix(), "sha256": _file_hash(workspace / LABELS), "self_sha256": _load(workspace / LABELS)["self_sha256"]},
        },
        "engine_source": {
            "path": Path(__file__).resolve().relative_to(workspace).as_posix(),
            "sha256": _file_hash(Path(__file__).resolve()),
        },
        "holdout_labels_opened": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    state["self_sha256"] = _canonical_hash(state)
    _write(run_dir / "recovery-state.json", state)
    _write(workspace / CURRENT, {
        "schema_version": "klax-v5a-current-campaign-v1",
        "campaign_id": identifier,
        "state_path": (run_dir / "recovery-state.json").relative_to(workspace).as_posix(),
    })
    return state


def _validate_bindings(root: Path, state: Mapping[str, Any]) -> None:
    for binding in state["bindings"].values():
        path = root / binding["path"]
        if not path.is_file() or _file_hash(path) != binding["sha256"]:
            raise DevelopmentSearchError("campaign input binding differs")
        value = _load(path)
        if value.get("self_sha256") != binding["self_sha256"]:
            raise DevelopmentSearchError("campaign input self hash differs")
    engine = state.get("engine_source", {})
    engine_path = root / str(engine.get("path", ""))
    if not engine_path.is_file() or _file_hash(engine_path) != engine.get("sha256"):
        raise DevelopmentSearchError("campaign engine source binding differs")


def run(root: Path | str, campaign_id: str | None = None) -> dict[str, Any]:
    workspace = Path(root).resolve()
    registered = register(workspace, campaign_id)
    identifier, run_dir = _campaign(workspace, registered["campaign_id"])
    state_path = run_dir / "recovery-state.json"
    state = _load(state_path)
    _verify(state)
    _validate_bindings(workspace, state)
    universe, predictions, labels = _inputs(workspace)
    now = _now()
    started = state.get("analysis_budget_started_at_utc")
    if started is None:
        started_at = now
        deadline = now + timedelta(seconds=WALL_SECONDS)
        state["analysis_budget_started_at_utc"] = _iso(started_at)
        state["absolute_deadline_utc"] = _iso(deadline)
    else:
        deadline = _parse(state["absolute_deadline_utc"])
    state["status"] = "RUNNING"
    state["process_id"] = os.getpid()

    if state["incumbent"] is None:
        baseline = evaluate(universe, predictions, labels, _baseline())
        state["incumbent"] = baseline
        state["iteration"] = 1
    last_write = time.monotonic()
    while _now() < deadline:
        iteration = int(state["iteration"])
        colony = COLONIES[iteration % len(COLONIES)]
        proposal = _mutate(state["incumbent"]["parameters"], iteration, colony)
        result = evaluate(universe, predictions, labels, proposal)
        state["proposal_count_by_colony"][colony] += 1
        state["iteration"] = iteration + 1
        leader = state["colony_leaders"][colony]
        if leader is None or _rank(result) > _rank(leader):
            state["colony_leaders"][colony] = result
        if _rank(result) > _rank(state["incumbent"]):
            state["incumbent"] = result
        if result["aggregate_realized_net_return"] > 0 and (
            state["best_positive"] is None or _rank(result) > _rank(state["best_positive"])
        ):
            state["best_positive"] = result
        if result["ten_percent_target_reached"]:
            state["target_candidate"] = result
            state["status"] = "DEVELOPMENT_TARGET_FOUND"
            break
        if time.monotonic() - last_write >= 5:
            state["updated_at_utc"] = _iso(_now())
            state["elapsed_wall_seconds_charged"] = min(
                WALL_SECONDS,
                (_now() - _parse(state["analysis_budget_started_at_utc"])).total_seconds(),
            )
            state.pop("self_sha256", None)
            state["self_sha256"] = _canonical_hash(state)
            _write(state_path, state)
            last_write = time.monotonic()
    if state["status"] == "RUNNING":
        state["status"] = "DEVELOPMENT_BUDGET_EXHAUSTED"
    leader = state["target_candidate"] or state["incumbent"]
    detailed = evaluate(
        universe, predictions, labels, leader["parameters"], include_trades=True,
    )
    _write(run_dir / "development-leader.json", detailed)
    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "campaign_id": identifier,
        "status": state["status"],
        "scientific_conclusion": (
            "DEVELOPMENT_TARGET_FOUND_HOLDOUT_UNTESTED"
            if detailed["ten_percent_target_reached"]
            else "DEVELOPMENT_POSITIVE_HOLDOUT_UNTESTED"
            if detailed["aggregate_realized_net_return"] > 0
            else "NO_POSITIVE_DEVELOPMENT_EDGE"
        ),
        "iterations": state["iteration"],
        "proposal_count_by_colony": state["proposal_count_by_colony"],
        "leader_result_sha256": detailed["result_sha256"],
        "leader_metrics": {key: detailed[key] for key in (
            "selected_trades", "mean_expected_net_return",
            "aggregate_realized_net_return", "positive_fold_count",
            "worst_nonempty_fold_return", "execution_grade_counts",
            "robust_positive", "ten_percent_target_reached", "sample_label",
        )},
        "holdout_labels_opened": False,
        "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
    }
    summary["self_sha256"] = _canonical_hash(summary)
    _write(run_dir / "development-summary.json", summary)
    state["development_leader_result_sha256"] = detailed["result_sha256"]
    state["development_summary_self_sha256"] = summary["self_sha256"]
    state["updated_at_utc"] = _iso(_now())
    state["elapsed_wall_seconds_charged"] = min(
        WALL_SECONDS,
        (_now() - _parse(state["analysis_budget_started_at_utc"])).total_seconds(),
    )
    state.pop("self_sha256", None)
    state["self_sha256"] = _canonical_hash(state)
    _write(state_path, state)
    return summary


def status(root: Path | str, campaign_id: str | None = None) -> dict[str, Any]:
    workspace = Path(root).resolve()
    if not (workspace / CURRENT).is_file():
        return {"schema_version": STATE_SCHEMA, "status": "NOT_REGISTERED"}
    _, run_dir = _campaign(workspace, campaign_id)
    state = _load(run_dir / "recovery-state.json")
    _verify(state)
    return state


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "run", "status"))
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--campaign-id")
    args = parser.parse_args()
    if args.action == "register":
        value = register(args.project_root, args.campaign_id)
    elif args.action == "run":
        value = run(args.project_root, args.campaign_id)
    else:
        value = status(args.project_root, args.campaign_id)
    print(json.dumps(value, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
