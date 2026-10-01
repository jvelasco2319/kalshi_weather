import json
from pathlib import Path

import pytest

from klax_lab.provenance import canonical_hash, sha256_file
from v4.dataset_v4 import (
    BUNDLE, DATASET_COMPONENT, DECISION_TIMES_UTC, DECISION_TIME_AMENDMENT,
    FOLD_COMPONENT, V4DatasetError, _immutable_json, verify_v4_dataset,
)


ROOT = Path(__file__).resolve().parents[2]


def test_frozen_v4_dataset_has_exact_new_grid_and_bound_components() -> None:
    verified = verify_v4_dataset(ROOT)
    assert verified["dataset_id"] == (
        "4d91f524c0aff05135d67b01d26835df6325c28ff6a30e09ae368d2437c0614e"
    )
    assert verified["decision_times_utc"] == list(DECISION_TIMES_UTC)
    assert verified["weather_training_feature_rows"] == 1095
    assert verified["calibration_feature_rows"] == 90
    assert verified["evaluation_feature_rows"] == 435
    assert verified["as_of_revalidated"] is True
    assert verified["label_separation_revalidated"] is True
    assert verified["protected_final_read"] is False

    dataset = json.loads((ROOT / DATASET_COMPONENT).read_text(encoding="utf-8"))
    folds = json.loads((ROOT / FOLD_COMPONENT).read_text(encoding="utf-8"))
    bundle = json.loads((ROOT / BUNDLE).read_text(encoding="utf-8"))
    amendment = json.loads((ROOT / DECISION_TIME_AMENDMENT).read_text(
        encoding="utf-8"))
    amendment_body = {key: value for key, value in amendment.items()
                      if key != "registration_sha256"}
    bundle_body = {key: value for key, value in bundle.items()
                   if key != "bundle_sha256"}

    assert amendment["registration_sha256"] == canonical_hash(amendment_body)
    assert amendment["later_retiming_permitted"] is False
    assert amendment["alternate_minute_fallback_permitted"] is False
    assert dataset["dataset_id"] == folds["dataset_id"] == bundle["dataset_id"]
    assert dataset["decision_times_utc"] == list(DECISION_TIMES_UTC)
    assert bundle["bundle_sha256"] == canonical_hash(bundle_body)
    assert bundle["dataset_component"]["sha256"] == sha256_file(
        ROOT / DATASET_COMPONENT)
    assert bundle["fold_component"]["sha256"] == sha256_file(ROOT / FOLD_COMPONENT)
    assert bundle["ready_for_v4_campaign"] is False
    assert bundle["protected_final_read"] is False
    assert bundle["decision_time_amendment"]["audit_artifact_sha256"] == (
        "2e1cd4e6a9863d561c0f027a5a99f32e5911c384f72c6caadcd632bd5b12d92d"
    )


def test_v4_manifest_publisher_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    _immutable_json(path, {"value": 1})
    _immutable_json(path, {"value": 1})
    with pytest.raises(V4DatasetError):
        _immutable_json(path, {"value": 2})

