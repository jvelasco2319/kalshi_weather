"""Outcome-blind readiness audit for a one-shot V5B confirmation.

This module deliberately reads no settlement outcome or protected-label file.
It applies the frozen V5B selection policy to the still-sealed V5A holdout,
inventories the proposed untouched confirmation windows, and writes a
hash-bound readiness artifact.  It never fetches data or scores returns.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from v5a.development_search import _fee
from v5b.evaluation import _adjust, validate_spec


PLAN = Path("configs/v5b_untouched_confirmation_plan.json")
HOLDOUT_SEAL = Path("data/manifests/v5a_holdout_seal.json")
UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
PREDICTIONS = Path("data/normalized/v5a/frozen_leader_probabilities.parquet")
FULL_DEPTH_ROOT = Path("data/raw/v5p/probalytics/full_history_20260601_20260831")
TRIAL_MANIFEST = Path("data/raw/v5p/probalytics/manifest.json")
TRIAL_COVERAGE = Path("data/raw/v5p/probalytics/kxhighlax_1800_coverage.csv")
WEATHER_ROOT = Path("data/normalized/v5p_probability_features")
DEFAULT_OUTPUT = Path("data/manifests/v5b_untouched_confirmation_readiness.json")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha(value: dict) -> str:
    payload = {key: item for key, item in value.items() if key != "self_sha256"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _dates(start: str, end: str) -> list[str]:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    result = []
    current = first
    while current <= last:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def _planned_dates(plan: dict) -> tuple[list[str], dict[str, list[str]]]:
    by_role = {
        window["role"]: _dates(window["date_start"], window["date_end"])
        for window in plan["confirmation_windows"]
    }
    all_dates = sorted(day for days in by_role.values() for day in days)
    if len(all_dates) != len(set(all_dates)):
        raise ValueError("confirmation windows overlap")
    return all_dates, by_role


def _holdout_selections(root: Path, plan: dict, seal: dict, universe: dict) -> list[dict]:
    freeze_path = root / plan["primary_strategy"]["strategy_freeze_path"]
    freeze = _load(freeze_path)
    if freeze["candidate_id"] != plan["primary_strategy"]["candidate_id"]:
        raise ValueError("frozen V5B candidate identity differs")
    parameters = validate_spec(freeze["parameters"])
    if parameters["calibration"] != "none":
        raise ValueError("outcome-blind selector supports only the frozen no-calibration policy")
    if seal.get("holdout_labels_opened") or seal.get("holdout_evaluations_consumed") != 0:
        raise ValueError("holdout seal is no longer pristine")
    holdout_dates = seal["holdout_dates"]
    if holdout_dates != universe["split"]["holdout_dates"]:
        raise ValueError("holdout date binding differs")

    frame = pd.read_parquet(root / PREDICTIONS)
    frame = frame[
        (frame.partition == "holdout")
        & frame.climate_date.astype(str).isin(holdout_dates)
    ]
    probabilities = {
        (str(row.climate_date), str(row.market_ticker)): float(row.yes_probability)
        for row in frame.itertuples(index=False)
    }
    rows_by_date = {day: [] for day in holdout_dates}
    for row in universe["records"]:
        if row["partition"] == "holdout" and row["climate_date"] in rows_by_date:
            rows_by_date[row["climate_date"]].append(row)

    selections = []
    for day in holdout_dates:
        rows = rows_by_date[day]
        yes_rows = sorted(
            (row for row in rows if row["contract_side"] == "YES"),
            key=lambda row: (
                -10000 if row["floor_strike"] is None else float(row["floor_strike"]),
                row["market_ticker"],
            ),
        )
        if len(yes_rows) < 2:
            raise ValueError(f"invalid bracket universe for {day}")
        base = [probabilities[(day, row["market_ticker"])] for row in yes_rows]
        adjusted = _adjust(base, parameters)
        if abs(sum(adjusted) - 1.0) > 1e-6:
            raise ValueError(f"probability mass differs for {day}")
        ordered = sorted(adjusted, reverse=True)
        entropy = -sum(value * math.log(value) for value in adjusted if value > 0) / math.log(len(adjusted))
        abstain = entropy > parameters["maximum_entropy"] + 1e-12
        abstain = abstain or ordered[0] - ordered[1] < parameters["minimum_probability_gap"]
        by_ticker = {
            row["market_ticker"]: probability
            for row, probability in zip(yes_rows, adjusted)
        }
        candidates = []
        for row in ([] if abstain else rows):
            grade, side = row["execution_evidence_grade"], row["contract_side"]
            tail = row["strike_type"] in {"less", "greater"}
            if grade not in parameters["allowed_grades"] or side not in parameters["allowed_sides"]:
                continue
            if parameters["tail_policy"] == "tails" and not tail:
                continue
            if parameters["tail_policy"] == "interior" and tail:
                continue
            ask, bid = row["execution_price_cents"], row["bid_price_cents"]
            if ask is None:
                continue
            price = int(ask) + parameters["additional_adverse_price_cents"]
            spread = 100 if bid is None else int(ask) - int(bid)
            if not parameters["minimum_price_cents"] <= price <= parameters["maximum_price_cents"]:
                continue
            if not 0 < price < 100 or spread < 0 or spread > parameters["maximum_spread_cents"]:
                continue
            probability = by_ticker[row["market_ticker"]]
            probability = max(
                0.0,
                (probability if side == "YES" else 1.0 - probability)
                - parameters["probability_haircut"],
            )
            fee = float(_fee(day, price))
            outlay = price / 100 + fee
            expected_profit = probability - outlay
            expected_return = expected_profit / outlay
            if expected_return < parameters["minimum_expected_net_return"]:
                continue
            candidates.append(
                {
                    "climate_date": day,
                    "market_ticker": row["market_ticker"],
                    "contract_side": side,
                    "execution_evidence_grade": grade,
                    "entry_price_cents": price,
                    "bid_price_cents": bid,
                    "spread_cents": spread,
                    "model_probability": probability,
                    "expected_net_return": expected_return,
                    "expected_profit_dollars": expected_profit,
                }
            )
        if candidates:
            field = (
                "expected_net_return"
                if parameters["selection_mode"] == "expected_return"
                else "expected_profit_dollars"
            )
            selections.append(
                max(
                    candidates,
                    key=lambda item: (
                        item[field],
                        -item["entry_price_cents"],
                        item["market_ticker"],
                        item["contract_side"],
                    ),
                )
            )
    return selections


def _local_market_dates(root: Path) -> tuple[set[str], set[str], set[str]]:
    audited_full_depth = set()
    usable_full_depth = set()
    source = root / FULL_DEPTH_ROOT
    if source.exists():
        for path in source.glob("date=*/manifest.json"):
            manifest = _load(path)
            audited_full_depth.add(str(manifest["climate_date"]))
            if int(manifest.get("target_rows", 0)) > 0:
                usable_full_depth.add(str(manifest["climate_date"]))
    trial = set()
    trial_manifest_path = root / TRIAL_MANIFEST
    coverage_path = root / TRIAL_COVERAGE
    if trial_manifest_path.exists() and coverage_path.exists():
        manifest = _load(trial_manifest_path)
        if manifest.get("credentials_persisted") is not False:
            raise ValueError("trial credential policy differs")
        with coverage_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if int(row["snapshots"]) > 0:
                    trial.add(str(row["d"]))
    return audited_full_depth, usable_full_depth, trial


def build(project_root: str | Path = ".") -> dict:
    root = Path(project_root).resolve()
    plan = _load(root / PLAN)
    seal = _load(root / HOLDOUT_SEAL)
    universe = _load(root / UNIVERSE)
    planned, by_role = _planned_dates(plan)
    selections = _holdout_selections(root, plan, seal, universe)
    grade_counts = Counter(row["execution_evidence_grade"] for row in selections)

    weather_dates = {
        path.name.split("=", 1)[1]
        for path in (root / WEATHER_ROOT).glob("date=*")
        if (path / "manifest.json").exists()
    }
    prediction_frame = pd.read_parquet(root / PREDICTIONS, columns=["climate_date"])
    prediction_dates = set(prediction_frame.climate_date.astype(str))
    audited_full_depth_dates, usable_full_depth_dates, trial_dates = _local_market_dates(root)
    planned_set = set(planned)
    weather_ready = sorted(planned_set & weather_dates)
    probability_ready = sorted(planned_set & prediction_dates)
    market_audited = sorted(planned_set & audited_full_depth_dates)
    normalized_market_usable = sorted(planned_set & usable_full_depth_dates)
    trial_market_ready = sorted(planned_set & trial_dates)

    minimum = int(plan["minimum_selected_days_before_label_open"])
    observed_rate = len(selections) / len(seal["holdout_dates"])
    projected = observed_rate * len(planned)
    minimum_calendar_days_at_observed_rate = math.ceil(minimum / observed_rate) if observed_rate else None
    all_input_ready = (
        len(weather_ready) == len(planned)
        and len(probability_ready) == len(planned)
        and len(market_audited) == len(planned)
    )
    selection_gate_ready = all_input_ready and len(selections) >= minimum

    freeze_path = root / plan["primary_strategy"]["strategy_freeze_path"]
    bindings = {
        str(path).replace("\\", "/"): _sha256(root / path)
        for path in (PLAN, HOLDOUT_SEAL, UNIVERSE, PREDICTIONS)
    }
    bindings[str(plan["primary_strategy"]["strategy_freeze_path"]).replace("\\", "/")] = _sha256(freeze_path)

    artifact = {
        "schema_version": "v5b-untouched-confirmation-readiness-v1",
        "status": "READY_TO_FREEZE_SELECTIONS" if selection_gate_ready else "BLOCKED_MISSING_UNTOUCHED_INPUTS",
        "primary_candidate_id": plan["primary_strategy"]["candidate_id"],
        "planned_calendar_date_count": len(planned),
        "planned_dates_by_role": by_role,
        "minimum_selected_days_before_label_open": minimum,
        "sealed_holdout": {
            "date_count": len(seal["holdout_dates"]),
            "outcome_blind_selected_day_count": len(selections),
            "selection_rate": observed_rate,
            "selected_dates": [row["climate_date"] for row in selections],
            "execution_grade_counts": {
                grade: grade_counts.get(grade, 0) for grade in ("A", "B_PLUS", "B")
            },
            "mean_model_implied_expected_net_return": (
                sum(row["expected_net_return"] for row in selections) / len(selections)
                if selections
                else None
            ),
            "returns_scored": False,
            "labels_opened": False,
            "evaluation_consumed": False,
        },
        "sample_planning": {
            "minimum_calendar_days_at_observed_holdout_selection_rate": minimum_calendar_days_at_observed_rate,
            "projected_selected_days_across_planned_window": projected,
            "projection_is_not_a_result": True,
        },
        "local_input_coverage": {
            "weather_ready_dates": len(weather_ready),
            "weather_missing_dates": sorted(planned_set - set(weather_ready)),
            "probability_ready_dates": len(probability_ready),
            "probability_missing_dates": sorted(planned_set - set(probability_ready)),
            "full_depth_market_audited_dates": len(market_audited),
            "full_depth_market_audit_missing_dates": sorted(planned_set - set(market_audited)),
            "normalized_full_depth_market_usable_dates": len(normalized_market_usable),
            "audited_but_unavailable_market_dates": sorted(
                set(market_audited) - set(normalized_market_usable)
            ),
            "trial_raw_market_dates_requiring_new_registration_and_normalization": trial_market_ready,
        },
        "readiness_gates": {
            "strategy_frozen": True,
            "holdout_seal_pristine": True,
            "all_planned_weather_ready": len(weather_ready) == len(planned),
            "all_planned_probabilities_ready": len(probability_ready) == len(planned),
            "all_planned_market_dates_audited": len(market_audited) == len(planned),
            "selected_day_minimum_met": False,
            "event_rules_and_fee_schedules_bound_for_all_dates": False,
            "exposure_audit_complete": False,
            "outcomes_authorized_to_open": False,
        },
        "next_actions": [
            "Authenticate to the paid Probalytics historical archive without persisting credentials.",
            "Acquire and normalize Level-2 snapshots for 2026-05-09 through 2026-05-31 and 2026-09-01 through 2026-09-27.",
            "Acquire HRRR and GEFS inputs and generate frozen V4 probabilities for the same 50 missing dates.",
            "Bind exact KXHIGHLAX event rules and historical fees for every proposed date.",
            "Complete the exposure audit, freeze the 78-date universe, and recompute selections without outcomes.",
            "Open outcomes exactly once only if at least 30 selections exist and every readiness gate passes."
        ],
        "source_bindings": bindings,
        "protected_confirmation_labels_read": False,
        "holdout_labels_opened": False,
        "actual_orders_placed": False,
        "network_used_by_audit": False,
    }
    artifact["self_sha256"] = _canonical_sha(artifact)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    artifact = build(root)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".pending")
    temporary.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps(artifact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
