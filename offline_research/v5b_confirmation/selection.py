"""Apply the frozen V5B policy and freeze selections before any labels are read."""

from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from v5a.development_search import _fee
from v5b.evaluation import _adjust, validate_spec

from .predictions import MANIFEST as PREDICTION_MANIFEST, OUTPUT as PREDICTIONS
from .universe import OUTPUT as UNIVERSE


PLAN = Path("configs/v5b_untouched_confirmation_plan.json")
READINESS = Path("data/manifests/v5b_untouched_confirmation_readiness_v2.json")
SELECTION_FREEZE = Path("data/manifests/v5b_untouched_selection_freeze.json")
READINESS_SCHEMA = "klax-v5b-untouched-confirmation-readiness-v2"
FREEZE_SCHEMA = "klax-v5b-untouched-selection-freeze-v1"


class ConfirmationSelectionError(ValueError):
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
        raise ConfirmationSelectionError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise ConfirmationSelectionError(f"{field} mismatch")


def select_days(
    dates: list[str], records: list[Mapping[str, Any]], probabilities: pd.DataFrame,
    parameters: Mapping[str, Any],
) -> list[dict[str, Any]]:
    spec = validate_spec(parameters)
    if spec["calibration"] != "none":
        raise ConfirmationSelectionError("confirmation selector cannot refit calibration")
    probability_by_key = {
        (str(row.climate_date), str(row.market_ticker)): float(row.yes_probability)
        for row in probabilities.itertuples(index=False)
    }
    rows_by_date: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        rows_by_date[str(row["climate_date"])].append(row)
    selections: list[dict[str, Any]] = []
    for day in dates:
        rows = rows_by_date[day]
        yes_rows = sorted(
            (row for row in rows if row["contract_side"] == "YES"),
            key=lambda row: (
                -10000 if row["floor_strike"] is None else float(row["floor_strike"]),
                row["market_ticker"],
            ),
        )
        if len(rows) != 12 or len(yes_rows) != 6:
            raise ConfirmationSelectionError(f"contract partition differs: {day}")
        try:
            base = [probability_by_key[(day, row["market_ticker"])] for row in yes_rows]
        except KeyError as exc:
            raise ConfirmationSelectionError(f"probability missing: {day}") from exc
        adjusted = _adjust(base, spec)
        if abs(sum(adjusted) - 1.0) > 1e-6:
            raise ConfirmationSelectionError(f"probability mass differs: {day}")
        ordered = sorted(adjusted, reverse=True)
        entropy = -sum(value * math.log(value) for value in adjusted if value > 0) / math.log(len(adjusted))
        abstain = entropy > spec["maximum_entropy"] + 1e-12
        abstain = abstain or ordered[0] - ordered[1] < spec["minimum_probability_gap"]
        by_ticker = {
            row["market_ticker"]: probability
            for row, probability in zip(yes_rows, adjusted)
        }
        candidates = []
        for row in ([] if abstain else rows):
            grade, side = row["execution_evidence_grade"], row["contract_side"]
            tail = row["strike_type"] in {"less", "greater"}
            if grade not in spec["allowed_grades"] or side not in spec["allowed_sides"]:
                continue
            if spec["tail_policy"] == "tails" and not tail:
                continue
            if spec["tail_policy"] == "interior" and tail:
                continue
            ask, bid = row["execution_price_cents"], row["bid_price_cents"]
            if ask is None:
                continue
            price = int(ask) + spec["additional_adverse_price_cents"]
            spread = 100 if bid is None else int(ask) - int(bid)
            if not spec["minimum_price_cents"] <= price <= spec["maximum_price_cents"]:
                continue
            if not 0 < price < 100 or spread < 0 or spread > spec["maximum_spread_cents"]:
                continue
            probability = by_ticker[row["market_ticker"]]
            probability = max(
                0.0,
                (probability if side == "YES" else 1.0 - probability)
                - spec["probability_haircut"],
            )
            fee = float(_fee(day, price))
            outlay = price / 100 + fee
            expected_profit = probability - outlay
            expected_return = expected_profit / outlay
            if expected_return < spec["minimum_expected_net_return"]:
                continue
            candidates.append({
                "climate_date": day,
                "confirmation_window_role": row["confirmation_window_role"],
                "settlement_source": row["settlement_sources"][0]["name"],
                "market_ticker": row["market_ticker"],
                "contract_side": side,
                "execution_evidence_grade": grade,
                "entry_price_cents": price,
                "bid_price_cents": bid,
                "spread_cents": spread,
                "model_probability": probability,
                "fee_dollars": fee,
                "expected_net_return": expected_return,
                "expected_profit_dollars": expected_profit,
                "assumed_fill": row["assumed_fill"],
                "execution_source_kind": row["execution_source_kind"],
            })
        if candidates:
            field = (
                "expected_net_return"
                if spec["selection_mode"] == "expected_return"
                else "expected_profit_dollars"
            )
            selections.append(max(candidates, key=lambda item: (
                item[field], -item["entry_price_cents"], item["market_ticker"],
                item["contract_side"],
            )))
    return selections


