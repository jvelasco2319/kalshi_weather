"""Synthetic regression demonstration, not a real scientific discovery."""
import math


def _summarize(rows):
    if not rows:
        raise ValueError("Empty dataset")
    groups = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row["squared_error"])
    return {
        "mse": sum(row["squared_error"] for row in rows) / len(rows),
        "worst_group_mse": max(sum(errors) / len(errors) for errors in groups.values()),
        "sample_count": len(rows),
    }


def evaluate(parameters, data):
    rows = []
    for index, item in enumerate(data):
        prediction = parameters["slope"] * float(item["x"]) + parameters["bias"]
        actual = float(item["y"])
        if not math.isfinite(prediction + actual):
            raise ValueError("Nonfinite input/output")
        rows.append({"index": index, "group": str(item["group"]), "prediction": prediction,
                     "actual": actual, "squared_error": (prediction - actual) ** 2})
    return {"metrics": _summarize(rows), "ledger": rows}


def replicate(parameters, data, result):
    # Independent numerical path: do not call evaluate or reuse its predicted errors.
    errors, groups = [], {}
    for item, row in zip(data, result["ledger"], strict=True):
        expected = float(item["x"]) * float(parameters["slope"]) + float(parameters["bias"])
        if not math.isclose(expected, row["prediction"], abs_tol=1e-12):
            return False
        if row["actual"] != float(item["y"]) or row["group"] != str(item["group"]):
            return False
        error = (float(item["y"]) - expected) ** 2
        if not math.isclose(error, row["squared_error"], abs_tol=1e-12):
            return False
        errors.append(error)
        groups.setdefault(str(item["group"]), []).append(error)
    if not errors:
        return False
    expected_metrics = {"mse": sum(errors) / len(errors), "sample_count": len(errors),
                        "worst_group_mse": max(sum(g) / len(g) for g in groups.values())}
    return all(math.isclose(value, result["metrics"][name], abs_tol=1e-12)
               for name, value in expected_metrics.items())
