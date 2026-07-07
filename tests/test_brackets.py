from kalshi_weather.edge_engine.brackets import determine_final_bracket, parse_bracket_label, parse_brackets


def test_parse_canonical_labels():
    assert parse_bracket_label("68-69°F").label == "68-69"
    assert parse_bracket_label("< 66").upper_f == 65
    assert parse_bracket_label(">73").lower_f == 74


def test_observation_eliminates_lower_range():
    bracket = parse_bracket_label("68-69")
    assert bracket.eliminated_by_observed_high(70)
    assert not bracket.eliminated_by_observed_high(69)


def test_determine_final_bracket():
    brackets = list(parse_brackets(["<66", "66-67", "68-69", "70-71", "72-73", "> 73"]).values())
    assert determine_final_bracket(65, brackets) == "<66"
    assert determine_final_bracket(70, brackets) == "70-71"
    assert determine_final_bracket(74, brackets) == "> 73"
