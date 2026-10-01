"""Independent verification of saved development decisions and settlement ledgers.

No imports from baseline, domain, evaluation, models or acquisition modules are
used. Probabilities use NormalDist.cdf and money is recomputed with Decimal.
This is saved-model/ledger verification, not an independent model refit, a raw
GRIB extraction audit, proof of executable fills, or Goal 2 completion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, localcontext
import hashlib
from itertools import product
import json
from math import exp, fsum, isfinite, pi, sqrt
from pathlib import Path
from statistics import NormalDist
from typing import Any


UTC = timezone.utc
PROBABILITY_TOLERANCE = 1e-12
RATIO_TOLERANCE = Decimal("1e-24")
SCOPE = "Independent saved-model forecasts, full scoring probabilities/daywise scores, base candidate selection, timing and ledger accounting; no forecast refit"
TABLE_PATHS = {
    "training_forecasts": "data/normalized/weather_training/features/forecasts.parquet",
    "training_climate": "data/normalized/weather_training/labels/climate.parquet",
    "forecasts": "data/normalized/selection/features/forecasts.parquet",
    "contracts": "data/normalized/selection/features/contracts.parquet",
    "candles": "data/normalized/selection/features/candles.parquet",
    "climate": "data/normalized/selection/labels/climate.parquet",
    "climate_versions": "data/normalized/selection/labels/climate_versions.parquet",
    "outcomes": "data/normalized/selection/labels/outcomes.parquet",
    "reconciliation": "data/normalized/selection/labels/reconciliation.parquet",
}


class VerificationError(ValueError):
    pass


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise VerificationError("Exact monetary values require decimal strings or integers")
    try:
        result = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise VerificationError("Invalid decimal value") from exc
    if not result.is_finite():
        raise VerificationError("Nonfinite decimal value")
    return result


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise VerificationError("Timestamp must be text")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise VerificationError("Timestamp must be timezone aware")
    return result.astimezone(UTC)


def _day(value: Any) -> date:
    if not isinstance(value, str):
        raise VerificationError("Climate date must be text")
    result = date.fromisoformat(value)
    if result.isoformat() != value:
        raise VerificationError("Climate date must be canonical")
    return result


def _quantity(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise VerificationError("Quantity must be a positive integer")
    return value


class _Checks:
    def __init__(self) -> None:
        self.count = 0
        self.failed: list[dict[str, str]] = []

    def check(self, condition: bool, code: str, context: str, detail: str) -> None:
        self.count += 1
        if not condition:
            self.failed.append({"check": code, "context": context, "detail": detail})

    def invalid(self, code: str, context: str, exc: Exception) -> None:
        self.check(False, code, context, f"{type(exc).__name__}: {exc}")

    def decimal(self, actual: Any, expected: Decimal, code: str, context: str, *, ratio: bool = False) -> None:
        try:
            difference = abs(_decimal(actual) - expected)
            self.check(difference <= (RATIO_TOLERANCE if ratio else Decimal(0)), code, context,
                       "Saved monetary value differs from independent arithmetic")
        except (ValueError, ArithmeticError, TypeError) as exc:
            self.invalid(code, context, exc)

    def report(self) -> dict[str, Any]:
        return {"status": "PASS" if not self.failed else "FAIL", "checks": self.count,
                "failed_checks": self.failed, "replication_scope": SCOPE,
                "independent_refit_performed": False, "protected_final_evaluated": False,
                "goal1_complete": False, "goal2_complete": False}


def _unique(rows: list[dict], key: str, checks: _Checks, table: str) -> dict[str, dict]:
    keys = [row[key] for row in rows]
    checks.check(len(keys) == len(set(keys)), "unique_rows", table, f"Duplicate {key} values")
    return {row[key]: row for row in rows}


def independent_prediction(model: dict, row: dict) -> tuple[float, float]:
    """Evaluate saved parameters only; deliberately do not call/refit models.py."""
    name = model["name"]
    if name == "seasonal_climatology":
        target = _day(row["climate_date"])
        position = date(2000, target.month, target.day).timetuple().tm_yday
        labels = [float(item["tmax_f"]) for item in model["seasonal_labels"]
                  if min(abs(item["position"] - position), 366 - abs(item["position"] - position)) <= 45]
        if len(labels) < 30 or any(not isfinite(value) for value in labels):
            raise VerificationError("Insufficient or nonfinite saved seasonal labels")
        mean = sum(labels) / len(labels)
        sd = max(1.0, sqrt(sum((value - mean) ** 2 for value in labels) / len(labels)))
    else:
        if name == "gfs_bias_corrected":
            feature = float(row["gfs"])
        elif name == "nbm_bias_corrected":
            feature = float(row["nbm"])
        elif name in {"equal_blend_bias_corrected", "collector_style_fixed_2f"}:
            feature = (float(row["gfs"]) + float(row["nbm"])) / 2
        else:
            raise VerificationError("Unknown saved baseline type")
        if name == "collector_style_fixed_2f" and (float(model["bias"]) != 0 or float(model["sd"]) != 2):
            raise VerificationError("Fixed-2F baseline changed its declared parameters")
        mean, sd = feature + float(model["bias"]), float(model["sd"])
    if not isfinite(mean) or not isfinite(sd) or sd <= 0:
        raise VerificationError("Invalid saved predictive distribution")
    return mean, sd


def independent_probability(mean: float, sd: float, lower: int | None, upper: int | None) -> float:
    """Integrate rounded integer outcomes with independent NormalDist.cdf."""
    for endpoint in (lower, upper):
        if endpoint is not None and type(endpoint) is not int:
            raise VerificationError("Normalized contract bounds must be integers or open tails")
    distribution = NormalDist(mean, sd)
    if lower is not None and upper is not None and lower > upper:
        return 0.0
    lower_mass = 0.0 if lower is None else distribution.cdf(lower - 0.5)
    upper_mass = 1.0 if upper is None else distribution.cdf(upper + 0.5)
    return max(0.0, min(1.0, upper_mass - lower_mass))


def _fee(price: Decimal, quantity: int, rate: Decimal, quantum: Decimal) -> Decimal:
    if not Decimal(0) < price < Decimal(1) or not Decimal(0) <= rate <= Decimal(1) or quantum <= 0:
        raise VerificationError("Invalid price or fee parameters")
    raw = rate * _quantity(quantity) * price * (Decimal(1) - price)
    return (raw / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum


def _eligible_days(tables: dict[str, list[dict]], checks: _Checks) -> set[str]:
    labels = _unique(tables["climate"], "climate_date", checks, "selection climate labels")
    forecasts = _unique(tables["forecasts"], "climate_date", checks, "selection forecasts")
    good_tickers = {row["ticker"] for row in tables["reconciliation"] if row.get("settlement_label_reconciled") is True}
    bad = {row["climate_date"] for row in tables["outcomes"] if row.get("mapping_consistent") is not True}
    bad |= {row["climate_date"] for row in tables["reconciliation"] if row.get("settlement_label_reconciled") is not True}
    bad |= {row["climate_date"] for row in tables["contracts"] if row["ticker"] not in good_tickers}
    return (set(labels) & set(forecasts)) - bad


def _audit_settlement_mapping(tables: dict[str, list[dict]], checks: _Checks) -> None:
    """Recheck explicit event temperatures or pre-settlement NWS fallback labels.

    Payout authority remains the untouched normalized actual binary result. NWS
    temperatures validate a contract's mapping; they never replace its payout.
    """
    contracts = {row["ticker"]: row for row in tables["contracts"]}
    reconciled = _unique(tables["reconciliation"], "ticker", checks, "settlement reconciliation")
    versions: dict[str, list[dict]] = defaultdict(list)
    for row in tables["climate_versions"]:
        issued, available = _time(row["issued_at"]), _time(row["available_at"])
        checks.check(issued <= available, "climate_revision_availability", row["climate_date"], "Climate product available before its issue time")
        versions[row["climate_date"]].append(row)
    event_values: dict[str, set[float]] = defaultdict(set)
    for row in tables["outcomes"]:
        if row.get("expiration_value_f") is not None:
            event_values[row["event_ticker"]].add(float(row["expiration_value_f"]))
    for row in tables["outcomes"]:
        ticker, day = row["ticker"], row["climate_date"]
        reconciliation = reconciled.get(ticker, {})
        if reconciliation.get("settlement_label_reconciled") is not True:
            continue
        binary = row["yes_outcome"]
        if type(binary) is not int or binary not in (0, 1):
            raise VerificationError("Reconciled market has a non-binary payout")
        values = event_values[row["event_ticker"]]
        checks.check(len(values) <= 1, "conflicting_event_temperatures", ticker,
                     "Conflicting explicit API temperatures cannot be treated as missing and rescued by NWS")
        if len(values) == 1:
            unique = next(iter(values))
            checks.check(row.get("settlement_temperature_f") == unique, "explicit_temperature_lineage", ticker,
                         "Event temperature is not the unique explicit own/sibling API value")
        else:
            checks.check(row.get("settlement_temperature_f") is None, "missing_temperature_not_invented", ticker,
                         "An event without explicit temperature must retain a missing API temperature")
        settled = _time(row["settlement_time"])
        available = [version for version in versions[day] if _time(version["available_at"]) <= settled]
        checks.check(bool(available), "climate_asof_settlement", ticker, "No NWS version was available by actual settlement")
        if not available:
            continue
        chosen = max(available, key=lambda version: _time(version["available_at"]))
        tied = [version for version in available if _time(version["available_at"]) == _time(chosen["available_at"])]
        checks.check(len({version["tmax_f"] for version in tied}) == 1, "unambiguous_climate_revision", ticker,
                     "Same-time NWS versions disagree on the daily high")
        temperature = chosen["tmax_f"]
        if type(temperature) is not int:
            raise VerificationError("NWS settlement-reference temperature must be an integer")
        contract = contracts[ticker]
        lower, upper = contract["lower_integer_f"], contract["upper_integer_f"]
        contains = (lower is None or temperature >= lower) and (upper is None or temperature <= upper)
        checks.check(contains == bool(binary), "NWS_binary_mapping", ticker, "NWS label and bounds contradict actual binary payout")
        checks.check(reconciliation.get("climate_source_sha256") == chosen["source_sha256"], "NWS_revision_source", ticker,
                     "Cited reconciliation report is not the latest available version at settlement")
        if len(values) == 1:
            checks.check(temperature == next(iter(values)), "NWS_explicit_temperature", ticker, "NWS label differs from explicit event temperature")
        else:
            checks.check(row.get("mapping_validation_basis") == "NWS_report_as_of_settlement_against_actual_binary_result",
                         "NWS_fallback_basis", ticker, "Missing API temperature requires an explicit NWS verification basis")
            checks.check(row.get("mapping_climate_source_sha256") == chosen["source_sha256"], "NWS_fallback_source", ticker,
                         "Missing-temperature fallback lacks matching NWS source lineage")


def _audit_timing(model: dict, tables: dict[str, list[dict]], policy: dict, checks: _Checks) -> None:
    fit = _time(policy["model_fit_cutoff_utc"])
    training_labels = _unique(tables["training_climate"], "climate_date", checks, "training climate labels")
    training_rows = _unique(tables["training_forecasts"], "climate_date", checks, "training forecasts")
    fitted_days = []
    for day, row in training_rows.items():
        target = _day(day)
        checks.check(policy["weather_training"][0] <= day <= policy["weather_training"][1],
                     "training_partition", day, "Training forecast falls outside the registered partition")
        complete = datetime(target.year, target.month, target.day, 8, tzinfo=UTC) + timedelta(days=1)
        checks.check(complete <= fit, "training_day_complete", day, "Training climate day was incomplete at fit cutoff")
        checks.check(_time(row["available_at"]) <= fit, "training_forecast_asof", day, "Training forecast not available at fit cutoff")
        label = training_labels.get(day)
        if label is not None:
            timely = _time(label["available_at"]) <= fit
            checks.check(timely, "training_label_asof", day, "Saved training label is a future revision")
            if timely:
                fitted_days.append(day)
    checks.check(model.get("training_days") == len(fitted_days), "saved_training_count", model["name"],
                 "Saved model training count differs from available forecast/label intersection")
    if model["name"] == "seasonal_climatology":
        expected = Counter((date(2000, _day(day).month, _day(day).day).timetuple().tm_yday, float(training_labels[day]["tmax_f"])) for day in fitted_days)
        actual = Counter((int(row["position"]), float(row["tmax_f"])) for row in model["seasonal_labels"])
        checks.check(actual == expected, "seasonal_label_provenance", model["name"], "Saved seasonal labels differ from pre-fit training labels")
    for row in tables["forecasts"]:
        day = row["climate_date"]
        target = _day(day)
        cutoff = datetime(target.year, target.month, target.day, policy["decision_hour_utc"], tzinfo=UTC)
        expected_release = datetime(target.year, target.month, target.day, policy["forecast_initialization_hour_utc"], tzinfo=UTC) + timedelta(hours=policy["availability_delay_hours"])
        available = _time(row["available_at"])
        checks.check(policy["selection"][0] <= day <= policy["selection"][1], "selection_partition", day, "Forecast is outside development selection partition")
        checks.check(fit < cutoff and expected_release <= available <= cutoff, "forecast_asof", day,
                     "Forecast release or model fitting violates the information cutoff")


def _base_candidates(model: dict, tables: dict[str, list[dict]], policy: dict, version: str,
                     dataset_version: str, saved_decisions: dict[str, dict], checks: _Checks) -> dict[str, dict]:
    forecasts = {row["climate_date"]: row for row in tables["forecasts"]}
    contracts = _unique(tables["contracts"], "ticker", checks, "contracts")
    outcomes = _unique(tables["outcomes"], "ticker", checks, "outcomes")
    quotes: dict[str, list[dict]] = defaultdict(list)
    keys = []
    for row in tables["candles"]:
        quotes[row["ticker"]].append(row)
        keys.append((row["ticker"], row["end_period_ts"]))
    checks.check(len(keys) == len(set(keys)), "unique_candles", model["name"], "Duplicate contract/hour price record")
    eligible = _eligible_days(tables, checks)
    slip = _decimal(policy["slippage_per_contract"])
    rate = _decimal(policy["fee_scenario"]["rate"])
    quantum = _decimal(policy["fee_scenario"]["rounding"])
    quantity = _quantity(policy["reference_quantity"])
    target_roi = _decimal(policy["target_expected_net_return"])
    if slip < 0 or target_roi < Decimal("0.10"):
        raise VerificationError("Invalid slippage or expected-return target")
    expected = {}
    for ticker, contract in sorted(contracts.items()):
        day = contract["climate_date"]
        if day not in eligible or day not in forecasts:
            continue
        target = _day(day)
        cutoff = datetime(target.year, target.month, target.day, policy["decision_hour_utc"], tzinfo=UTC)
        entry = cutoff + timedelta(seconds=policy["execution_delay_seconds"])
        outcome = outcomes.get(ticker)
        if not outcome or outcome.get("mapping_consistent") is not True:
            continue
        if not _time(contract["open_time"]) <= cutoff <= entry < _time(contract["close_time"]):
            continue
        completed = [row for row in quotes[ticker] if row["end_period_ts"] <= cutoff.timestamp()]
        if not completed:
            continue
        quote = max(completed, key=lambda row: row["end_period_ts"])
        checks.check(quote["climate_date"] == day, "quote_contract_date", ticker, "Candle is assigned to another climate date")
        observed = datetime.fromtimestamp(quote["end_period_ts"], UTC)
        released = observed + timedelta(seconds=60)
        mean, sd = independent_prediction(model, forecasts[day])
        yes_probability = independent_probability(mean, sd, contract["lower_integer_f"], contract["upper_integer_f"])
        for side, field in (("YES", "yes_ask_close"), ("NO", "yes_bid_close")):
            if quote[field] is None:
                continue
            base = _decimal(quote[field]) if side == "YES" else Decimal(1) - _decimal(quote[field])
            price = base + slip
            if not Decimal(0) < base < Decimal(1) or not Decimal(0) < price < Decimal(1):
                continue
            decision_id = f"{version}:{day}:{ticker}:{side}"
            independent_p = yes_probability if side == "YES" else 1 - yes_probability
            # Isolate numerical CDF tolerance from exact monetary arithmetic.
            # Every supplied probability is independently checked before use.
            saved = saved_decisions.get(decision_id)
            probability = _decimal(saved["purchased_probability"]) if saved else Decimal(str(independent_p))
            checks.check(Decimal(0) <= probability <= Decimal(1) and abs(float(probability) - independent_p) <= PROBABILITY_TOLERANCE,
                         "independent_probability", decision_id, "Saved probability differs from NormalDist integral")
            fee = _fee(price, quantity, rate, quantum)
            outlay = quantity * price + fee
            profit = quantity * probability - outlay
            roi = profit / outlay
            status, reason = "SKIPPED", "EXPECTED_RETURN_BELOW_TARGET"
            if released > entry:
                reason = "PRICE_NOT_AVAILABLE_AS_OF_DECISION"
            elif (entry - observed).total_seconds() > policy["max_quote_age_seconds"]:
                reason = "STALE_PRICE"
            elif roi >= target_roi:
                status, reason = "ACCEPTED", "ASSUMED_FILL_SCENARIO"
            expected[decision_id] = {
                "decision_id": decision_id, "ticker": ticker, "climate_date": day,
                "status": status, "reason": reason, "information_cutoff": cutoff,
                "decision_at": entry, "dataset_version": dataset_version, "model_version": version,
                "side": side, "quantity": quantity, "entry_price": price, "entry_fee": fee,
                "settlement_cost": Decimal(0), "entry_outlay": outlay,
                "expected_net_profit": profit, "expected_net_return": roi, "target_return": target_roi,
                "evidence_grade": "B", "price_source": quote["source_sha256"],
                "purchased_probability": probability,
            }
    return expected


def _audit_base_decisions(saved: dict[str, dict], expected: dict[str, dict], checks: _Checks) -> set[str]:
    checks.check(set(saved) == set(expected), "complete_candidate_universe", "base decisions",
                 "Saved decision IDs omit or add candidates relative to frozen inputs")
    exact = ("ticker", "climate_date", "status", "reason", "dataset_version", "model_version", "side", "quantity", "evidence_grade", "price_source")
    monetary = ("entry_price", "entry_fee", "settlement_cost", "entry_outlay", "expected_net_profit", "expected_net_return", "target_return")
    for decision_id in sorted(set(saved) & set(expected)):
        actual, wanted = saved[decision_id], expected[decision_id]
        checks.check(type(actual.get("quantity")) is int, "decision_quantity_type", decision_id, "Decision quantity must be an integer, not boolean")
        for field in exact:
            checks.check(actual.get(field) == wanted[field], "decision_" + field, decision_id, "Saved decision differs from independent reconstruction")
        for field in monetary:
            checks.decimal(actual.get(field), wanted[field], "decision_" + field, decision_id, ratio=field == "expected_net_return")
        for field in ("information_cutoff", "decision_at"):
            checks.check(_time(actual[field]) == wanted[field], "decision_" + field, decision_id, "Saved decision time differs from registered timing")
        checks.check(type(actual.get("selected")) is bool, "selection_flag_type", decision_id, "Selection flag must be boolean")
    by_day: dict[str, list[dict]] = defaultdict(list)
    for candidate in expected.values():
        if candidate["status"] == "ACCEPTED":
            by_day[candidate["climate_date"]].append(candidate)
    chosen = {min(rows, key=lambda row: (-row["expected_net_return"], row["ticker"], row["side"]))["decision_id"] for rows in by_day.values()}
    actual_chosen = {decision_id for decision_id, row in saved.items() if row.get("selected") is True}
    checks.check(actual_chosen == chosen, "highest_ev_daily_selection", "base decisions", "Selected trades differ from highest expected ROI with registered tie-breaks")
    return chosen


def _ledger_accounting(ledger: list[dict], outcomes: dict[str, dict], rate: Decimal,
                       quantum: Decimal, checks: _Checks, context: str, *,
                       expected: dict[str, dict] | None = None, chosen: set[str] | None = None,
                       expected_quantity: int | None = None, timing_policy: dict | None = None) -> dict[str, Any]:
    records = _unique(ledger, "decision_id", checks, context)
    checks.check(len({row["climate_date"] for row in ledger}) == len(ledger), "daily_entry_cap", context, "Ledger contains multiple entries for one weather day")
    if chosen is not None:
        checks.check(set(records) == chosen, "ledger_matches_selection", context, "Ledger does not exactly match selected decisions")
    total_profit, total_outlay, returns = Decimal(0), Decimal(0), []
    for decision_id, row in sorted(records.items()):
        try:
            ticker = row["ticker"]
            outcome = outcomes[ticker]
            quantity = _quantity(row["quantity"])
            if expected_quantity is not None:
                checks.check(quantity == expected_quantity, "ledger_quantity", decision_id, "Ledger size differs from scenario")
            checks.check(row["climate_date"] == outcome["climate_date"], "settlement_day", decision_id, "Outcome belongs to another weather day")
            checks.check(outcome.get("mapping_consistent") is True, "settlement_mapping", decision_id, "Settlement mapping has not passed reconciliation")
            if type(outcome["yes_outcome"]) is not int or outcome["yes_outcome"] not in (0, 1):
                raise VerificationError("Normalized payout label is not binary")
            if row["side"] not in {"YES", "NO"}:
                raise VerificationError("Invalid ledger side")
            price = _decimal(row["entry_price"])
            fee = _fee(price, quantity, rate, quantum)
            outlay = quantity * price + fee
            payout = Decimal(quantity * (outcome["yes_outcome"] if row["side"] == "YES" else 1 - outcome["yes_outcome"]))
            # The current registered replay has no settlement fee or rebate.
            settlement_cost = Decimal(0)
            profit = payout - outlay - settlement_cost
            roi = profit / outlay
            for field, value in (("entry_fee", fee), ("entry_outlay", outlay), ("payout", payout),
                                 ("settlement_cost", settlement_cost), ("net_profit", profit), ("net_return", roi)):
                checks.decimal(row.get(field), value, "ledger_" + field, decision_id, ratio=field == "net_return")
            settled_at = _time(outcome["settlement_time"])
            checks.check(_time(row["settled_at"]) == settled_at, "settlement_timestamp", decision_id, "Ledger timestamp differs from normalized outcome")
            if timing_policy is not None:
                target_day = _day(row["climate_date"])
                planned_entry = datetime(target_day.year, target_day.month, target_day.day, timing_policy["decision_hour_utc"], tzinfo=UTC) + timedelta(seconds=timing_policy["execution_delay_seconds"])
                checks.check(settled_at >= planned_entry, "settlement_after_planned_entry", decision_id, "Settlement precedes the scenario's registered entry time")
            checks.check(row.get("evidence_grade") == "B", "ledger_evidence_grade", decision_id, "Hourly summaries cannot be upgraded to executable depth")
            checks.check("not actual account gains" in row.get("simulation_label", ""), "simulation_label", decision_id, "Ledger must identify hypothetical gains")
            if expected is not None and decision_id in expected:
                decision = expected[decision_id]
                checks.check(settled_at >= decision["decision_at"], "settlement_after_entry", decision_id, "Outcome predates simulated entry")
                for field in ("ticker", "climate_date", "side", "quantity"):
                    checks.check(row.get(field) == decision[field], "ledger_decision_" + field, decision_id, "Ledger differs from reconstructed decision")
                for field in ("entry_price", "entry_fee", "entry_outlay", "expected_net_return"):
                    checks.decimal(row.get(field), decision[field], "ledger_decision_" + field, decision_id, ratio=field == "expected_net_return")
            total_profit += profit
            total_outlay += outlay
            returns.append(roi)
        except (ValueError, KeyError, ArithmeticError, TypeError, OverflowError) as exc:
            checks.invalid("ledger_record_valid", decision_id, exc)
    return {"trade_count": len(ledger), "total_net_profit": str(total_profit), "total_entry_outlay": str(total_outlay),
            "capital_weighted_return": str(total_profit / total_outlay) if total_outlay else None,
            "mean_trade_return": str(sum(returns, Decimal(0)) / len(returns)) if returns else None}


def _audit_summary(saved: dict[str, Any], totals: dict[str, Any], checks: _Checks, context: str) -> None:
    checks.check(saved.get("trade_count") == totals["trade_count"], "summary_trade_count", context, "Reported trade count differs from ledger")
    for field in ("total_net_profit", "total_entry_outlay", "capital_weighted_return", "mean_trade_return"):
        if totals[field] is None:
            checks.check(saved.get(field) is None, "summary_" + field, context, "Empty ledger return must be undefined")
        else:
            checks.decimal(saved.get(field), _decimal(totals[field]), "summary_" + field, context,
                           ratio=field in {"capital_weighted_return", "mean_trade_return"})


def verify_model_records(*, model: dict, decisions: list[dict], ledger: list[dict],
                         policy: dict, tables: dict[str, list[dict]], dataset_version: str,
                         summary: dict | None = None) -> dict[str, Any]:
    """Pure-data audit suitable for synthetic tests and saved base-run artifacts."""
    checks = _Checks()
    totals = None
    with localcontext() as arithmetic:
        arithmetic.prec = 28
        try:
            saved = _unique(decisions, "decision_id", checks, "saved decisions")
            _audit_timing(model, tables, policy, checks)
            _audit_settlement_mapping(tables, checks)
            version = _canonical_hash(model)
            expected = _base_candidates(model, tables, policy, version, dataset_version, saved, checks)
            chosen = _audit_base_decisions(saved, expected, checks)
            outcomes = _unique(tables["outcomes"], "ticker", checks, "normalized outcomes")
            totals = _ledger_accounting(ledger, outcomes, _decimal(policy["fee_scenario"]["rate"]),
                                       _decimal(policy["fee_scenario"]["rounding"]), checks, "base ledger",
                                       expected=expected, chosen=chosen, expected_quantity=policy["reference_quantity"], timing_policy=policy)
            if summary is not None:
                _audit_summary(summary, totals, checks, model["name"])
        except (ValueError, KeyError, ArithmeticError, TypeError, OverflowError) as exc:
            checks.invalid("valid_model_inputs", str(model.get("name", "unknown")), exc)
    return {**checks.report(), "model": model.get("name"), "recomputed_totals": totals,
            "probability_absolute_tolerance": PROBABILITY_TOLERANCE, "ratio_absolute_tolerance": str(RATIO_TOLERANCE)}


def verify_saved_forecasts(*, model: dict, tables: dict, dataset_version: str, predictions: list[dict],
                           contract_probabilities: list[dict], daywise_scores: list[dict], summary: dict) -> dict:
    """Independent saved-prediction scoring audit; no production scorer imports."""
    checks = _Checks()
    def close(actual, expected, code, context):
        checks.check(type(actual) in (int, float) and isfinite(actual) and abs(actual - expected) <= 1e-10,
                     code, context, "Saved score or prediction differs from independent calculation")
    try:
        version = _canonical_hash(model)
        features = _unique(tables["forecasts"], "climate_date", checks, "forecast features")
        labels = _unique(tables["climate"], "climate_date", checks, "weather labels")
        eligible = _eligible_days(tables, checks)
        distributions = {day: independent_prediction(model, row) for day, row in features.items()}
        saved = _unique(predictions, "climate_date", checks, "saved forecasts")
        checks.check(set(saved) == set(features), "saved_forecast_coverage", model["name"], "Not every available feature day has exactly one saved prediction")
        def lineage(row, context):
            checks.check(row.get("model_sha256") == version and row.get("dataset_version") == dataset_version,
                         "saved_forecast_lineage", context, "Saved artifact has a different fitted model or dataset")
        for day, row in saved.items():
            lineage(row, day)
            mean, sd = distributions[day]
            close(row["mean_f"], mean, "saved_forecast_mean", day)
            close(row["sd_f"], sd, "saved_forecast_spread", day)
            checks.check(type(row["eligible"]) is bool and row["eligible"] == (day in eligible), "saved_forecast_eligibility", day, "Saved eligibility differs")
            checks.check(row["feature_available_at"] == features[day]["available_at"], "saved_forecast_availability", day, "Feature timestamp lineage differs")
        actual = _unique(tables["outcomes"], "ticker", checks, "score outcomes")
        expected_contracts = {r["ticker"]: r for r in tables["contracts"] if r["climate_date"] in eligible and r["ticker"] in actual}
        scored = _unique(contract_probabilities, "ticker", checks, "saved full contract probabilities")
        checks.check(set(scored) == set(expected_contracts), "saved_probability_coverage", model["name"], "Saved scored-contract universe is incomplete")
        errors = defaultdict(list)
        for ticker, row in scored.items():
            contract = expected_contracts[ticker]
            day = contract["climate_date"]
            lineage(row, ticker)
            probability = independent_probability(*distributions[day], contract["lower_integer_f"], contract["upper_integer_f"])
            outcome = actual[ticker]["yes_outcome"]
            checks.check(type(outcome) is int and outcome in (0, 1) and type(row["yes_outcome"]) is int and row["yes_outcome"] == outcome and row["climate_date"] == day,
                         "saved_probability_outcome", ticker, "Saved scoring outcome or climate date differs")
            close(row["yes_probability"], probability, "saved_contract_probability", ticker)
            error = (probability - outcome) ** 2
            close(row["brier_contribution"], error, "saved_contract_brier", ticker)
            errors[day].append(error)
        daily = _unique(daywise_scores, "climate_date", checks, "saved daywise scores")
        checks.check(set(daily) == eligible, "saved_daywise_coverage", model["name"], "Saved per-day score dates differ")
        crps_values, differences = [], []
        for day, row in daily.items():
            lineage(row, day)
            mean, sd = distributions[day]
            difference = mean - labels[day]["tmax_f"]
            z = -difference / sd
            crps = sd * (z * (2 * NormalDist().cdf(z) - 1) + 2 * exp(-z * z / 2) / sqrt(2 * pi) - 1 / sqrt(pi))
            close(row["crps_f"], crps, "saved_daywise_crps", day)
            close(row["mean_error_f"], difference, "saved_daywise_bias", day)
            close(row["absolute_error_f"], abs(difference), "saved_daywise_absolute_error", day)
            close(row["brier_sum"], fsum(errors[day]), "saved_daywise_brier_sum", day)
            checks.check(row["contract_count"] == len(errors[day]), "saved_daywise_contract_count", day, "Scored contract count differs")
            if errors[day]: close(row["brier"], fsum(errors[day]) / len(errors[day]), "saved_daywise_brier", day)
            else: checks.check(row["brier"] is None, "empty_daywise_brier", day, "An empty contract set has undefined Brier")
            crps_values.append(crps)
            differences.append(difference)
        checks.check(summary["weather_days"] == len(eligible) and summary["contract_count"] == len(expected_contracts),
                     "forecast_summary_counts", model["name"], "Forecast summary counts differ")
        for key, values in (("gaussian_crps_f", crps_values), ("mae_f", [abs(v) for v in differences]),
                            ("bias_f", differences), ("brier", [v for group in errors.values() for v in group])):
            if values: close(summary[key], fsum(values) / len(values), "forecast_summary_" + key, model["name"])
            else: checks.check(summary[key] is None, "empty_forecast_summary", key, "Empty score must be undefined")
    except (ValueError, KeyError, ArithmeticError, TypeError, OverflowError) as exc:
        checks.invalid("saved_forecast_inputs", model.get("name", "unknown"), exc)
    return {**checks.report(), "saved_prediction_artifacts_verified": not checks.failed,
            "forecast_days_verified": len(predictions), "contract_probabilities_verified": len(contract_probabilities),
            "daywise_scores_verified": len(daywise_scores)}


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise VerificationError("Artifact path must remain inside the project")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise VerificationError("Artifact path escapes the project")
    if "protected_final" in {part.lower() for part in path.parts}:
        raise VerificationError("Development replication must not read protected-final inputs")
    return path


def verify_frozen_manifest(root: Path | str, manifest: dict) -> dict[str, Any]:
    """Independent hash check; reject protected paths before opening any file."""
    root = Path(root).resolve()
    checks = _Checks()
    try:
        expected_version = _canonical_hash({"files": manifest["files"], "policy": manifest["policy"]})
        checks.check(manifest.get("version") == expected_version, "manifest_version", "manifest", "Manifest body checksum changed")
        paths = [record["path"] for record in manifest["files"]]
        checks.check(len(paths) == len(set(paths)), "manifest_unique_paths", "manifest", "Duplicate manifest path")
        # Preflight every path before reading any manifest-listed content.
        resolved = [(record, _safe_path(root, record["path"])) for record in manifest["files"]]
        for record, path in resolved:
            try:
                checks.check(path.is_file() and path.stat().st_size == record["bytes"] and _file_hash(path) == record["sha256"],
                             "frozen_file_hash", record["path"], "Frozen input or source code differs from manifest")
            except OSError as exc:
                checks.invalid("frozen_file_hash", record["path"], exc)
    except (ValueError, KeyError, TypeError) as exc:
        checks.invalid("manifest_structure", "manifest", exc)
    return checks.report()


def verify_development_run(project_root: Path | str, run_directory: Path | str,
                           manifest_path: Path | str | None = None,
                           output_path: Path | str | None = None) -> dict[str, Any]:
    """Verify a completed development run; never fetch inputs or inspect holdout.

    Base decisions receive full candidate-universe reconstruction. Sensitivity
    artifacts have no saved decision arrays, so their scope is ledger accounting
    and settlement consistency only, explicitly not selection verification.
    """
    root, run = Path(project_root).resolve(), Path(run_directory).resolve()
    checks = _Checks()
    model_reports, sensitivity_reports, forecast_reports, files_used = {}, {}, {}, []
    report: dict[str, Any]
    try:
        if not run.is_relative_to(root) or "protected_final" in {part.lower() for part in run.parts}:
            raise VerificationError("Development run must lie within the project, outside protected paths")
        def read_json(path: Path) -> Any:
            path = _safe_path(root, path.relative_to(root).as_posix())
            before = _file_hash(path)
            content = json.loads(path.read_text(encoding="utf-8"))
            if before != _file_hash(path):
                raise VerificationError("Artifact changed during read")
            files_used.append({"path": path.relative_to(root).as_posix(), "sha256": before})
            return content
        run_summary = read_json(run / "summary.json")
        version = run_summary["dataset_version"]
        if not isinstance(version, str) or len(version) != 64 or any(c not in "0123456789abcdef" for c in version):
            raise VerificationError("Invalid dataset version")
        path = Path(manifest_path).resolve() if manifest_path else root / "data/manifests" / f"development-baselines-{version}.json"
        manifest = read_json(path)
        manifest_report = verify_frozen_manifest(root, manifest)
        checks.count += manifest_report["checks"]
        checks.failed.extend(manifest_report["failed_checks"])
        if manifest_report["status"] != "PASS":
            raise VerificationError("Frozen manifest verification failed; input loading stopped")
        checks.check(version == manifest["version"], "dataset_version", "run", "Run and manifest use different datasets")
        if version != manifest["version"]:
            raise VerificationError("Dataset identity mismatch")
        policy = manifest["policy"]["evaluation"]
        listed = {record["path"] for record in manifest["files"]}
        if not set(TABLE_PATHS.values()) <= listed:
            raise VerificationError("Required development tables are absent from frozen manifest")
        import pyarrow.parquet as pq
        tables = {name: pq.read_table(_safe_path(root, relative)).to_pylist() for name, relative in TABLE_PATHS.items()}
        post_read = verify_frozen_manifest(root, manifest)
        checks.count += post_read["checks"]
        checks.failed.extend(post_read["failed_checks"])
        if post_read["status"] != "PASS":
            raise VerificationError("Inputs changed during loading")
        models = read_json(run / "fitted_models.json")
        if not isinstance(models, dict) or not models:
            raise VerificationError("Fitted model registry must be nonempty")
        checks.check(set(models) == set(run_summary["models"]), "model_inventory", "run", "Saved models differ from report inventory")
        if "baseline_definitions" in manifest["policy"]:
            checks.check(set(models) == set(manifest["policy"]["baseline_definitions"]), "registered_model_inventory", "run", "Saved models differ from preregistered baseline definitions")
        eligibility_record = run_summary["eligibility_artifact"]
        if (eligibility_record["path"] != "selection_eligibility.json" or
                (run / eligibility_record["path"]).stat().st_size != eligibility_record["bytes"] or
                _file_hash(run / eligibility_record["path"]) != eligibility_record["sha256"]):
            raise VerificationError("Baseline eligibility artifact is unbound or changed")
        saved_eligibility = read_json(run / "selection_eligibility.json")
        checks.check(saved_eligibility["eligible_days"] == sorted(_eligible_days(tables, checks)), "saved_eligible_days", "run", "Saved eligibility differs from source tables")
        for name, model in sorted(models.items()):
            if name not in {"seasonal_climatology", "gfs_bias_corrected", "nbm_bias_corrected", "equal_blend_bias_corrected", "collector_style_fixed_2f"} or model.get("name") != name:
                raise VerificationError("Unexpected model filename or identity")
            checks.check(run_summary["models"][name].get("model_sha256") == _canonical_hash(model),
                         "saved_model_identity", name, "Reported fitted-model identity differs from saved parameters")
            decisions = read_json(run / f"{name}_decisions.json")
            ledger = read_json(run / f"{name}_ledger.json")
            model_report = verify_model_records(model=model, decisions=decisions, ledger=ledger, policy=policy,
                                                tables=tables, dataset_version=version,
                                                summary=run_summary["models"][name]["historical_assumed_fill"])
            model_reports[name] = model_report
            checks.count += model_report["checks"]
            checks.failed.extend({**failure, "model": name} for failure in model_report["failed_checks"])
            artifact_records = run_summary["models"][name]["forecast_artifacts"]
            expected_names = {f"{name}_{kind}.json" for kind in ("predictions", "contract_probabilities", "daywise_scores")}
            if len(artifact_records) != 3 or {r["path"] for r in artifact_records} != expected_names:
                raise VerificationError("Saved baseline forecast artifact inventory differs")
            saved_forecasts = {}
            for record in artifact_records:
                artifact_path = run / record["path"]
                if artifact_path.stat().st_size != record["bytes"] or _file_hash(artifact_path) != record["sha256"]:
                    raise VerificationError("Saved baseline forecast artifact hash differs")
                kind = record["path"][len(name) + 1:-5]
                saved_forecasts[kind] = read_json(artifact_path)
            forecast_report = verify_saved_forecasts(model=model, tables=tables, dataset_version=version, **saved_forecasts,
                                                    summary=run_summary["models"][name]["forecast_scores"])
            forecast_reports[name] = forecast_report
            checks.count += forecast_report["checks"]
            checks.failed.extend({**failure, "model": name} for failure in forecast_report["failed_checks"])
            sensitivity_reports[name] = []
            scenarios = run_summary["models"][name].get("predeclared_cost_sensitivity", [])
            expected_scenarios = {_canonical_hash({"slippage": slip, "fee_rate": rate, "quantity": quantity})[:12]
                                  for slip, rate, quantity in product(policy["sensitivity_slippage"], policy["sensitivity_fee_rate"], policy["sensitivity_quantities"])}
            actual_scenario_ids = [scenario["scenario_id"] for scenario in scenarios]
            checks.check(len(actual_scenario_ids) == len(set(actual_scenario_ids)) and set(actual_scenario_ids) == expected_scenarios,
                         "sensitivity_inventory", name, "Saved sensitivity scenarios omit, duplicate or add preregistered cases")
            for scenario in scenarios:
                assumptions = {key: scenario[key] for key in ("slippage", "fee_rate", "quantity")}
                scenario_id = _canonical_hash(assumptions)[:12]
                if scenario["scenario_id"] != scenario_id:
                    raise VerificationError("Sensitivity scenario identity mismatch")
                scenario_checks = _Checks()
                scenario_checks.check(scenario["slippage"] in policy["sensitivity_slippage"] and scenario["fee_rate"] in policy["sensitivity_fee_rate"] and scenario["quantity"] in policy["sensitivity_quantities"],
                                      "sensitivity_preregistered", scenario_id, "Sensitivity values were not preregistered")
                scenario_ledger = read_json(run / "sensitivity" / f"{name}_{scenario_id}_ledger.json")
                with localcontext() as arithmetic:
                    arithmetic.prec = 28
                    totals = _ledger_accounting(scenario_ledger, {row["ticker"]: row for row in tables["outcomes"]},
                                               _decimal(scenario["fee_rate"]), _decimal(policy["fee_scenario"]["rounding"]),
                                               scenario_checks, scenario_id, expected_quantity=scenario["quantity"], timing_policy=policy)
                    _audit_summary(scenario["summary"], totals, scenario_checks, scenario_id)
                sensitivity_report = {**scenario_checks.report(), "scenario_id": scenario_id,
                    "replication_scope": "Sensitivity ledger accounting and normalized settlement verification only; candidate selection and slippage reconstruction not verified",
                    "candidate_selection_verified": False, "recomputed_totals": totals}
                sensitivity_reports[name].append(sensitivity_report)
                checks.count += scenario_checks.count
                checks.failed.extend({**failure, "model": name, "scenario_id": scenario_id} for failure in scenario_checks.failed)
        final_snapshot_check = verify_frozen_manifest(root, manifest)
        checks.count += final_snapshot_check["checks"]
        checks.failed.extend(final_snapshot_check["failed_checks"])
        for artifact in files_used:
            checks.check(_file_hash(_safe_path(root, artifact["path"])) == artifact["sha256"], "artifact_unchanged_after_verification",
                         artifact["path"], "Saved result changed during independent verification")
        report = {**checks.report(), "dataset_version": version, "models": model_reports,
                  "sensitivity_ledger_checks": sensitivity_reports}
    except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as exc:
        checks.invalid("run_verification", "development run", exc)
        report = {**checks.report(), "models": model_reports, "sensitivity_ledger_checks": sensitivity_reports}
    report.update({"saved_forecast_checks": forecast_reports,
                   "saved_prediction_artifacts_verified": bool(forecast_reports) and report["status"] == "PASS" and all(r["saved_prediction_artifacts_verified"] for r in forecast_reports.values()),
                   "artifact_hashes": files_used, "verifier_sha256": _file_hash(Path(__file__)),
                   "limitations": ["No independent forecast refit or raw GRIB extraction audit",
                       "Verifies arithmetic under saved assumptions; does not establish historical availability or executable fills",
                       "Final holdout is neither opened nor scored", "Sensitivity selection is outside this verification scope"]})
    if output_path is not None:
        destination = Path(output_path).resolve()
        if not destination.is_relative_to(root) or "protected_final" in {part.lower() for part in destination.parts}:
            raise VerificationError("Report output must stay within nonprotected project paths")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report
