from kalshi_weather.rules_engine_ext.correlated_thesis import ThesisPosition, evaluate_thesis_exposure


def test_correlated_thesis_exposure_blocks_large_group():
    rows = [ThesisPosition("69-70", "YES", 44), ThesisPosition("69-70", "YES", 40)]
    out = evaluate_thesis_exposure(rows, top_bracket="69-70", max_risk_dollars=75)
    exact = [x for x in out if x.thesis_label == "exact_center:69-70"][0]
    assert not exact.thesis_allowed
    assert exact.thesis_rejection_reason == "correlated_thesis_exposure_too_high"
