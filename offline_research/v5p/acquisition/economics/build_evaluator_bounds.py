"""Build the content-addressed V5P economics evaluator bounds.

This builder performs no network access.  It only binds public evidence already
saved under V5P and the frozen V5 acquisition that V5P extends.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "v5p" / "acquisition" / "economics" / "manifests" / "evaluator-bounds.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bind(relative_path: str, authority: str, purpose: str) -> dict[str, Any]:
    path = ROOT / relative_path
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "authority": authority,
        "bytes": path.stat().st_size,
        "path": relative_path.replace("\\", "/"),
        "purpose": purpose,
        "sha256": sha256(path),
    }


def build() -> dict[str, Any]:
    evidence = [
        bind(
            "v5p/acquisition/economics/manifests/source-manifest.json",
            "v5p_public_source_manifest",
            "Inventory of the bounded public acquisition and its safety declarations.",
        ),
        bind(
            "v5p/acquisition/economics/sources/cdx-kalshi-com-docs-kalshi-fee-schedule-pdf.json",
            "internet_archive_cdx_index",
            "Snapshot index for the official Kalshi fee-schedule URL; 37 captures from 2025-01-08 through 2026-06-12.",
        ),
        bind(
            "v5p/acquisition/economics/sources/kalshi-fee-schedule-effective-2026-07-07.pdf",
            "kalshi_official",
            "Official July 7, 2026 fee, rounding, default multiplier, settlement-fee, and FCM disclosure schedule.",
        ),
        bind(
            "v5p/acquisition/economics/sources/kalshi-fee-rounding-current.html",
            "kalshi_api_docs",
            "Official fill- and order-level fee-rounding documentation and account-precision distinction.",
        ),
        bind(
            "v5p/acquisition/economics/sources/kxhiglax-series-fee-history.json",
            "kalshi_public_api",
            "Current official historical series fee-change response; returned an empty change array.",
        ),
        bind(
            "v5p/acquisition/economics/sources/cftc-laxhigh-certification.pdf",
            "cftc_product_certification",
            "LAXHIGH settlement source, threshold semantics, schedule, and payout evidence.",
        ),
        bind(
            "v5p/acquisition/economics/sources/kalshi-globaltemperature-certification.pdf",
            "kalshi_product_certification",
            "GLOBALTEMPERATURE initial-listing date, source hierarchy, threshold semantics, and payout evidence.",
        ),
        bind(
            "v5/acquisition/economics/manifests/source-manifest.json",
            "frozen_v5_upstream_manifest",
            "Content-addressed upstream inventory; referenced without modifying frozen V5.",
        ),
        bind(
            "v5/acquisition/economics/manifests/kxhiglax-event-metadata.json",
            "kalshi_public_api_frozen_v5_capture",
            "604 bounded event metadata records and historical settlement-source evidence.",
        ),
        bind(
            "v5/acquisition/economics/manifests/kxhiglax-event-fee-changes.json",
            "kalshi_public_api_frozen_v5_capture",
            "604 event-level fee-history queries, all successful and without an event override.",
        ),
        bind(
            "v5/acquisition/economics/sources/kalshi-fee-schedule-capture-2025-07-08.pdf",
            "archived_kalshi_primary",
            "Official schedule captured July 8, 2025.",
        ),
        bind(
            "v5/acquisition/economics/sources/kalshi-fee-schedule-capture-2025-09-17.pdf",
            "archived_kalshi_primary",
            "Official schedule captured September 17, 2025.",
        ),
        bind(
            "v5/acquisition/economics/sources/kalshi-fee-schedule-capture-2025-10-08.pdf",
            "archived_kalshi_primary",
            "Official schedule stating an October 1, 2025 effective date.",
        ),
        bind(
            "v5/acquisition/economics/sources/kalshi-fee-schedule-capture-2026-02-14.pdf",
            "archived_kalshi_primary",
            "Official schedule stating a February 5, 2026 effective date.",
        ),
        bind(
            "v5/acquisition/economics/sources/cftc-vip-termination-and-replacement-2025-08-31.pdf",
            "cftc_filing",
            "Proof that the January 2025 proposed rebate never took effect, plus replacement-program conditions.",
        ),
        bind(
            "v5/acquisition/economics/sources/cftc-vip-update-2026-08-04.pdf",
            "cftc_filing",
            "August 2026 incentive update and its notice/account-dependence.",
        ),
    ]

    result: dict[str, Any] = {
        "schema_version": "klax-v5p-economics-evaluator-bounds-v1",
        "campaign": "V5P_PARTIAL_EVIDENCE",
        "series_ticker": "KXHIGHLAX",
        "target_period": {"date_start": "2025-07-01", "date_end": "2026-08-31"},
        "status": "RESEARCH_USABLE_WITH_EXCLUSIONS",
        "promotion_ready": False,
        "strict_verdict": "FEE_OR_SETTLEMENT_EVIDENCE_INCOMPLETE",
        "evaluator_policy": {
            "may_run_research": True,
            "may_claim_verified_profitability": False,
            "may_use_network_during_replay": False,
            "may_access_protected_confirmation_labels": False,
            "may_place_orders": False,
            "rebate_credit_usd": 0,
            "required_fill_model": "marketable/taker execution with quote-depth evidence; assumed fills remain non-promotable",
            "required_participant_class": "direct Kalshi exchange participant, or bind every additional FCM/broker fee from contemporaneous account evidence",
        },
        "fee_formula": {
            "taker": "0.07 * contract_count * price_dollars * (1 - price_dollars)",
            "maker": "0.0175 * contract_count * price_dollars * (1 - price_dollars)",
            "price_domain": "0 <= price_dollars <= 1",
            "currency": "USD",
        },
        "fee_periods": [
            {
                "date_start": "2025-07-01",
                "date_end": "2025-07-07",
                "taker_multiplier": None,
                "maker_multiplier": None,
                "rounding": None,
                "evaluator_action": "EXCLUDE",
                "reason": "No saved authoritative schedule is effective for these seven target dates; later continuity and the 2022 filing are insufficient for VERIFIED_EXACT.",
            },
            {
                "date_start": "2025-07-08",
                "date_end": "2025-09-30",
                "taker_multiplier": 1,
                "maker_multiplier": 0,
                "rounding": "ceil total order fee to the next whole cent",
                "evaluator_action": "ALLOW_TAKER_DIRECT_ONLY_WITH_FILL_EVIDENCE",
                "exactness": "schedule_exact_at_date_granularity",
                "basis": "July 8 and September 17 archived official schedules; KXHIGHLAX is absent from their enumerated maker-fee series.",
            },
            {
                "date_start": "2025-10-01",
                "date_end": "2026-07-06",
                "taker_multiplier": 1,
                "maker_multiplier": None,
                "rounding": "ceil total order fee to the next whole cent",
                "evaluator_action": "ALLOW_TAKER_DIRECT_ONLY_WITH_FILL_EVIDENCE; EXCLUDE_MAKER",
                "exactness": "taker_schedule_exact_at_date_granularity; maker_applicability_unresolved",
                "basis": "October 1 and February 5 effective schedules; maker applicability was delegated to a changing page. Empty current official series/event fee histories corroborate no override but do not substitute for an immutable page snapshot.",
            },
            {
                "date_start": "2026-07-07",
                "date_end": "2026-08-31",
                "taker_multiplier": 1,
                "maker_multiplier": 0,
                "rounding": "round upward so fee plus positionCost aligns to one centicent ($0.0001)",
                "evaluator_action": "ALLOW_TAKER_DIRECT_ONLY_WITH_FILL_EVIDENCE",
                "exactness": "schedule_exact_at_date_granularity",
                "basis": "Official schedule last updated and effective July 7, 2026; KXHIGHLAX is absent from non-standard fees, so defaults apply.",
            },
        ],
        "rebate_binding": {
            "credited_amount_usd": 0,
            "exact_for_evaluator": True,
            "reason": "The January 2025 proposal never took effect. Later programs require contemporaneous market designation, participant eligibility, volume, notice, and award evidence that is absent; the fail-closed value is zero.",
        },
        "settlement_binding": {
            "ordinary_winning_contract_payout_usd": 1,
            "exchange_settlement_fee_usd": 0,
            "historical_source_observed_for_all_604_bounded_events": "NWS Los Angeles Airport daily climatological report",
            "common_threshold_semantics": {
                "above": "strictly greater than",
                "below": "strictly less than",
                "between": "inclusive endpoints",
            },
            "semantic_equivalence_research_usable": True,
            "exact_event_rule_revision_bound": False,
            "promotion_action": "FAIL_CLOSED_UNTIL_EXACT_EVENT_RULE_REVISION_IS_BOUND",
            "reason": "Historical event objects expose settlement sources but not their contract-rule URL/revision. The December 8, 2025 GLOBALTEMPERATURE certification's initial-listing date does not prove the event-by-event KXHIGHLAX transition from LAXHIGH.",
        },
        "remaining_unresolvable_public_gaps": [
            {
                "id": "FEE_2025_07_01_TO_07_07",
                "impact": "Exclude seven target dates from exact economics evaluation.",
                "needed_evidence": "An authoritative fee schedule effective for July 1-7, 2025.",
            },
            {
                "id": "MAKER_2025_10_01_TO_2026_07_06",
                "impact": "Exclude maker fills in this interval; taker fills remain evaluable.",
                "needed_evidence": "Immutable historical maker-fee applicability page or trade/account fee record.",
            },
            {
                "id": "EVENT_RULE_REVISION",
                "impact": "Ordinary payout semantics may be explored, but no candidate can receive the strict promotion verdict.",
                "needed_evidence": "Per-event contract terms/revision or authoritative transition notice mapping KXHIGHLAX events to LAXHIGH versus GLOBALTEMPERATURE.",
            },
            {
                "id": "PARTICIPANT_AND_EXECUTION_FACTS",
                "impact": "No simulated trade is promotable from public data alone.",
                "needed_evidence": "Order arrival, quote depth, fill, maker/taker role, direct-vs-FCM class, account precision, and actual additional intermediary fees.",
            },
            {
                "id": "VIP_ENTITLEMENT",
                "impact": "Credit zero rebate.",
                "needed_evidence": "Contemporaneous designation, participant eligibility, volume, notice, and award record.",
            },
        ],
        "public_gap_resolution": {
            "july_2026_schedule_and_rounding": "CLEARED",
            "july_2026_kxhiglax_default_multipliers": "CLEARED",
            "historical_taker_fee_from_2025_07_08": "CLEARED_FOR_DIRECT_TAKER_TRADES",
            "historical_maker_applicability_2025_10_01_to_2026_07_06": "UNRESOLVED",
            "event_rule_transition": "UNRESOLVED",
        },
        "evidence": evidence,
        "safety": {
            "protected_confirmation_labels_read": False,
            "actual_orders_placed": False,
            "private_account_data_read": False,
            "historical_replay_network_allowed": False,
            "frozen_v5_modified": False,
        },
    }
    result["self_sha256"] = canonical_hash(result)
    return result


if __name__ == "__main__":
    value = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(OUT), "self_sha256": value["self_sha256"]}, sort_keys=True))
