"""Create and verify the immutable V8 strategy for a future confirmation window."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from past7_replay.engine import _file_hash, _read_json, _seal, _verify_seal, _write_immutable_json


CONFIG = Path("configs/v8_campaign.json")
PROBABILITY_SPEC = Path("v8/probability_repair_spec.json")
PROBABILITY_RESULT = Path("runs/v8/probability-repair/results.json")
REPLAY_FREEZE = Path(
    "runs/campaigns_v8/v8-offline-20260930T004226Z/probability-order-freeze.json"
)
V5B_STRATEGY = Path(
    "runs/campaigns_v5b/v5b-development-20260927T183845017734Z/strategy-freeze.json"
)
OUTPUT = Path("runs/v8/frozen-primary-strategy/strategy-freeze.json")
LEADER = "rolling_confusion-alpha-2-w-0.75"
IMPLEMENTATION_FILES = (
    Path("v8/probability_repair.py"),
    Path("v8/economic_replay.py"),
    Path("past7_replay/audited.py"),
    Path("past7_replay/engine.py"),
)


class StrategyFreezeError(ValueError):
    pass


def _inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = _read_json(root / CONFIG)
    result = _read_json(root / PROBABILITY_RESULT)
    _verify_seal(result)
    replay = _read_json(root / REPLAY_FREEZE, sealed=True)
    v5b = _read_json(root / V5B_STRATEGY, sealed=True)
    if (
        config.get("frozen_leader", {}).get("candidate_id") != LEADER
        or result.get("leader", {}).get("candidate_id") != LEADER
        or result.get("leader", {}).get("passes_all_development_gates") is not True
        or replay.get("candidate_id") != LEADER
        or replay.get("2026_label_updates_used") is not False
        or replay.get("network_used") is not False
    ):
        raise StrategyFreezeError("V8 leader or safety boundary differs")
    return config, replay, v5b


def build(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    config, replay, v5b = _inputs(root)
    bindings = {
        path.as_posix(): _file_hash(root / path)
        for path in (
            CONFIG,
            PROBABILITY_SPEC,
            PROBABILITY_RESULT,
            REPLAY_FREEZE,
            V5B_STRATEGY,
            *IMPLEMENTATION_FILES,
        )
    }
    return _seal({
        "schema_version": "klax-v8-primary-strategy-freeze-v1",
        "strategy_id": "v8-klax-primary-rolling-confusion-no-v1",
        "status": "FROZEN_FOR_GENUINELY_NEW_GRADE_A_CONFIRMATION",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_boundary": {
            "development_2025": "EXPOSED",
            "replay_2026_07_30_through_2026_09_27": "EXPOSED",
            "future_confirmation": "MUST_BE_GENUINELY_NEW_AND_OUTCOME_BLINDED",
        },
        "probability_model": {
            "underlying_signal": "frozen six-bracket V7Y HRRR/GEFS probabilities",
            "candidate_id": LEADER,
            "method": "modal-position confusion recalibration",
            "fit_population": replay["fit_population"],
            "dirichlet_alpha_per_output_bracket": 2.0,
            "base_probability_weight": 0.25,
            "conditional_confusion_weight": 0.75,
            "modal_confusion_counts_with_dirichlet_prior": replay[
                "modal_confusion_counts_with_dirichlet_prior"
            ],
            "conditional_distributions_by_base_modal_position": replay[
                "conditional_distributions_by_base_modal_position"
            ],
            "future_outcome_updates_allowed": False,
            "regime_overlay_enabled": False,
        },
        "trade_policy": {
            "selector": "frozen V5B expected-profit selector",
            "selector_parameters": v5b["parameters"],
            "eligible_side": "NO",
            "decision_time_utc": config["forward_stability_replay"]["decision_time_utc"],
            "arrival_delay_seconds": config["forward_stability_replay"]["arrival_delay_seconds"],
            "execution_evidence_required": "GRADE_A_FULL_BOOK",
            "missing_or_invalid_execution_evidence": "ABSTAIN",
            "position_exit": "HOLD_TO_SETTLEMENT",
            "fees": "EXACT_EVENT_SPECIFIC_HISTORICAL_OR_REGISTERED_FEE_RULE",
            "one_order_maximum_per_weather_day": True,
        },
        "confirmation_protocol": {
            "minimum_genuinely_new_grade_a_days": 100,
            "minimum_filled_trades": 40,
            "chronological_folds": 5,
            "minimum_positive_folds": 4,
            "minimum_capital_weighted_return_after_fees": 0.10,
            "expected_net_return_per_trade_target": 0.10,
            "report_unweighted_mean_realized_return_per_trade": True,
            "require_expected_vs_realized_calibration_check": True,
            "bootstrap_one_sided_confidence": 0.95,
            "bootstrap_block_days": 3,
            "require_bootstrap_lower_bound_above_zero": True,
            "adverse_entry_stress_cents": 2,
            "require_positive_stress_return": True,
            "require_positive_best_trade_removed_return": True,
            "one_shot": True,
            "retuning_before_or_after_confirmation_score_allowed": False,
        },
        "safety": {
            "network_use_during_replay": False,
            "paper_orders_authorized": False,
            "live_orders_authorized": False,
            "current_or_live_market_feed_authorized": False,
        },
        "source_bindings": dict(sorted(bindings.items())),
        "prior_replay_result": {
            "promotion_passed": False,
            "ten_percent_expected_return_per_trade_confirmed": False,
        },
    })


def verify(project_root: str | Path, artifact: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(project_root).resolve()
    value = artifact if artifact is not None else _read_json(root / OUTPUT)
    _verify_seal(value)
    if (
        value.get("schema_version") != "klax-v8-primary-strategy-freeze-v1"
        or value.get("status") != "FROZEN_FOR_GENUINELY_NEW_GRADE_A_CONFIRMATION"
        or value.get("probability_model", {}).get("candidate_id") != LEADER
        or value.get("probability_model", {}).get("future_outcome_updates_allowed") is not False
        or value.get("probability_model", {}).get("regime_overlay_enabled") is not False
        or value.get("trade_policy", {}).get("execution_evidence_required") != "GRADE_A_FULL_BOOK"
        or value.get("confirmation_protocol", {}).get("retuning_before_or_after_confirmation_score_allowed") is not False
        or value.get("safety") != {
            "network_use_during_replay": False,
            "paper_orders_authorized": False,
            "live_orders_authorized": False,
            "current_or_live_market_feed_authorized": False,
        }
    ):
        raise StrategyFreezeError("frozen V8 strategy invariant differs")
    for relative, expected in value["source_bindings"].items():
        if _file_hash(root / relative) != expected:
            raise StrategyFreezeError(f"frozen V8 source changed: {relative}")
    _inputs(root)
    return value


def freeze(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if (root / OUTPUT).exists():
        return verify(root)
    artifact = build(root)
    _write_immutable_json(root / OUTPUT, artifact)
    return verify(root)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = freeze(args.project_root) if args.action == "freeze" else verify(args.project_root)
    print(value["self_sha256"])


if __name__ == "__main__":
    main()
