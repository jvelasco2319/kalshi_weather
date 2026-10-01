from datetime import date

from v5a.development_labels import _member_target


def test_only_post_close_clilax_members_map_to_prior_day():
    assert _member_target("CLILAX_202608040830.txt") == date(2026, 8, 3)
    assert _member_target("CLILAX_202608040140.txt") is None
