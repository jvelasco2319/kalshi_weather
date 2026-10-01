"""Finite V3 weather compatibility acquisition tests."""
from datetime import date

from klax_lab.acquire_weather_v3 import (
    acquire_compatibility_pilot, acquire_gefs_derived_product_pilot,
    acquire_revised_weather_history,
)
from klax_lab.weather_sources_v3 import (
    build_compatibility_pilot, build_gefs_derived_product_pilot,
)


DAY = date(2025, 1, 5)
NOW = date(2026, 9, 25)


class Response:
    def __init__(self, status, body, headers):
        self.status_code = status
        self.body = body
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_content(self, _):
        yield self.body


class Session:
    def __init__(self, indices):
        self.indices = indices
        self.calls = []

    def get(self, url, *, headers, **_):
        self.calls.append((url, dict(headers)))
        if url.endswith(".idx"):
            body = self.indices[url]
            return Response(200, body, {"Content-Length": str(len(body)), "ETag": "index"})
        start_text, end_text = headers["Range"].removeprefix("bytes=").split("-")
        start, end = int(start_text), int(end_text)
        body = b"GRIB" + b"x" * (end - start - 3)
        return Response(206, body, {
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes {start}-{end}/{end + 100}",
            "ETag": "field",
        })


def _indices():
    pilot = build_compatibility_pilot(DAY, today=NOW)
    result = {}
    for plan in (pilot["hrrr"], pilot["gefs"]):
        for item in plan.objects:
            rows, offset = [], 0
            forecast = "anl" if item.lead_hours == 0 else f"{item.lead_hours} hour fcst"
            initialized = "d=" + item.initialized_at.strftime("%Y%m%d%H")
            for number, field in enumerate(item.fields, 1):
                rows.append(f"{number}:{offset}:{initialized}:{field.variable}:{field.level}:{forecast}:")
                offset += 8
            rows.append(f"{len(item.fields) + 1}:{offset}:{initialized}:DUMMY:surface:{forecast}:")
            result[item.index_url] = ("\n".join(rows) + "\n").encode()
    return result


def _derived_indices():
    plan = build_gefs_derived_product_pilot(DAY, today=NOW)
    result = {}
    for item in plan.objects:
        qualifier = "ens mean" if item.member_id == "avg" else "ens std dev"
        initialized = "d=" + item.initialized_at.strftime("%Y%m%d%H")
        forecast = f"{item.lead_hours} hour fcst"
        result[item.index_url] = (
            f"1:0:{initialized}:TMP:2 m above ground:{forecast}:{qualifier}\n"
            f"2:8:{initialized}:DUMMY:surface:{forecast}:\n"
        ).encode()
    return result


def test_acquires_exact_pilot_then_verifies_cache_offline(tmp_path):
    session = Session(_indices())
    report = acquire_compatibility_pilot(
        tmp_path, DAY, today=NOW, session_override=session,
    )
    assert report["status"] == "COMPATIBILITY_PILOT_ACQUIRED_AND_CACHE_VERIFIED"
    assert report["network_requests"] == 56
    assert report["transferred_bytes"] > 0
    assert report["coverage_complete"] is False
    assert report["protected_final_read"] is False
    assert report["hrrr_cache_verification"]["objects_verified"] == 4
    assert report["gefs_cache_verification"]["objects_verified"] == 12
    assert (tmp_path / "data/manifests/v3_weather_compatibility_pilot.json").is_file()

    cached = acquire_compatibility_pilot(
        tmp_path, DAY, today=NOW, session_override=Session({}),
    )
    assert cached["network_requests"] == 0
    assert cached["cache_hits"] == 56


def test_acquires_gefs_derived_mean_spread_pilot(tmp_path):
    report = acquire_gefs_derived_product_pilot(
        tmp_path, DAY, today=NOW, session_override=Session(_derived_indices()),
    )
    assert report["status"] == "GEFS_DERIVED_PRODUCT_PILOT_ACQUIRED_AND_CACHE_VERIFIED"
    assert report["network_requests"] == 32
    assert report["cache_verification"]["objects_verified"] == 16
    assert report["coverage_complete"] is False


def test_revised_history_is_resumable_and_partial_scope_stays_partial(tmp_path):
    indices = _indices()
    indices.update(_derived_indices())
    report = acquire_revised_weather_history(
        tmp_path, DAY, DAY, today=NOW, session_override=Session(indices),
    )
    assert report["status"] == "PARTIAL_RAW_RANGE_ACQUISITION_COMPLETE"
    assert report["coverage_complete"] is False
    assert report["days_complete"] == 1
    assert report["days"][0]["network_requests"] == 60
    cached = acquire_revised_weather_history(
        tmp_path, DAY, DAY, today=NOW, session_override=Session({}),
    )
    assert cached["days"][0]["network_requests"] == 0
    assert cached["days"][0]["cache_hits"] == 60
