"""Development-only deterministic evaluator; no network, orders or holdout API."""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean

from v5a.development_search import (
    _inputs, _fee, _contains, _transform, _file_hash,
    UNIVERSE, PREDICTION_MANIFEST, LABELS,
)


DEFAULTS = {
    "probability_power": 1.0, "uniform_blend": 0.0,
    "probability_haircut": 0.0, "allowed_grades": ["A", "B_PLUS", "B"],
    "allowed_sides": ["YES", "NO"], "minimum_price_cents": 5,
    "maximum_price_cents": 80, "maximum_spread_cents": 5,
    "minimum_expected_net_return": 0.10, "additional_adverse_price_cents": 0,
    "neighbor_smoothing": 0.0, "tail_policy": "all", "maximum_entropy": 1.0,
    "minimum_probability_gap": 0.0, "selection_mode": "expected_return",
    "calibration": "none", "calibration_strength": 20.0,
    "calibration_min_days": 10, "probability_source": "calibrated",
}


def validate_spec(spec):
    """Normalize executable defaults; reject silently unsupported research features."""
    if not isinstance(spec, dict) or set(spec) - set(DEFAULTS):
        raise ValueError("unknown candidate parameter or non-object specification")
    p = json.loads(json.dumps(DEFAULTS))
    p.update(spec)
    bounds = {
        "probability_power": (0.1, 5), "uniform_blend": (0, 1),
        "probability_haircut": (0, 1), "minimum_price_cents": (1, 99),
        "maximum_price_cents": (1, 99), "maximum_spread_cents": (0, 100),
        "minimum_expected_net_return": (0, 100),
        "additional_adverse_price_cents": (0, 20), "neighbor_smoothing": (0, 1),
        "maximum_entropy": (0, 1), "minimum_probability_gap": (0, 1),
        "calibration_strength": (0, 1000), "calibration_min_days": (10, 64),
    }
    integers = {"minimum_price_cents", "maximum_price_cents", "maximum_spread_cents",
                "additional_adverse_price_cents", "calibration_min_days"}
    for key, (lo, hi) in bounds.items():
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"invalid {key}")
        if key in integers and int(value) != value:
            raise ValueError(f"integer required: {key}")
        p[key] = int(value) if key in integers else float(value)
    if p["minimum_price_cents"] > p["maximum_price_cents"]:
        raise ValueError("inverted price interval")
    for key, choices in (("allowed_grades", ["A", "B_PLUS", "B"]),
                         ("allowed_sides", ["YES", "NO"])):
        value = p[key]
        if not isinstance(value, list) or not value or any(x not in choices for x in value):
            raise ValueError(f"invalid {key}")
        p[key] = [x for x in choices if x in value]
    for key, choices in (("tail_policy", {"all", "interior", "tails"}),
                         ("selection_mode", {"expected_return", "expected_profit"}),
                         ("calibration", {"none", "expanding_rank_frequency"}),
                         ("probability_source", {"calibrated", "raw"})):
        if not isinstance(p[key], str) or p[key] not in choices:
            raise ValueError(f"invalid {key}")
    return p


def load_development(root):
    """Read verified frozen inputs and return only the 64 permitted dates."""
    root = Path(root).resolve()
    universe, predictions, labels = _inputs(root)
    dates = universe["split"]["development_dates"]
    if dates != sorted(set(dates)) or len(dates) != 64:
        raise ValueError("development chronology differs")
    allowed = set(dates)
    rows = {d: [] for d in dates}
    for row in universe["records"]:
        if row["partition"] == "development" and row["climate_date"] in allowed:
            if not row["fee_rule_ready"] or not row["settlement_rule_revision_exact"]:
                raise ValueError("unverified fee or contract rule")
            rows[row["climate_date"]].append(row)
    # Filter before exposing the frame or constructing scoring probability lookup.
    frame = predictions[(predictions.partition == "development") &
                        predictions.climate_date.astype(str).isin(allowed)]
    probs = {(str(r.climate_date), str(r.market_ticker)): float(r.yes_probability)
             for r in frame.itertuples(index=False)}
    manifest = json.loads((root / PREDICTION_MANIFEST).read_text(encoding="utf-8-sig"))
    bindings = {str(path).replace("\\", "/"): _file_hash(root / path)
                for path in (UNIVERSE, PREDICTION_MANIFEST, LABELS, Path(manifest["output"]["path"]))}
    raw_probs = {(str(r.climate_date), str(r.market_ticker)): float(r.raw_yes_probability)
                 for r in frame.itertuples(index=False)}
    return {"dates": dates, "rows_by_date": rows, "probabilities": probs, "raw_probabilities": raw_probs,
            "labels": labels, "input_bindings": bindings, "partition": "development"}


