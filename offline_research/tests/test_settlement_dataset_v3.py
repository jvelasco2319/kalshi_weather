"""Exact, partition-isolated V3 settlement-target tests; no network."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
from unittest.mock import patch

import pyarrow.parquet as pq
import pytest

from klax_lab.dataset import _write_parquet
from klax_lab.settlement_dataset_v3 import (
    INPUT_TABLES,
    build_development_targets,
    build_partition_targets,
    build_protected_final_targets,
)
import klax_lab.settlement_dataset_v3 as settlement_module


UTC = timezone.utc


def fixture(partition="selection", day=date(2025, 1, 5), temperature=75, digest="a" * 64):
    day_text = day.isoformat()
    event = f"KXHIGHLAX-{day.strftime('%y%b%d').upper()}"
    issued = datetime(day.year, day.month, day.day, 8, 30, tzinfo=UTC) + timedelta(days=1)
    settled = issued + timedelta(hours=1)
    date_phrase = day.strftime("%B %d, %Y")
    base_rule = (f"If the highest temperature recorded in Los Angeles Airport, CA for {date_phrase} "
                 "as reported by the National Weather Service's Daily Climate Report, is {interval}, "
                 "then the market resolves to Yes.")
    contracts = [
        {"ticker": event + "-T76", "event_ticker": event, "climate_date": day_text,
         "partition": partition, "lower_integer_f": None, "upper_integer_f": 75,
         "strike_type": None, "bounds_source": "primary_rule_text",
         "rules_primary": base_rule.format(interval="less than 76°"),
         "source_sha256": "b" * 64, "historical_rule_revision_verified": False},
        {"ticker": event + "-T75", "event_ticker": event, "climate_date": day_text,
         "partition": partition, "lower_integer_f": 76, "upper_integer_f": None,
         "strike_type": "greater", "bounds_source": "strike_metadata",
         "rules_primary": base_rule.format(interval="greater than 75°"),
         "source_sha256": "b" * 64, "historical_rule_revision_verified": False},
    ]
    report = {"climate_date": day_text, "partition": partition, "issued_at": issued.isoformat(),
              "available_at": issued.isoformat(), "tmax_f": temperature, "record_flag": None,
              "source_member": "CLILAX_" + issued.strftime("%Y%m%d%H%M") + ".txt",
              "source_sha256": digest,
              "availability_status": "NWS_issuance_proxy_archive_receipt_latency_unverified",
              "label_role": "NWS_CLILAX_archival_copy", "archive_sha256": "c" * 64}
    outcomes, reconciliations = [], []
    for contract, yes in zip(contracts, (1, 0)):
        outcomes.append({"ticker": contract["ticker"], "event_ticker": event, "climate_date": day_text,
                         "partition": partition, "yes_outcome": yes, "settlement_temperature_f": float(temperature),
                         "event_temperature_status": "unique", "mapping_consistent": True,
                         "economic_eligible_mapping": True, "settlement_time": settled.isoformat(),
                         "source_sha256": "d" * 64})
        reconciliations.append({"ticker": contract["ticker"], "climate_date": day_text,
                                "partition": partition, "settlement_time_parseable": True,
                                "climate_as_of_settlement_available": True,
                                "climate_matches_expiration": True, "climate_matches_binary_result": True,
                                "settlement_label_reconciled": True, "climate_source_sha256": digest})
    return {"climate_versions": [report], "contracts": contracts,
            "outcomes": outcomes, "reconciliation": reconciliations}


def write_partition(root: Path, partition: str, rows: dict[str, list[dict]]) -> None:
    base = root / "data/normalized" / partition
    for name, relative in INPUT_TABLES.items():
        _write_parquet(base / relative, rows[name])


def test_exact_target_preserves_report_contract_and_reconciliation_evidence():
    targets, exclusions = build_partition_targets(fixture(), "selection")
    assert exclusions == [] and len(targets) == 1
    target = targets[0]
    assert target["station"] == "KLAX" and target["reported_high_f"] == 75
    assert target["source_sha256"] == "a" * 64
    assert target["report_issued_at"] == target["report_available_at"]
    assert target["settlement_at"] > target["report_available_at"]
    assert target["winning_contract_count"] == 1
    intervals = {row["ticker"]: row["interval"] for row in target["contract_outcome_reconciliation"]}
    lower = next(value for ticker, value in intervals.items() if ticker.endswith("T76"))
    upper = next(value for ticker, value in intervals.items() if ticker.endswith("T75"))
    assert lower["rule_upper_f"] == 76 and lower["rule_upper_inclusive"] is False
    assert lower["integer_upper_f"] == 75 and lower["integer_upper_inclusive"] is True
    assert lower["source_strike_type"] is None and lower["bounds_source"] == "primary_rule_text"
    assert upper["rule_lower_f"] == 75 and upper["rule_lower_inclusive"] is False
    assert all(row["yes_outcome"] == row["target_implied_yes_outcome"]
               for row in target["contract_outcome_reconciliation"])


@pytest.mark.parametrize("mutation, message", [
    (lambda rows: rows["climate_versions"][0].update(label_role="NWS_CLILAX_partial"), "Incomplete or partial"),
    (lambda rows: rows["climate_versions"][0].update(tmax_f=75.5), "integer Fahrenheit"),
    (lambda rows: rows["contracts"][0].update(rules_primary="Los Angeles weather"), "Ambiguous station"),
    (lambda rows: rows["outcomes"][0].update(yes_outcome=0), "outcome conflicts"),
    (lambda rows: rows["reconciliation"].pop(), "Incomplete contract"),
])
def test_ambiguous_partial_noninteger_or_conflicting_inputs_fail_closed(mutation, message):
    rows = fixture()
    mutation(rows)
    with pytest.raises(ValueError, match=message):
        build_partition_targets(rows, "selection")


def test_missing_complete_report_is_ineligible_instead_of_becoming_a_target():
    rows = fixture()
    rows["climate_versions"] = []
    for reconciliation in rows["reconciliation"]:
        reconciliation.update(climate_source_sha256=None, climate_as_of_settlement_available=False,
                              climate_matches_expiration=None, climate_matches_binary_result=None,
                              settlement_label_reconciled=None)
    targets, exclusions = build_partition_targets(rows, "selection")
    assert targets == []
    assert exclusions == [{"climate_date": "2025-01-05", "partition": "selection",
                           "reason": "no_complete_CLILAX_report_available_at_settlement",
                           "contract_count": 2}]


def test_development_and_protected_builders_read_and_write_disjoint_partitions():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_partition(root, "weather_training", fixture("weather_training", date(2024, 1, 1), digest="1" * 64))
        write_partition(root, "selection", fixture("selection", date(2025, 1, 5), digest="2" * 64))
        write_partition(root, "protected_final", fixture("protected_final", date(2025, 7, 1), digest="3" * 64))
        calls = []
        original = settlement_module._read_partition_tables

        def recording_reader(path, partition):
            calls.append(partition)
            return original(path, partition)

        with patch.object(settlement_module, "_read_partition_tables", side_effect=recording_reader):
            development = build_development_targets(root)
        assert calls == ["weather_training", "selection"]
        assert "protected_final" not in calls
        assert set(development["partitions"]) == {"weather_training", "selection"}
        component = __import__("json").loads(
            (root / "data/manifests/v3_settlement_reconciliation.json").read_text(encoding="utf-8")
        )
        assert component["status"] == "DEVELOPMENT_COMPLETE"
        assert component["protected_final_read"] is False
        assert component["eligible_target_count"] == 2
        assert pq.read_table(root / "data/normalized/v3_development/selection/labels/settlement_targets.parquet").num_rows == 1
        assert not (root / "data/normalized/v3_development/protected_final").exists()

        calls.clear()
        with patch.object(settlement_module, "_read_partition_tables", side_effect=recording_reader):
            protected = build_protected_final_targets(root)
        assert calls == ["protected_final"]
        assert set(protected["partitions"]) == {"protected_final"}
        assert pq.read_table(root / "data/normalized/v3_protected_final/protected_final/labels/settlement_targets.parquet").num_rows == 1
        assert not (root / "data/normalized/v3_protected_final/selection").exists()
