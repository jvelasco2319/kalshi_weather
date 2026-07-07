from kalshi_weather.edge_engine.settlement import (
    ObservationStatus,
    default_high_temp_bracket_set,
    observation_status_for_bracket,
    settle_bracket_result,
)


def test_default_bracket_set_maps_temperature_to_canonical_label():
    brackets = default_high_temp_bracket_set()
    assert brackets.label_for_temp(65) == "<66"
    assert brackets.label_for_temp(66) == "66-67"
    assert brackets.label_for_temp(71) == "70-71"
    assert brackets.label_for_temp(74) == "> 73"


def test_observed_high_probably_eliminates_lower_bin():
    b = default_high_temp_bracket_set().by_label()["66-67"]
    assert observation_status_for_bracket(b, observed_high_f=70) == ObservationStatus.ELIMINATED_PROBABLE


def test_settle_bracket_result_yes_and_no():
    assert settle_bracket_result("YES", "70-71", "70-71") == 1
    assert settle_bracket_result("NO", "70-71", "70-71") == 0
    assert settle_bracket_result("NO", "72-73", "70-71") == 1
