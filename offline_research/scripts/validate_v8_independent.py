"""Independent numerical verifier for the frozen V8 probability repair and replay.

This verifier intentionally does not import ``v8.probability_repair`` or
``v8.v7i_fixed_replay``.  It reconstructs the registered rolling-confusion
candidate, forecast scores, frozen 2026 mapping, book-backed simulated fills,
fees, returns, stress result, folds, and bootstrap directly from frozen inputs.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence
import zipfile

import numpy as np


V7Y_RUN = Path("runs/replays/v7y-hrrr-gefs-calendar-2025")
V7Y_FREEZE = V7Y_RUN / "prediction-freeze.json"
V7Y_SUMMARY = V7Y_RUN / "summary.json"
V7Y_SCORED = V7Y_RUN / "scored-days.csv"
V8_SPEC = Path("v8/probability_repair_spec.json")
V8_RESULTS = Path("runs/v8/probability-repair/results.json")
V8_DAILY = Path("runs/v8/probability-repair/daily-leader.csv")
V8_CANDIDATES = Path("runs/v8/probability-repair/candidates.csv")
V8_CAMPAIGN = Path("configs/v8_campaign.json")
V8_ECONOMICS_AUDIT = Path("runs/v8/adversarial-economics-audit.json")
V7I_SOURCE = Path("runs/replays/v7i-historical-20260730-20260927/prediction-order-freeze.json")
V7I_OUTCOMES = Path("runs/replays/v7i-historical-20260730-20260927/settlement-outcomes.jsonl")
V8_FORWARD_FREEZE = Path("runs/v8/v7i-static-confusion-replay/forecast-order-freeze.json")
V8_FORWARD_SCORE = Path("runs/v8/v7i-static-confusion-replay/score.json")
OUTPUT = Path("runs/v8/independent-validation")
PRIMARY_2025_START = "2025-02-04"
CANDIDATE_ID = "rolling_confusion-alpha-2-w-0.75"
ABS_TOL = 1e-12


class VerificationError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise VerificationError(f"JSON object required: {path}")
    return value


def assert_seal(value: Mapping[str, Any], label: str) -> None:
    if value.get("self_sha256") != canonical_sha256(value):
        raise VerificationError(f"self seal mismatch: {label}")


def close(left: float, right: float, *, label: str, tol: float = ABS_TOL) -> None:
    if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tol):
        raise VerificationError(f"{label}: {left!r} != {right!r}")


def normalize(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != 6 or any(not math.isfinite(value) or value < 0 for value in result):
        raise VerificationError("expected six finite nonnegative values")
    total = sum(result)
    if total <= 0:
        raise VerificationError("probability mass is not positive")
    output = tuple(value / total for value in result)
    close(sum(output), 1.0, label="normalization")
    return output


def modal_index(values: Sequence[float]) -> int:
    # This is the frozen tie rule: the higher bracket position wins an exact tie.
    return max(range(6), key=lambda index: (float(values[index]), index))


def brier(values: Sequence[float], actual: int) -> float:
    return sum((float(value) - float(index == actual)) ** 2
               for index, value in enumerate(values))


def log_loss(values: Sequence[float], actual: int) -> float:
    return -math.log(max(float(values[actual]), 1e-15))


def score_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise VerificationError("cannot score an empty cohort")
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": mean(float(row["brier"]) for row in rows),
        "mean_log_loss": mean(float(row["log_loss"]) for row in rows),
        "modal_bracket_accuracy": mean(bool(row["modal_hit"]) for row in rows),
        "mean_true_bracket_probability": mean(
            float(row["probabilities"][int(row["actual_index"])]) for row in rows
        ),
        "zero_true_probability_count": sum(
            float(row["probabilities"][int(row["actual_index"])]) == 0.0
            for row in rows
        ),
        "minimum_probability": min(min(float(value) for value in row["probabilities"])
                                   for row in rows),
    }


def compare_metrics(recomputed: Mapping[str, Any], reported: Mapping[str, Any], label: str) -> None:
    integer_fields = ("date_count", "zero_true_probability_count")
    float_fields = (
        "mean_multiclass_brier", "mean_log_loss", "modal_bracket_accuracy",
        "mean_true_bracket_probability", "minimum_probability",
    )
    for field in integer_fields:
        if int(recomputed[field]) != int(reported[field]):
            raise VerificationError(f"{label}/{field} differs")
    for field in float_fields:
        close(float(recomputed[field]), float(reported[field]), label=f"{label}/{field}")


def verify_binding_set(root: Path, bindings: Mapping[str, str], label: str) -> int:
    checked = 0
    for relative, expected in bindings.items():
        path = root / str(relative)
        if not path.is_file():
            raise VerificationError(f"missing bound input {label}: {relative}")
        if file_sha256(path) != str(expected):
            raise VerificationError(f"bound input changed {label}: {relative}")
        checked += 1
    return checked


def load_2025_examples(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    freeze = read_object(root / V7Y_FREEZE)
    summary = read_object(root / V7Y_SUMMARY)
    assert_seal(freeze, V7Y_FREEZE.as_posix())
    assert_seal(summary, V7Y_SUMMARY.as_posix())
    if freeze.get("status") != "FROZEN_BEFORE_CLILAX_LABEL_READ":
        raise VerificationError("V7Y prediction artifact is not a pre-label freeze")
    if len(freeze.get("records", [])) != 361:
        raise VerificationError("V7Y prediction count differs")
    if summary.get("prediction_freeze_sha256") != freeze.get("self_sha256"):
        raise VerificationError("V7Y score is not bound to the prediction freeze")
    label_bindings = summary.get("label_bindings", {})
    archives = [str(relative) for relative in label_bindings if str(relative).lower().endswith(".zip")]
    if len(archives) != 1:
        raise VerificationError("V7Y summary must bind exactly one CLILAX archive")
    archive_path = root / archives[0]
    if file_sha256(archive_path) != str(label_bindings[archives[0]]):
        raise VerificationError("CLILAX archive differs from the sealed V7Y summary")
    month_number = {
        name: index for index, name in enumerate((
            "", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
            "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
        )) if name
    }
    date_pattern = re.compile(
        r"CLIMATE SUMMARY FOR ([A-Z]+)\s+(\d{1,2})\s+(\d{4})"
    )
    maximum_pattern = re.compile(r"^\s+MAXIMUM\s+(-?\d+)(?:R)?\b", re.MULTILINE)
    quarantined = set(summary.get("label_audit", {}).get("quarantined_members", {}))
    raw_reports: dict[str, list[tuple[str, int]]] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.namelist():
            if member in quarantined:
                continue
            text = archive.read(member).decode("utf-8", errors="replace")
            date_match = date_pattern.search(text)
            maximum_match = maximum_pattern.search(text)
            if not date_match or not maximum_match:
                continue
            month_name, day_text, year_text = date_match.groups()
            if month_name not in month_number:
                continue
            climate_date = (
                f"{int(year_text):04d}-{month_number[month_name]:02d}-{int(day_text):02d}"
            )
            if climate_date.startswith("2025-"):
                raw_reports.setdefault(climate_date, []).append(
                    (member, int(maximum_match.group(1)))
                )
    # The archive can contain preliminary and corrected reports.  The final
    # lexicographic member timestamp is the final archived issuance.
    raw_highs = {
        day: sorted(reports, key=lambda item: item[0])[-1][1]
        for day, reports in raw_reports.items()
    }
    if len(raw_highs) != 363:
        raise VerificationError("unexpected direct CLILAX 2025 coverage")

    # Cross-check the existing V7Y daily audit, but do not use it as the label
    # source for this verification.
    scored_audit: dict[str, dict[str, str]] = {}
    with (root / V7Y_SCORED).open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            day = str(row["climate_date"])
            if day in scored_audit:
                raise VerificationError(f"duplicate V7Y score date: {day}")
            scored_audit[day] = row
    if len(scored_audit) != 359:
        raise VerificationError("V7Y scored-day count differs")
    examples: list[dict[str, Any]] = []
    omitted: list[str] = []
    for record in freeze["records"]:
        day = str(record["climate_date"])
        if day not in raw_highs:
            omitted.append(day)
            continue
        tickers = [str(item["ticker"]) for item in record["contracts"]]
        if len(tickers) != 6 or len(set(tickers)) != 6:
            raise VerificationError(f"contract universe differs: {day}")
        probability_map = record["yes_probabilities"]
        if set(tickers) != set(probability_map):
            raise VerificationError(f"probability keys differ: {day}")
        actual_high = int(raw_highs[day])
        winning_indices = []
        for index, contract in enumerate(record["contracts"]):
            strike = str(contract["strike_type"])
            if strike == "less":
                won = actual_high < int(contract["cap_strike"])
            elif strike == "between":
                won = int(contract["floor_strike"]) <= actual_high <= int(contract["cap_strike"])
            elif strike == "greater":
                won = actual_high > int(contract["floor_strike"])
            else:
                raise VerificationError(f"unsupported contract strike: {day}/{strike}")
            if won:
                winning_indices.append(index)
        if len(winning_indices) != 1:
            raise VerificationError(f"CLILAX high has no unique winning bracket: {day}")
        actual_index = winning_indices[0]
        winning_ticker = tickers[actual_index]
        audit = scored_audit.get(day)
        if (
            audit is None
            or int(audit["reported_high_f"]) != actual_high
            or str(audit["winning_ticker"]) != winning_ticker
        ):
            raise VerificationError(f"V7Y daily audit differs from direct CLILAX parse: {day}")
        base = normalize(probability_map[ticker] for ticker in tickers)
        examples.append({
            "date": day,
            "base": base,
            "base_mode": modal_index(base),
            "actual_index": actual_index,
        })
    if omitted != ["2025-06-10", "2025-06-11"]:
        raise VerificationError(f"unexpected V7Y exclusions: {omitted}")
    dates = [row["date"] for row in examples]
    if dates != sorted(set(dates)) or len(examples) != 359:
        raise VerificationError("V7Y examples are not an ordered 359-day cohort")
    return examples, {
        "freeze": freeze,
        "summary": summary,
        "omitted": omitted,
        "direct_clilax_archive_member_count": sum(len(values) for values in raw_reports.values()),
        "direct_clilax_final_2025_date_count": len(raw_highs),
    }


def rolling_prediction(
    base: Sequence[float], base_mode: int, history: Sequence[Mapping[str, Any]],
) -> tuple[float, ...]:
    # Dirichlet alpha=2 for each of six output positions, followed by the
    # registered 25% base / 75% conditional-confusion blend.
    counts = [2.0] * 6
    for prior in history:
        if int(prior["base_mode"]) == base_mode:
            counts[int(prior["actual_index"])] += 1.0
    total = sum(counts)
    conditional = [value / total for value in counts]
    return normalize(0.25 * float(value) + 0.75 * conditional[index]
                     for index, value in enumerate(base))


def reconstruct_2025(examples: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    history: list[Mapping[str, Any]] = []
    output: list[dict[str, Any]] = []
    leakage_violations = 0
    for example in examples:
        day = str(example["date"])
        if any(str(prior["date"]) >= day for prior in history):
            leakage_violations += 1
        probabilities = rolling_prediction(
            example["base"], int(example["base_mode"]), history,
        )
        actual = int(example["actual_index"])
        output.append({
            "date": day,
            "probabilities": probabilities,
            "actual_index": actual,
            "brier": brier(probabilities, actual),
            "log_loss": log_loss(probabilities, actual),
            "modal_hit": modal_index(probabilities) == actual,
            "prior_outcome_count": len(history),
        })
        history.append(example)
    return output, leakage_violations


def verify_probability_repair(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    examples, source = load_2025_examples(root)
    results = read_object(root / V8_RESULTS)
    spec = read_object(root / V8_SPEC)
    campaign = read_object(root / V8_CAMPAIGN)
    assert_seal(results, V8_RESULTS.as_posix())
    if results.get("status") != "COMPLETE_DEVELOPMENT_ONLY":
        raise VerificationError("V8 probability repair is not complete")
    if results.get("leader", {}).get("candidate_id") != CANDIDATE_ID:
        raise VerificationError("reported V8 leader differs")
    registered = spec.get("cross_colony_candidate_fixed_before_final_run", {})
    if (
        registered.get("candidate_id") != CANDIDATE_ID
        or float(registered.get("dirichlet_alpha_per_bracket")) != 2.0
        or float(registered.get("blend_weight")) != 0.75
        or registered.get("updates") != "strictly prior outcomes only"
    ):
        raise VerificationError("registered fixed candidate differs")
    if file_sha256(root / V8_SPEC) != results["spec_binding"]["sha256"]:
        raise VerificationError("V8 result spec binding differs")
    if file_sha256(root / V8_SPEC) != campaign["forecast_development"]["probability_spec_sha256"]:
        raise VerificationError("campaign probability-spec binding differs")
    if file_sha256(root / V8_RESULTS) != campaign["forecast_development"]["probability_result_sha256"]:
        raise VerificationError("campaign probability-result binding differs")
    if campaign["frozen_leader"]["candidate_id"] != CANDIDATE_ID:
        raise VerificationError("campaign frozen leader differs")
    result_binding_count = verify_binding_set(root, results["input_bindings"], "V8 probability result")

    catalog_rows: list[dict[str, str]] = []
    with (root / V8_CANDIDATES).open("r", encoding="utf-8-sig", newline="") as stream:
        catalog_rows = list(csv.DictReader(stream))
    if len(catalog_rows) != 104 or len({row["candidate_id"] for row in catalog_rows}) != 104:
        raise VerificationError("finite V8 catalog differs")
    selected = [row for row in catalog_rows if row["candidate_id"] == CANDIDATE_ID]
    if len(selected) != 1 or int(selected[0]["rank"]) != 1:
        raise VerificationError("candidate is not the unique rank-one catalog entry")
    all_gate_count = sum(row["passes_all_development_gates"].lower() == "true"
                         for row in catalog_rows)
    if all_gate_count != int(results["all_gate_candidate_count"]):
        raise VerificationError("all-gate catalog count differs")

    reconstructed, leakage_violations = reconstruct_2025(examples)
    if leakage_violations:
        raise VerificationError("rolling candidate consumed a current or future label")
    primary = [row for row in reconstructed if row["date"] >= PRIMARY_2025_START]
    if len(primary) != 329 or primary[0]["prior_outcome_count"] != 30:
        raise VerificationError("V8 primary/warmup partition differs")
    daily_rows: list[dict[str, str]] = []
    with (root / V8_DAILY).open("r", encoding="utf-8-sig", newline="") as stream:
        daily_rows = list(csv.DictReader(stream))
    if len(daily_rows) != len(primary):
        raise VerificationError("V8 daily leader row count differs")
    for expected, reported in zip(primary, daily_rows, strict=True):
        if reported["climate_date"] != expected["date"]:
            raise VerificationError("V8 daily chronology differs")
        values = json.loads(reported["probabilities"])
        for index, value in enumerate(expected["probabilities"]):
            close(value, values[index], label=f"daily probability {expected['date']}/{index}")
        if int(reported["actual_index"]) != int(expected["actual_index"]):
            raise VerificationError(f"daily actual index differs: {expected['date']}")
        close(expected["brier"], float(reported["brier"]), label=f"daily Brier {expected['date']}")
        close(expected["log_loss"], float(reported["log_loss"]), label=f"daily log {expected['date']}")
        if (reported["modal_hit"].lower() == "true") != bool(expected["modal_hit"]):
            raise VerificationError(f"daily modal hit differs: {expected['date']}")

    aggregate = score_rows(primary)
    compare_metrics(aggregate, results["leader"], "V8 leader aggregate")
    fold_ranges = [(len(primary) * index // 5, len(primary) * (index + 1) // 5)
                   for index in range(5)]
    fold_metrics = [score_rows(primary[start:end]) for start, end in fold_ranges]
    for index, (recomputed, reported) in enumerate(zip(
        fold_metrics, results["leader"]["folds"], strict=True
    ), 1):
        compare_metrics(recomputed, reported, f"V8 leader fold {index}")

    identity_rows = []
    for example in examples:
        if example["date"] < PRIMARY_2025_START:
            continue
        values = example["base"]
        actual = int(example["actual_index"])
        identity_rows.append({
            "probabilities": values,
            "actual_index": actual,
            "brier": brier(values, actual),
            "log_loss": log_loss(values, actual),
            "modal_hit": modal_index(values) == actual,
        })
    identity = score_rows(identity_rows)
    compare_metrics(identity, results["baselines"]["identity"], "V7Y identity baseline")
    identity_folds = [score_rows(identity_rows[start:end]) for start, end in fold_ranges]
    brier_better = sum(left["mean_multiclass_brier"] < right["mean_multiclass_brier"]
                       for left, right in zip(fold_metrics, identity_folds, strict=True))
    log_better = sum(left["mean_log_loss"] < right["mean_log_loss"]
                     for left, right in zip(fold_metrics, identity_folds, strict=True))
    if brier_better != 5 or log_better != 5:
        raise VerificationError("V8 fold-improvement count differs")

    return {
        "status": "PASS",
        "candidate_id": CANDIDATE_ID,
        "candidate_registration_sha256": file_sha256(root / V8_SPEC),
        "result_self_sha256": results["self_sha256"],
        "result_file_sha256": file_sha256(root / V8_RESULTS),
        "campaign_result_binding_verified": True,
        "bound_input_file_count": result_binding_count,
        "catalog_candidate_count": len(catalog_rows),
        "all_gate_candidate_count": all_gate_count,
        "primary_date_count": len(primary),
        "warmup_outcomes_before_first_primary_prediction": primary[0]["prior_outcome_count"],
        "strictly_prior_only_violation_count": leakage_violations,
        "aggregate": aggregate,
        "folds": fold_metrics,
        "brier_better_than_identity_fold_count": brier_better,
        "log_loss_better_than_identity_fold_count": log_better,
        "identity": identity,
        "excluded_dates": source["omitted"],
        "direct_clilax_archive_member_count": source["direct_clilax_archive_member_count"],
        "direct_clilax_final_2025_date_count": source["direct_clilax_final_2025_date_count"],
        "development_only": results.get("data_status") == "2025_OUTCOMES_ALREADY_EXPOSED_NOT_CONFIRMATORY",
    }, examples


def static_confusion_mapping(examples: Sequence[Mapping[str, Any]]) -> list[tuple[float, ...]]:
    output: list[tuple[float, ...]] = []
    for mode in range(6):
        counts = [2.0] * 6
        for example in examples:
            if int(example["base_mode"]) == mode:
                counts[int(example["actual_index"])] += 1.0
        total = sum(counts)
        output.append(tuple(value / total for value in counts))
    return output


def static_prediction(base: Sequence[float], mapping: Sequence[Sequence[float]]) -> tuple[float, ...]:
    normalized = normalize(base)
    row = mapping[modal_index(normalized)]
    return normalize(0.25 * value + 0.75 * float(row[index])
                     for index, value in enumerate(normalized))


def parse_timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def fee_for(day: str, price_cents: int) -> Decimal:
    price = Decimal(price_cents) / Decimal(100)
    raw = Decimal("0.07") * price * (Decimal(1) - price)
    quantum = Decimal("0.01") if day < "2026-07-07" else Decimal("0.0001")
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def load_outcomes(path: Path, records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    ticker_day = {
        str(item["market_ticker"]): str(record["climate_date"])
        for record in records for item in record["probabilities"]
    }
    result = {str(record["climate_date"]): {} for record in records}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ticker = str(row["platform_id"])
        if ticker not in ticker_day:
            raise VerificationError(f"outcome outside forecast universe: {ticker}")
        if row.get("status") != "RESOLVED" or row.get("resolution_type") != "STANDARD":
            raise VerificationError(f"invalid resolved outcome: {ticker}")
        day = ticker_day[ticker]
        if ticker in result[day]:
            raise VerificationError(f"duplicate outcome: {ticker}")
        result[day][ticker] = str(row["resolution_winning_outcome_id"]).upper()
    for day, values in result.items():
        if len(values) != 6 or list(values.values()).count("YES") != 1:
            raise VerificationError(f"nonexhaustive outcomes: {day}")
    return result


def raw_quote_for_fill(root: Path, freeze: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    day = str(record["climate_date"])
    order = record["order"]
    paths = [relative for relative in freeze["input_bindings"]
             if "target_books.jsonl" in relative and f"date={day}" in relative]
    if len(paths) != 1:
        raise VerificationError(f"filled day must bind exactly one book file: {day}")
    matched = []
    for line in (root / paths[0]).read_text(encoding="utf-8-sig").splitlines():
        row = json.loads(line)
        if (
            int(row["target_offset_s"]) == 5
            and str(row["market_platform_id"]) == str(order["market_ticker"])
            and str(row["outcome_name"]).upper() == str(order["contract_side"]).upper()
        ):
            matched.append(row)
    if len(matched) != 1:
        raise VerificationError(f"raw arrival quote is not unique: {day}")
    raw = matched[0]
    asks = raw["before_asks"]
    bids = raw["before_bids"]
    if not asks or not bids:
        raise VerificationError(f"raw arrival book has no two-sided top: {day}")
    best_ask = int(round(float(asks[0]["price"]) * 100))
    best_bid = int(round(float(bids[0]["price"]) * 100))
    if best_ask != int(order["arrival_best_ask_cents"]):
        raise VerificationError(f"arrival ask differs from raw book: {day}")
    if best_bid != int(order["arrival_best_bid_cents"]):
        raise VerificationError(f"arrival bid differs from raw book: {day}")
    close(float(asks[0]["size"]), float(order["arrival_ask_size"]),
          label=f"arrival size {day}")
    quote = parse_timestamp(str(raw["before_timestamp"]))
    target = parse_timestamp(f"{day}T18:00:05Z")
    age_ms = (target - quote).total_seconds() * 1000.0
    close(age_ms, float(order["arrival_quote_age_ms"]), label=f"arrival quote age {day}", tol=1e-6)
    if float(order["arrival_ask_size"]) < int(order["quantity"]):
        raise VerificationError(f"arrival depth cannot fill quantity: {day}")
    if order.get("arrival_failure_reasons"):
        raise VerificationError(f"filled order carries arrival failures: {day}")
    return {"path": paths[0], "quote_state": raw.get("before_state"), "quote_age_ms": age_ms}


def replay_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fills = [row for row in rows if row["execution_status"] == "FILLED"]
    outlay = sum((Decimal(str(row["entry_outlay_dollars"])) for row in fills), Decimal(0))
    profit = sum((Decimal(str(row["net_profit_dollars"])) for row in fills), Decimal(0))
    return {
        "date_count": len(rows),
        "filled_count": len(fills),
        "win_count": sum(bool(row["won"]) for row in fills),
        "total_entry_outlay_dollars": format(outlay, "f"),
        "total_net_profit_dollars": format(profit, "f"),
        "aggregate_return": None if not outlay else float(profit / outlay),
        "mean_multiclass_brier": mean(float(row["multiclass_brier"]) for row in rows),
        "mean_log_loss": mean(float(row["log_loss"]) for row in rows),
        "zero_probability_outcomes": sum(float(row["true_bracket_probability"]) == 0.0
                                         for row in rows),
    }


def compare_replay_summary(left: Mapping[str, Any], right: Mapping[str, Any], label: str) -> None:
    for field in ("date_count", "filled_count", "win_count", "zero_probability_outcomes"):
        if int(left[field]) != int(right[field]):
            raise VerificationError(f"{label}/{field} differs")
    for field in ("total_entry_outlay_dollars", "total_net_profit_dollars"):
        if Decimal(str(left[field])) != Decimal(str(right[field])):
            raise VerificationError(f"{label}/{field} differs")
    for field in ("aggregate_return", "mean_multiclass_brier", "mean_log_loss"):
        if left[field] is None or right[field] is None:
            if left[field] != right[field]:
                raise VerificationError(f"{label}/{field} differs")
        else:
            close(float(left[field]), float(right[field]), label=f"{label}/{field}")


def block_bootstrap(primary_rows: Sequence[Mapping[str, Any]], dates: Sequence[str]) -> float:
    daily = {day: (Decimal(0), Decimal(0)) for day in dates}
    for row in primary_rows:
        if row["execution_status"] == "FILLED":
            daily[str(row["climate_date"])] = (
                Decimal(str(row["entry_outlay_dollars"])),
                Decimal(str(row["net_profit_dollars"])),
            )
    values = [daily[day] for day in dates]
    rng = np.random.default_rng(20260929)
    returns: list[float] = []
    for _ in range(10_000):
        sample: list[tuple[Decimal, Decimal]] = []
        while len(sample) < len(values):
            start = int(rng.integers(0, len(values)))
            sample.extend(values[(start + offset) % len(values)] for offset in range(3))
        outlay = sum((item[0] for item in sample[:len(values)]), Decimal(0))
        profit = sum((item[1] for item in sample[:len(values)]), Decimal(0))
        returns.append(float(profit / outlay) if outlay else 0.0)
    return float(np.quantile(np.asarray(returns), 0.05, method="linear"))


def verify_forward_replay(
    root: Path, examples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    freeze = read_object(root / V8_FORWARD_FREEZE)
    score = read_object(root / V8_FORWARD_SCORE)
    source = read_object(root / V7I_SOURCE)
    assert_seal(freeze, V8_FORWARD_FREEZE.as_posix())
    assert_seal(score, V8_FORWARD_SCORE.as_posix())
    assert_seal(source, V7I_SOURCE.as_posix())
    if (
        freeze.get("status") != "FROZEN_WITHOUT_2026_OUTCOME_READ"
        or freeze.get("outcomes_read") is not False
        or freeze.get("candidate", {}).get("updates_from_2026_outcomes") != 0
    ):
        raise VerificationError("2026 forecast artifact is not a static pre-outcome freeze")
    forbidden_bindings = [name for name in freeze["input_bindings"]
                          if "outcome" in name.lower() or "scored-results" in name.lower()]
    if forbidden_bindings:
        raise VerificationError(f"forecast freeze binds post-outcome data: {forbidden_bindings}")
    bound_input_count = verify_binding_set(root, freeze["input_bindings"], "V8 forward freeze")
    if score.get("forecast_freeze_sha256") != freeze.get("self_sha256"):
        raise VerificationError("V8 forward score is not bound to its freeze")
    outcome_path = root / V7I_OUTCOMES
    if score["outcome_binding"].get(V7I_OUTCOMES.as_posix()) != file_sha256(outcome_path):
        raise VerificationError("V8 forward score outcome binding differs")

    source_records = source["methods"]["v5b_causal_no"]["records"]
    source_by_date = {str(row["climate_date"]): row for row in source_records}
    target_dates = [str(value) for value in freeze["target_dates"]]
    primary_dates = [str(value) for value in freeze["primary_dates"]]
    if len(target_dates) != 60 or len(primary_dates) != 55 or primary_dates != target_dates[5:]:
        raise VerificationError("V8 forward chronology differs")
    mapping = static_confusion_mapping(examples)
    probability_value_count = 0
    for record in freeze["records"]:
        day = str(record["climate_date"])
        base_items = sorted(source_by_date[day]["probabilities"], key=lambda row: int(row["contract_order"]))
        base = [float(item["yes_probability"]) for item in base_items]
        expected = static_prediction(base, mapping)
        frozen_items = sorted(record["probabilities"], key=lambda row: int(row["contract_order"]))
        if [item["market_ticker"] for item in base_items] != [item["market_ticker"] for item in frozen_items]:
            raise VerificationError(f"2026 contract ordering differs: {day}")
        for index, value in enumerate(expected):
            close(value, float(frozen_items[index]["yes_probability"]),
                  label=f"2026 frozen probability {day}/{index}")
            probability_value_count += 1

    outcomes = load_outcomes(outcome_path, freeze["records"])
    recomputed_rows: list[dict[str, Any]] = []
    raw_fill_checks = 0
    fee_checks = 0
    for record in freeze["records"]:
        day = str(record["climate_date"])
        probabilities = {str(item["market_ticker"]): float(item["yes_probability"])
                         for item in record["probabilities"]}
        base = {str(item["market_ticker"]): float(item["yes_probability"])
                for item in source_by_date[day]["probabilities"]}
        winner = next(ticker for ticker, side in outcomes[day].items() if side == "YES")
        order = record["order"]
        row: dict[str, Any] = {
            "climate_date": day,
            "winning_market_ticker": winner,
            "true_bracket_probability": probabilities[winner],
            "multiclass_brier": sum((value - float(ticker == winner)) ** 2
                                     for ticker, value in probabilities.items()),
            "log_loss": -math.log(max(probabilities[winner], 1e-15)),
            "base_multiclass_brier": sum((value - float(ticker == winner)) ** 2
                                          for ticker, value in base.items()),
            "base_log_loss": -math.log(max(base[winner], 1e-15)),
            "decision_status": order["decision_status"],
            "execution_status": order["execution_status"],
            "fill_price_cents": order.get("fill_price_cents"),
            "won": None,
            "entry_outlay_dollars": None,
            "net_profit_dollars": None,
        }
        if order["execution_status"] == "FILLED":
            raw_quote_for_fill(root, freeze, record)
            raw_fill_checks += 1
            price = int(order["fill_price_cents"])
            if price != int(order["arrival_best_ask_cents"]):
                raise VerificationError(f"fill price differs from arrival ask: {day}")
            fee = fee_for(day, price)
            outlay = Decimal(price) / Decimal(100) + fee
            if fee != Decimal(str(order["fill_fee_dollars"])):
                raise VerificationError(f"fill fee differs: {day}")
            if outlay != Decimal(str(order["fill_entry_outlay_dollars"])):
                raise VerificationError(f"fill outlay differs: {day}")
            decision_price = int(order["decision_best_ask_cents"])
            decision_fee = fee_for(day, decision_price)
            decision_outlay = Decimal(decision_price) / Decimal(100) + decision_fee
            if decision_fee != Decimal(str(order["decision_fee_dollars"])):
                raise VerificationError(f"decision fee differs: {day}")
            if decision_outlay != Decimal(str(order["decision_entry_outlay_dollars"])):
                raise VerificationError(f"decision outlay differs: {day}")
            won = outcomes[day][str(order["market_ticker"])] == str(order["contract_side"])
            profit = Decimal(int(won)) - outlay
            row.update(
                won=won,
                entry_outlay_dollars=format(outlay, "f"),
                net_profit_dollars=format(profit, "f"),
            )
            fee_checks += 1
        recomputed_rows.append(row)

    if len(recomputed_rows) != len(score["rows"]):
        raise VerificationError("V8 forward scored-row count differs")
    for expected, reported in zip(recomputed_rows, score["rows"], strict=True):
        for field in (
            "climate_date", "winning_market_ticker", "decision_status", "execution_status",
            "fill_price_cents", "won", "entry_outlay_dollars", "net_profit_dollars",
        ):
            if expected[field] != reported[field]:
                raise VerificationError(f"forward row differs {expected['climate_date']}/{field}")
        for field in (
            "true_bracket_probability", "multiclass_brier", "log_loss",
            "base_multiclass_brier", "base_log_loss",
        ):
            close(float(expected[field]), float(reported[field]),
                  label=f"forward row {expected['climate_date']}/{field}")

    primary = [row for row in recomputed_rows if row["climate_date"] in set(primary_dates)]
    summary = replay_summary(primary)
    compare_replay_summary(summary, score["primary_summary"], "V8 forward primary")
    fold_rows = [primary[index:index + 11] for index in range(0, len(primary), 11)]
    folds = [replay_summary(rows) for rows in fold_rows]
    for index, (expected, reported) in enumerate(zip(folds, score["robustness"]["folds"], strict=True), 1):
        compare_replay_summary(expected, reported, f"V8 forward fold {index}")
    positive_fold_count = sum(item["aggregate_return"] is not None and item["aggregate_return"] > 0
                              for item in folds)
    if positive_fold_count != int(score["robustness"]["positive_fold_count"]):
        raise VerificationError("positive-fold count differs")

    fills = [row for row in primary if row["execution_status"] == "FILLED"]
    stress_outlay = Decimal(0)
    stress_profit = Decimal(0)
    for row in fills:
        price = min(99, int(row["fill_price_cents"]) + 2)
        outlay = Decimal(price) / Decimal(100) + fee_for(str(row["climate_date"]), price)
        stress_outlay += outlay
        stress_profit += Decimal(int(bool(row["won"]))) - outlay
    stress_return = float(stress_profit / stress_outlay)
    close(stress_return, float(score["robustness"]["two_cent_stress_return"]),
          label="two-cent stress return")
    without_best = sorted(fills, key=lambda row: Decimal(str(row["net_profit_dollars"])), reverse=True)[1:]
    removed_outlay = sum((Decimal(str(row["entry_outlay_dollars"])) for row in without_best), Decimal(0))
    removed_profit = sum((Decimal(str(row["net_profit_dollars"])) for row in without_best), Decimal(0))
    removed_return = float(removed_profit / removed_outlay)
    close(removed_return, float(score["robustness"]["best_trade_removed_return"]),
          label="best-trade-removed return")
    bootstrap = block_bootstrap(primary, primary_dates)
    close(bootstrap, float(score["robustness"]["bootstrap_95pct_lower_bound"]),
          label="block-bootstrap lower bound")

    base_brier = mean(float(row["base_multiclass_brier"]) for row in primary)
    base_log = mean(float(row["base_log_loss"]) for row in primary)
    close(base_brier, float(score["base_forecast_comparison"]["base_brier"]), label="base Brier")
    close(base_log, float(score["base_forecast_comparison"]["base_log_loss"]), label="base log loss")

    checks = score["checks"]
    if (
        summary["filled_count"] != 13
        or summary["aggregate_return"] is None
        or not checks["aggregate_return_at_least_10pct"]
        or checks["minimum_40_fills"]
        or checks["at_least_four_positive_folds"]
        or checks["bootstrap_95pct_lower_bound_above_zero"]
        or checks["statistically_untouched"]
        or score.get("promotion_passed")
        or score.get("realized_account_return") is not None
        or score.get("paper_orders_placed") != 0
        or score.get("live_orders_placed") != 0
    ):
        raise VerificationError("V8 replay claim boundary or gate interpretation differs")

    economics_audit = read_object(root / V8_ECONOMICS_AUDIT)
    assert_seal(economics_audit, V8_ECONOMICS_AUDIT.as_posix())
    economics_binding_count = verify_binding_set(
        root, economics_audit["input_bindings"], "V8 adversarial economics audit"
    )
    boundary = economics_audit["return_claim_boundary"]
    if boundary.get("realized_account_return_available") or boundary.get("promotion_grade_return_available"):
        raise VerificationError("adversarial audit overstates return evidence")

    return {
        "status": "PASS",
        "forecast_freeze_self_sha256": freeze["self_sha256"],
        "score_self_sha256": score["self_sha256"],
        "bound_forecast_input_file_count": bound_input_count,
        "adversarial_audit_bound_input_file_count": economics_binding_count,
        "static_2025_training_date_count": len(examples),
        "updates_from_2026_outcomes": 0,
        "frozen_probability_value_count_verified": probability_value_count,
        "target_date_count": len(target_dates),
        "primary_date_count": len(primary_dates),
        "primary_summary": summary,
        "raw_book_fill_count_verified": raw_fill_checks,
        "fee_and_outlay_count_verified": fee_checks,
        "folds": folds,
        "positive_fold_count": positive_fold_count,
        "two_cent_stress_return": stress_return,
        "best_trade_removed_return": removed_return,
        "bootstrap_95pct_lower_bound": bootstrap,
        "base_brier": base_brier,
        "base_log_loss": base_log,
        "candidate_brier": summary["mean_multiclass_brier"],
        "candidate_log_loss": summary["mean_log_loss"],
        "claim_boundary": {
            "statistically_untouched": False,
            "promotion_passed": False,
            "realized_account_return": None,
            "return_kind": score["return_kind"],
            "paper_orders_placed": score["paper_orders_placed"],
            "live_orders_placed": score["live_orders_placed"],
        },
    }


def write_outputs(root: Path, probability: Mapping[str, Any], forward: Mapping[str, Any]) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": "v8-independent-validation-v1",
        "status": "PASS",
        "method": (
            "Standalone reconstruction from frozen V7Y/V7I inputs; no imports from "
            "v8.probability_repair or v8.v7i_fixed_replay"
        ),
        "probability_repair": probability,
        "forward_economic_replay": forward,
        "overall_conclusion": (
            "The probability repair and exposed 2026 historical simulation reproduce, "
            "but the 13-fill replay fails sample, fold, bootstrap, and untouched-data gates."
        ),
    }
    artifact["self_sha256"] = canonical_sha256(artifact)
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "verification.json"
    pending = json_path.with_name(json_path.name + ".pending")
    pending.write_text(json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
                       encoding="utf-8", newline="\n")
    pending.replace(json_path)
    probability_metrics = probability["aggregate"]
    replay = forward["primary_summary"]
    report = f"""# Independent V8 verification

