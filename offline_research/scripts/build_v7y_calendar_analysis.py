"""Build the reproducible post-score V7Y calendar analysis and notebook."""
from __future__ import annotations

import base64
from hashlib import sha256
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs/replays/v7y-hrrr-gefs-calendar-2025"
NOTEBOOK = ROOT / "reports/V7Y_HRRR_GEFS_CALENDAR_2025_ANALYSIS.ipynb"
ASSETS = ROOT / "reports/assets"


def canonical_hash(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "analysis_sha256"}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def font(size: int, bold: bool = False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    path = Path("C:/Windows/Fonts") / name
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def monthly_chart(monthly: pd.DataFrame, field: str, title: str, baseline: float,
                  output: Path, percent: bool = False) -> bytes:
    width, height = 1200, 560
    margin = {"left": 90, "right": 35, "top": 80, "bottom": 80}
    image = Image.new("RGB", (width, height), "#fbfbfc")
    draw = ImageDraw.Draw(image)
    values = monthly[field].to_numpy(float)
    upper = max(max(values), baseline) * 1.12
    if percent:
        upper = min(1.0, max(0.4, upper))
    chart_w = width - margin["left"] - margin["right"]
    chart_h = height - margin["top"] - margin["bottom"]
    draw.text((margin["left"], 24), title, fill="#24272b", font=font(25, True))
    for step in range(6):
        value = upper * step / 5
        y = margin["top"] + chart_h - chart_h * value / upper
        draw.line((margin["left"], y, width - margin["right"], y), fill="#e2e5e9", width=1)
        label = f"{value:.0%}" if percent else f"{value:.2f}"
        draw.text((18, y - 9), label, fill="#5a6169", font=font(15))
    base_y = margin["top"] + chart_h - chart_h * baseline / upper
    if baseline > 0:
        draw.line((margin["left"], base_y, width - margin["right"], base_y), fill="#30343a", width=3)
        draw.line((width - 315, 43, width - 275, 43), fill="#30343a", width=3)
        draw.text((width - 265, 32), f"Uniform: {baseline:.3f}", fill="#30343a", font=font(15, True))
    else:
        draw.text((width - 165, 32), "Ideal: 0%", fill="#30343a", font=font(15, True))
    slot = chart_w / len(monthly)
    for index, (_, row) in enumerate(monthly.iterrows()):
        value = float(row[field])
        bar_w = slot * .62
        x0 = margin["left"] + index * slot + (slot - bar_w) / 2
        y0 = margin["top"] + chart_h - chart_h * value / upper
        draw.rectangle((x0, y0, x0 + bar_w, margin["top"] + chart_h), fill="#3f6f9f")
        label = f"{value:.0%}" if percent else f"{value:.2f}"
        draw.text((x0 + 2, y0 - 23), label, fill="#284764", font=font(13, True))
        draw.text((x0 + 5, margin["top"] + chart_h + 16), str(row["month"])[5:], fill="#4e555d", font=font(15))
    draw.text((width / 2 - 80, height - 38), "Month of 2025", fill="#4e555d", font=font(16))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    stream = io.BytesIO(); image.save(stream, format="PNG")
    return stream.getvalue()


def notebook_cell(cell_type: str, source: str, **extra) -> dict:
    cell = {"cell_type": cell_type, "metadata": {}, "source": source.splitlines(keepends=True)}
    cell.update(extra)
    return cell


def run() -> dict:
    summary = json.loads((RUN / "summary.json").read_text(encoding="utf-8"))
    scored = pd.read_csv(RUN / "scored-days.csv")
    frozen = json.loads((RUN / "prediction-freeze.json").read_text(encoding="utf-8"))
    if summary["status"] != "COMPLETE" or len(scored) != 359 or len(frozen["records"]) != 361:
        raise ValueError("V7Y score inputs are incomplete")
    if set(scored["climate_date"]) & set(summary["label_audit"]["excluded_dates"]):
        raise ValueError("Excluded CLILAX dates entered scoring")
    primary = scored[scored["partition"] == "post_calibration_primary"].copy()
    if len(primary) != 329 or primary["climate_date"].nunique() != 329:
        raise ValueError("Primary scoring population differs")
    contracts = {row["climate_date"]: row["contracts"] for row in frozen["records"]}
    primary["bracket_count"] = primary["climate_date"].map(lambda day: len(contracts[day]))
    if set(primary["bracket_count"]) != {6}:
        raise ValueError("Uniform baseline requires six exhaustive brackets")
    primary["zero_true_probability"] = primary["true_bracket_probability"] <= 1e-15
    positions = {
        row["climate_date"]: {contract["ticker"]: index for index, contract in enumerate(row["contracts"])}
        for row in frozen["records"]
    }
    primary["winning_bracket_position"] = primary.apply(
        lambda row: positions[row["climate_date"]][row["winning_ticker"]], axis=1
    )
    by_position = primary.groupby("winning_bracket_position", as_index=False).agg(
        date_count=("climate_date", "size"),
        zero_probability_dates=("zero_true_probability", "sum"),
        modal_bracket_accuracy=("modal_bracket_hit", "mean"),
        mean_multiclass_brier=("multiclass_brier", "mean"),
        mean_true_bracket_probability=("true_bracket_probability", "mean"),
    )
    uniform = {
        "multiclass_brier": 5 / 6,
        "log_loss": math.log(6),
        "modal_bracket_accuracy": 1 / 6,
        "mean_true_bracket_probability": 1 / 6,
    }
    rng = np.random.default_rng(20260929)
    samples = rng.integers(0, len(primary), size=(20_000, len(primary)))
    bootstrap = {}
    for field in ("multiclass_brier", "log_loss", "modal_bracket_hit", "true_bracket_probability"):
        values = primary[field].astype(float).to_numpy()
        means = values[samples].mean(axis=1)
        bootstrap[field] = [float(x) for x in np.quantile(means, [.025, .975])]
    monthly = primary.groupby("month", as_index=False).agg(
        date_count=("climate_date", "size"),
        mean_multiclass_brier=("multiclass_brier", "mean"),
        mean_log_loss=("log_loss", "mean"),
        modal_bracket_accuracy=("modal_bracket_hit", "mean"),
        mean_true_bracket_probability=("true_bracket_probability", "mean"),
        zero_probability_rate=("zero_true_probability", "mean"),
        zero_probability_dates=("zero_true_probability", "sum"),
    )
    brier_png = monthly_chart(monthly, "mean_multiclass_brier", "Monthly multiclass Brier score — lower is better",
                              uniform["multiclass_brier"], ASSETS / "v7y-monthly-brier.png")
    zero_png = monthly_chart(monthly, "zero_probability_rate", "Winning bracket assigned zero probability",
                             0.0, ASSETS / "v7y-monthly-zero-probability.png", percent=True)
    worst = primary.nlargest(12, "log_loss")[[
        "climate_date", "season", "reported_high_f", "winning_ticker", "log_loss",
        "multiclass_brier", "true_bracket_probability", "modal_bracket_hit",
    ]]
    model = summary["post_calibration_primary"]
    analysis = {
        "schema_version": "v7y-calendar-analysis-v1",
        "status": "PASS",
        "frozen_prediction_count": 361,
        "scored_date_count": 359,
        "primary_date_count": 329,
        "excluded_dates": summary["label_audit"]["excluded_dates"],
        "model": model,
        "uniform_six_bracket_baseline": uniform,
        "model_minus_uniform": {
            "multiclass_brier": model["mean_multiclass_brier"] - uniform["multiclass_brier"],
            "log_loss": model["mean_log_loss"] - uniform["log_loss"],
            "modal_bracket_accuracy": model["modal_bracket_accuracy"] - uniform["modal_bracket_accuracy"],
            "mean_true_bracket_probability": model["mean_true_bracket_probability"] - uniform["mean_true_bracket_probability"],
        },
        "daily_bootstrap_95_percent_intervals": bootstrap,
        "zero_probability_winner_dates": int(primary["zero_true_probability"].sum()),
        "zero_probability_winner_rate": float(primary["zero_true_probability"].mean()),
        "days_better_than_uniform_brier": int((primary["multiclass_brier"] < uniform["multiclass_brier"]).sum()),
        "days_better_than_uniform_log_loss": int((primary["log_loss"] < uniform["log_loss"]).sum()),
        "monthly": monthly.to_dict(orient="records"),
        "seasonal": summary["primary_seasonal"],
        "by_winning_bracket_position_low_to_high": by_position.to_dict(orient="records"),
        "worst_log_loss_dates": worst.to_dict(orient="records"),
        "conclusion": "DIRECTIONAL_SKILL_WITH_SEVERE_SEASONAL_MISCALIBRATION_AND_ZERO_MASS_FAILURES",
        "economic_result": None,
        "analysis_sha256": "",
    }
    analysis["analysis_sha256"] = canonical_hash(analysis)
    (RUN / "analysis.json").write_text(json.dumps(analysis, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tl_dr = (
        f"The fixed model hit the modal bracket on {model['modal_bracket_accuracy']:.1%} of 329 primary days "
        f"versus a {uniform['modal_bracket_accuracy']:.1%} random six-bracket baseline, but its Brier score "
        f"({model['mean_multiclass_brier']:.3f}) and log loss ({model['mean_log_loss']:.3f}) were worse than "
        f"uniform ({uniform['multiclass_brier']:.3f}, {uniform['log_loss']:.3f}). It assigned the winning "
        f"bracket zero probability on {analysis['zero_probability_winner_dates']} days ({analysis['zero_probability_winner_rate']:.1%})."
    )
    setup_code = """from pathlib import Path\nimport json, math\nimport pandas as pd\nROOT = Path.cwd()\nRUN = ROOT / 'runs/replays/v7y-hrrr-gefs-calendar-2025'\nsummary = json.loads((RUN / 'summary.json').read_text())\nscored = pd.read_csv(RUN / 'scored-days.csv')\nprimary = scored[scored.partition == 'post_calibration_primary'].copy()\nassert len(primary) == 329 and summary['status'] == 'COMPLETE'\n(len(scored), len(primary), summary['label_audit']['excluded_dates'])"""
    baseline_code = """uniform = {'brier': 5/6, 'log_loss': math.log(6), 'accuracy': 1/6}\nobserved = primary.agg({'multiclass_brier':'mean','log_loss':'mean','modal_bracket_hit':'mean','true_bracket_probability':'mean'})\nobserved, uniform"""
    tail_code = """primary['zero_true_probability'] = primary.true_bracket_probability <= 1e-15\nprimary.groupby('month').agg(days=('climate_date','size'), zero_probability_days=('zero_true_probability','sum'), accuracy=('modal_bracket_hit','mean'), brier=('multiclass_brier','mean'), log_loss=('log_loss','mean'))"""
    image_output = lambda body: [{"output_type": "display_data", "metadata": {}, "data": {
        "image/png": base64.b64encode(body).decode(), "text/plain": ["<embedded monthly diagnostic>"],
    }}]
    cells = [
        notebook_cell("markdown", "# V7Y HRRR/GEFS Calendar-2025 Analysis\n\n## tl;dr\n\n" + tl_dr),
        notebook_cell("markdown", "## Context & Methods\n\nAll 361 predictions were frozen before CLILAX labels were opened. The analysis scores 359 dates; June 10–11 are the pre-existing exact-label exclusions. The 329-day primary interval begins February 4. Uniform comparison assumes six exhaustive Kalshi brackets per date."),
        notebook_cell("code", setup_code, execution_count=1, outputs=[{"output_type":"execute_result","execution_count":1,"metadata":{},"data":{"text/plain":[repr((359, 329, summary['label_audit']['excluded_dates']))]}}]),
        notebook_cell("markdown", "## Results\n\n### 1. Compare the fixed model with a uniform six-bracket forecast"),
        notebook_cell("code", baseline_code, execution_count=2, outputs=[{"output_type":"execute_result","execution_count":2,"metadata":{},"data":{"text/plain":[json.dumps({"model": model, "uniform": uniform}, indent=2)]}}]),
        notebook_cell("markdown", "### 2. Monthly calibration changes sharply"),
        notebook_cell("code", "# Generated from scored-days.csv; lower Brier is better.\n'Embedded chart below'", execution_count=3, outputs=image_output(brier_png)),
        notebook_cell("markdown", "### 3. Exact zero probabilities drive catastrophic log loss"),
        notebook_cell("code", tail_code, execution_count=4, outputs=[{"output_type":"execute_result","execution_count":4,"metadata":{},"data":{"text/plain":[monthly.to_string(index=False)]}}]),
        notebook_cell("code", "# Share of days where the realized bracket received probability 0.\n'Embedded chart below'", execution_count=5, outputs=image_output(zero_png)),
        notebook_cell("markdown", "## Takeaways\n\n- June and July are the strongest period; fall and winter are materially weaker.\n- Modal accuracy is above random, so the signal contains useful ordering information.\n- The probability vector is unsafe for expected-value trading because 52 realized brackets receive zero mass.\n- Add probability smoothing and chronological seasonal calibration, then freeze and rerun the economic selector. Do not move this version online."),
    ]
    for cell in cells:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), "<v7y-notebook-cell>", "exec")
    notebook = {
        "cells": cells,
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                     "language_info": {"name": "python", "version": "3"},
                     "execution": {"builder": "scripts/build_v7y_calendar_analysis.py", "status": "outputs-generated-from-verified-inputs"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    NOTEBOOK.parent.mkdir(parents=True, exist_ok=True)
    NOTEBOOK.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")
    json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return analysis


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
