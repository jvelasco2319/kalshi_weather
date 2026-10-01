"""Deterministic V3 training/development dataset freeze and fold construction.

The builder consumes normalized rows supplied by its caller.  It performs no
acquisition and resolves no input paths.  Every input table is bound to an
explicit manifest and every retained feature is selected at or before its
simulated decision cutoff.  Settlement labels are written to a physically
separate artifact from discovery features.

Weather-model fitting evidence is restricted to calendar year 2024.  The
development interval is split physically into a fixed January 5 through
February 3, 2025 calibration prefix and a February 4 through June 30 scored
evaluation window.  Protected-final material is rejected rather than filtered.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .provenance import canonical_hash, sha256_file, write_json


UTC = timezone.utc
TRAINING_START = date(2024, 1, 1)
TRAINING_END = date(2024, 12, 31)
DEVELOPMENT_START = date(2025, 1, 5)
DEVELOPMENT_END = date(2025, 6, 30)
CALIBRATION_END = date(2025, 2, 3)
EVALUATION_START = date(2025, 2, 4)
PROTECTED_FINAL_START = date(2025, 7, 1)
PARTITION = "selection"
TRAINING_PARTITION = "weather_training"
FOLD_COUNT = 5
DATASET_SCHEMA_VERSION = "klax-v3-frozen-development-v1"
FOLD_SCHEMA_VERSION = "klax-v3-five-contiguous-folds-v1"
INPUT_SCHEMA_VERSION = "klax-v3-normalized-input-binding-v1"
DEVELOPMENT_COMPONENTS = ("settlement", "market", "observation", "forecast")
TRAINING_COMPONENTS = ("training_settlement", "training_observation", "training_forecast")
COMPONENTS = TRAINING_COMPONENTS + DEVELOPMENT_COMPONENTS
SHA256 = re.compile(r"[0-9a-f]{64}")
DECISION = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
OBSERVATION_FIELDS = (
    "temperature_f", "dewpoint_f", "wind_direction_degrees", "wind_speed_kt",
    "pressure_hpa", "cloud_ceiling_ft", "visibility_miles",
)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Normalized rows and manifests must be finite JSON values") from exc


def _frozen_copy(value: Any) -> Any:
    """Copy through JSON so later caller mutation cannot change the freeze."""
    return json.loads(_canonical_json(value))


def _canonical_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copied = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("Every normalized input row must be an object")
        copied.append(_frozen_copy(dict(row)))
    return sorted(copied, key=_canonical_json)


def _source_hashes(value: Any, *, key: str = "") -> set[str]:
    hashes: set[str] = set()
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            name = str(child_key)
            if name == "source_sha256" or name.endswith("_source_sha256"):
                if child is not None:
                    if not isinstance(child, str) or SHA256.fullmatch(child) is None:
                        raise ValueError(f"{name} is not a lowercase SHA-256 digest")
                    hashes.add(child)
            elif name.endswith("_source_sha256s"):
                if child is not None and not isinstance(child, list):
                    raise ValueError(f"{name} must be a list of lowercase SHA-256 digests")
                for digest in child or []:
                    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                        raise ValueError(f"{name} contains an invalid SHA-256 digest")
                    hashes.add(digest)
            else:
                hashes.update(_source_hashes(child, key=name))
    elif isinstance(value, list):
        for child in value:
            hashes.update(_source_hashes(child, key=key))
    return hashes


def _manifest_path_values(value: Any, *, key: str = "") -> Iterable[str]:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            name = str(child_key)
            if name == "path" or name.endswith("_path"):
                if isinstance(child, str):
                    yield child
            yield from _manifest_path_values(child, key=name)
    elif isinstance(value, list):
        for child in value:
            yield from _manifest_path_values(child, key=key)


def _has_protected_path(path: str) -> bool:
    return "protected_final" in re.split(r"[\\/]", path.casefold())


def normalized_input_manifest(
    component: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    upstream_manifests: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a deterministic binding for one caller-normalized input table.

    Upstream manifests are hashed as complete JSON objects.  The builder later
    checks the row count, order-independent row digest, and exact set of source
    object digests found recursively in the normalized rows.
    """
    if component not in COMPONENTS:
        raise ValueError("Unknown V3 normalized input component")
    partition = TRAINING_PARTITION if component in TRAINING_COMPONENTS else PARTITION
    canonical = _canonical_rows(rows)
    sources = sorted(_source_hashes(canonical))
    if canonical and not sources:
        raise ValueError(f"{component} rows have no bound source SHA-256 values")
    upstream = []
    for manifest in upstream_manifests:
        if not isinstance(manifest, Mapping):
            raise ValueError("Upstream manifests must be JSON objects")
        frozen = _frozen_copy(dict(manifest))
        upstream.append({"sha256": canonical_hash(frozen), "manifest": frozen})
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "component": component,
        "partitions": [partition],
        "protected_final_read": False,
        "row_count": len(canonical),
        "rows_sha256": canonical_hash(canonical),
        "source_sha256s": sources,
        "upstream_manifests": sorted(upstream, key=lambda item: item["sha256"]),
    }


def _validate_binding(component: str, rows: list[dict[str, Any]],
                      manifest: Mapping[str, Any]) -> dict[str, Any]:
    frozen = _frozen_copy(dict(manifest))
    partition = TRAINING_PARTITION if component in TRAINING_COMPONENTS else PARTITION
    expected = normalized_input_manifest(component, rows)
    required = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "component": component,
        "partitions": [partition],
        "protected_final_read": False,
        "row_count": len(rows),
        "rows_sha256": canonical_hash(rows),
        "source_sha256s": sorted(_source_hashes(rows)),
    }
    for key, value in required.items():
        if frozen.get(key) != value:
            raise ValueError(f"{component} input manifest has an invalid {key} binding")
    upstream = frozen.get("upstream_manifests")
    if not isinstance(upstream, list):
        raise ValueError(f"{component} input manifest lacks upstream manifest bindings")
    for item in upstream:
        if (not isinstance(item, dict) or set(item) != {"sha256", "manifest"}
                or not isinstance(item["sha256"], str)
                or SHA256.fullmatch(item["sha256"]) is None
                or not isinstance(item["manifest"], dict)
                or canonical_hash(item["manifest"]) != item["sha256"]):
            raise ValueError(f"{component} upstream manifest binding is invalid")
    if any(_has_protected_path(path) for path in _manifest_path_values(frozen)):
        raise ValueError("Protected-final paths cannot enter a development input manifest")
    # Keep this assignment explicit: the generated subset above intentionally
    # does not replace caller-supplied upstream hashes.
    del expected
    return frozen


def _parse_day(value: Any, field: str = "climate_date", *,
               partition: str = PARTITION) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc
    if parsed >= PROTECTED_FINAL_START:
        raise ValueError("Protected-final rows cannot enter the V3 freeze")
    allowed = ((TRAINING_START, TRAINING_END) if partition == TRAINING_PARTITION
               else (DEVELOPMENT_START, DEVELOPMENT_END))
    if not allowed[0] <= parsed <= allowed[1]:
        raise ValueError(f"Rows outside the registered V3 {partition} interval are forbidden")
    return parsed


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