def _summary(trades):
    outlay = sum(t["entry_outlay_dollars"] for t in trades)
    profit = sum(t["net_profit_dollars"] for t in trades)
    return {"selected_days": len(trades), "total_entry_outlay_dollars": outlay,
            "total_net_profit_dollars": profit,
            "aggregate_realized_net_return": profit / outlay if outlay else -1.0}


def _adjust(base, p):
    values = _transform(base, p["probability_power"], p["uniform_blend"])
    amount = p["neighbor_smoothing"]
    diffused = [0.0] * len(values)
    for i, value in enumerate(values):
        neighbors = [j for j in (i - 1, i + 1) if 0 <= j < len(values)]
        diffused[i] += value * (1 - amount) if neighbors else value
        for j in neighbors:
            diffused[j] += value * amount / len(neighbors)
    return diffused


def evaluate_candidate(context, spec):
    """One selection/date with prequential calibration and fixed-selection stress."""
    p = validate_spec(spec)
    dates = context["dates"]
    if not dates or context.get("partition") != "development" or dates != sorted(set(dates)) or set(context["labels"]) != set(dates):
        raise ValueError("only chronological development evaluation is permitted")
    trades, abstentions, briers, baseline_briers, log_losses = [], [], [], [], []
    rank_counts, history, calibration_history = {}, 0, []
    for date in dates:
        rows = context["rows_by_date"][date]
        yes = sorted([r for r in rows if r["contract_side"] == "YES"],
                     key=lambda r: (-10000 if r["floor_strike"] is None else float(r["floor_strike"]), r["market_ticker"]))
        if len(yes) < 2 or len({r["market_ticker"] for r in yes}) != len(yes):
            raise ValueError("invalid mutually exclusive bracket universe")
        source_key = "raw_probabilities" if p["probability_source"] == "raw" else "probabilities"
        base = [context[source_key][(date, r["market_ticker"])] for r in yes]
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in base) or abs(sum(base) - 1) > 1e-6:
            raise ValueError("invalid probability mass")
        adjusted = _adjust(base, p)
        rank = sorted(range(len(yes)), key=lambda i: (-adjusted[i], i))
        if p["calibration"] != "none":
            calibration_history.append(history)
            if history >= p["calibration_min_days"]:
                # Prior outcomes by forecast-probability rank; today's label is not read yet.
                counts = rank_counts.get(len(yes), [0] * len(yes))
                total = sum(counts)
                if total:
                    strength = p["calibration_strength"]
                    for position, i in enumerate(rank):
                        adjusted[i] = (strength * adjusted[i] + counts[position]) / (strength + total)
        actual = context["labels"][date]
        winners = [int(_contains(r, actual)) for r in yes]
        if sum(winners) != 1:
            raise ValueError("settlement not mutually exclusive and exhaustive")
        briers.append(sum((v - y) ** 2 for v, y in zip(adjusted, winners)))
        frozen_reference = [context["probabilities"][(date, r["market_ticker"])] for r in yes]
        baseline_briers.append(sum((v - y) ** 2 for v, y in zip(frozen_reference, winners)))
        log_losses.append(-math.log(max(adjusted[winners.index(1)], 1e-12)))
        probabilities = {r["market_ticker"]: v for r, v in zip(yes, adjusted)}
        entropy = -sum(v * math.log(v) for v in adjusted if v > 0) / math.log(len(adjusted))
        ordered = sorted(adjusted, reverse=True)
        abstain_reason = None
        if p["calibration"] != "none" and history < p["calibration_min_days"]:
            abstain_reason = "calibration_warmup"
        elif entropy > p["maximum_entropy"] + 1e-12:
            abstain_reason = "entropy"
        elif ordered[0] - ordered[1] < p["minimum_probability_gap"]:
            abstain_reason = "probability_gap"
        candidates = []
        for row in ([] if abstain_reason else rows):
            grade, side = row["execution_evidence_grade"], row["contract_side"]
            tail = row["strike_type"] in {"less", "greater"}
            if grade not in p["allowed_grades"] or side not in p["allowed_sides"]:
                continue
            if (p["tail_policy"] == "tails" and not tail) or (p["tail_policy"] == "interior" and tail):
                continue
            ask, bid = row["execution_price_cents"], row["bid_price_cents"]
            if ask is None:
                continue
            price = int(ask) + p["additional_adverse_price_cents"]
            spread = 100 if bid is None else int(ask) - int(bid)
            if not p["minimum_price_cents"] <= price <= p["maximum_price_cents"] or not 0 < price < 100 or spread < 0 or spread > p["maximum_spread_cents"]:
                continue
            probability = probabilities[row["market_ticker"]]
            probability = max(0., (probability if side == "YES" else 1 - probability) - p["probability_haircut"])
            fee = float(_fee(date, price))
            outlay = price / 100 + fee
            expected_profit = probability - outlay
            expected_return = expected_profit / outlay
            if expected_return < p["minimum_expected_net_return"]:
                continue
            won = _contains(row, actual) == (side == "YES")
            candidates.append({"climate_date": date, "market_ticker": row["market_ticker"],
                "contract_side": side, "execution_evidence_grade": grade,
                "assumed_fill": bool(row["assumed_fill"]), "verified_fill": False,
                "model_probability": probability, "entry_price_cents": price,
                "fee_dollars": fee, "entry_outlay_dollars": outlay,
                "expected_net_return": expected_return, "expected_profit_dollars": expected_profit,
                "won": won, "net_profit_dollars": int(won) - outlay,
                "realized_net_return": (int(won) - outlay) / outlay,
                "normalized_entropy": entropy, "calibration_prior_days": history,
                "strike_type": row["strike_type"], "floor_strike": row["floor_strike"],
                "cap_strike": row["cap_strike"], "reported_high_f": actual,
                "quote_at_utc": row.get("quote_at_utc"), "decision_time_utc": row.get("decision_time_utc", "18:00")})
        if candidates:
            field = "expected_net_return" if p["selection_mode"] == "expected_return" else "expected_profit_dollars"
            trades.append(max(candidates, key=lambda t: (t[field], -t["entry_price_cents"], t["market_ticker"], t["contract_side"])))
        else:
            abstentions.append({"climate_date": date, "reason": abstain_reason or "no_eligible_contract"})
        counts = rank_counts.setdefault(len(yes), [0] * len(yes))
        counts[rank.index(winners.index(1))] += 1
        history += 1
    folds = []
    date_fold = {d: min(4, i * 5 // len(dates)) for i, d in enumerate(dates)}
    for f in range(5):
        folds.append({"fold": f + 1, **_summary([t for t in trades if date_fold[t["climate_date"]] == f])})
    stress = {}
    for cents in (1, 2, 3):
        stressed = []
        for t in trades:
            price = min(100, t["entry_price_cents"] + cents)
            outlay = price / 100 + float(_fee(t["climate_date"], price))
            stressed.append({**t, "entry_outlay_dollars": outlay, "net_profit_dollars": int(t["won"]) - outlay})
        stress[str(cents)] = {**_summary(stressed), "selection_fixed": True}
    removed = max(trades, key=lambda t: t["net_profit_dollars"]) if trades else None
    grades = {g: sum(t["execution_evidence_grade"] == g for t in trades) for g in ("A", "B_PLUS", "B")}
    quality = {"A": 1., "B_PLUS": .65, "B": .30}
    result = {"schema_version": "v5b-development-evaluation-v1", "parameters": p,
        **_summary(trades), "selected_trades": len(trades),
        "mean_expected_net_return": mean(t["expected_net_return"] for t in trades) if trades else -1.,
        "temporal_folds": folds, "positive_fold_count": sum(f["selected_days"] > 0 and f["aggregate_realized_net_return"] > 0 for f in folds),
        "worst_nonempty_fold_return": min((f["aggregate_realized_net_return"] for f in folds if f["selected_days"]), default=-1.),
        "multiclass_brier": mean(briers), "baseline_multiclass_brier": mean(baseline_briers),
        "multiclass_log_loss": mean(log_losses), "execution_grade_counts": grades,
        "evidence_quality_score": mean(quality[t["execution_evidence_grade"]] for t in trades) if trades else 0.,
        "adverse_stress": stress,
        "best_day_removed_return": _summary([t for t in trades if t is not removed])["aggregate_realized_net_return"],
        "grade_a_sensitivity": {**_summary([t for t in trades if t["execution_evidence_grade"] == "A"]), "selection_fixed": True},
        "trades": trades, "abstentions": abstentions,
        "calibration": {"method": p["calibration"], "chronological_only": True,
            "min_history_dates": min(calibration_history, default=0), "max_history_dates": max(calibration_history, default=0)},
        "development_only": True, "evaluation_partition": "development",
        "holdout_labels_opened": False, "protected_confirmation_labels_read": False,
        "actual_orders_placed": False,
        "limitations": ["Frozen 18:00 UTC inputs only; no new timing or raw weather model features.",
            "Historical simulations do not establish fills; B/B+ are weaker execution evidence. Frozen quotes may be seconds after 18:00; no exact-decision fill is claimed.",
            "Expanding rank calibration uses past development labels only; adaptive campaign selection still reuses development data."]}
    json.dumps(result, allow_nan=False)
    return result
