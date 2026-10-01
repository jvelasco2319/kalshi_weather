from klax_lab.quality import exhaustive_bounds


def test_partition_must_cover_integer_outcomes_once():
    def row(lo, hi):
        return {"lower_integer_f": lo, "upper_integer_f": hi}
    assert exhaustive_bounds([row(None, 64), row(65, 66), row(67, None)])
    assert not exhaustive_bounds([row(None, 64), row(64, 66), row(67, None)])
    assert not exhaustive_bounds([row(None, 64), row(66, 67), row(68, None)])
    assert not exhaustive_bounds([row(60, 64), row(65, None)])
