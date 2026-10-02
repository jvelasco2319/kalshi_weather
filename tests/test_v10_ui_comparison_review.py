"""Independent as-of and like-for-like review of model comparison displays."""
from datetime import datetime, timezone
import math
import json
from hashlib import sha256

import pytest

from v10_ui import comparison as c

DAY = "2026-10-01"


def instant(hour, minute=0):
    return datetime(2026, 10, 1, hour, minute, tzinfo=timezone.utc).isoformat()


def point(model="gfs", lead=18, temperature=80, received=None):
    issue = datetime(2026, 10, 1, tzinfo=timezone.utc)
    return {"model": model, "date": DAY, "issue_time_utc": issue.isoformat(),
            "lead_hours": lead, "time": instant(lead), "temperature_f": temperature,
            "raw_value_kelvin": (temperature-32)/1.8+273.15,
            "field": "instantaneous_two_meter_temperature", "target_station": "KLAX",
            "source_url": c.source_url(model, DAY, lead), "retrieved_at_utc": received or instant(16)}


def store(tmp_path, points=(), now=None):
    value = c.ComparisonStore(tmp_path, now=lambda: now or datetime(2026, 10, 1, 20, tzinfo=timezone.utc))
    value._days[DAY] = [{"date": DAY, "points": list(points), "failures": [], "created_at_utc": instant(16)}]
    return value


def model(state, name):
    return next(row for row in state["models"] if row["id"] == name)


def test_all_four_names_survive_unavailable_data_without_fabricated_points(tmp_path):
    s = store(tmp_path).state(DAY, [])
    assert {m["id"] for m in s["models"]} == {"gfs", "gfs_seamless", "nam", "nbm"}
    assert all(m["points"] == [] and m["status"] == "unavailable" for m in s["models"])
    assert all(m["metrics"]["mae_f"] is None for m in s["models"])
    assert s["shared_matched_points"] == 0
    assert s["orders"] == 0


def test_gfs_seamless_is_the_exact_gfs_alias_not_an_independent_prediction(tmp_path):
    s = store(tmp_path, [point()]).state(DAY, [{"time": instant(17,53), "temperature_f": 82}])
    gfs, alias = model(s, "gfs"), model(s, "gfs_seamless")
    assert alias["alias_of"] == "gfs"
    assert alias["points"] == gfs["points"]
    assert alias["metrics"] == gfs["metrics"]
    assert gfs["metrics"]["mae_f"] == pytest.approx(2)
    assert gfs["metrics"]["matched_points"] == 1


@pytest.mark.parametrize("received", [instant(18), instant(18,1), instant(19)])
def test_late_or_equal_time_capture_never_scores_already_elapsed_forecast(tmp_path, received):
    s = store(tmp_path, [point(received=received)]).state(DAY, [{"time": instant(18,10), "temperature_f": 82}])
    assert model(s,"gfs")["metrics"]["matched_points"] == 0
    assert model(s,"gfs")["metrics"]["mae_f"] is None


def test_observation_before_actual_collection_cannot_be_scored(tmp_path):
    s = store(tmp_path, [point(received=instant(17,58))]).state(DAY, [{"time": instant(17,53), "temperature_f":82}])
    assert model(s,"gfs")["metrics"]["matched_points"] == 0


def test_future_observations_and_forecast_valid_times_do_not_score(tmp_path):
    value = store(tmp_path, [point(lead=21)], now=datetime(2026,10,1,20,tzinfo=timezone.utc))
    s=value.state(DAY,[{"time":instant(20,53),"temperature_f":82}])
    assert model(s,"gfs")["metrics"]["mae_f"] is None
    assert model(s,"gfs")["points"][0]["accuracy_eligible"] is False


@pytest.mark.parametrize("observation,expected", [(instant(17,30),1), (instant(17,29),0), (instant(18,30),1), (instant(18,31),0)])
def test_fixed_thirty_minute_matching_window(tmp_path, observation, expected):
    s=store(tmp_path,[point()]).state(DAY,[{"time":observation,"temperature_f":81}])
    assert model(s,"gfs")["metrics"]["matched_points"] == expected


def test_equal_distance_tie_uses_earlier_report(tmp_path):
    s=store(tmp_path,[point()]).state(DAY,[{"time":instant(18,20),"temperature_f":70},
                                         {"time":instant(17,40),"temperature_f":84}])
    assert model(s,"gfs")["metrics"]["mae_f"] == 4


@pytest.mark.parametrize("bad", [float("nan"),float("inf"),None,"missing",161,-101])
def test_invalid_observations_do_not_become_zero_error_matches(tmp_path,bad):
    s=store(tmp_path,[point()]).state(DAY,[{"time":instant(17,53),"temperature_f":bad}])
    assert model(s,"gfs")["metrics"]["mae_f"] is None


