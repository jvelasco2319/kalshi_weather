from datetime import datetime, timezone
import pytest
from klax_lab.climate import parse_product, choose_as_of


def product(day="JANUARY 1 2024", partial=False):
    return ("CDUS46 KLOX 020942\nCLILAX\n...THE LOS ANGELES INTL AIRPORT CA CLIMATE SUMMARY FOR " + day + "...\n" + ("VALID TODAY AS OF 0500 PM LOCAL TIME.\n" if partial else "") + "TEMPERATURE (F)\n YESTERDAY\n MAXIMUM 67 1:47 PM\n MINIMUM 50").encode()


def test_final_label_and_partial_exclusion():
    r = parse_product("CLILAX_202401020942.txt", product())
    assert r["tmax_f"] == 67 and r["climate_date"] == "2024-01-01"
    assert parse_product("CLILAX_202401020942.txt", product(partial=True)) is None
    assert parse_product("CLILAX_202401020942.txt", product("JANUARY 2 2024")) is None


def test_revision_as_of():
    first = {"available_at": "2025-01-02T09:00:00+00:00", "tmax_f": 65}
    revision = {"available_at": "2025-01-04T09:00:00+00:00", "tmax_f": 66}
    assert choose_as_of([first, revision], datetime(2025, 1, 3, tzinfo=timezone.utc)) == first


def test_record_temperature_flag_is_not_missing():
    row = parse_product("CLILAX_202401020942.txt", product().replace(b"MAXIMUM 67 ", b"MAXIMUM 102R "))
    assert row["tmax_f"] == 102 and row["record_flag"] == "R"


def test_uncorrected_yesterday_with_wrong_summary_date_quarantined():
    with pytest.raises(ValueError, match="inconsistent summary date"):
        parse_product("CLILAX_202401020942.txt", product("DECEMBER 31 2023"))
