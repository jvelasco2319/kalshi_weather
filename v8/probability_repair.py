"""Chronological probability repairs for the exposed V7Y development replay.

This module never rebuilds or mutates V7Y predictions.  It binds to the
immutable V7Y prediction freeze and scored summary, applies a finite catalog
of deterministic transforms, and evaluates those transforms with only prior
outcomes available to rolling calibrators.  Calendar 2025 is already exposed
development evidence and must not be described as untouched confirmation.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Iterable, Sequence

from scripts import run_v7y_hrrr_gefs_year_study as v7y
from scripts.acquire_v7y_weather_history import file_hash


PROJECT = Path(__file__).resolve().parents[1]
V7Y_FREEZE = Path("runs/replays/v7y-hrrr-gefs-calendar-2025/prediction-freeze.json")
V7Y_SUMMARY = Path("runs/replays/v7y-hrrr-gefs-calendar-2025/summary.json")
SPEC = Path("v8/probability_repair_spec.json")
OUTPUT = Path("runs/v8/probability-repair")
RESULTS = OUTPUT / "results.json"
CANDIDATES = OUTPUT / "candidates.csv"
DAILY_LEADER = OUTPUT / "daily-leader.csv"
REPORT = OUTPUT / "REPORT.md"
UNIFORM_BRIER = 5.0 / 6.0
UNIFORM_LOG_LOSS = math.log(6.0)


class RepairError(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    family: str
    params: tuple[tuple[str, float], ...] = ()

    def get(self, key: str) -> float:
        return dict(self.params)[key]


@dataclass(frozen=True)
class Example:
    climate_date: str
    base: tuple[float, ...]
    actual_index: int


@dataclass(frozen=True)
class ScoredDay:
    climate_date: str
    probabilities: tuple[float, ...]
    actual_index: int
    brier: float
    log_loss: float
    modal_hit: bool


def _canonical_hash(value: dict[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RepairError(f"JSON object required: {path}")
    return value


def _verify_seal(value: dict[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise RepairError(f"{field} mismatch")


def _normalize(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if len(result) != 6 or any(not math.isfinite(value) or value < 0 for value in result):
        raise RepairError("six finite nonnegative probabilities required")
    total = sum(result)
    if total <= 0:
        raise RepairError("probability mass must be positive")
    normalized = tuple(value / total for value in result)
    if not math.isclose(sum(normalized), 1.0, rel_tol=0, abs_tol=1e-12):
        raise RepairError("probability normalization failed")
    return normalized


def catalog() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = [Candidate("identity", "identity")]
    for alpha in (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4):
        candidates.append(Candidate(f"uniform-{alpha:g}", "uniform", (("alpha", alpha),)))
    for floor in (0.001, 0.0025, 0.005, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1):
        candidates.append(Candidate(f"floor-{floor:g}", "floor", (("floor", floor),)))
    for power in (0.5, 0.65, 0.8, 0.9):
        for epsilon in (0.001, 0.005, 0.01, 0.02):
            candidates.append(Candidate(
                f"power-{power:g}-eps-{epsilon:g}", "power",
                (("epsilon", epsilon), ("power", power)),
            ))
    for family in ("cauchy", "exponential"):
        for scale in (0.5, 1.0, 2.0):
            for weight in (0.05, 0.1, 0.15, 0.2, 0.3, 0.4):
                candidates.append(Candidate(
                    f"{family}-{scale:g}-w-{weight:g}", family,
                    (("scale", scale), ("weight", weight)),
                ))
    for family in ("rolling_climatology", "rolling_confusion"):
        for prior in (6.0, 18.0, 36.0):
            for weight in (0.1, 0.2, 0.3, 0.4, 0.5):
                candidates.append(Candidate(
                    f"{family}-prior-{prior:g}-w-{weight:g}", family,
                    (("prior", prior), ("weight", weight)),
                ))
    # Cross-colony preflight candidate, fixed before the final V8 catalog run.
    # Dirichlet alpha=2 per bracket is total prior mass 12 across six brackets.
    candidates.append(Candidate(
        "rolling_confusion-alpha-2-w-0.75", "rolling_confusion",
        (("prior", 12.0), ("weight", 0.75)),
    ))
    result = tuple(candidates)
    if len({candidate.candidate_id for candidate in result}) != len(result):
        raise RepairError("candidate identifiers must be unique")
    return result


def _kernel(base: Sequence[float], *, family: str, scale: float) -> tuple[float, ...]:
    output = [0.0] * 6
    for source, mass in enumerate(base):
        if family == "cauchy":
            weights = [1.0 / (1.0 + ((target - source) / scale) ** 2) for target in range(6)]
        elif family == "exponential":
            weights = [math.exp(-abs(target - source) / scale) for target in range(6)]
        else:
            raise RepairError(f"unsupported kernel: {family}")
        denominator = sum(weights)
        for target, weight in enumerate(weights):
            output[target] += float(mass) * weight / denominator
    return _normalize(output)


def _rolling_distribution(
    history: Sequence[Example], *, prior: float, predicted_mode: int | None = None
) -> tuple[float, ...]:
    selected = history
    if predicted_mode is not None:
        selected = [row for row in history if max(range(6), key=lambda i: (row.base[i], i)) == predicted_mode]
    counts = [prior / 6.0] * 6
    for row in selected:
        counts[row.actual_index] += 1.0
    return _normalize(counts)


def transform(
    base: Sequence[float], candidate: Candidate, history: Sequence[Example] = ()
) -> tuple[float, ...]:
    p = _normalize(base)
    family = candidate.family
    if family == "identity":
        return p
    if family == "uniform":
        alpha = candidate.get("alpha")
        return _normalize((1.0 - alpha) * value + alpha / 6.0 for value in p)
    if family == "floor":
        floor = candidate.get("floor")
        return _normalize(max(value, floor) for value in p)
    if family == "power":
        epsilon, power = candidate.get("epsilon"), candidate.get("power")
        return _normalize((value + epsilon) ** power for value in p)
    if family in {"cauchy", "exponential"}:
        weight = candidate.get("weight")
        smoothed = _kernel(p, family=family, scale=candidate.get("scale"))
        return _normalize((1.0 - weight) * value + weight * other for value, other in zip(p, smoothed, strict=True))
    if family in {"rolling_climatology", "rolling_confusion"}:
        mode = max(range(6), key=lambda index: (p[index], index))
        rolling = _rolling_distribution(
            history,
            prior=candidate.get("prior"),
            predicted_mode=mode if family == "rolling_confusion" else None,
        )
        weight = candidate.get("weight")
        return _normalize((1.0 - weight) * value + weight * other for value, other in zip(p, rolling, strict=True))
    raise RepairError(f"unsupported candidate family: {family}")


def _load_examples(root: Path) -> tuple[list[Example], dict[str, Any]]:
    freeze_path, summary_path = root / V7Y_FREEZE, root / V7Y_SUMMARY
    frozen, summary = _read(freeze_path), _read(summary_path)
    _verify_seal(frozen)
    _verify_seal(summary)
    if (
        frozen.get("schema_version") != "v7y-hrrr-gefs-calendar-2025-prediction-freeze-v2"
        or frozen.get("status") != "FROZEN_BEFORE_CLILAX_LABEL_READ"
        or len(frozen.get("records", [])) != 361
        or summary.get("schema_version") != "v7y-hrrr-gefs-calendar-2025-score-v1"
        or summary.get("status") != "COMPLETE"
        or summary.get("prediction_freeze_sha256") != frozen.get("self_sha256")
        or summary.get("scored_date_count") != 359
    ):
        raise RepairError("frozen V7Y development inputs differ")
    labels, label_bindings, label_audit = v7y._labels(root)
    if label_bindings != summary.get("label_bindings") or label_audit != summary.get("label_audit"):
        raise RepairError("V7Y exposed-label binding differs")
    examples: list[Example] = []
    for record in frozen["records"]:
        day = record["climate_date"]
        if day in v7y.LABEL_EXCLUSIONS:
            continue
        contracts = record["contracts"]
        tickers = [row["ticker"] for row in contracts]
        probabilities = record["yes_probabilities"]
        if set(tickers) != set(probabilities):
            raise RepairError(f"contract probability map differs: {day}")
        actual = int(labels[day]["tmax_f"])
        winner = v7y._winner_ticker(contracts, actual)
        examples.append(Example(
            climate_date=day,
            base=_normalize(probabilities[ticker] for ticker in tickers),
            actual_index=tickers.index(winner),
        ))
    if len(examples) != 359 or examples[0].climate_date != "2025-01-05" or examples[-1].climate_date != "2025-12-31":
        raise RepairError("V7Y example coverage differs")
    return examples, {
        V7Y_FREEZE.as_posix(): file_hash(freeze_path),
        V7Y_SUMMARY.as_posix(): file_hash(summary_path),
        **label_bindings,
    }


def evaluate(candidate: Candidate, examples: Sequence[Example]) -> list[ScoredDay]:
    history: list[Example] = []
    output: list[ScoredDay] = []
    for row in examples:
        repaired = transform(row.base, candidate, history)
        actual_probability = repaired[row.actual_index]
        modal = max(range(6), key=lambda index: (repaired[index], index))
        output.append(ScoredDay(
            climate_date=row.climate_date,
            probabilities=repaired,
            actual_index=row.actual_index,
            brier=sum((value - (1.0 if index == row.actual_index else 0.0)) ** 2 for index, value in enumerate(repaired)),
            log_loss=-math.log(max(actual_probability, 1e-15)),
            modal_hit=modal == row.actual_index,
        ))
        history.append(row)
    return output


def _metrics(rows: Sequence[ScoredDay]) -> dict[str, Any]:
    if not rows:
        raise RepairError("metric cohort cannot be empty")
    return {
        "date_count": len(rows),
        "mean_multiclass_brier": statistics.mean(row.brier for row in rows),
        "mean_log_loss": statistics.mean(row.log_loss for row in rows),
        "modal_bracket_accuracy": statistics.mean(row.modal_hit for row in rows),
        "mean_true_bracket_probability": statistics.mean(row.probabilities[row.actual_index] for row in rows),
        "zero_true_probability_count": sum(row.probabilities[row.actual_index] <= 0.0 for row in rows),
        "minimum_probability": min(min(row.probabilities) for row in rows),
    }


def _fold_slices(length: int, folds: int = 5) -> list[tuple[int, int]]:
    return [(length * index // folds, length * (index + 1) // folds) for index in range(folds)]


def _bootstrap_delta(
    leader: Sequence[ScoredDay], reference: Sequence[ScoredDay], *, seed: int = 20260929, draws: int = 20_000
) -> dict[str, Any]:
    if len(leader) != len(reference):
        raise RepairError("paired bootstrap cohorts differ")
    rng, size = random.Random(seed), len(leader)
    brier_delta, log_delta = [], []
    daily_brier = [left.brier - right.brier for left, right in zip(leader, reference, strict=True)]
    daily_log = [left.log_loss - right.log_loss for left, right in zip(leader, reference, strict=True)]
    for _ in range(draws):
        indices = [rng.randrange(size) for _ in range(size)]
        brier_delta.append(statistics.mean(daily_brier[index] for index in indices))
        log_delta.append(statistics.mean(daily_log[index] for index in indices))
    brier_delta.sort()
    log_delta.sort()
    low, high = int(draws * 0.025), int(draws * 0.975)
    return {
        "draws": draws,
        "seed": seed,
        "interpretation": "post-selection descriptive paired bootstrap; not untouched confirmation",
        "brier_delta_leader_minus_identity": {
            "mean": statistics.mean(daily_brier), "ci_95": [brier_delta[low], brier_delta[high]]
        },
        "log_loss_delta_leader_minus_identity": {
            "mean": statistics.mean(daily_log), "ci_95": [log_delta[low], log_delta[high]]
        },
    }


def run(root: Path) -> dict[str, Any]:
    examples, bindings = _load_examples(root)
    warmup = [row for row in examples if row.climate_date < v7y.PRIMARY_START.isoformat()]
    primary_examples = [row for row in examples if row.climate_date >= v7y.PRIMARY_START.isoformat()]
    if len(warmup) != 30 or len(primary_examples) != 329:
        raise RepairError("fixed V8 development partition differs")
    all_scores: dict[str, list[ScoredDay]] = {}
    all_metrics: list[dict[str, Any]] = []
    folds = _fold_slices(len(primary_examples))
    for candidate in catalog():
        scored = evaluate(candidate, examples)
        primary = [row for row in scored if row.climate_date >= v7y.PRIMARY_START.isoformat()]
        all_scores[candidate.candidate_id] = primary
        aggregate = _metrics(primary)
        fold_metrics = [_metrics(primary[start:end]) for start, end in folds]
        all_metrics.append({
            "candidate_id": candidate.candidate_id,
            "family": candidate.family,
            "params": dict(candidate.params),
            **aggregate,
            "folds": fold_metrics,
        })
    identity = next(row for row in all_metrics if row["candidate_id"] == "identity")
    identity_folds = identity["folds"]
    for row in all_metrics:
        row["brier_better_than_identity_fold_count"] = sum(
            fold["mean_multiclass_brier"] < ref["mean_multiclass_brier"]
            for fold, ref in zip(row["folds"], identity_folds, strict=True)
        )
        row["log_loss_better_than_identity_fold_count"] = sum(
            fold["mean_log_loss"] < ref["mean_log_loss"]
            for fold, ref in zip(row["folds"], identity_folds, strict=True)
        )
        row["gates"] = {
            "nonzero_outcomes": row["zero_true_probability_count"] == 0 and row["minimum_probability"] > 0,
            "aggregate_brier_beats_identity_and_uniform": row["mean_multiclass_brier"] < min(identity["mean_multiclass_brier"], UNIFORM_BRIER),
            "aggregate_log_loss_beats_identity_and_uniform": row["mean_log_loss"] < min(identity["mean_log_loss"], UNIFORM_LOG_LOSS),
            "modal_accuracy_preserved": row["modal_bracket_accuracy"] >= identity["modal_bracket_accuracy"] - 0.005,
            "brier_improves_at_least_four_folds": row["brier_better_than_identity_fold_count"] >= 4,
            "log_loss_improves_at_least_four_folds": row["log_loss_better_than_identity_fold_count"] >= 4,
        }
        row["passes_all_development_gates"] = all(row["gates"].values())
    eligible = [row for row in all_metrics if row["candidate_id"] != "identity" and row["passes_all_development_gates"]]
    pool = eligible or [row for row in all_metrics if row["candidate_id"] != "identity"]
    pool.sort(key=lambda row: (row["mean_multiclass_brier"], row["mean_log_loss"], row["candidate_id"]))
    leader = pool[0]
    ranking = sorted(all_metrics, key=lambda row: (not row["passes_all_development_gates"], row["mean_multiclass_brier"], row["mean_log_loss"], row["candidate_id"]))
    for rank, row in enumerate(ranking, 1):
        row["rank"] = rank
    family_leaders = []
    for family in sorted({row["family"] for row in all_metrics}):
        family_rows = [row for row in all_metrics if row["family"] == family]
        family_leaders.append(min(
            family_rows,
            key=lambda row: (not row["passes_all_development_gates"], row["mean_multiclass_brier"], row["mean_log_loss"], row["candidate_id"]),
        ))
    bootstrap = _bootstrap_delta(all_scores[leader["candidate_id"]], all_scores["identity"])
    result = {
        "schema_version": "v8-probability-repair-development-v1",
        "status": "COMPLETE_DEVELOPMENT_ONLY",
        "data_status": "2025_OUTCOMES_ALREADY_EXPOSED_NOT_CONFIRMATORY",
        "candidate_count": len(all_metrics),
        "all_gate_candidate_count": len(eligible),
        "partition": {
            "chronological_warmup_start": warmup[0].climate_date,
            "chronological_warmup_end": warmup[-1].climate_date,
            "warmup_date_count": len(warmup),
            "primary_start": primary_examples[0].climate_date,
            "primary_end": primary_examples[-1].climate_date,
            "primary_date_count": len(primary_examples),
            "excluded_dates": v7y.LABEL_EXCLUSIONS,
            "rolling_calibrators_use_only_strictly_prior_outcomes": True,
        },
        "baselines": {
            "identity": identity,
            "uniform_six_bracket": {"mean_multiclass_brier": UNIFORM_BRIER, "mean_log_loss": UNIFORM_LOG_LOSS},
        },
        "leader": leader,
        "family_leaders": family_leaders,
        "top_candidates": ranking[:10],
        "descriptive_bootstrap": bootstrap,
        "input_bindings": dict(sorted(bindings.items())),
        "spec_binding": {"path": SPEC.as_posix(), "sha256": file_hash(root / SPEC)},
        "network_used": False,
        "market_prices_read": False,
        "economic_result": None,
        "paper_orders_placed": 0,
        "live_orders_placed": 0,
        "limitations": [
            "All 2025 outcomes were exposed before V8 and are development evidence only.",
            "Candidate selection and bootstrap use the same exposed year, so improvement is not independent confirmation.",
            "This component repairs forecast probabilities only; it does not estimate prices, fees, fills, or return.",
        ],
    }
    result["self_sha256"] = _canonical_hash(result)
    output = root / OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    results_path = root / RESULTS
    if results_path.is_file() and results_path.read_text(encoding="utf-8") != encoded:
        raise RepairError("existing V8 probability result differs; refusing overwrite")
    results_path.write_text(encoded, encoding="utf-8")
    with (root / CANDIDATES).open("w", encoding="utf-8", newline="") as stream:
        fields = [
            "rank", "candidate_id", "family", "params", "date_count", "mean_multiclass_brier",
            "mean_log_loss", "modal_bracket_accuracy", "mean_true_bracket_probability",
            "zero_true_probability_count", "minimum_probability", "brier_better_than_identity_fold_count",
            "log_loss_better_than_identity_fold_count", "passes_all_development_gates",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in ranking:
            writer.writerow({key: json.dumps(row[key], sort_keys=True) if key == "params" else row[key] for key in fields})
    leader_scores = all_scores[leader["candidate_id"]]
    with (root / DAILY_LEADER).open("w", encoding="utf-8", newline="") as stream:
        fields = ["climate_date", "actual_index", "probabilities", "brier", "log_loss", "modal_hit"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in leader_scores:
            writer.writerow({
                "climate_date": row.climate_date,
                "actual_index": row.actual_index,
                "probabilities": json.dumps(row.probabilities),
                "brier": row.brier,
                "log_loss": row.log_loss,
                "modal_hit": row.modal_hit,
            })
    gates = leader["gates"]
    report = f"""# V8 probability-repair colony\n\n## Result\n\nThe finite catalog evaluated **{len(all_metrics)}** deterministic repairs on **{len(primary_examples)}** exposed development dates. **{len(eligible)}** candidates passed every development gate. The leader is `{leader['candidate_id']}`.\n\n| Metric | Frozen V7Y | V8 leader | Uniform |\n| --- | ---: | ---: | ---: |\n| Multiclass Brier (lower) | {identity['mean_multiclass_brier']:.4f} | {leader['mean_multiclass_brier']:.4f} | {UNIFORM_BRIER:.4f} |\n| Log loss (lower) | {identity['mean_log_loss']:.4f} | {leader['mean_log_loss']:.4f} | {UNIFORM_LOG_LOSS:.4f} |\n| Modal accuracy | {identity['modal_bracket_accuracy']:.1%} | {leader['modal_bracket_accuracy']:.1%} | conceptual 16.7% |\n| Zero-probability realized outcomes | {identity['zero_true_probability_count']} | {leader['zero_true_probability_count']} | 0 |\n\nThe leader improved Brier in **{leader['brier_better_than_identity_fold_count']}/5** chronological folds and log loss in **{leader['log_loss_better_than_identity_fold_count']}/5**. All gates passed: **{all(gates.values())}**.\n\n## Scientific boundary\n\nCalendar 2025 is already exposed. Rolling calibrators used only outcomes strictly earlier than each prediction, but the catalog and leader were selected using the full exposed development year. These results support a probability-repair direction; they do not confirm out-of-sample performance or trading profit. No market prices, fees, fills, network feeds, paper orders, or live orders were used.\n"""
    (root / REPORT).write_text(report, encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "status"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    if args.action == "status":
        result = {"results_exists": (root / RESULTS).is_file(), "candidate_count": len(catalog())}
    else:
        result = run(root)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

