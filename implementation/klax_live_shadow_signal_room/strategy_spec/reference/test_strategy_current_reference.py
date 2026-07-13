from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from strategy_current_reference import (
    BookSequence,
    CANONICAL_MODELS,
    ForecastPoint,
    ResidualRecord,
    build_model_distribution,
    combine_distributions,
    dirichlet_bounds,
    drift_flag,
    effective_sample_size,
    event_outcome_pnl,
    fee,
    full_kelly_fraction,
    future_max_asof,
    live_state,
    max_price,
    model_spread,
    nbm_maturity_cap,
    recency_weights,
    reliability_weights,
    trade_roi,
    validate_model_set,
    validate_trade_rows,
)

PT = timezone(timedelta(hours=-7))

def dt(h, m=0):
    return datetime(2026, 7, 7, h, m, tzinfo=PT)


def quantizer(value):
    # Six illustrative brackets: <69, 69-70, 71-72, 73-74, 75-76, >76
    if value < 69:
        return 0
    if value < 71:
        return 1
    if value < 73:
        return 2
    if value < 75:
        return 3
    if value < 77:
        return 4
    return 5


def records(center_shift=0.0, n=40):
    out = []
    residual_pattern = [-1.5, -0.5, 0.0, 0.5, 1.5]
    for i in range(n):
        out.append(ResidualRecord(
            target_date=f"d{i}",
            residual_f=residual_pattern[i % len(residual_pattern)] + center_shift,
            settlement_gap_f=[0.0, 0.1, -0.1][i % 3],
            age_target_dates=i,
        ))
    return out


def test_exact_model_set():
    assert validate_model_set(CANONICAL_MODELS) == CANONICAL_MODELS
    with pytest.raises(ValueError):
        validate_model_set(("ecmwf_ifs", "gfs013", "gfs_global", "nam", "nbm"))


def test_july7_past_maximum_cannot_survive():
    points = [
        ForecastPoint("past-hot", "gfs_seamless", "r1", dt(12), 76.3, dt(7), dt(7)),
        ForecastPoint("future-1", "gfs_seamless", "r1", dt(15), 73.94, dt(7), dt(7)),
        ForecastPoint("future-2", "gfs_seamless", "r1", dt(16), 73.5, dt(7), dt(7)),
    ]
    assert future_max_asof(points, dt(15), dt(23, 59)) == 73.94


def test_future_source_and_future_receipt_rejected():
    future_source = [ForecastPoint("x", "gfs013", "r", dt(10), 74, dt(8, 36), dt(8, 36))]
    with pytest.raises(ValueError):
        future_max_asof(future_source, dt(7, 24), dt(23, 59))
    future_receipt = [ForecastPoint("x", "gfs013", "r", dt(10), 74, dt(7), dt(8, 36))]
    with pytest.raises(ValueError):
        future_max_asof(future_receipt, dt(7, 24), dt(23, 59))


def test_observed_floor():
    assert live_state(72.0, 73.94) == 73.94


def test_recency_weights_and_effective_sample():
    w = recency_weights([0, 1, 2], half_life=1)
    assert pytest.approx(sum(w)) == 1
    assert w[0] > w[1] > w[2]
    neff = effective_sample_size(w)
    assert 1 < neff < 3


def test_model_distribution_bounds_and_observation_floor():
    dist = build_model_distribution("gfs013", 74.0, 73.94, records(), quantizer, 6)
    assert pytest.approx(sum(dist.bracket_counts), rel=1e-12) == dist.effective_sample_size
    assert all(0 <= x <= 1 for x in dist.component_safe_yes)
    assert all(s <= m for s, m in zip(dist.component_safe_yes, dist.component_mean_yes))
    assert dist.corrected_point_f >= 73.94


def test_nbm_maturity_caps():
    assert nbm_maturity_cap(9) == 0
    assert nbm_maturity_cap(10) == 0.10
    assert nbm_maturity_cap(30) == 0.20
    assert nbm_maturity_cap(60) == 0.25


def test_reliability_weights_caps_and_sum():
    completed = {m: 45 for m in CANONICAL_MODELS}
    losses = {"ecmwf_ifs": 1.2, "gfs013": 0.8, "gfs_seamless": 0.9, "nam": 1.3, "nbm": 0.7}
    weights = reliability_weights(completed, losses, 6)
    assert pytest.approx(sum(weights.values()), rel=1e-12) == 1
    assert weights["gfs013"] + weights["gfs_seamless"] <= 0.45 + 1e-12
    assert weights["nbm"] <= 0.20 + 1e-12
    assert max(weights.values()) <= 0.35 + 1e-12