def _validate_row_scope(row: Mapping[str, Any], *, partition: str = PARTITION) -> date:
    if row.get("partition") != partition:
        if row.get("partition") == "protected_final" or row.get("protected_final") is True:
            raise ValueError("Protected-final rows cannot enter the development freeze")
        raise ValueError(f"Every V3 frozen row must use the {partition} partition")
    if row.get("protected_final") not in (None, False):
        raise ValueError("Protected-final rows cannot enter the development freeze")
    return _parse_day(row.get("climate_date"), partition=partition)


def _unique(rows: Sequence[dict[str, Any]], key, name: str) -> None:
    seen: set[Any] = set()
    for row in rows:
        grain = key(row)
        if grain in seen:
            raise ValueError(f"Duplicate {name} grain: {grain}")
        seen.add(grain)


def _decision_times(values: Sequence[str]) -> tuple[str, ...]:
    if not values:
        raise ValueError("At least one decision time is required")
    if any(not isinstance(value, str) or DECISION.fullmatch(value) is None for value in values):
        raise ValueError("Decision times must use HH:MM UTC")
    result = tuple(sorted(set(values)))
    if len(result) != len(values):
        raise ValueError("Decision times must be unique")
    return result


def _decision_at(day: date, value: str) -> datetime:
    hour, minute = map(int, value.split(":"))
    return datetime.combine(day, time(hour, minute), UTC)


def _market_kind(row: Mapping[str, Any]) -> str:
    if "end_period_ts" in row:
        return "candle"
    if "trade_id" in row:
        return "trade"
    kind = row.get("record_type")
    if kind in {"candle", "trade"}:
        return str(kind)
    raise ValueError("Market row is neither a one-minute candle nor a public trade")


def _market_time(row: Mapping[str, Any]) -> datetime:
    if _market_kind(row) == "candle":
        if row.get("period_minutes") != 1:
            raise ValueError("V3 frozen market candles must be one-minute bars")
        stamp = row.get("end_period_ts")
        if type(stamp) is not int or stamp < 0:
            raise ValueError("Market candle end_period_ts must be a Unix integer")
        end = datetime.fromtimestamp(stamp, UTC)
        if "available_at" in row and _parse_timestamp(row["available_at"], "available_at") != end:
            raise ValueError("Candle availability must equal its completed bar timestamp")
        return end
    if "created_time" in row:
        created = _parse_timestamp(row["created_time"], "created_time")
    elif "available_at" in row:
        created = _parse_timestamp(row["available_at"], "available_at")
    else:
        raise ValueError("Public trade lacks a timestamp")
    if "created_ts" in row and row["created_ts"] != int(created.timestamp()):
        raise ValueError("Public-trade timestamp representations disagree")
    if "available_at" in row and _parse_timestamp(row["available_at"], "available_at") != created:
        raise ValueError("Public-trade availability must equal its publication timestamp")
    return created


def _quantity(row: Mapping[str, Any]) -> Decimal:
    value = row.get("quantity_contracts", "0")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Public-trade quantity is invalid") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Public-trade quantity must be positive")
    return result


