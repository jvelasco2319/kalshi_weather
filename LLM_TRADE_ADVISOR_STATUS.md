# LLM Trade Advisor Status

Canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`

## Quality Gates

- Tests passed: yes (`python -m pytest`: 204 passed, 2 warnings)
- Ruff passed: yes (`python -m ruff check .`: all checks passed)
- CLI help passed: yes (`python -m kalshi_weather.cli --help`)
- Live trading enabled: false
- Live order endpoint present: false
- Fake-money only: true

## Implementation

- Advisor schema implemented: yes
- Trade quality score implemented: yes
- Prompt file implemented: yes
- Rule-based advisor implemented: yes
- Prompt-only advisor implemented: yes
- Optional `llm_json` adapter implemented: yes, fail-closed unless configured
- Hard risk validator implemented: yes
- Model-race advisor integration implemented: yes
- Advisor dry-run implemented: yes
- Advisor synthetic test implemented: yes
- Advisor decision report implemented: yes
- Training export implemented: yes

## Latest Advisor Synthetic Test

- Command: `python -m kalshi_weather.cli advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch`
- Result: 15 passed, 0 failed
- Network used: false
- Live trading enabled: false

## Latest Advisor Smoke Run

- Command: `python -m kalshi_weather.cli paper-model-race-run --series KXHIGHLAX --station KLAX --race-id advisor_smoke --starting-cash-per-model 1000 --race-mode independent --advisor-mode rule_based --entry-interval-seconds 300 --exit-interval-seconds 30 --max-entry-iterations 1 --max-exit-iterations 2 --max-open-positions-per-model 1 --cooldown-after-stop-minutes 30 --max-risk-per-trade 15 --force-flat-at-end`
- Result: completed fake-money only
- Advisor decisions logged for race `advisor_smoke`: yes
- Latest `advisor_smoke` decision count: 46
- Real orders placed: false
- Summary: advisor recommended WAIT during the smoke run because trade quality stayed below the buy threshold or signals needed confirmation.

## Commands Run

- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `python -m kalshi_weather.cli advisor-synthetic-test --advisor-mode rule_based --fail-on-mismatch`
- `python -m kalshi_weather.cli advisor-dry-run --series KXHIGHLAX --station KLAX --advisor-mode rule_based --json --output reports\llm_trade_advisor\latest_advisor_dry_run.json`
- `python -m kalshi_weather.cli paper-model-race-run --series KXHIGHLAX --station KLAX --race-id advisor_smoke --starting-cash-per-model 1000 --race-mode independent --advisor-mode rule_based --entry-interval-seconds 300 --exit-interval-seconds 30 --max-entry-iterations 1 --max-exit-iterations 2 --max-open-positions-per-model 1 --cooldown-after-stop-minutes 30 --max-risk-per-trade 15 --force-flat-at-end`
- `python -m kalshi_weather.cli advisor-decision-report --race-id advisor_smoke`
- `python -m kalshi_weather.cli advisor-export-training-examples --race-id advisor_smoke --output-dir reports\llm_trade_advisor\training_examples`
- Safety search from the package prompt

## Known Limitations

- Advisor mode makes fake-money trades more selective but does not prove profitability.
- The optional `llm_json` provider is intentionally fail-closed unless a local response JSON path is configured.
- Current dry-run smoke disabled direct NOAA via environment variables to avoid slow Herbie/cfgrib fetches; NOAA model-race usage remains available through existing model settings.
- The smoke run did not open fake positions because confirmed-edge criteria were not met during the run.

## Next Recommended Work

- Run advisor mode for several full fake-money market days.
- Fetch official outcomes after settlement and score advisor decisions against final highs.
- Review exported JSONL training examples before tuning the prompt or thresholds.
- Keep all real-money/live-order work out of scope unless a separate reviewed package is created.
