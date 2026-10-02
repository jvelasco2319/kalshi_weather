"""Read-only adapter for the unchanged, frozen V10 probability chain.

Loading verifies old artifacts and prediction code without fitting or reading
outcomes. Prediction consumes one canonical six-contract event and the original
06Z HRRR / 00Z GEFS sampling surface. Collection, actual receipt timestamps,
market rule verification, persistence and scheduling belong to the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from klax_lab.candidate_model_v3 import fitted_candidate_model_from_state
from klax_lab.domain import ContractBounds


V10_PATH = "runs/v10/frozen-development-candidate/candidate-freeze.json"
V8_PATH = "runs/v8/frozen-primary-strategy/strategy-freeze.json"
BASE_PATH = (
    "runs/campaigns_v4/v4-offline-20260926T212122609Z/candidates/primary/"
    "a779fd17e7b160650c8f65afceffb73434a9dacc56afad19672e596de5a97461/compiled_manifest.json"
)
POLICY_PATH = "configs/v7y_hrrr_gefs_availability_policy.json"
V10_SEAL = "ec762e9524664b6b9892b4586b417ce7de357ef7e10576cac0b2a17c69824f79"
V8_SEAL = "a227b8f72ad040c2411fe63d3863d1b9c9c11121c6df5fce2fadb198ea2379fd"
BASE_IDENTITY = "9bf8b1ba469b1c7258108f5dd11c67c2822e7487edff5c819688d37319574f93"
PINNED_FILES = {
    V10_PATH: "c0a48855fee8a5a67a06c6fb4b7423eda369c6d08879e86f9a0d9128cbed817a",
    V8_PATH: "b9320be7018c74b8f36904ad6021e7b46b065bbc3c7baad92e0315387a862317",
    BASE_PATH: "e1bcdfff09602cf63b0c598c8c320ceb931ce49afefc119f58a0da3d2f11cd0f",
    POLICY_PATH: "f4955ca34495584dccb24ab3a47e2c720d935629f342cd80b8b6489639133e93",
    "src/klax_lab/candidate_model_v3.py": "ac5b3ed84b429260da82158ea7f21712505067cbc52a9d5fd7583367d05663fc",
    "src/klax_lab/features_v3.py": "ca0ac79202b6c00c3ae9ad3e8e7b6a54b42fb24b4128f9c6fb2207b13d5aafa7",
    "src/klax_lab/probability_v3.py": "2811ef2b7ac082f068d06d90f72e9bc6e3fc1f586c3acc26b8e15204bf08d059",
    "src/klax_lab/regimes_v3.py": "a5cb14e78a4bd44ca29a99877eef94ea033d70ea50f2cfd0081617093c99930c",
    "src/klax_lab/domain.py": "7e6e3ff53ad55f88e5b1cf13c3d53445237a4c460b905f56582f5a23988adba0",
    "src/klax_lab/forecast_models_v3.py": "3e3abccd9647ade5df9918a9c46e694ef4beea048a15d3f04c9be33c06ddd081",
    "src/klax_lab/ordinal_v3.py": "86d039ab3a77c442622213c02fcc0baa749d17c102d16459e7a886eeaa34115d",
    "src/klax_lab/provenance.py": "0735f35c5335f75f6ba881a62a3c1f8380e47b2e917d17430899d9cd738a92ba",
}
PRESSURE_STATES = ("offshore", "neutral", "onshore")
HRRR_FIELDS = {
    "temperature_2m", "total_cloud_cover", "cloud_ceiling", "wind_u_10m",
    "wind_v_10m", "mean_sea_level_pressure",
}
LABEL_FIELDS = {
    "reported_high_f", "yes_outcome", "settlement_label", "outcome",
    "outcome_position", "winning_market_ticker", "realized_high_f",
}


class FrozenModelError(ValueError):
    """An input cannot be used by the unchanged frozen model."""


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Mapping[str, Any], field: str = "self_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    def invalid(value: str) -> None:
        raise FrozenModelError("nonfinite JSON: " + value)
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid)
    if not isinstance(value, dict):
        raise FrozenModelError("frozen JSON object required")
    return value


def _utc(value: Any, field: str) -> datetime:
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise FrozenModelError(field + " needs a UTC timestamp") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise FrozenModelError(field + " needs a timezone")
    return moment.astimezone(timezone.utc)


def _day(value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise FrozenModelError("canonical climate date required") from exc
    if day.isoformat() != value:
        raise FrozenModelError("canonical climate date required")
    return day


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise FrozenModelError(field + " must be finite numeric")
    return float(value)


def _no_labels(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in LABEL_FIELDS:
                raise FrozenModelError("outcomes cannot enter frozen prediction")
            _no_labels(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _no_labels(child)


def probability_vector(values: Sequence[float], *, strictly_positive: bool = False) -> tuple[float, ...]:
    if len(values) != 6:
        raise FrozenModelError("six probabilities required")
    result = tuple(_number(value, "probability") for value in values)
    if any(value < 0 or value > 1 or (strictly_positive and value == 0) for value in result):
        raise FrozenModelError("invalid probability range")
    if not isclose(sum(result), 1.0, rel_tol=0.0, abs_tol=1e-10):
        raise FrozenModelError("six-bracket probability mass is not conserved")
    return result


def canonical_contracts(day: str, contracts: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, ...], tuple[ContractBounds, ...]]:
    """Require the original six contiguous, ordered integer-weather brackets."""
    _no_labels(contracts)
    target = _day(day)
    if len(contracts) != 6:
        raise FrozenModelError("six canonical weather contracts required")
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    event = f"KXHIGHLAX-{target.year % 100:02d}{months[target.month - 1]}{target.day:02d}"
    tickers, bounds = [], []
    for index, row in enumerate(contracts):
        ticker = row.get("market_ticker")
        if not isinstance(ticker, str) or not ticker.startswith(event + "-"):
            raise FrozenModelError("contract ticker differs from the KLAX target event")
        if row.get("event_ticker", event) != event or row.get("climate_date", day) != day:
            raise FrozenModelError("contract date/event differs")
        if row.get("contract_order", index) != index:
            raise FrozenModelError("contract ordering changed")
        strike = row.get("strike_type")
        lower, upper = row.get("floor_strike"), row.get("cap_strike")
        if index == 0 and strike == "less" and lower is None:
            bound = ContractBounds(None, _number(upper, "cap_strike"), upper_inclusive=False)
        elif index == 5 and strike == "greater" and upper is None:
            bound = ContractBounds(_number(lower, "floor_strike"), None, lower_inclusive=False)
        elif 1 <= index <= 4 and strike == "between":
            bound = ContractBounds(_number(lower, "floor_strike"), _number(upper, "cap_strike"))
        else:
            raise FrozenModelError("canonical tail/interior strike types required")
        lo, hi = bound.integer_bounds()
        if 1 <= index <= 4 and (lo is None or hi is None or hi - lo != 1):
            raise FrozenModelError("interior bracket width changed from two integer temperatures")
        if index and bounds[-1].integer_bounds()[1] + 1 != lo:
            raise FrozenModelError("brackets overlap, have gaps or are not ordered")
        tickers.append(ticker)
        bounds.append(bound)
    if len(set(tickers)) != 6:
        raise FrozenModelError("duplicate market ticker")
    return tuple(tickers), tuple(bounds)


def validated_forecasts(day: str, forecasts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate the unchanged temperature sampling and conservative six-hour lag.

    Other original HRRR fields may accompany temperature rows. They do not
    affect this temperature-only base, but still must be label-free and as-of.
    """
    _no_labels(forecasts)
    midnight = datetime.combine(_day(day), datetime.min.time(), timezone.utc)
    decision = midnight + timedelta(hours=18)
    expected = {("hrrr", None, lead) for lead in (2, 8, 14, 20)}
    expected |= {("gefs", member, lead) for member in ("avg", "spr") for lead in range(9, 31, 3)}
    actual = set()
    output = []
    identities = set()
    for original in forecasts:
        row = dict(original)
        model, field = row.get("model"), row.get("field_id")
        if model not in {"hrrr", "gefs"} or (model == "hrrr" and field not in HRRR_FIELDS) or (model == "gefs" and field != "temperature_2m"):
            raise FrozenModelError("unexpected forecast model or field")
        if row.get("climate_date", day) != day or row.get("point_id", "KLAX") != "KLAX":
            raise FrozenModelError("forecast target date/station differs")
        if row.get("as_of_validated") is not True or row.get("contains_settlement_label", False) is not False:
            raise FrozenModelError("forecast needs validated label-free provenance")
        nominal = _utc(row.get("nominal_issue_time_utc"), "nominal_issue_time_utc")
        required_nominal = midnight + timedelta(hours=6 if model == "hrrr" else 0)
        if nominal != required_nominal:
            raise FrozenModelError("forecast cycle changed from frozen 06Z HRRR / 00Z GEFS")
        lead = row.get("lead_hours")
        if isinstance(lead, bool) or not isinstance(lead, int):
            raise FrozenModelError("integer forecast lead required")
        valid = _utc(row.get("valid_time_utc"), "valid_time_utc")
        if valid != nominal + timedelta(hours=lead) or not midnight + timedelta(hours=8) <= valid < midnight + timedelta(hours=32):
            raise FrozenModelError("forecast valid time does not match the fixed PST climate day")
        available = _utc(row.get("information_available_at_utc"), "information_available_at_utc")
        effective = max(nominal + timedelta(hours=6), available)
        explicit = row.get("effective_information_available_at_utc")
        if explicit is not None:
            declared = _utc(explicit, "effective_information_available_at_utc")
            if declared < effective:
                raise FrozenModelError("forecast availability understates the frozen bound")
            effective = declared
        if available < nominal or effective > decision:
            raise FrozenModelError("forecast was unavailable at the decision cutoff")
        member = row.get("member_id")
        identity = (model, field, member, lead)
        if identity in identities:
            raise FrozenModelError("duplicate forecast row")
        identities.add(identity)
        if field == "temperature_2m":
            key = (model, member, lead)
            if key not in expected:
                raise FrozenModelError("temperature sampling surface changed")
            value = _number(row.get("value"), "forecast temperature")
            if row.get("is_missing") is not False:
                raise FrozenModelError("missing forecast temperature")
            required_units = "delta_degF" if member == "spr" else "degF"
            if row.get("units") != required_units:
                raise FrozenModelError("forecast units changed")
            if member == "spr" and value < 0:
                raise FrozenModelError("negative GEFS spread")
            actual.add(key)
        output.append(row)
    if actual != expected:
        raise FrozenModelError("incomplete frozen HRRR/GEFS temperature surface")
    return output


