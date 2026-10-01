"""One-shot offline evaluation of the frozen V5B confirmation selections."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

import pandas as pd

from v5a.development_search import _fee
from v5b.evaluation import _adjust, validate_spec

from .predictions import OUTPUT as PREDICTIONS
from .selection import PLAN, SELECTION_FREEZE
from .universe import OUTPUT as UNIVERSE


LABELS = Path("data/raw/v5b_untouched/confirmation/labels.jsonl")
LABEL_MANIFEST = Path("data/raw/v5b_untouched/confirmation/manifest.json")
OUTPUT = Path("data/manifests/v5b_untouched_confirmation_result.json")
SCHEMA = "klax-v5b-untouched-confirmation-result-v1"


class ConfirmationEvaluationError(ValueError):
    pass


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
        raise ConfirmationEvaluationError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise ConfirmationEvaluationError(f"{field} mismatch")


def _summary(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    outlay = sum(float(row["entry_outlay_dollars"]) for row in trades)
    profit = sum(float(row["net_profit_dollars"]) for row in trades)
    return {
        "selected_days": len(trades),
        "total_entry_outlay_dollars": outlay,
        "total_net_profit_dollars": profit,
        "aggregate_realized_net_return": profit / outlay if outlay else -1.0,
        "win_count": sum(bool(row["won"]) for row in trades),
    }


def _winner(row: Mapping[str, Any]) -> str:
    winning_id = str(row["resolution_winning_outcome_id"])
    winners = [
        side for side in row["contract_sides"] if str(side["id"]) == winning_id
    ]
    if len(winners) != 1:
        raise ConfirmationEvaluationError("market winner is ambiguous")
    side = str(winners[0]["name"]).upper()
    if side not in {"YES", "NO"}:
        raise ConfirmationEvaluationError("market winner side differs")
    return side


def build(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    plan = _load(workspace / PLAN)
    universe = _load(workspace / UNIVERSE)
    selection = _load(workspace / SELECTION_FREEZE)
    label_manifest = _load(workspace / LABEL_MANIFEST)
    for value in (universe, selection, label_manifest):
        _verify(value)
    if (
        selection.get("status") != "FROZEN_BEFORE_OUTCOMES"
        or selection.get("selected_day_count", 0) < plan["minimum_selected_days_before_label_open"]
        or selection.get("universe_self_sha256") != universe.get("self_sha256")
        or label_manifest.get("status") != "ONE_SHOT_LABEL_OPEN_COMPLETE"
        or label_manifest.get("one_shot_outcome_open_consumed") is not True
        or label_manifest.get("selection_freeze", {}).get("self_sha256") != selection.get("self_sha256")
        or label_manifest.get("universe", {}).get("self_sha256") != universe.get("self_sha256")
        or label_manifest.get("artifact", {}).get("sha256") != _file_hash(workspace / LABELS)
    ):
        raise ConfirmationEvaluationError("one-shot input identity differs")
    strategy_path = workspace / plan["primary_strategy"]["strategy_freeze_path"]
    strategy = _load(strategy_path)
    parameters = validate_spec(strategy["parameters"])
    if parameters["calibration"] != "none":
        raise ConfirmationEvaluationError("confirmation cannot fit calibration")

    raw_labels = [
        json.loads(line)
        for line in (workspace / LABELS).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = {str(row["platform_id"]): row for row in raw_labels}
    if len(labels) != label_manifest["resolved_market_count"]:
        raise ConfirmationEvaluationError("label market identities are not unique")
    records_by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in universe["records"]:
        records_by_date[row["climate_date"]].append(row)
    probabilities = pd.read_parquet(workspace / PREDICTIONS)
    probability_by_key = {
        (str(row.climate_date), str(row.market_ticker)): float(row.yes_probability)
        for row in probabilities.itertuples(index=False)
    }

    trades: list[dict[str, Any]] = []
    briers: list[float] = []
    reference_briers: list[float] = []
    for frozen in selection["selections"]:
        day, ticker = frozen["climate_date"], frozen["market_ticker"]
        market_label = labels.get(ticker)
        if market_label is None:
            raise ConfirmationEvaluationError(f"selected market label missing: {ticker}")
        winner = _winner(market_label)
        won = winner == frozen["contract_side"]
        fee = float(_fee(day, int(frozen["entry_price_cents"])))
        if abs(fee - float(frozen["fee_dollars"])) > 1e-12:
            raise ConfirmationEvaluationError("frozen fee differs")
        outlay = int(frozen["entry_price_cents"]) / 100 + fee
        profit = int(won) - outlay
        trades.append({
            **frozen,
            "winning_contract_side": winner,
            "won": won,
            "entry_outlay_dollars": outlay,
            "net_profit_dollars": profit,
            "realized_net_return": profit / outlay,
        })

        yes_rows = sorted(
            (row for row in records_by_date[day] if row["contract_side"] == "YES"),
            key=lambda row: (
                -10000 if row["floor_strike"] is None else float(row["floor_strike"]),
                row["market_ticker"],
            ),
        )
        if len(yes_rows) != 6:
            raise ConfirmationEvaluationError("six-market Brier partition required")
        base = [probability_by_key[(day, row["market_ticker"])] for row in yes_rows]
        adjusted = _adjust(base, parameters)
        winners = [int(_winner(labels[row["market_ticker"]]) == "YES") for row in yes_rows]
        if sum(winners) != 1:
            raise ConfirmationEvaluationError("market outcomes are not exhaustive")
        briers.append(sum((prob - truth) ** 2 for prob, truth in zip(adjusted, winners)))
        reference_briers.append(sum((prob - truth) ** 2 for prob, truth in zip(base, winners)))

    summary = _summary(trades)
    folds = []
    date_fold = {
        day: min(4, index * 5 // len(universe["dates"]))
        for index, day in enumerate(universe["dates"])
    }
    for fold in range(5):
        folds.append({
            "fold": fold + 1,
            **_summary([row for row in trades if date_fold[row["climate_date"]] == fold]),
        })
    stress_cents = int(plan["evaluation_gates"]["adverse_fill_stress_cents"])
    stressed = []
    for trade in trades:
        price = int(trade["entry_price_cents"]) + stress_cents
        outlay = price / 100 + float(_fee(trade["climate_date"], price))
        stressed.append({
            **trade,
            "entry_outlay_dollars": outlay,
            "net_profit_dollars": int(trade["won"]) - outlay,
        })
    removed = max(trades, key=lambda row: row["net_profit_dollars"])
    best_removed = _summary([row for row in trades if row is not removed])
    positive_folds = sum(
        fold["selected_days"] > 0 and fold["aggregate_realized_net_return"] > 0
        for fold in folds
    )
    worst_fold = min(
        fold["aggregate_realized_net_return"] for fold in folds if fold["selected_days"]
    )
    candidate_brier = mean(briers)
    reference_brier = mean(reference_briers)
    gates = {
        "minimum_selected_days": len(trades) >= plan["minimum_selected_days_before_label_open"],
        "minimum_aggregate_net_return": summary["aggregate_realized_net_return"] >= plan["evaluation_gates"]["minimum_aggregate_net_return"],
        "minimum_positive_folds": positive_folds >= plan["evaluation_gates"]["minimum_positive_folds"],
        "minimum_worst_fold_return": worst_fold >= plan["evaluation_gates"]["minimum_worst_fold_return"],
        "positive_adverse_fill_return": _summary(stressed)["aggregate_realized_net_return"] > 0,
        "positive_best_day_removed_return": best_removed["aggregate_realized_net_return"] > 0,
        "brier_no_worse_than_frozen_reference": candidate_brier <= reference_brier + 1e-12,
    }
    grade_counts = Counter(row["execution_evidence_grade"] for row in trades)
    role_counts = Counter(row["confirmation_window_role"] for row in trades)
    source_counts = Counter(row["settlement_source"] for row in trades)
    offline_pass = all(gates.values())
    verified_fills = all(
        row["execution_evidence_grade"] == "A" and row["assumed_fill"] is False
        for row in trades
    )
    body = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "primary_strategy_candidate_id": strategy["candidate_id"],
        **summary,
        "temporal_folds": folds,
        "positive_fold_count": positive_folds,
        "worst_nonempty_fold_return": worst_fold,
        "adverse_fill_stress_cents": stress_cents,
        "adverse_fill_stress": _summary(stressed),
        "best_day_removed": best_removed,
        "multiclass_brier": candidate_brier,
        "frozen_reference_multiclass_brier": reference_brier,
        "execution_grade_counts": {
            grade: grade_counts.get(grade, 0) for grade in ("A", "B_PLUS", "B")
        },
        "confirmation_window_role_counts": dict(sorted(role_counts.items())),
        "settlement_source_counts": dict(sorted(source_counts.items())),
        "evaluation_gates": gates,
        "ten_percent_target_confirmed_under_registered_offline_evidence": offline_pass,
        "verified_fill_profitability_confirmed": offline_pass and verified_fills,
        "trades": trades,
        "source_bindings": {
            path.as_posix(): _file_hash(workspace / path)
            for path in (PLAN, UNIVERSE, SELECTION_FREEZE, PREDICTIONS, LABELS, LABEL_MANIFEST)
        } | {plan["primary_strategy"]["strategy_freeze_path"]: _file_hash(strategy_path)},
        "one_shot_evaluation_consumed": True,
        "strategy_substitution_performed": False,
        "parameter_search_performed": False,
        "network_used_by_evaluator": False,
        "protected_confirmation_labels_read": True,
        "settlement_outcomes_read": True,
        "actual_orders_placed": False,
        "limitations": [
            "Grades B and B+ are historical quote or proxy evidence and are not verified fills.",
            "September contracts settle from The Weather Company while May and August contracts use the NWS Climatological Report.",
            "Passing the offline screen does not guarantee future profitability.",
        ],
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def write(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    path = workspace / OUTPUT
    if path.exists():
        existing = _load(path)
        _verify(existing)
        return existing
    value = build(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)
    return value


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    print(json.dumps(write(args.project_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