def _settlement_tables(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    _unique(rows, lambda row: row.get("climate_date"), "settlement date")
    labels, contracts_by_day = [], {}
    for row in sorted(rows, key=lambda item: item["climate_date"]):
        day = _validate_row_scope(row)
        if row.get("station") != "KLAX" or row.get("reconciliation_status") != "passed":
            raise ValueError("Settlement targets must be passed exact KLAX reconciliations")
        high = row.get("reported_high_f")
        if type(high) is not int or not -100 <= high <= 150:
            raise ValueError("Settlement target must be an integer Fahrenheit value")
        event = row.get("event_ticker")
        contracts = row.get("contract_outcome_reconciliation")
        if not isinstance(event, str) or not event.startswith("KXHIGHLAX-"):
            raise ValueError("Settlement target has an invalid event identity")
        if not isinstance(contracts, list) or not contracts:
            raise ValueError("Settlement target has no reconciled contracts")
        if row.get("contract_count") != len(contracts):
            raise ValueError("Settlement target contract count is incomplete")
        tickers: list[str] = []
        label_contracts = []
        feature_contracts = []
        for contract in sorted(contracts, key=lambda item: item.get("ticker", "")):
            ticker = contract.get("ticker")
            outcome = contract.get("yes_outcome")
            if (not isinstance(ticker, str) or not ticker.startswith(event + "-")
                    or outcome not in (0, 1)
                    or contract.get("settlement_label_reconciled") is not True):
                raise ValueError("Settlement contract reconciliation is incomplete")
            interval = contract.get("interval")
            if not isinstance(interval, dict):
                raise ValueError("Settlement contract lacks its exact interval")
            tickers.append(ticker)
            feature_contracts.append({"ticker": ticker, "interval": interval})
            label_contracts.append({"ticker": ticker, "yes_outcome": outcome})
        if len(set(tickers)) != len(tickers):
            raise ValueError("Duplicate ticker in settlement target")
        winning = sum(item["yes_outcome"] for item in label_contracts)
        if winning != 1 or row.get("winning_contract_count") != 1:
            raise ValueError("Each exhaustive settlement event must have exactly one winning contract")
        contracts_by_day[day.isoformat()] = tickers
        labels.append({
            "schema_version": 1,
            "partition": PARTITION,
            "climate_date": day.isoformat(),
            "event_ticker": event,
            "reported_high_f": high,
            "contracts": label_contracts,
            "settlement_source_sha256": row["source_sha256"],
            "label_role": "evaluation_only_not_discovery_feature",
        })
        row["_feature_contracts"] = feature_contracts
    if len(labels) < FOLD_COUNT:
        raise ValueError("At least five complete development days are required for five folds")
    return labels, contracts_by_day


def _training_label_tables(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate exact 2024 KLAX targets without requiring Kalshi contracts."""
    _unique(rows, lambda row: row.get("climate_date"), "weather-training settlement date")
    labels = []
    for row in sorted(rows, key=lambda item: item["climate_date"]):
        day = _validate_row_scope(row, partition=TRAINING_PARTITION)
        if row.get("station") != "KLAX":
            raise ValueError("Weather-training settlement targets must use KLAX")
        high = row.get("reported_high_f")
        if type(high) is not int or not -100 <= high <= 150:
            raise ValueError("Weather-training target must be an integer Fahrenheit value")
        status = row.get("reconciliation_status")
        if status not in (None, "passed"):
            raise ValueError("Weather-training settlement target failed reconciliation")
        source = row.get("source_sha256")
        if not isinstance(source, str) or SHA256.fullmatch(source) is None:
            raise ValueError("Weather-training target lacks source provenance")
        labels.append({
            "schema_version": 1,
            "partition": TRAINING_PARTITION,
            "data_role": "weather_model_training_label",
            "climate_date": day.isoformat(),
            "station": "KLAX",
            "reported_high_f": high,
            "settlement_source_sha256": source,
            "label_role": "fitting_only_not_scored_evaluation",
        })
    if not labels:
        raise ValueError("Weather-training labels are empty")
    return labels


def _development_role(day: date) -> str:
    if DEVELOPMENT_START <= day <= CALIBRATION_END:
        return "development_calibration"
    if EVALUATION_START <= day <= DEVELOPMENT_END:
        return "development_evaluation"
    raise ValueError("Development day is outside calibration and evaluation roles")


def _market_grain(row: Mapping[str, Any]) -> tuple[Any, ...]:
    kind = _market_kind(row)
    if kind == "candle":
        return kind, row.get("ticker"), row.get("end_period_ts")
    return kind, row.get("trade_id")


def _observation_grain(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return row.get("climate_date"), row.get("station"), row.get("observed_at")


def _forecast_grain(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("climate_date"), row.get("model"), row.get("member_id"),
        row.get("initialized_at"), row.get("valid_at"), row.get("field_id"),
        row.get("location_id"),
    )


def _trade_summary(rows: Sequence[dict[str, Any]], decision: datetime) -> dict[str, Any]:
    start = decision - timedelta(hours=1)
    recent = [(row, _market_time(row)) for row in rows]
    recent = [(row, stamp) for row, stamp in recent if start < stamp <= decision]
    volume = sum((_quantity(row) for row, _ in recent), Decimal("0"))
    latest = max(recent, key=lambda item: (item[1], item[0]["trade_id"]))[0] if recent else None
    return {
        "window_start_exclusive": start.isoformat(),
        "window_end_inclusive": decision.isoformat(),
        "trade_count": len(recent),
        "quantity_contracts": format(volume, "f"),
        "latest_trade": latest,
        "source_sha256s": sorted(_source_hashes([row for row, _ in recent])),
    }


def _market_snapshot(ticker: str, rows: Sequence[dict[str, Any]],
                     decision: datetime) -> dict[str, Any]:
    candles, trades = [], []
    for row in rows:
        if row.get("ticker") != ticker:
            continue
        stamp = _market_time(row)
        if stamp <= decision:
            (candles if _market_kind(row) == "candle" else trades).append((row, stamp))
    if not candles:
        raise ValueError(f"No completed one-minute candle exists as of {decision.isoformat()} for {ticker}")
    candle = max(candles, key=lambda item: item[1])[0]
    if _market_time(candle) > decision:
        raise AssertionError("Market as-of selection crossed its decision cutoff")
    return {
        "availability_basis": "completed_one_minute_bar_end_timestamp",
        "latest_completed_candle": candle,
        "public_trades_last_60_minutes": _trade_summary([row for row, _ in trades], decision),
    }


def _observation_snapshot(rows: Sequence[dict[str, Any]], decision: datetime,
                          required_stations: tuple[str, ...],
                          optional_stations: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    result = []
    required = set(required_stations)
    for station in required_stations + optional_stations:
        candidates = []
        for row in rows:
            if row.get("station") != station:
                continue
            if row.get("as_of_validated") is not True:
                raise ValueError("Observation row lacks the registered as-of audit")
            if not isinstance(row.get("source_sha256"), str) or SHA256.fullmatch(row["source_sha256"]) is None:
                raise ValueError("Observation row lacks exact source provenance")
            observed = _parse_timestamp(row.get("observed_at"), "observed_at")
            available = _parse_timestamp(row.get("available_at"), "available_at")
            issued = _parse_timestamp(row.get("issued_at"), "issued_at")
            if issued < observed or available < issued:
                raise ValueError("Observation timestamp order is inconsistent")
            if observed <= decision and available <= decision:
                candidates.append((row, observed, available))
        if not candidates and station in required:
            raise ValueError(f"No {station} observation exists as of {decision.isoformat()}")
        if not candidates:
            continue
        selected = max(candidates, key=lambda item: (item[1], item[2], item[0].get("source_record_id", "")))[0]
        if any(field not in selected for field in OBSERVATION_FIELDS):
            raise ValueError("Observation snapshot lacks a normalized weather field")
        result.append(selected)
    return result


def _forecast_snapshot(rows: Sequence[dict[str, Any]], decision: datetime,
                       required_models: tuple[str, ...],
                       minimum_records: Mapping[str, int],
                       required_fields: Mapping[str, tuple[str, ...]],
                       required_members: Mapping[str, tuple[str | None, ...]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for model in required_models:
        cycles: dict[datetime, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("model") != model:
                continue
            if row.get("as_of_validated") is not True:
                raise ValueError("Forecast row lacks the registered as-of audit")
            if "member_id" not in row:
                raise ValueError("Forecast row lacks an explicit member_id")
            if not isinstance(row.get("source_sha256"), str) or SHA256.fullmatch(row["source_sha256"]) is None:
                raise ValueError("Forecast row lacks exact source provenance")
            if not isinstance(row.get("field_id"), str) or not row["field_id"]:
                raise ValueError("Forecast row lacks a normalized field_id")
            if type(row.get("is_missing")) is not bool:
                raise ValueError("Forecast row lacks an explicit is_missing flag")
            value = row.get("value")
            if row["is_missing"] is (value is not None):
                raise ValueError("Forecast missingness flag contradicts its normalized value")
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise ValueError("Forecast value must be finite numeric or null")
            initialized = _parse_timestamp(row.get("initialized_at"), "initialized_at")
            available = _parse_timestamp(row.get("available_at"), "available_at")
            _parse_timestamp(row.get("valid_at"), "valid_at")
            if available < initialized:
                raise ValueError("Forecast availability precedes initialization")
            if initialized <= decision and available <= decision:
                cycles.setdefault(initialized, []).append(row)
        minimum = minimum_records[model]
        complete = [
            (cycle, values) for cycle, values in cycles.items()
            if len(values) >= minimum
            and set(required_fields[model]) <= {row["field_id"] for row in values}
            and set(required_members[model]) <= {row["member_id"] for row in values}
        ]
        if not complete:
            raise ValueError(
                f"No complete {model} forecast cycle exists as of {decision.isoformat()}"
            )
        cycle, values = max(complete, key=lambda item: item[0])
        if any(_parse_timestamp(row["initialized_at"], "initialized_at") != cycle for row in values):
            raise AssertionError("Forecast cycle selection is inconsistent")
        selected.extend(sorted(values, key=_canonical_json))
    return selected


def build_five_chronological_folds(
    dates: Sequence[str], *, dataset_id: str,
    calibration_dates: Sequence[str] = (),
) -> dict[str, Any]:
    """Split only scored evaluation dates into five contiguous folds."""
    if not isinstance(dataset_id, str) or SHA256.fullmatch(dataset_id) is None:
        raise ValueError("dataset_id must be a lowercase SHA-256 digest")
    parsed = [_parse_day(value, "fold date") for value in dates]
    ordered = sorted(parsed)
    if ordered != parsed or len(set(ordered)) != len(ordered):
        raise ValueError("Fold dates must be sorted and unique")
    if len(ordered) < FOLD_COUNT:
        raise ValueError("Five nonempty development folds require at least five dates")
    if any(value < EVALUATION_START for value in ordered):
        raise ValueError("Calibration-prefix dates cannot enter scored evaluation folds")
    calibration = [_parse_day(value, "calibration date") for value in calibration_dates]
    if calibration != sorted(calibration) or len(set(calibration)) != len(calibration):
        raise ValueError("Calibration dates must be sorted and unique")
    expected_calibration = [
        DEVELOPMENT_START + timedelta(days=index)
        for index in range((CALIBRATION_END - DEVELOPMENT_START).days + 1)
    ]
    if calibration != expected_calibration:
        raise ValueError("The frozen calibration prefix must contain every date from 2025-01-05 through 2025-02-03")
    calibration_text = [value.isoformat() for value in calibration]
    quotient, remainder = divmod(len(ordered), FOLD_COUNT)
    folds, offset = [], 0
    for index in range(FOLD_COUNT):
        size = quotient + (1 if index < remainder else 0)
        values = [value.isoformat() for value in ordered[offset:offset + size]]
        folds.append({
            "fold_id": f"v3-evaluation-{index + 1}",
            "fold_index": index + 1,
            "start_date": values[0],
            "end_date": values[-1],
            "day_count": len(values),
            "dates": values,
            "dates_sha256": canonical_hash(values),
            "permitted_calibration_dates": calibration_text,
            "permitted_calibration_dates_sha256": canonical_hash(calibration_text),
        })
        offset += size
    body = {
        "schema_version": FOLD_SCHEMA_VERSION,
        "component": "five_fold_split",
        "dataset_id": dataset_id,
        "partition": PARTITION,
        "method": "sorted eligible development settlement days split into five contiguous folds; earliest folds receive at most one extra day",
        "training_rule": "weather/regime fitting uses only weather_training through 2024-12-31; market-residual and conformal fitting may additionally use the fixed 2025-01-05 through 2025-02-03 calibration prefix; no scored evaluation date may be used for fitting or calibration",
        "protected_final_included": False,
        "calibration_dates": calibration_text,
        "calibration_dates_sha256": canonical_hash(calibration_text),
        "evaluation_dates": [value.isoformat() for value in ordered],
        "evaluation_dates_sha256": canonical_hash([value.isoformat() for value in ordered]),
        "folds": folds,
    }
    return {**body, "folds_id": canonical_hash(body)}


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return ("".join(_canonical_json(row) + "\n" for row in rows)).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    import hashlib
    return hashlib.sha256(value).hexdigest()


def _write_immutable(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != contents:
            raise ValueError(f"Cannot overwrite frozen artifact with different bytes: {path.name}")
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(contents)
    temporary.replace(path)


def build_frozen_development_dataset(
    destination: Path,
    *,
    training_settlement_rows: Iterable[Mapping[str, Any]],
    training_settlement_manifest: Mapping[str, Any],
    training_observation_rows: Iterable[Mapping[str, Any]],
    training_observation_manifest: Mapping[str, Any],
    training_forecast_rows: Iterable[Mapping[str, Any]],
    training_forecast_manifest: Mapping[str, Any],
    settlement_rows: Iterable[Mapping[str, Any]],
    settlement_manifest: Mapping[str, Any],
    market_rows: Iterable[Mapping[str, Any]],
    market_manifest: Mapping[str, Any],
    observation_rows: Iterable[Mapping[str, Any]],
    observation_manifest: Mapping[str, Any],
    forecast_rows: Iterable[Mapping[str, Any]],
    forecast_manifest: Mapping[str, Any],
    decision_times_utc: Sequence[str],
    required_stations: Sequence[str] = ("KLAX",),
    required_forecast_models: Sequence[str] = ("hrrr", "gefs"),
    minimum_forecast_records_per_model: Mapping[str, int] | None = None,
    required_forecast_fields_by_model: Mapping[str, Sequence[str]] | None = None,
    required_forecast_members_by_model: Mapping[str, Sequence[str | None]] | None = None,
    optional_observation_stations: Sequence[str] = (),
    training_exclusions: Mapping[str, str] | None = None,
    development_exclusions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build one immutable V3 training/calibration/evaluation freeze."""
    destination = Path(destination).resolve()
    if _has_protected_path(str(destination)):
        raise ValueError("Development dataset destination cannot be a protected-final path")
    decisions = _decision_times(tuple(decision_times_utc))
    stations = tuple(sorted(set(required_stations)))
    optional_stations = tuple(sorted(set(optional_observation_stations)))
    models = tuple(sorted(set(required_forecast_models)))
    if (not stations or len(stations) != len(required_stations)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{4}", value)
                   for value in stations)):
        raise ValueError("Required observation stations must be unique four-character identifiers")
    if (len(optional_stations) != len(optional_observation_stations)
            or any(not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{4}", value)
                   for value in optional_stations)
            or set(stations) & set(optional_stations)):
        raise ValueError(
            "Optional observation stations must be unique four-character identifiers "
            "disjoint from required stations"
        )
    if (not models or len(models) != len(required_forecast_models)
            or any(not isinstance(value, str) or not value for value in models)):
        raise ValueError("Required forecast models must be unique nonempty identifiers")
    minima = dict(minimum_forecast_records_per_model or {model: 1 for model in models})
    if set(minima) != set(models) or any(type(value) is not int or value < 1 for value in minima.values()):
        raise ValueError("Every required forecast model needs a positive minimum record count")
    raw_fields = (required_forecast_fields_by_model
                  or {model: ("temperature_2m",) for model in models})
    if set(raw_fields) != set(models):
        raise ValueError("Every required forecast model needs an explicit field contract")
    required_fields = {}
    for model in models:
        fields = tuple(sorted(set(raw_fields[model])))
        if (not fields or len(fields) != len(raw_fields[model])
                or any(not isinstance(value, str) or not value for value in fields)):
            raise ValueError("Required forecast fields must be unique nonempty identifiers")
        required_fields[model] = fields
    raw_members = (required_forecast_members_by_model or {
        model: (("avg", "spr") if model == "gefs" else (None,)) for model in models
    })
    if set(raw_members) != set(models):
        raise ValueError("Every required forecast model needs an explicit member contract")
    required_members = {}
    for model in models:
        values = tuple(raw_members[model])
        members = tuple(sorted(set(values), key=lambda value: "" if value is None else value))
        if (not members or len(members) != len(values)
                or any(value is not None and (not isinstance(value, str) or not value)
                       for value in members)):
            raise ValueError("Required forecast members must be unique strings or null")
        required_members[model] = members

    tables = {
        "training_settlement": _canonical_rows(training_settlement_rows),
        "training_observation": _canonical_rows(training_observation_rows),
        "training_forecast": _canonical_rows(training_forecast_rows),
        "settlement": _canonical_rows(settlement_rows),
        "market": _canonical_rows(market_rows),
        "observation": _canonical_rows(observation_rows),
        "forecast": _canonical_rows(forecast_rows),
    }
    manifests = {
        "training_settlement": _validate_binding(
            "training_settlement", tables["training_settlement"], training_settlement_manifest,
        ),
        "training_observation": _validate_binding(
            "training_observation", tables["training_observation"], training_observation_manifest,
        ),
        "training_forecast": _validate_binding(
            "training_forecast", tables["training_forecast"], training_forecast_manifest,
        ),
        "settlement": _validate_binding("settlement", tables["settlement"], settlement_manifest),
        "market": _validate_binding("market", tables["market"], market_manifest),
        "observation": _validate_binding("observation", tables["observation"], observation_manifest),
        "forecast": _validate_binding("forecast", tables["forecast"], forecast_manifest),
    }
    if any(not rows for rows in tables.values()):
        raise ValueError("Every V3 frozen input component must contain rows")
    for component, rows in tables.items():
        for row in rows:
            _validate_row_scope(
                row, partition=(TRAINING_PARTITION if component in TRAINING_COMPONENTS else PARTITION),
            )

    _unique(tables["market"], _market_grain, "market")
    _unique(tables["observation"], _observation_grain, "observation")
    _unique(tables["forecast"], _forecast_grain, "forecast")
    _unique(tables["training_observation"], _observation_grain, "weather-training observation")
    _unique(tables["training_forecast"], _forecast_grain, "weather-training forecast")
    training_labels = _training_label_tables(tables["training_settlement"])
    labels, contracts_by_day = _settlement_tables(tables["settlement"])
    training_exclusions = dict(training_exclusions or {})
    development_exclusions = dict(development_exclusions or {})

    def validate_exclusions(values: Mapping[str, str], partition: str) -> dict[str, str]:
        normalized = {}
        for day_text, reason in values.items():
            day = _parse_day(day_text, "excluded date", partition=partition)
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("Every excluded date requires a nonempty preregistered reason")
            normalized[day.isoformat()] = reason.strip()
        return dict(sorted(normalized.items()))

    training_exclusions = validate_exclusions(training_exclusions, TRAINING_PARTITION)
    development_exclusions = validate_exclusions(development_exclusions, PARTITION)
    if any(date.fromisoformat(value) <= CALIBRATION_END for value in development_exclusions):
        raise ValueError("The fixed 30-day calibration prefix cannot contain exclusions")

    def complete_calendar(start: date, end: date) -> set[str]:
        return {(start + timedelta(days=index)).isoformat()
                for index in range((end - start).days + 1)}

    training_eligible = {row["climate_date"] for row in training_labels}
    eligible = set(contracts_by_day)
    if training_eligible & set(training_exclusions):
        raise ValueError("A weather-training date cannot be both eligible and excluded")
    if eligible & set(development_exclusions):
        raise ValueError("A development date cannot be both eligible and excluded")
    if training_eligible | set(training_exclusions) != complete_calendar(TRAINING_START, TRAINING_END):
        raise ValueError("Weather-training labels and exclusions do not cover every 2024 date")
    if eligible | set(development_exclusions) != complete_calendar(DEVELOPMENT_START, DEVELOPMENT_END):
        raise ValueError("Development labels and exclusions do not cover the registered interval")
    calibration_dates = sorted(
        value for value in eligible if date.fromisoformat(value) <= CALIBRATION_END
    )
    expected_calibration = [
        (DEVELOPMENT_START + timedelta(days=index)).isoformat()
        for index in range((CALIBRATION_END - DEVELOPMENT_START).days + 1)
    ]
    if calibration_dates != expected_calibration:
        raise ValueError("The calibration prefix must contain all 30 registered dates")

    for component in ("market", "observation", "forecast"):
        extra = ({row["climate_date"] for row in tables[component]} - eligible
                 - set(development_exclusions))
        if extra:
            raise ValueError(f"{component} rows contain dates without an eligible settlement target")
    for row in tables["market"]:
        if (row["climate_date"] in eligible
                and row.get("ticker") not in contracts_by_day[row["climate_date"]]):
            raise ValueError("Market row ticker is outside its reconciled event")
    for component in ("training_observation", "training_forecast"):
        extra = ({row["climate_date"] for row in tables[component]} - training_eligible
                 - set(training_exclusions))
        if extra:
            raise ValueError(f"{component} rows contain dates without an eligible training target")

    development_by_day = {
        component: {
            day: [row for row in rows if row["climate_date"] == day]
            for day in sorted(eligible)
        }
        for component, rows in tables.items() if component != "settlement"
        and component in DEVELOPMENT_COMPONENTS
    }
    training_by_day = {
        component: {
            day: [row for row in tables[component] if row["climate_date"] == day]
            for day in sorted(training_eligible)
        }
        for component in ("training_observation", "training_forecast")
    }
    training_features = []
    for day_text in sorted(training_eligible):
        day = date.fromisoformat(day_text)
        for decision_text in decisions:
            decision = _decision_at(day, decision_text)
            training_features.append({
                "schema_version": 1,
                "partition": TRAINING_PARTITION,
                "data_role": "weather_model_training",
                "climate_date": day_text,
                "decision_time_utc": decision_text,
                "decision_at": decision.isoformat(),
                "observations": _observation_snapshot(
                    training_by_day["training_observation"][day_text], decision, stations,
                    optional_stations,
                ),
                "forecasts": _forecast_snapshot(
                    training_by_day["training_forecast"][day_text], decision, models, minima,
                    required_fields, required_members,
                ),
                "as_of_join_validated": True,
                "contains_market_evidence": False,
                "contains_settlement_label": False,
            })
    settlement_by_day = {row["climate_date"]: row for row in tables["settlement"]}
    features = []
    for day_text in sorted(eligible):
        day = date.fromisoformat(day_text)
        settlement = settlement_by_day[day_text]
        for decision_text in decisions:
            decision = _decision_at(day, decision_text)
            observations = _observation_snapshot(
                development_by_day["observation"][day_text], decision, stations,
                optional_stations,
            )
            forecasts = _forecast_snapshot(
                development_by_day["forecast"][day_text], decision, models, minima,
                required_fields, required_members,
            )
            contracts = []
            feature_contracts = settlement["_feature_contracts"]
            for contract in feature_contracts:
                contracts.append({
                    **contract,
                    "market": _market_snapshot(
                        contract["ticker"], development_by_day["market"][day_text], decision,
                    ),
                })
            features.append({
                "schema_version": 1,
                "partition": PARTITION,
                "data_role": _development_role(day),
                "climate_date": day_text,
                "event_ticker": settlement["event_ticker"],
                "decision_time_utc": decision_text,
                "decision_at": decision.isoformat(),
                "contracts": contracts,
                "observations": observations,
                "forecasts": forecasts,
                "as_of_join_validated": True,
                "contains_settlement_label": False,
            })

    features = sorted(features, key=lambda row: (row["climate_date"], row["decision_at"]))
    labels = sorted(labels, key=lambda row: row["climate_date"])
    for row in labels:
        row["data_role"] = _development_role(date.fromisoformat(row["climate_date"])) + "_label"
    training_features = sorted(
        training_features, key=lambda row: (row["climate_date"], row["decision_at"]),
    )
    training_labels = sorted(training_labels, key=lambda row: row["climate_date"])
    calibration_features = [row for row in features if row["data_role"] == "development_calibration"]
    evaluation_features = [row for row in features if row["data_role"] == "development_evaluation"]
    calibration_labels = [row for row in labels if row["data_role"] == "development_calibration_label"]
    evaluation_labels = [row for row in labels if row["data_role"] == "development_evaluation_label"]
    artifact_rows = {
        "weather_training_features.jsonl": training_features,
        "weather_training_labels.jsonl": training_labels,
        "development_calibration_features.jsonl": calibration_features,
        "development_calibration_labels.jsonl": calibration_labels,
        "development_evaluation_features.jsonl": evaluation_features,
        "development_evaluation_labels.jsonl": evaluation_labels,
    }
    artifact_bytes = {name: _jsonl_bytes(rows) for name, rows in artifact_rows.items()}
    roles = {
        "weather_training_features.jsonl": "weather_model_and_regime_fitting_features",
        "weather_training_labels.jsonl": "weather_model_and_regime_fitting_labels",
        "development_calibration_features.jsonl": "market_residual_and_conformal_calibration_features",
        "development_calibration_labels.jsonl": "market_residual_and_conformal_calibration_labels",
        "development_evaluation_features.jsonl": "scored_as_of_evaluation_features",
        "development_evaluation_labels.jsonl": "scored_evaluation_labels",
    }
    file_records = [
        {"path": name, "bytes": len(artifact_bytes[name]),
         "sha256": _sha256_bytes(artifact_bytes[name]), "rows": len(artifact_rows[name]),
         "role": roles[name]}
        for name in artifact_rows
    ]
    input_bindings = [
        {
            "component": component,
            "manifest_sha256": canonical_hash(manifests[component]),
            "rows_sha256": manifests[component]["rows_sha256"],
            "row_count": manifests[component]["row_count"],
            "source_sha256s": manifests[component]["source_sha256s"],
            "upstream_manifests": manifests[component]["upstream_manifests"],
        }
        for component in COMPONENTS
    ]
    dataset_body = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "component": "frozen_dataset",
        "status": "DEVELOPMENT_FROZEN",
        "partitions": [TRAINING_PARTITION, PARTITION],
        "weather_training": {
            "date_start": training_labels[0]["climate_date"],
            "date_end": training_labels[-1]["climate_date"],
            "eligible_day_count": len(training_labels),
            "excluded_dates": training_exclusions,
            "feature_row_count": len(training_features),
            "label_row_count": len(training_labels),
        },
        "development_calibration": {
            "date_start": calibration_labels[0]["climate_date"],
            "date_end": calibration_labels[-1]["climate_date"],
            "eligible_day_count": len(calibration_labels),
            "feature_row_count": len(calibration_features),
            "label_row_count": len(calibration_labels),
            "scored": False,
        },
        "development_evaluation": {
            "date_start": evaluation_labels[0]["climate_date"],
            "date_end": evaluation_labels[-1]["climate_date"],
            "eligible_day_count": len(evaluation_labels),
            "excluded_dates": development_exclusions,
            "feature_row_count": len(evaluation_features),
            "label_row_count": len(evaluation_labels),
            "scored": True,
        },
        "decision_times_utc": list(decisions),
        "required_stations": list(stations),
        "optional_observation_stations_preserved_when_available": list(optional_stations),
        "required_forecast_models": list(models),
        "minimum_forecast_records_per_model": {key: minima[key] for key in sorted(minima)},
        "required_forecast_fields_by_model": {
            key: list(required_fields[key]) for key in sorted(required_fields)
        },
        "required_forecast_members_by_model": {
            key: list(required_members[key]) for key in sorted(required_members)
        },
        "network_used": False,
        "protected_final_read": False,
        "protected_final_included": False,
        "role_separation": "weather training, development calibration, and scored development evaluation each use physically separate feature and label artifacts",
        "as_of_rule": "initialized_at, observed_at, publication/availability, and completed market bar time must be at or before decision_at",
        "market_rule": "latest completed one-minute candle plus public-trade summary over (decision-60m, decision]",
        "forecast_cycle_rule": "latest as-of cycle meeting the registered per-model minimum record count",
        "input_bindings": input_bindings,
        "files": file_records,
    }
    dataset_id = canonical_hash(dataset_body)
    dataset_manifest = {**dataset_body, "dataset_id": dataset_id}
    folds = build_five_chronological_folds(
        [row["climate_date"] for row in evaluation_labels], dataset_id=dataset_id,
        calibration_dates=[row["climate_date"] for row in calibration_labels],
    )
    fold_bytes = _json_bytes(folds)
    dataset_manifest = {
        **dataset_manifest,
        "folds": {"path": "folds.json", "bytes": len(fold_bytes),
                  "sha256": _sha256_bytes(fold_bytes), "folds_id": folds["folds_id"]},
    }
    manifest_bytes = _json_bytes(dataset_manifest)
    for name, contents in artifact_bytes.items():
        _write_immutable(destination / name, contents)
    _write_immutable(destination / "folds.json", fold_bytes)
    _write_immutable(destination / "manifest.json", manifest_bytes)
    verify_frozen_development_dataset(destination)
    return deepcopy(dataset_manifest)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row at {path.name}:{line_number} is not an object")
        rows.append(value)
    return rows