def test_nbm_provisional_weight_is_capped():
    completed = {"ecmwf_ifs": 45, "gfs013": 45, "gfs_seamless": 45, "nam": 45, "nbm": 15}
    losses = {m: 1.0 for m in CANONICAL_MODELS}
    weights = reliability_weights(completed, losses, 6)
    assert weights["nbm"] <= 0.10 + 1e-12


def test_too_few_models_rejected():
    completed = {"ecmwf_ifs": 45, "gfs013": 45, "gfs_seamless": 45, "nam": 0, "nbm": 0}
    with pytest.raises(ValueError):
        reliability_weights(completed, {}, 6)


def test_mixture_preserves_minority_signal_and_is_conservative():
    dists = {
        "ecmwf_ifs": build_model_distribution("ecmwf_ifs", 76.0, 65, records(0.0), quantizer, 6),
        "gfs013": build_model_distribution("gfs013", 76.0, 65, records(0.0), quantizer, 6),
        "gfs_seamless": build_model_distribution("gfs_seamless", 76.0, 65, records(0.0), quantizer, 6),
        "nam": build_model_distribution("nam", 75.5, 65, records(0.0), quantizer, 6),
        "nbm": build_model_distribution("nbm", 73.5, 65, records(0.0), quantizer, 6),
    }
    weights = {"ecmwf_ifs": .20, "gfs013": .25, "gfs_seamless": .20, "nam": .15, "nbm": .20}
    mix = combine_distributions(dists, weights)
    # NBM's 73-74 minority signal survives in bracket 3.
    assert mix["raw_mixture_frequency"][3] > 0
    assert mix["safe_yes"][3] <= mix["posterior_mean_yes"][3]
    assert pytest.approx(sum(mix["raw_mixture_frequency"]), rel=1e-12) == 1


def test_dirichlet_no_bound_is_not_one_minus_yes_bound():
    mean_y, safe_y, mean_n, safe_n = dirichlet_bounds([10, 20, 10, 5, 3, 2])
    assert pytest.approx(mean_y[0] + mean_n[0]) == 1
    assert safe_n[0] != pytest.approx(1 - safe_y[0])


def test_spread_and_drift():
    states = {"ecmwf_ifs": 74, "gfs013": 75, "gfs_seamless": 76, "nam": 73, "nbm": 74.5}
    assert model_spread(states) == 3
    assert drift_flag(1.0, -1.1)
    assert drift_flag(2.0, 0.4)
    assert not drift_flag(0.2, 0.4)


def test_fee_examples_and_price_ceiling():
    assert fee(100, Decimal("0.50"), "taker") == Decimal("1.7500")
    levels = [Decimal(i) / 100 for i in range(1, 100)]
    assert max_price(1, 100, "taker", Decimal("0.15"), levels) == Decimal("0.86")
    assert max_price(1, 100, "taker", Decimal("0.10"), levels) == Decimal("0.90")
    assert trade_roi(1, 100, Decimal("0.87"), "taker") < Decimal("0.15")


def test_kelly():
    assert full_kelly_fraction(Decimal("0.60"), Decimal("0.50")) == Decimal("0.2")
    assert full_kelly_fraction(Decimal("0.40"), Decimal("0.50")) == 0


def test_trade_quantity_completeness():
    validate_trade_rows([{"count_fp": "1.00"}])
    with pytest.raises(ValueError):
        validate_trade_rows([{}])
    with pytest.raises(ValueError):
        validate_trade_rows([{"count_fp": "0"}])


def test_orderbook_sequence_gap_invalidates():
    book = BookSequence()
    book.snapshot(10)
    book.delta(11)
    assert book.valid
    with pytest.raises(ValueError):
        book.delta(13)
    assert not book.valid


def test_event_outcome_matrix_yes_and_no():
    pnl = event_outcome_pnl(3, [
        {"bracket_index": 0, "side": "yes", "count": "10", "all_in_cost": "4"},
        {"bracket_index": 2, "side": "no", "count": "5", "all_in_cost": "2"},
    ])
    assert pnl == (Decimal("9"), Decimal("-1"), Decimal("-6"))