def test_shared_mae_uses_identical_observation_truth_across_families(tmp_path):
    points=[point("gfs",temperature=79),point("nam",temperature=89,received=instant(17,58)),
            point("nbm",temperature=78)]
    s=store(tmp_path,points).state(DAY,[{"time":instant(17,53),"temperature_f":80},
                                      {"time":instant(18,20),"temperature_f":90}])
    # A legitimate conservative implementation drops this unmatched report pair.
    # A joint matcher can retain it only by sharing the later report (90F).
    if s["shared_matched_points"] == 0:
        assert all(m["metrics"]["shared_mae_f"] is None for m in s["models"])
    else:
        assert s["shared_matched_points"] == 1
        assert model(s,"gfs")["metrics"]["shared_mae_f"] == 11
        assert model(s,"nam")["metrics"]["shared_mae_f"] == 1
        assert model(s,"nbm")["metrics"]["shared_mae_f"] == 12


def test_earliest_saved_receipt_is_retained_after_later_refresh(tmp_path):
    value=store(tmp_path,[point(temperature=79,received=instant(16))])
    value._days[DAY].append({"date":DAY,"points":[point(temperature=90,received=instant(19))],
                             "failures":[],"created_at_utc":instant(19)})
    s=value.state(DAY,[{"time":instant(17,53),"temperature_f":80}])
    assert model(s,"gfs")["points"][0]["temperature_f"] == 79
    assert model(s,"gfs")["metrics"]["mae_f"] == 1


def test_local_day_leads_include_dst_boundaries_and_fixed_cycle():
    assert len(c.local_leads("2026-11-01",1)) == 25
    assert len(c.local_leads("2026-03-08",1)) == 23
    assert c.local_leads(DAY,3) == (9,12,15,18,21,24,27,30)
    assert "/gfs.20261001/00/" in c.source_url("gfs",DAY,18)


@pytest.mark.parametrize("url", ["http://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261001/00/atmos/gfs.t00z.pgrb2.0p25.f018",
                                 "https://user:secret@noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261001/00/atmos/gfs.t00z.pgrb2.0p25.f018",
                                 "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261001/12/atmos/gfs.t12z.pgrb2.0p25.f018",
                                 "https://example.com/model.grib2",
                                 "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261001/00/atmos/gfs.t00z.pgrb2.0p25.f049",
                                 "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20261001/00/atmos/gfs.t00z.pgrb2.0p25.f018?token=secret"])
def test_source_allowlist_rejects_credentials_external_hosts_and_unbounded_cycles(url):
    with pytest.raises(ValueError):
        c.validate_url(url)


@pytest.mark.parametrize("field", ["TMAX:2 m above ground:12-18 hour max fcst",
                                   "TMP:surface:18 hour fcst", "TMP:2 m above ground:18 hour ave fcst",
                                   "TMP:2 m above ground:18 hour fcst:90% level"])
def test_index_rejects_interval_non_two_meter_and_probabilistic_fields(field):
    text=f"1:0:d=2026100100:{field}:\n2:100:d=2026100100:OTHER:surface:18 hour fcst:\n"
    with pytest.raises(ValueError):
        c.exact_temperature_range(text,18)


def test_index_requires_unique_instantaneous_field_and_increasing_offsets():
    valid="1:0:d=2026100100:TMP:2 m above ground:18 hour fcst:\n2:100:d=2026100100:OTHER:surface:18 hour fcst:\n"
    assert c.exact_temperature_range(valid,18)[:2] == (0,99)
    duplicate=valid+"3:200:d=2026100100:TMP:2 m above ground:18 hour fcst:\n4:300:d=2026100100:OTHER:surface:18 hour fcst:\n"
    with pytest.raises(ValueError):
        c.exact_temperature_range(duplicate,18)
    with pytest.raises(ValueError):
        c.exact_temperature_range(valid.replace("2:100:","2:0:"),18)


def test_forecast_must_be_committed_before_observation_not_just_downloaded(tmp_path):
    value=store(tmp_path,[point(received=instant(16))])
    value._days[DAY][0]["created_at_utc"]=instant(19)
    s=value.state(DAY,[{"time":instant(17,53),"temperature_f":82}])
    assert model(s,"gfs")["metrics"]["matched_points"] == 0


def small_grib(changes=None):
    import eccodes
    handle=eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        settings=[("Ni",2),("Nj",2),("latitudeOfFirstGridPointInDegrees",34.),
                  ("latitudeOfLastGridPointInDegrees",33.5),("longitudeOfFirstGridPointInDegrees",241.5),
                  ("longitudeOfLastGridPointInDegrees",242.),("iDirectionIncrementInDegrees",.5),
                  ("jDirectionIncrementInDegrees",.5),("dataDate",20261001),("dataTime",0),
                  ("shortName","2t"),("forecastTime",18)]
        for key,value in settings:
            eccodes.codes_set(handle,key,value)
        for key,value in (changes or {}).items():
            eccodes.codes_set(handle,key,value)
        eccodes.codes_set_values(handle,[299.,300.,298.,297.])
        return eccodes.codes_get_message(handle)
    finally:
        eccodes.codes_release(handle)


