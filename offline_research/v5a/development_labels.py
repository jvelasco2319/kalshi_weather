"""Open only the frozen V5A development labels from the CLILAX archive."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping
import zipfile

from klax_lab.climate import parse_product


UNIVERSE = Path("data/manifests/v5a_outcome_blind_universe.json")
CLIMATE_MANIFEST = Path(
    "data/raw/v5p/climate_workspace/data/manifests/climate_2025-07-01_2026-09-01.json"
)
OUTPUT = Path("data/development/v5a/development_labels.json")
SCHEMA = "klax-v5a-development-labels-v1"
_MEMBER = re.compile(r"CLILAX_(\d{12})\.txt\Z")


class DevelopmentLabelError(ValueError):
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
        raise DevelopmentLabelError(f"JSON object required: {path}")
    return value


def _verify(value: Mapping[str, Any], field: str = "self_sha256") -> None:
    if value.get(field) != _canonical_hash(value, field):
        raise DevelopmentLabelError(f"{field} mismatch")


def _member_target(name: str) -> date | None:
    match = _MEMBER.fullmatch(Path(name).name)
    if not match:
        raise DevelopmentLabelError("unexpected CLILAX archive member")
    issued = datetime.strptime(match.group(1), "%Y%m%d%H%M").replace(tzinfo=UTC)
    # Complete LAX climate-day products arrive after the fixed-PST day closes
    # at 08:00 UTC.  Earlier same-date products are partial "today so far"
    # reports and are never opened here.
    if not 8 <= issued.hour < 12:
        return None
    return issued.date() - timedelta(days=1)


def build(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    universe_path = workspace / UNIVERSE
    universe = _load(universe_path)
    _verify(universe)
    if (
        universe.get("status") != "FROZEN_OUTCOME_BLIND"
        or universe.get("holdout_access_authorized") is not False
        or universe.get("outcomes_read") is not False
    ):
        raise DevelopmentLabelError("universe must be frozen before development labels open")
    development_dates = set(universe["split"]["development_dates"])
    holdout_dates = set(universe["split"]["holdout_dates"])
    if development_dates & holdout_dates or len(development_dates) != 64 or len(holdout_dates) != 28:
        raise DevelopmentLabelError("frozen split differs")

    manifest_path = workspace / CLIMATE_MANIFEST
    climate = _load(manifest_path)
    archive_path = workspace / "data/raw/v5p/climate_workspace" / str(climate["path"])
    if (
        climate.get("status") != "downloaded"
        or not archive_path.is_file()
        or _file_hash(archive_path) != climate.get("sha256")
    ):
        raise DevelopmentLabelError("CLILAX archive binding differs")

    versions: dict[str, list[dict[str, Any]]] = {target: [] for target in development_dates}
    read_members = []
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            inferred = _member_target(member.filename)
            if inferred is None or inferred.isoformat() not in development_dates:
                continue
            content = archive.read(member)
            row = parse_product(member.filename, content)
            if row is None:
                continue
            if row["climate_date"] != inferred.isoformat():
                raise DevelopmentLabelError("opened CLILAX member targets a different date")
            versions[row["climate_date"]].append(row)
            read_members.append({
                "source_member": member.filename,
                "source_sha256": row["source_sha256"],
                "climate_date": row["climate_date"],
            })
    labels = []
    for climate_date in sorted(development_dates):
        candidates = versions[climate_date]
        if not candidates:
            raise DevelopmentLabelError(f"development CLILAX label missing: {climate_date}")
        # Kalshi's NWS-era rules use the first complete official report.
        selected = min(candidates, key=lambda row: row["issued_at"])
        labels.append({
            "climate_date": climate_date,
            "reported_high_f": int(selected["tmax_f"]),
            "issued_at": selected["issued_at"],
            "source_member": selected["source_member"],
            "source_sha256": selected["source_sha256"],
            "selection_rule": "first_complete_official_CLILAX_report_after_08_UTC",
        })
    if set(row["climate_date"] for row in labels) != development_dates:
        raise DevelopmentLabelError("development label coverage differs")
    if any(row["climate_date"] in holdout_dates for row in labels):
        raise DevelopmentLabelError("holdout label crossed the development boundary")
    body = {
        "schema_version": SCHEMA,
        "status": "DEVELOPMENT_LABELS_OPENED_HOLDOUT_REMAINS_SEALED",
        "universe": {
            "path": UNIVERSE.as_posix(),
            "sha256": _file_hash(universe_path),
            "self_sha256": universe["self_sha256"],
        },
        "climate_archive": {
            "manifest_path": CLIMATE_MANIFEST.as_posix(),
            "manifest_sha256": _file_hash(manifest_path),
            "archive_path": archive_path.relative_to(workspace).as_posix(),
            "archive_sha256": _file_hash(archive_path),
        },
        "development_date_count": len(labels),
        "holdout_date_count": len(holdout_dates),
        "labels": labels,
        "read_members": sorted(read_members, key=lambda row: row["source_member"]),
        "development_labels_opened": True,
        "holdout_labels_opened": False,
        "protected_confirmation_labels_read": False,
        "network_used": False,
        "actual_orders_placed": False,
    }
    body["self_sha256"] = _canonical_hash(body)
    return body


def write(root: Path | str) -> dict[str, Any]:
    workspace = Path(root).resolve()
    value = build(workspace)
    path = workspace / OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _load(path) != value:
        raise DevelopmentLabelError("immutable development labels differ")
    if not path.exists():
        pending = path.with_name(path.name + ".pending")
        pending.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n",
            encoding="utf-8", newline="\n",
        )
        pending.replace(path)
    return value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    value = write(args.project_root)
    print(json.dumps({
        "output": OUTPUT.as_posix(),
        "self_sha256": value["self_sha256"],
        "development_date_count": value["development_date_count"],
        "holdout_labels_opened": value["holdout_labels_opened"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
