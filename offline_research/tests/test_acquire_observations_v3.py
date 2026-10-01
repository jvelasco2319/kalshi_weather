"""Archived local-observation acquisition and parsing tests."""
from datetime import date
from hashlib import sha256

from klax_lab.acquire_observations_v3 import archive_url, parse_archive_csv


def test_archive_url_is_fixed_to_registered_station_partition_and_fields():
    url = archive_url("LAX", date(2024, 1, 1), date(2024, 12, 31))
    assert url.startswith("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?")
    assert "station=LAX" in url and "data=metar" in url


def test_parser_keeps_explicit_reports_deduplicates_and_normalizes_units():
    contents = ("# fixture\n"
        "station,valid,tmpf,dwpf,drct,sknt,alti,mslp,vsby,skyc1,skyl1,skyc2,skyl2,skyc3,skyl3,metar\n"
        "LAX,2024-01-01 08:00,59,50,250,10,30.00,,10,BKN,1000,,,,,METAR LAX fixture B\n"
        "LAX,2024-01-01 08:00,59,50,250,10,30.00,1015,10,BKN,900,,,,,METAR LAX fixture A\n"
        "LAX,2024-01-01 08:05,,50,250,10,30.00,1015,10,,,,,,,SPECI LAX missing temp\n"
        "LAX,2024-01-01 09:00,60,51,260,11,30.01,1016,9,FEW,12000,,,,,LAX official report IEM_GHCNH\n"
        "LAX,2024-01-01 08:10,60,51,260,11,30.01,1016,9,FEW,12000,,,,,KLAX MADIS row MADISHF\n").encode()
    rows, audit = parse_archive_csv(
        contents, station="LAX", partition="weather_training",
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        source_sha256=sha256(contents).hexdigest(),
    )
    assert len(rows) == 2
    assert audit == {"retained": 2, "rejected": 2, "invalid": 0, "duplicates": 1}
    row = rows[0]
    assert row["station"] == "KLAX" and row["temperature_f"] == 59
    assert row["pressure_hpa"] == 1015 and row["cloud_ceiling_ft"] == 900
    assert row["available_at"].endswith("08:15:00+00:00")
    assert row["historical_receipt_time_proven"] is False