Status: **PASS**

The registered `{CANDIDATE_ID}` probability repair was reconstructed directly from the
frozen V7Y probability vectors. Settlement highs were parsed independently from the
hash-bound raw CLILAX ZIP, and the winning contract was derived from each frozen day's
strike rules. The V7Y scored-days file was used only as a reconciliation cross-check.
The repair used only strictly earlier outcomes for each 2025 prediction. All 329 primary
daily vectors, aggregate metrics, and five chronological folds match the V8 artifacts.

| 2025 probability result | Independently reproduced |
| --- | ---: |
| Multiclass Brier | {probability_metrics['mean_multiclass_brier']:.12f} |
| Log loss | {probability_metrics['mean_log_loss']:.12f} |
| Modal accuracy | {probability_metrics['modal_bracket_accuracy']:.4%} |
| Zero-probability outcomes | {probability_metrics['zero_true_probability_count']} |
| Better Brier folds vs frozen V7Y | {probability['brier_better_than_identity_fold_count']}/5 |
| Better log-loss folds vs frozen V7Y | {probability['log_loss_better_than_identity_fold_count']}/5 |

The static 2026 mapping was then reconstructed from all 359 scorable 2025 dates. All 360
frozen probability values match. Every simulated fill was traced to the bound 18:00:05
archived book snapshot, and its fee, outlay, settlement PnL, folds, stress result, and
bootstrap were recomputed.