@pytest.mark.parametrize("changes", [{"dataDate":20261002},{"shortName":"2d"},{"level":10},
                                     {"forecastTime":21},{"stepType":"avg"}])
def test_actual_grib_decoder_rejects_wrong_cycle_field_level_lead_or_interval(changes):
    with pytest.raises(ValueError,match="exact"):
        c.decode_temperature(small_grib(changes),"gfs",DAY,18)


@pytest.fixture
def cached_grib(tmp_path):
    from v10_online.storage import hash_file,write_immutable
    folder=tmp_path/c.RUN/DAY/"fixture"
    raw=folder/"raw"
    raw.mkdir(parents=True)
    field=small_grib()
    index=(f"1:0:d=2026100100:TMP:2 m above ground:18 hour fcst:\n"
           f"2:{len(field)}:d=2026100100:OTHER:surface:18 hour fcst:\n").encode()
    url=c.source_url("gfs",DAY,18)
    bindings={}
    for name,body,is_field in [("index.raw",index,False),("field.raw",field,True)]:
        path=raw/name
        path.write_bytes(body)
        headers={"content-range":f"bytes 0-{len(field)-1}/{len(field)+100}"} if is_field else {}
        receipt=write_immutable(raw/(name+".json"),{
            "url":url if is_field else url+".idx","method":"GET","credentials_used":False,
            "status_code":206 if is_field else 200,"retrieved_at_utc":instant(16),
            "headers":headers,"request_headers":{"Range":f"bytes=0-{len(field)-1}"} if is_field else {},
            "sha256":sha256(body).hexdigest(),"raw_path":name,"bytes":len(body)})
        bindings[path.relative_to(tmp_path).as_posix()]=hash_file(path)
        rp=raw/(name+".json")
        bindings[rp.relative_to(tmp_path).as_posix()]=hash_file(rp)
    p=c.decode_temperature(field,"gfs",DAY,18)
    p.update(source_url=url,source_index_url=url+".idx",source_index_line=index.decode().splitlines()[0],
             source_range_start=0,source_range_end=len(field)-1,source_sha256=sha256(field).hexdigest(),
             index_sha256=sha256(index).hexdigest(),source_bytes=len(field),retrieved_at_utc=instant(16))
    capture=write_immutable(folder/"capture.json",{"date":DAY,"points":[p],"failures":[],
                            "created_at_utc":instant(16,1),"raw_bindings":bindings,"orders":0})
    return tmp_path,folder,capture


def test_real_grib_cache_reloads_and_scores_original_bound_value(cached_grib):
    root,_,_=cached_grib
    value=c.ComparisonStore(root,now=lambda:datetime(2026,10,1,20,tzinfo=timezone.utc))
    s=value.state(DAY,[{"time":instant(17,53),"temperature_f":80}])
    assert model(s,"gfs")["points"][0]["raw_value_kelvin"] == 299
    assert model(s,"gfs")["metrics"]["mae_f"] == pytest.approx(1.47)


@pytest.mark.parametrize("mutation", ["earlier_retrieval","temperature","grid_location","source_index",
                                      "source_range","units","created_future"])
def test_resealed_normalized_metadata_cannot_override_original_receipts_or_grib(cached_grib,mutation):
    from v10_online.storage import seal
    root,folder,capture=cached_grib
    p=capture["points"][0]
    if mutation=="earlier_retrieval":
        p["retrieved_at_utc"]=instant(15)
    elif mutation=="temperature":
        p["raw_value_kelvin"]=300
        p["temperature_f"]=(300-273.15)*1.8+32
    elif mutation=="grid_location":
        p["grid_latitude"]=33.5
    elif mutation=="source_index":
        p["source_index_line"]=p["source_index_line"].replace("18 hour","21 hour")
    elif mutation=="source_range":
        p["source_range_end"]-=1
    elif mutation=="units":
        p["units"]="degC"
    elif mutation=="created_future":
        capture["created_at_utc"]=instant(21)
    (folder/"capture.json").write_text(json.dumps(seal(capture)),encoding="utf-8")
    with pytest.raises(ValueError):
        c.ComparisonStore(root,now=lambda:datetime(2026,10,1,20,tzinfo=timezone.utc))


def test_modified_archived_source_bytes_fail_before_comparison_is_displayed(cached_grib):
    root,folder,_=cached_grib
    (folder/"raw/field.raw").write_bytes(b"corrupted")
    with pytest.raises(ValueError,match="binding"):
        c.ComparisonStore(root,now=lambda:datetime(2026,10,1,20,tzinfo=timezone.utc))