def verify_frozen_development_dataset(destination: Path) -> dict[str, Any]:
    """Recompute content identities and core isolation/fold invariants."""
    destination = Path(destination).resolve()
    if _has_protected_path(str(destination)):
        raise ValueError("Development dataset destination cannot be a protected-final path")
    manifest_path = destination / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("Frozen development manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_body = {key: value for key, value in manifest.items()
                    if key not in {"dataset_id", "folds"}}
    if canonical_hash(dataset_body) != manifest.get("dataset_id"):
        raise ValueError("Frozen development dataset identity changed")
    if (manifest.get("schema_version") != DATASET_SCHEMA_VERSION
            or manifest.get("partitions") != [TRAINING_PARTITION, PARTITION]
            or manifest.get("protected_final_read") is not False
            or manifest.get("protected_final_included") is not False):
        raise ValueError("Frozen development scope or schema differs")
    for record in manifest.get("files", []):
        path = (destination / record["path"]).resolve()
        path.relative_to(destination)
        if (not path.is_file() or path.stat().st_size != record["bytes"]
                or sha256_file(path) != record["sha256"]):
            raise ValueError(f"Frozen dataset artifact changed: {record['path']}")
    folds_record = manifest.get("folds", {})
    folds_path = (destination / folds_record.get("path", "")).resolve()
    folds_path.relative_to(destination)
    if (not folds_path.is_file() or folds_path.stat().st_size != folds_record.get("bytes")
            or sha256_file(folds_path) != folds_record.get("sha256")):
        raise ValueError("Frozen fold artifact changed")
    names = (
        "weather_training_features.jsonl", "weather_training_labels.jsonl",
        "development_calibration_features.jsonl", "development_calibration_labels.jsonl",
        "development_evaluation_features.jsonl", "development_evaluation_labels.jsonl",
    )
    rows_by_name = {name: _read_jsonl(destination / name) for name in names}
    file_by_name = {record["path"]: record for record in manifest["files"]}
    if set(file_by_name) != set(names) or any(
            len(rows_by_name[name]) != file_by_name[name].get("rows") for name in names):
        raise ValueError("Frozen dataset row count changed")
    features = [row for name in names if "features" in name for row in rows_by_name[name]]
    if any(row.get("contains_settlement_label") is not False
           or "reported_high_f" in row or "yes_outcome" in _canonical_json(row)
           for row in features):
        raise ValueError("Settlement labels leaked into the discovery feature artifact")
    role_contract = {
        "weather_training_features.jsonl": (TRAINING_PARTITION, "weather_model_training"),
        "weather_training_labels.jsonl": (TRAINING_PARTITION, "weather_model_training_label"),
        "development_calibration_features.jsonl": (PARTITION, "development_calibration"),
        "development_calibration_labels.jsonl": (PARTITION, "development_calibration_label"),
        "development_evaluation_features.jsonl": (PARTITION, "development_evaluation"),
        "development_evaluation_labels.jsonl": (PARTITION, "development_evaluation_label"),
    }
    for name, (partition, role) in role_contract.items():
        for row in rows_by_name[name]:
            day = _validate_row_scope(row, partition=partition)
            if row.get("data_role") != role:
                raise ValueError("Frozen dataset role is mixed across physical artifacts")
            if "calibration" in role and not DEVELOPMENT_START <= day <= CALIBRATION_END:
                raise ValueError("Calibration artifact contains a scored evaluation date")
            if "evaluation" in role and not EVALUATION_START <= day <= DEVELOPMENT_END:
                raise ValueError("Evaluation artifact contains a calibration-prefix date")
    calibration_labels = rows_by_name["development_calibration_labels.jsonl"]
    evaluation_labels = rows_by_name["development_evaluation_labels.jsonl"]
    calibration_dates = [row["climate_date"] for row in calibration_labels]
    evaluation_dates = [row["climate_date"] for row in evaluation_labels]
    if set(calibration_dates) & set(evaluation_dates):
        raise ValueError("Calibration and evaluation label dates overlap")
    folds = json.loads(folds_path.read_text(encoding="utf-8"))
    fold_body = {key: value for key, value in folds.items() if key != "folds_id"}
    if (canonical_hash(fold_body) != folds.get("folds_id")
            or folds.get("dataset_id") != manifest["dataset_id"]
            or folds.get("folds_id") != folds_record.get("folds_id")):
        raise ValueError("Frozen fold identity changed or is not bound to the dataset")
    flattened = [day for fold in folds.get("folds", []) for day in fold.get("dates", [])]
    if (flattened != evaluation_dates or folds.get("calibration_dates") != calibration_dates
            or len(folds.get("folds", [])) != FOLD_COUNT):
        raise ValueError("Frozen folds do not exactly partition only the scored evaluation labels")
    sizes = [fold["day_count"] for fold in folds["folds"]]
    if max(sizes) - min(sizes) > 1:
        raise ValueError("Frozen fold sizes differ by more than one day")
    return {
        "status": "PASS",
        "dataset_id": manifest["dataset_id"],
        "folds_id": folds["folds_id"],
        "weather_training_feature_rows": len(rows_by_name["weather_training_features.jsonl"]),
        "weather_training_label_rows": len(rows_by_name["weather_training_labels.jsonl"]),
        "calibration_feature_rows": len(rows_by_name["development_calibration_features.jsonl"]),
        "calibration_label_rows": len(calibration_labels),
        "evaluation_feature_rows": len(rows_by_name["development_evaluation_features.jsonl"]),
        "evaluation_label_rows": len(evaluation_labels),
        "protected_final_read": False,
    }