def build(root: Path | str = ".") -> tuple[dict[str, Any], dict[str, Any] | None]:
    workspace = Path(root).resolve()
    plan = _load(workspace / PLAN)
    universe = _load(workspace / UNIVERSE)
    prediction_manifest = _load(workspace / PREDICTION_MANIFEST)
    _verify(universe)
    _verify(prediction_manifest)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("calendar_date_count") != 78
        or prediction_manifest.get("status") != "OUTCOME_BLIND_PROBABILITIES_COMPLETE"
        or prediction_manifest.get("date_count") != 78
        or prediction_manifest.get("universe", {}).get("self_sha256") != universe.get("self_sha256")
        or universe.get("outcomes_read") is not False
        or prediction_manifest.get("protected_confirmation_labels_read") is not False
    ):
        raise ConfirmationSelectionError("input identity or safety differs")
    freeze_path = workspace / plan["primary_strategy"]["strategy_freeze_path"]
    strategy = _load(freeze_path)
    if strategy.get("candidate_id") != plan["primary_strategy"]["candidate_id"]:
        raise ConfirmationSelectionError("frozen strategy identity differs")
    frame = pd.read_parquet(workspace / PREDICTIONS)
    selections = select_days(
        universe["dates"], universe["records"], frame, strategy["parameters"]
    )
    minimum = int(plan["minimum_selected_days_before_label_open"])
    grade_counts = Counter(row["execution_evidence_grade"] for row in selections)
    source_counts = Counter(row["settlement_source"] for row in selections)
    role_counts = Counter(row["confirmation_window_role"] for row in selections)
    exposure_audit_complete = (
        len({row["climate_date"] for row in selections}) == len(selections)
        and all(row["contract_side"] == "NO" for row in selections)
        and all(0 < row["entry_price_cents"] < 100 for row in selections)
        and all(row["expected_net_return"] >= 0.10 - 1e-12 for row in selections)
    )
    gates = {
        "strategy_frozen": True,
        "universe_frozen_before_outcomes": True,
        "all_78_weather_dates_bound": len(universe["source_bindings"]["weather_by_date"]) == 78,
        "all_78_probability_dates_ready": frame["climate_date"].astype(str).nunique() == 78,
        "all_market_dates_audited": True,
        "event_rules_exact_for_all_contracts": all(
            row["settlement_rule_revision_exact"] is True for row in universe["records"]
        ),
        "fee_schedules_bound_for_all_dates": all(
            row["fee_rule_ready"] is True for row in universe["records"]
        ),
        "selected_day_minimum_met": len(selections) >= minimum,
        "exposure_audit_complete": exposure_audit_complete,
    }
    authorized = all(gates.values())
    bindings = {
        path.as_posix(): _file_hash(workspace / path)
        for path in (PLAN, UNIVERSE, PREDICTIONS, PREDICTION_MANIFEST)
    }
    bindings[plan["primary_strategy"]["strategy_freeze_path"]] = _file_hash(freeze_path)
    readiness = {
        "schema_version": READINESS_SCHEMA,
        "status": "READY_FOR_ONE_SHOT_OUTCOME_OPEN" if authorized else "STOPPED_BEFORE_OUTCOMES",
        "primary_strategy_candidate_id": strategy["candidate_id"],
        "calendar_date_count": 78,
        "minimum_selected_days_before_label_open": minimum,
        "selected_day_count": len(selections),
        "selection_rate": len(selections) / 78,
        "selected_dates": [row["climate_date"] for row in selections],
        "selection_grade_counts": {
            grade: grade_counts.get(grade, 0) for grade in ("A", "B_PLUS", "B")
        },
        "selection_role_counts": dict(sorted(role_counts.items())),
        "selection_settlement_source_counts": dict(sorted(source_counts.items())),
        "mean_model_implied_expected_net_return": (
            sum(row["expected_net_return"] for row in selections) / len(selections)
            if selections else None
        ),
        "readiness_gates": gates,
        "outcomes_authorized_to_open": authorized,
        "source_bindings": bindings,
        "protected_confirmation_labels_read": False,
        "settlement_outcomes_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    readiness["self_sha256"] = _canonical_hash(readiness)
    selection_freeze = None
    if authorized:
        selection_freeze = {
            "schema_version": FREEZE_SCHEMA,
            "status": "FROZEN_BEFORE_OUTCOMES",
            "primary_strategy_candidate_id": strategy["candidate_id"],
            "strategy_freeze_sha256": bindings[plan["primary_strategy"]["strategy_freeze_path"]],
            "universe_self_sha256": universe["self_sha256"],
            "prediction_manifest_self_sha256": prediction_manifest["self_sha256"],
            "readiness_self_sha256": readiness["self_sha256"],
            "selected_day_count": len(selections),
            "selections": selections,
            "one_trade_per_selected_day": True,
            "labels_opened": False,
            "outcomes_read": False,
            "protected_confirmation_labels_read": False,
            "actual_orders_placed": False,
        }
        selection_freeze["self_sha256"] = _canonical_hash(selection_freeze)
    return readiness, selection_freeze


def write(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    readiness, selection_freeze = build(workspace)
    readiness_path = workspace / READINESS
    readiness_path.parent.mkdir(parents=True, exist_ok=True)
    pending = readiness_path.with_name(readiness_path.name + ".pending")
    pending.write_text(json.dumps(readiness, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(readiness_path)
    if selection_freeze is not None:
        freeze_path = workspace / SELECTION_FREEZE
        if freeze_path.exists():
            if _load(freeze_path) != selection_freeze:
                raise ConfirmationSelectionError("immutable selection freeze differs")
        else:
            temporary = freeze_path.with_name(freeze_path.name + ".pending")
            temporary.write_text(
                json.dumps(selection_freeze, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(freeze_path)
    return readiness


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    print(json.dumps(write(args.project_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
