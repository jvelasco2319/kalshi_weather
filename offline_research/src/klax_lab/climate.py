"""Versioned NWS daily-high labels and settlement reconciliation, evaluator only."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import zipfile

from .dataset import _write_parquet, partition
from .domain import PST, climate_day_bounds, round_fahrenheit
from .provenance import sha256_file, write_json


def parse_product(name: str, content: bytes) -> dict | None:
    match = re.fullmatch(r"CLILAX_(\d{12})\.txt", Path(name).name)
    if not match:
        raise ValueError("Unexpected climate archive member")
    issued = datetime.strptime(match[1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    text = content.decode("utf-8", errors="strict").upper()
    if "CLILAX" not in text or "LOS ANGELES INTL AIRPORT" not in text:
        raise ValueError("Wrong station product")
    summary = re.search(r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})", text)
    if not summary:
        return None
    target = datetime.strptime(" ".join(summary.groups()), "%B %d %Y").date()
    _, end = climate_day_bounds(target)
    # Partial afternoon reports are not daily settlement labels.
    if "VALID TODAY AS OF" in text or issued < end or issued > end + timedelta(days=7):
        return None
    header = re.search(r"CDUS46 KLOX (\d{6})", text)
    if not header or header[1] != issued.strftime("%d%H%M"):
        raise ValueError("Archive issuance and WMO timestamp disagree")
    corrected = "CORRECTED" in text or re.search(r"CDUS46 KLOX \d{6}\s+CC[A-Z]", text) is not None
    if re.search(r"^\s*YESTERDAY\s*$", text, re.M) and target != issued.astimezone(PST).date() - timedelta(days=1) and not corrected:
        raise ValueError("Uncorrected yesterday report has an inconsistent summary date")
    maximum = re.search(r"TEMPERATURE\s*\(F\).*?\bMAXIMUM\s+(-?\d+|MM)(R?)\s", text, re.S)
    if not maximum or maximum[1] == "MM":
        return None
    value = int(maximum[1])
    if not -30 <= value <= 140:
        raise ValueError("Implausible LAX temperature")
    return {"climate_date": target.isoformat(), "issued_at": issued.isoformat(),
            "available_at": issued.isoformat(), "tmax_f": value,
            "record_flag": maximum[2] or None,
            "source_member": name, "source_sha256": hashlib.sha256(content).hexdigest(),
            "availability_status": "NWS_issuance_proxy_archive_receipt_latency_unverified",
            "label_role": "NWS_CLILAX_archival_copy"}


def choose_as_of(versions: list[dict], cutoff: datetime) -> dict | None:
    if cutoff.tzinfo is None:
        raise ValueError("Label cutoff needs timezone")
    eligible = [r for r in versions if datetime.fromisoformat(r["available_at"]) <= cutoff]
    return max(eligible, key=lambda r: r["available_at"]) if eligible else None


def normalize_climate(root: Path, policy: dict) -> dict:
    import pyarrow.parquet as pq
    manifest = json.loads((root / "data/manifests/climate_2024-01-01_2026-01-08.json").read_text())
    source = root / manifest["path"]
    if sha256_file(source) != manifest["sha256"]:
        raise ValueError("Climate archive hash mismatch")
    versions = []
    ignored = 0
    quarantined = []
    with zipfile.ZipFile(source) as archive:
        for member in archive.infolist():
            name = member.filename
            try:
                row = parse_product(name, archive.read(member))
            except ValueError as error:
                quarantined.append({"source_member": name, "reason": str(error)})
                continue
            if row is None:
                ignored += 1
                continue
            row["archive_sha256"] = manifest["sha256"]
            row["partition"] = partition(row["climate_date"], policy)
            versions.append(row)
    groups = defaultdict(list)
    for row in versions:
        groups[row["climate_date"]].append(row)
    cutoff = datetime.fromisoformat(policy["model_fit_cutoff_utc"].replace("Z", "+00:00"))
    labels, reconciled = [], []
    for day, records in sorted(groups.items()):
        split = partition(day, policy)
        if split == "excluded":
            continue
        # Training revisions must be available before any 2025 research entry.
        label = choose_as_of(records, cutoff) if split == "weather_training" else max(records, key=lambda x: x["issued_at"])
        if label:
            labels.append({**label, "partition": split})
    base = root / "data/normalized"
    for split in ("weather_training", "selection", "protected_final"):
        folder = base / split / "labels"
        _write_parquet(folder / "climate_versions.parquet", [r for r in versions if r["partition"] == split])
        _write_parquet(folder / "climate.parquet", [r for r in labels if r["partition"] == split])
        outcomes = pq.read_table(folder / "outcomes.parquet").to_pylist()
        contracts = {r["ticker"]: r for r in pq.read_table(base / split / "features/contracts.parquet").to_pylist()}
        noaa = {r["climate_date"]: r for r in pq.read_table(folder / "noaa.parquet").to_pylist()}
        for result in outcomes:
            try:
                settled = datetime.fromisoformat(result["settlement_time"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                settled = None
            row = choose_as_of(groups[result["climate_date"]], settled) if settled else None
            reference = noaa.get(result["climate_date"])
            expiration = result["settlement_temperature_f"]
            from .domain import ContractBounds
            contract = contracts[result["ticker"]]
            binary_consistent = ContractBounds(contract["lower_integer_f"], contract["upper_integer_f"]).contains(row["tmax_f"]) == bool(result["yes_outcome"]) if row else None
            conflicted = result["event_temperature_status"] == "conflicting"
            reconciles = False if conflicted else (row["tmax_f"] == expiration) if row and expiration is not None else binary_consistent
            # A missing API expiration value is not an invented temperature.
            # Validate the actual binary payout independently against the NWS
            # report available at settlement and retain that distinct basis.
            if result["event_temperature_status"] == "missing" and row:
                result["mapping_consistent"] = binary_consistent
                result["mapping_validation_basis"] = "NWS_report_as_of_settlement_against_actual_binary_result"
                result["mapping_climate_source_sha256"] = row["source_sha256"]
            else:
                result["mapping_validation_basis"] = "explicit_API_event_temperature" if expiration is not None else "conflicting_API_event_temperatures" if conflicted else "unresolved"
                result["mapping_climate_source_sha256"] = None
            result["economic_eligible_mapping"] = result["mapping_consistent"] is True
            reconciled.append({"ticker": result["ticker"], "climate_date": result["climate_date"], "partition": split,
                               "settlement_time_parseable": settled is not None,
                               "climate_as_of_settlement_available": row is not None,
                               "climate_matches_expiration": (row["tmax_f"] == expiration) if row and expiration is not None else None,
                               "climate_matches_binary_result": binary_consistent,
                               "settlement_label_reconciled": reconciles,
                               "ncei_matches_expiration": (round_fahrenheit(reference["tmax_f"]) == expiration) if reference and expiration is not None else None,
                               "climate_source_sha256": row["source_sha256"] if row else None})
        _write_parquet(folder / "reconciliation.parquet", [r for r in reconciled if r["partition"] == split])
        _write_parquet(folder / "outcomes.parquet", outcomes)
    write_json(root / "data/manifests/climate_quarantine.json", quarantined)
    report = {"archive_products": manifest["product_count"], "retained_final_versions": len(versions),
              "quarantined_products": len(quarantined),
              "partial_or_missing_reports_excluded": ignored,
              "daily_labels": {s: sum(r["partition"] == s for r in labels) for s in ("weather_training", "selection", "protected_final")},
              "settlement_comparisons": len(reconciled),
              "climate_expiration_disagreements": sum(r["climate_matches_expiration"] is False for r in reconciled),
              "ncei_expiration_disagreements": sum(r["ncei_matches_expiration"] is False for r in reconciled),
              "climate_binary_result_disagreements": sum(r["climate_matches_binary_result"] is False for r in reconciled),
              "climate_asof_missing": sum(not r["climate_as_of_settlement_available"] for r in reconciled),
              "limitations": ["Archive copy issuance used as availability proxy; reception latency not independently verified", "Actual Kalshi binary outcomes remain the sole payout labels", "Selection/final forecast scores use latest retained climate labels; only training labels are fit-cutoff limited"]}
    write_json(root / "data/manifests/climate_normalization.json", report)
    return report