def _read_parquet_rows(root: Path, paths: Sequence[Path], component: str) -> tuple[list[dict], list[dict]]:
    """Read explicit normalized artifacts while binding their exact bytes.

    This helper is intentionally private to the project-artifact adapter.  The
    core builder above remains independent of files and cannot fetch missing
    inputs.
    """
    import pyarrow.parquet as pq

    if not paths:
        raise ValueError(f"At least one normalized {component} artifact path is required")
    rows, artifacts = [], []
    for supplied in paths:
        path = Path(supplied)
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Normalized {component} artifact escapes the project root") from exc
        if _has_protected_path(relative.as_posix()):
            raise ValueError("Protected-final artifacts cannot enter the development adapter")
        if not path.is_file():
            raise ValueError(f"Missing normalized {component} artifact: {relative.as_posix()}")
        table = pq.read_table(path)
        if table.column_names == ["empty"]:
            values = []
        else:
            values = table.to_pylist()
        rows.extend(values)
        artifacts.append({
            "path": relative.as_posix(), "bytes": path.stat().st_size,
            "sha256": sha256_file(path), "rows": len(values),
        })
    return rows, artifacts


def _read_manifest_files(root: Path, paths: Sequence[Path], component: str) -> list[dict]:
    if not paths:
        raise ValueError(f"At least one {component} source manifest path is required")
    values = []
    for supplied in paths:
        path = Path(supplied)
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{component} source manifest escapes the project root") from exc
        if _has_protected_path(relative.as_posix()):
            raise ValueError("Protected-final manifests cannot enter the development adapter")
        if not path.is_file():
            raise ValueError(f"Missing {component} source manifest: {relative.as_posix()}")
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid {component} source manifest JSON") from exc
        if not isinstance(body, dict):
            raise ValueError(f"{component} source manifest must be a JSON object")
        if body.get("protected_final_read") is not False:
            raise ValueError(f"{component} source manifest does not prove development-only access")
        values.append({
            "path": relative.as_posix(), "bytes": path.stat().st_size,
            "sha256": sha256_file(path), "manifest": body,
        })
    return values


