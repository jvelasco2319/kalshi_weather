from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_UI_SHA256 = "79d7fe8a7ab8e445a05f31c43e54f84b940969ab9a296da59614b3c0839c2018"
EXPECTED_MODELS = ["ecmwf_ifs", "gfs013", "gfs_seamless", "nam", "nbm"]

REQUIRED_FILES = [
    "README.md",
    "CODEX_MASTER_PROMPT.md",
    "MANIFEST.json",
    "contracts/explainability_snapshot.schema.json",
    "fixtures/sample_explainability_snapshot.json",
    "docs/01_AUDIT_CHECKLIST.md",
    "docs/02_BACKEND_CONTRACT_AND_WIRING.md",
    "docs/03_UI_IMPLEMENTATION_MAPPING.md",
    "docs/04_ACCEPTANCE_TESTS.md",
    "ui_reference/approved_probability_lab_exact.html",
    "ui_reference/preview.html",
    "ui_reference/README.md",
    "ui_reference/approved_probability_lab.png",
    "ui_reference/APPROVED_UI_SHA256.txt",
    "reference/requirements.txt",
    "reference/verify_package.py",
]

REQUIRED_UI_TEXT = [
    "KLAX Signal Room",
    "Probability Lab",
    "Physical-high scenario distributions",
    "Model contribution ledger",
    "Probability funnel",
    "Equation trace",
    "Bracket probability matrix",
    "Market versus weather probability",
    "Price sensitivity",
    "Calculation and data health",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            fail(f"missing required file: {relative}")

    ui_path = ROOT / "ui_reference/approved_probability_lab_exact.html"
    preview_path = ROOT / "ui_reference/preview.html"
    actual_ui_hash = sha256(ui_path)
    if sha256(preview_path) != EXPECTED_UI_SHA256:
        fail("preview.html does not match the approved UI hash")
    if actual_ui_hash != EXPECTED_UI_SHA256:
        fail(
            "approved UI hash mismatch: "
            f"expected {EXPECTED_UI_SHA256}, got {actual_ui_hash}"
        )

    recorded_hash = (ROOT / "ui_reference/APPROVED_UI_SHA256.txt").read_text(encoding="utf-8").strip()
    if recorded_hash != EXPECTED_UI_SHA256:
        fail(f"recorded UI hash mismatch: {recorded_hash}")

    ui_text = ui_path.read_text(encoding="utf-8")
    for required in REQUIRED_UI_TEXT:
        if required not in ui_text:
            fail(f"approved UI missing required text: {required}")
    if "http://" in ui_text or "https://" in ui_text:
        fail("approved UI contains a remote URL")

    schema = json.loads((ROOT / "contracts/explainability_snapshot.schema.json").read_text(encoding="utf-8"))
    sample = json.loads((ROOT / "fixtures/sample_explainability_snapshot.json").read_text(encoding="utf-8"))

    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as exc:
        print(
            "jsonschema is required: python -m pip install -r reference/requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(sample), key=lambda error: list(error.path))
    if errors:
        for error in errors:
            print(f"schema error at {list(error.path)}: {error.message}", file=sys.stderr)
        return 1

    actual_models = [row["modelKey"] for row in sample["models"]]
    if actual_models != EXPECTED_MODELS:
        fail(f"fixture model order mismatch: {actual_models}")

    for model in sample["models"]:
        temperatures = model["scenarioTemperaturesF"]
        weights = model["scenarioWeights"]
        if len(temperatures) != len(weights):
            fail(f"scenario length mismatch for {model['modelKey']}")
        if weights and abs(sum(weights) - 1.0) > 1e-8:
            fail(f"scenario weights do not sum to 1 for {model['modelKey']}")
        posterior_means = [row["pMeanYes"] for row in model["bracketProbabilities"]]
        if posterior_means and all(value is not None for value in posterior_means):
            if abs(sum(posterior_means) - 1.0) > 1e-8:
                fail(f"posterior means do not sum to 1 for {model['modelKey']}")

    mixture = sample["mixture"]
    if len(mixture["scenarioTemperaturesF"]) != len(mixture["scenarioWeights"]):
        fail("mixture scenario length mismatch")
    if mixture["scenarioWeights"] and abs(sum(mixture["scenarioWeights"]) - 1.0) > 1e-8:
        fail("mixture scenario weights do not sum to 1")

    mixture_means = [row["pMeanYes"] for row in mixture["bracketProbabilities"]]
    if mixture_means and all(value is not None for value in mixture_means):
        if abs(sum(mixture_means) - 1.0) > 1e-8:
            fail("mixture posterior means do not sum to 1")

    for row in mixture["bracketProbabilities"]:
        if all(row[key] is not None for key in ["pTradeYes", "mixtureLowerBoundYes", "weightedComponentLowerBoundYes"]):
            if row["pTradeYes"] > min(row["mixtureLowerBoundYes"], row["weightedComponentLowerBoundYes"]) + 1e-12:
                fail(f"pTradeYes exceeds conservative inputs for {row['marketTicker']}")
        if all(row[key] is not None for key in ["pTradeNo", "mixtureLowerBoundNo", "weightedComponentLowerBoundNo"]):
            if row["pTradeNo"] > min(row["mixtureLowerBoundNo"], row["weightedComponentLowerBoundNo"]) + 1e-12:
                fail(f"pTradeNo exceeds conservative inputs for {row['marketTicker']}")

    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    for relative, expected_hash in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"manifest file missing: {relative}")
        if sha256(path) != expected_hash:
            fail(f"manifest hash mismatch: {relative}")

    print("KLAX Probability Lab live-audit package verification passed")
    print(f"validated {len(REQUIRED_FILES)} required files")
    print("validated exact approved UI hash and required panels")
    print("validated explainability schema fixture and probability invariants")
    print(f"validated {len(manifest['files'])} manifest hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
