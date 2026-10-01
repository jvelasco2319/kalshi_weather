from datetime import date, timedelta
import pytest
from v5b_next.research_specs import (model_specs, registered_grid, fingerprint,
    validate_model_spec, shared_score_dates, feature_capability_check,
    seeds, propose, synthesis, validate_hypothesis, COLONIES)


def test_finite_unique_budget_and_fixed_families():
    grid = registered_grid()
    assert len(grid) == len({fingerprint(s) for s in grid}) == 24
    assert {s["epoch_arm"] for s in grid} == {1, 2, 3, 4}
    assert len(model_specs()) == 6
    assert all(validate_model_spec(s) == s for s in model_specs())


def test_no_unregistered_feature_or_regularization():
    spec = model_specs()[-1]
    spec["feature_keys"].append("observed_marine_layer")
    with pytest.raises(ValueError): validate_model_spec(spec)
    spec = model_specs()[-1]
    spec["ridge_alpha"] = 1
    with pytest.raises(ValueError): validate_model_spec(spec)


def test_shared_scoring_and_duplicate_dates_denied():
    dates = [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(64)]
    assert len(shared_score_dates(dates)) == 44
    assert shared_score_dates(dates)[0] == "2026-06-21"
    with pytest.raises(ValueError): shared_score_dates(dates[::-1])
    with pytest.raises(ValueError): shared_score_dates(dates[:-1] + [dates[0]])


def test_feature_missingness_never_becomes_experiment_readiness():
    spec = model_specs()[-1]
    row = {"climate_date":"2026-06-01", **{k:1. for k in spec["required_feature_keys"]}}
    result = feature_capability_check(spec, [row], [row["climate_date"]])
    assert result["structural_features_available"]
    assert not result["registration_ready"]
    row[spec["required_feature_keys"][0]] = float("nan")
    assert not feature_capability_check(spec, [row], [row["climate_date"]])["structural_features_available"]


def test_reviewed_arms_total24_with_explicit_population_ownership():
    records = seeds()
    populations = {c: [] for c in COLONIES}
    for h in records: populations[h["colony"]].append(h)
    for epoch in range(3):
        batch = propose(populations, critiques=[{"findings":["Actual review gate is controller-owned"]}], epoch=epoch)
        assert len(batch) == 6
        for h in batch:
            validate_hypothesis(h)
            assert h["parent_ids"]
            populations[h["colony"]].append(h)
        records += batch
    assert len({h["fingerprint"] for h in records}) == 24
    assert all(populations[c] for c in COLONIES)
    assert propose(populations, epoch=3) == []
    assert synthesis(records[0], records[-1]) is None
    with pytest.raises(ValueError): propose(populations, critiques=[])
