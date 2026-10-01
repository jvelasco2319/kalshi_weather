"""Atomic reservations protect later-stage resources; unknown usage stays reserved."""
import math

from .artifacts import checked, utcnow
from .store import event, transaction


def initialize_budget(run_dir, registration):
    with transaction(run_dir) as db:
        for stage, units in registration["spec"]["runtime"]["allocations"].items():
            db.execute("INSERT OR IGNORE INTO allocations VALUES(?,?)", (stage, units))


def totals(db):
    limits = dict(db.execute("SELECT stage,units FROM allocations").fetchall())
    used = {stage: 0 for stage in limits}
    unknown = 0
    for row in db.execute("SELECT * FROM reservations"):
        used[row["stage"]] += row["reserved_units"] if row["actual_units"] is None else row["actual_units"]
        unknown += row["actual_units"] is None
    return {"allocations": limits, "committed": used, "available": {k: limits[k] - used[k] for k in limits},
            "unknown_usage_reservations": unknown, "overspent": any(used[k] > limits[k] for k in limits)}


def reserve_db(db, identifier, stage, units):
    if type(units) not in (int, float) or not math.isfinite(units) or units <= 0:
        raise ValueError("Reserve a positive finite cost bound")
    if db.execute("SELECT 1 FROM reservations WHERE id=?", (identifier,)).fetchone():
        raise ValueError("Reservation already exists; do not relaunch an unknown invocation")
    budget = totals(db)
    if budget["overspent"] or stage not in budget["available"] or budget["available"][stage] < units:
        raise ValueError("Admission would consume reserved resources or exceed the global budget")
    db.execute("INSERT INTO reservations VALUES(?,?,?,?,?,?)", (identifier, stage, units, None, "RESERVED", utcnow().isoformat()))
    event(db, "budget_reserved", {"id": identifier, "stage": stage, "units": units})


def reserve(run_dir, identifier, stage, units):
    with transaction(run_dir) as db:
        reserve_db(db, identifier, stage, units)


def reconcile(run_dir, identifier, actual_units=None, *, conservative=False):
    with transaction(run_dir) as db:
        row = db.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Unknown cost reservation")
        if row["actual_units"] is not None:
            if not conservative and actual_units == row["actual_units"]:
                return totals(db)
            raise ValueError("Already reconciled; cancellation cannot erase incurred usage")
        if conservative:
            actual_units = row["reserved_units"]
        if type(actual_units) not in (float, int) or not math.isfinite(actual_units) or actual_units < 0:
            raise ValueError("Actual usage must be known, or conservatively charge the reserved bound")
        db.execute("UPDATE reservations SET actual_units=?,status=? WHERE id=?", (actual_units, "CONSERVATIVE" if conservative else "RECONCILED", identifier))
        event(db, "usage_reconciled", {"id": identifier, "actual_units": actual_units, "conservative": conservative})
        return totals(db)


def transfer(run_dir, source, target, units, reason):
    if source == target or not reason or type(units) not in (int, float) or not math.isfinite(units) or units <= 0:
        raise ValueError("A resource transfer needs distinct stages, positive units and a reason")
    with transaction(run_dir) as db:
        budget = totals(db)
        if source not in budget["available"] or target not in budget["available"] or budget["available"][source] < units:
            raise ValueError("Transfers cannot consume committed or unknown usage")
        db.execute("UPDATE allocations SET units=units-? WHERE stage=?", (units, source))
        db.execute("UPDATE allocations SET units=units+? WHERE stage=?", (units, target))
        event(db, "budget_transferred", {"source": source, "target": target, "units": units, "reason": reason})
        return totals(db)


def budget_status(run_dir):
    with transaction(run_dir) as db:
        value = totals(db)
    spec = checked(run_dir / "registration.json")["spec"]
    return {**value, "units": spec["runtime"]["budget_units"], "enforcement": spec["runtime"]["budget_enforcement"],
            "provider_calls_metered_automatically": False}