def freeze_project_development_artifacts(
    root: Path,
    *,
    training_settlement_path: Path,
    training_settlement_manifest_path: Path,
    training_forecast_paths: Sequence[Path],
    training_forecast_manifest_paths: Sequence[Path],
    forecast_paths: Sequence[Path],
    forecast_manifest_paths: Sequence[Path],
    decision_times_utc: Sequence[str],
    destination: Path | None = None,
    settlement_path: Path = Path(
        "data/normalized/v3_development/selection/labels/settlement_targets.parquet"
    ),
    market_paths: Sequence[Path] = (
        Path("data/normalized/selection/features/candles_1m.parquet"),
        Path("data/normalized/selection/features/trades.parquet"),
    ),
    observation_path: Path = Path(
        "data/normalized/v3_observations/selection/features/local_observations.parquet"
    ),
    training_observation_path: Path = Path(
        "data/normalized/v3_observations/weather_training/features/local_observations.parquet"
    ),
    settlement_manifest_path: Path = Path(
        "data/manifests/v3_settlement_reconciliation.json"
    ),
    market_manifest_path: Path = Path("data/manifests/v3_minute_kalshi.json"),
    observation_manifest_path: Path = Path(
        "data/manifests/v3_local_observations_partial.json"
    ),
    required_stations: Sequence[str] = ("KLAX",),
    required_forecast_models: Sequence[str] = ("hrrr", "gefs"),
    minimum_forecast_records_per_model: Mapping[str, int] | None = None,
    required_forecast_fields_by_model: Mapping[str, Sequence[str]] | None = None,
    required_forecast_members_by_model: Mapping[str, Sequence[str | None]] | None = None,
    optional_observation_stations: Sequence[str] = (),
    training_exclusions: Mapping[str, str] | None = None,
    development_exclusions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Adapt explicit project Parquet/manifests into the core V3 freeze.

    Forecast artifacts are deliberately mandatory and have no guessed default:
    the adapter fails until the decoder has produced explicit normalized files
    and source manifests.  It never invokes acquisition or normalization.
    """
    root = Path(root).resolve()
    output = (root / "data/frozen/v3_development" if destination is None
              else Path(destination).resolve() if Path(destination).is_absolute()
              else (root / destination).resolve())
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise ValueError("Frozen project dataset destination must remain inside the project root") from exc
    if _has_protected_path(output.relative_to(root).as_posix()):
        raise ValueError("Frozen project dataset cannot use a protected-final destination")

    path_sets = {
        "training_settlement": (training_settlement_path,),
        "training_observation": (training_observation_path,),
        "training_forecast": tuple(training_forecast_paths),
        "settlement": (settlement_path,),
        "market": tuple(market_paths),
        "observation": (observation_path,),
        "forecast": tuple(forecast_paths),
    }
    manifest_sets = {
        "training_settlement": (training_settlement_manifest_path,),
        "training_observation": (observation_manifest_path,),
        "training_forecast": tuple(training_forecast_manifest_paths),
        "settlement": (settlement_manifest_path,),
        "market": (market_manifest_path,),
        "observation": (observation_manifest_path,),
        "forecast": tuple(forecast_manifest_paths),
    }
    tables, bindings = {}, {}
    for component in COMPONENTS:
        rows, artifacts = _read_parquet_rows(root, path_sets[component], component)
        upstream = _read_manifest_files(root, manifest_sets[component], component)
        envelope = {"normalized_artifacts": artifacts, "source_manifests": upstream}
        tables[component] = rows
        bindings[component] = normalized_input_manifest(
            component, rows, upstream_manifests=(envelope,),
        )
    return build_frozen_development_dataset(
        output,
        training_settlement_rows=tables["training_settlement"],
        training_settlement_manifest=bindings["training_settlement"],
        training_observation_rows=tables["training_observation"],
        training_observation_manifest=bindings["training_observation"],
        training_forecast_rows=tables["training_forecast"],
        training_forecast_manifest=bindings["training_forecast"],
        settlement_rows=tables["settlement"], settlement_manifest=bindings["settlement"],
        market_rows=tables["market"], market_manifest=bindings["market"],
        observation_rows=tables["observation"], observation_manifest=bindings["observation"],
        forecast_rows=tables["forecast"], forecast_manifest=bindings["forecast"],
        decision_times_utc=decision_times_utc,
        required_stations=required_stations,
        required_forecast_models=required_forecast_models,
        minimum_forecast_records_per_model=minimum_forecast_records_per_model,
        required_forecast_fields_by_model=required_forecast_fields_by_model,
        required_forecast_members_by_model=required_forecast_members_by_model,
        optional_observation_stations=optional_observation_stations,
        training_exclusions=training_exclusions,
        development_exclusions=development_exclusions,
    )


def publish_frozen_dataset_component_manifests(root: Path, destination: Path) -> dict[str, dict]:
    """Publish readiness *components* only after a real freeze verifies.

    These artifacts make no campaign-readiness or performance claim.  The V3
    readiness validator remains responsible for checking them with every other
    registered component before issuing any ticket.
    """
    root = Path(root).resolve()
    destination = Path(destination)
    destination = (destination.resolve() if destination.is_absolute()
                   else (root / destination).resolve())
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("Frozen dataset component must remain inside the project root") from exc
    if tuple(part.casefold() for part in relative.parts[:2]) != ("data", "frozen"):
        raise ValueError("Frozen dataset component must be below data/frozen")
    verified = verify_frozen_development_dataset(destination)
    manifest_path = destination / "manifest.json"
    folds_path = destination / "folds.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    folds = json.loads(folds_path.read_text(encoding="utf-8"))
    frozen_component = {
        "schema_version": 1,
        "component": "frozen_dataset",
        "status": "TRAINING_CALIBRATION_EVALUATION_FROZEN",
        "dataset_id": verified["dataset_id"],
        "dataset_manifest_path": manifest_path.relative_to(root).as_posix(),
        "dataset_manifest_sha256": sha256_file(manifest_path),
        "partitions": manifest["partitions"],
        "weather_training": manifest["weather_training"],
        "development_calibration": manifest["development_calibration"],
        "development_evaluation": manifest["development_evaluation"],
        "files": [
            {**record, "path": (relative / record["path"]).as_posix()}
            for record in manifest["files"]
        ],
        "network_used": False,
        "protected_final_read": False,
        "ready_for_v3_campaign": False,
    }
    fold_component = {
        "schema_version": 1,
        "component": "five_fold_split",
        "status": "EVALUATION_FOLDS_FROZEN",
        "dataset_id": verified["dataset_id"],
        "folds_id": verified["folds_id"],
        "fold_path": folds_path.relative_to(root).as_posix(),
        "fold_sha256": sha256_file(folds_path),
        "calibration_dates": folds["calibration_dates"],
        "calibration_dates_sha256": folds["calibration_dates_sha256"],
        "evaluation_dates": folds["evaluation_dates"],
        "evaluation_dates_sha256": folds["evaluation_dates_sha256"],
        "folds": folds["folds"],
        "training_rule": folds["training_rule"],
        "network_used": False,
        "protected_final_read": False,
        "ready_for_v3_campaign": False,
    }
    manifest_dir = root / "data/manifests"
    write_json(manifest_dir / "v3_dataset.json", frozen_component)
    write_json(manifest_dir / "v3_five_fold_split.json", fold_component)
    return {"frozen_dataset": frozen_component, "five_fold_split": fold_component}