| Exposed 2026 replay | Independently reproduced |
| --- | ---: |
| Primary dates | {replay['date_count']} |
| Simulated fills | {replay['filled_count']} |
| Wins | {replay['win_count']} |
| Net profit | ${Decimal(replay['total_net_profit_dollars']):.4f} |
| Entry outlay | ${Decimal(replay['total_entry_outlay_dollars']):.4f} |
| Aggregate simulated return | {replay['aggregate_return']:.4%} |
| Positive folds | {forward['positive_fold_count']}/5 |
| 3-day block-bootstrap 5th percentile | {forward['bootstrap_95pct_lower_bound']:.4%} |
| Two-cent adverse-entry return | {forward['two_cent_stress_return']:.4%} |
| Best-trade-removed return | {forward['best_trade_removed_return']:.4%} |

The apparent 24.59% simulated return is not promotion evidence. It comes from only 13
fills on outcomes already exposed before V8, has only 3/5 positive folds, and its
bootstrap lower bound is negative. It is a Grade-A historical execution simulation,
not realized account return. No paper or live orders were placed.
"""
    report_path = output / "REPORT.md"
    pending_report = report_path.with_name(report_path.name + ".pending")
    pending_report.write_text(report, encoding="utf-8", newline="\n")
    pending_report.replace(report_path)
    return artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    probability, examples = verify_probability_repair(root)
    forward = verify_forward_replay(root, examples)
    artifact = write_outputs(root, probability, forward)
    print(json.dumps({
        "status": artifact["status"],
        "candidate_id": probability["candidate_id"],
        "probability_primary_dates": probability["primary_date_count"],
        "forward_primary_dates": forward["primary_date_count"],
        "forward_fills": forward["primary_summary"]["filled_count"],
        "forward_return": forward["primary_summary"]["aggregate_return"],
        "promotion_passed": forward["claim_boundary"]["promotion_passed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