def pressure_state(day: str, observations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact frozen latest-KLAX / last-valid-KDAG pressure selection.

    A latest KLAX report with missing pressure deliberately remains missing;
    using an older KLAX pressure would silently change V10's behavior.
    """
    _no_labels(observations)
    midnight = datetime.combine(_day(day), datetime.min.time(), timezone.utc)
    start, decision = midnight + timedelta(hours=12), midnight + timedelta(hours=18)
    admitted = {station: [] for station in ("KLAX", "KDAG")}
    identities = set()
    for row in observations:
        station = row.get("station")
        if station not in admitted:
            raise FrozenModelError("unexpected pressure station")
        observed = _utc(row.get("observed_at"), "observed_at")
        available = _utc(row.get("available_at"), "available_at")
        if available < observed + timedelta(minutes=15):
            raise FrozenModelError("pressure availability understates the frozen 15-minute lag")
        identity = (station, observed)
        if identity in identities:
            raise FrozenModelError("duplicate pressure report")
        identities.add(identity)
        value = row.get("pressure_hpa")
        pressure = None if value is None else _number(value, "pressure_hpa")
        if start <= observed and available <= decision:
            admitted[station].append((observed, available, pressure))
    for values in admitted.values():
        values.sort(key=lambda item: item[0])
    coast = admitted["KLAX"][-1] if admitted["KLAX"] else None
    inland_valid = [row for row in admitted["KDAG"] if row[2] is not None]
    inland = inland_valid[-1] if inland_valid else None
    gradient = None if coast is None or inland is None or coast[2] is None else coast[2] - inland[2]
    state = "offshore" if gradient is not None and gradient <= -2 else "onshore" if gradient is not None and gradient >= 2 else "neutral"
    return {
        "pressure_and_flow": state,
        "observed_pressure_gradient_hpa": gradient,
        "klax_asof_report_count": len(admitted["KLAX"]),
        "kdag_asof_report_count": len(admitted["KDAG"]),
        "klax_selected_observed_at": None if coast is None else coast[0].isoformat(),
        "kdag_selected_observed_at": None if inland is None else inland[0].isoformat(),
        "pressure_missing": gradient is None,
        "missing_pressure_fallback": "FROZEN_NEUTRAL_POSTERIOR" if gradient is None else None,
    }


@dataclass(frozen=True)
class FrozenV10:
    base_model: Any
    confusion: tuple[tuple[float, ...], ...]
    pressure_tables: Mapping[tuple[int, str], tuple[float, ...]]
    bindings: Mapping[str, str]

    @classmethod
    def load(cls, project_root: str | Path) -> "FrozenV10":
        """Verify immutable fitted artifacts; never call a fit routine."""
        root = Path(project_root).resolve()
        bindings = dict(PINNED_FILES)
        for relative, expected in bindings.items():
            if _digest(root / relative) != expected:
                raise FrozenModelError("frozen model binding changed: " + relative)
        v10, v8, source, policy = (_json(root / path) for path in (V10_PATH, V8_PATH, BASE_PATH, POLICY_PATH))
        if _canonical(v10) != V10_SEAL or v10.get("self_sha256") != V10_SEAL or _canonical(v8) != V8_SEAL or v8.get("self_sha256") != V8_SEAL:
            raise FrozenModelError("frozen V10/V8 artifact seal changed")
        for artifact, key in ((v10, "input_bindings"), (v8, "source_bindings")):
            for relative, expected in artifact[key].items():
                path = (root / relative).resolve()
                if not path.is_relative_to(root) or _digest(path) != expected:
                    raise FrozenModelError("frozen evidence binding changed: " + relative)
                bindings[relative] = expected
        if v10.get("candidate_id") != "V10-pressure_and_flow-W75" or v10.get("future_outcome_updates_allowed") is not False or v10.get("hierarchical_prior_strength") != 12 or v10.get("observed_report_availability_delay_minutes") != 15:
            raise FrozenModelError("V10 fitted candidate invariant changed")
        probability_model = v8["probability_model"]
        if probability_model["base_probability_weight"] != .25 or probability_model["conditional_confusion_weight"] != .75 or probability_model["future_outcome_updates_allowed"] is not False or policy.get("availability_delay_hours") != 6 or policy.get("policy_sha256") != "6d052ac852014edfe6f957441d2cfa5e80ad803eb5779bac9b5604c953bb6f86":
            raise FrozenModelError("frozen V8 or availability policy changed")
        base_model = fitted_candidate_model_from_state(source["fitted_model_state"])
        if base_model.identity != BASE_IDENTITY or source.get("fitted_model_sha256") != BASE_IDENTITY or base_model.encoder.feature_set != "temperature_only" or base_model.encoder.forecast_source_set != "hrrr_gefs_summary":
            raise FrozenModelError("underlying frozen probability model changed")
        confusion = tuple(probability_vector(row, strictly_positive=True) for row in probability_model["conditional_distributions_by_base_modal_position"])
        if len(confusion) != 6 or tuple(tuple(row) for row in v10["fitted_parameters"]["pooled_posteriors"]) != confusion:
            raise FrozenModelError("frozen pooled V8/V10 tables differ")
        pressure_tables = {
            (mode, state): probability_vector(v10["fitted_parameters"]["pressure_conditioned_tables"][str(mode)][state]["hierarchical_posterior"], strictly_positive=True)
            for mode in range(6) for state in PRESSURE_STATES
        }
        bindings["v10_online/model.py"] = _digest(Path(__file__).resolve())
        return cls(base_model, confusion, MappingProxyType(pressure_tables), MappingProxyType(dict(sorted(bindings.items()))))

    def from_base(self, base: Sequence[float], tickers: Sequence[str], state: str) -> dict[str, Any]:
        """Apply static overlays; supports independent replay-equivalence checks."""
        raw = probability_vector(base)
        if len(tickers) != 6 or len(set(tickers)) != 6 or any(not isinstance(ticker, str) for ticker in tickers) or state not in PRESSURE_STATES:
            raise FrozenModelError("invalid ticker identity or pressure state")
        v8_mode = max(range(6), key=lambda index: (raw[index], index))
        v8_values = [.25 * p + .75 * q for p, q in zip(raw, self.confusion[v8_mode], strict=True)]
        v8_total = sum(v8_values)
        repaired = probability_vector([p / v8_total for p in v8_values], strictly_positive=True)
        v10_mode = max(range(6), key=lambda index: (raw[index], tickers[index]))
        posterior = self.pressure_tables[v10_mode, state]
        values = [.25 * p + .75 * q for p, q in zip(repaired, posterior, strict=True)]
        total = sum(values)
        final = probability_vector([p / total for p in values], strictly_positive=True)
        return {
            "base_probabilities": list(raw), "v8_probabilities": list(repaired),
            "probabilities": list(final), "base_modal_position_for_v8": v8_mode,
            "base_modal_position_for_v10": v10_mode,
        }

    def predict(self, day: str, contracts: Sequence[Mapping[str, Any]], forecasts: Sequence[Mapping[str, Any]], pressure_observations: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        """Compute one label-free fixed V10 vector without IO or model updates."""
        tickers, bounds = canonical_contracts(day, contracts)
        rows = validated_forecasts(day, forecasts)
        pressure = pressure_state(day, pressure_observations)
        prediction = self.base_model.predict({
            "climate_date": day, "forecasts": rows, "observations": [],
            "contains_settlement_label": False, "as_of_join_validated": True,
        }, bounds)
        result = self.from_base(prediction.probabilities, tickers, pressure["pressure_and_flow"])
        result.update({
            "schema_version": "klax-online-frozen-v10-prediction-v1",
            "strategy_id": "v10-klax-observed-pressure-flow-w75-v1",
            "climate_date": day, "decision_at_utc": day + "T18:00:00+00:00",
            "tickers": list(tickers), "pressure_state": pressure["pressure_and_flow"],
            "pressure_evidence": pressure, "fitted_model_sha256": BASE_IDENTITY,
            "v8_strategy_seal": V8_SEAL, "v10_candidate_seal": V10_SEAL,
            "model_bindings": dict(self.bindings), "target_updates_used": False,
            "outcomes_read": False, "live_orders_placed": 0, "paper_orders_placed": 0,
            "prediction_rows": [
                {"market_ticker": ticker, "contract_order": index,
                 "yes_probability": result["probabilities"][index],
                 "no_probability": 1.0 - result["probabilities"][index]}
                for index, ticker in enumerate(tickers)
            ],
        })
        return result
