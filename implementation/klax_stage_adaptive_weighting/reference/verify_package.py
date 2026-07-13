from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    'README.md',
    'CODEX_MASTER_PROMPT.md',
    'CODEX_TASK_GRAPH.yaml',
    'config/stage_adaptive_weights.shadow.yaml',
    'docs/02_STAGE_DEFINITIONS_AND_PRIORS.md',
    'docs/03_WEIGHTING_MATH.md',
    'docs/06_PROBABILITY_LAB_UI.md',
    'docs/07_ACCEPTANCE_TESTS.md',
    'contracts/stage_weight_snapshot.schema.json',
    'fixtures/stage_weight_snapshot.example.json',
    'reference/stage_weight_reference.py',
    'reference/test_stage_weight_reference.py',
    'ui_reference/approved_probability_lab_exact.html',
    'evidence/klax_model_metrics_by_market_stage.csv',
    'evidence/klax_model_metrics_common_window.csv',
]


def main() -> int:
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        raise SystemExit(f'Missing required files: {missing}')

    config = yaml.safe_load((ROOT/'config/stage_adaptive_weights.shadow.yaml').read_text())
    assert config['safety']['order_submission_reachable'] is False
    assert config['safety']['live_trading_enabled'] is False
    assert config['primary_shadow_weighting_mode'] == 'stage_reliability'
    models = set(config['models']['canonical_order'])
    assert models == {'ecmwf_ifs','gfs013','gfs_seamless','nam','nbm'}
    for stage, priors in config['stage_priors'].items():
        assert set(priors) == models
        assert abs(sum(priors.values()) - 1.0) < 1e-12, stage
        assert priors['gfs013'] + priors['gfs_seamless'] <= .45 + 1e-12

    schema = json.loads((ROOT/'contracts/stage_weight_snapshot.schema.json').read_text())
    fixture = json.loads((ROOT/'fixtures/stage_weight_snapshot.example.json').read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(fixture)
    assert abs(sum(m['finalWeight'] for m in fixture['models']) - 1.0) < 1e-9
    assert fixture['familyTotals']['GFS'] <= .45 + 1e-9

    html = (ROOT/'ui_reference/approved_probability_lab_exact.html').read_text(encoding='utf-8')
    assert 'KLAX Signal Room' in html and 'Probability Lab' in html
    assert 'https://cdn' not in html and '<script src="http' not in html

    cmd = [sys.executable, '-m', 'pytest', '-q', str(ROOT/'reference/test_stage_weight_reference.py')]
    completed = subprocess.run(cmd, cwd=ROOT/'reference', capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stdout)
        print(completed.stderr, file=sys.stderr)
        return completed.returncode

    print(completed.stdout.strip())
    print('Package verification passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
