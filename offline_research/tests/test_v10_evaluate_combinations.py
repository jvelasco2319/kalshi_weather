from v10.evaluate_combinations import candidate_catalog


BLOCKS = (
    "season", "forecast_marine", "observed_marine", "pressure_and_flow",
    "inversion_and_moisture", "ensemble_uncertainty",
)


def test_v10_catalog_is_complete_powerset_at_three_weights_plus_control():
    catalog = candidate_catalog(BLOCKS, (0.25, 0.5, 0.75))
    assert len(catalog) == 193
    assert catalog[0]["candidate_id"] == "V8_CONTROL"
    subsets = {tuple(row["blocks"]) for row in catalog[1:]}
    assert len(subsets) == 64
    assert () in subsets
    assert BLOCKS in subsets
    assert {row["weight"] for row in catalog[1:]} == {0.25, 0.5, 0.75}
