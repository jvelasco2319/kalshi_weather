from kalshi_weather.rules_engine_ext.settlement_scenarios import Position, settlement_report


def test_settlement_report_contains_all_brackets_and_worst_case():
    report = settlement_report(
        ["67-68", "69-70", "71-72", ">72"],
        [Position("69-70", "YES", 100, 35), Position("71-72", "NO", 90, 54)],
        cash_dollars=900,
        starting_cash_dollars=1000,
        current_equity_dollars=980,
    )
    assert len(report.scenarios) == 4
    assert report.best_case_scenario in {"69-70", "67-68", ">72"}
    assert report.worst_case_scenario == "71-72"
