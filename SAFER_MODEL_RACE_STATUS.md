# Safer Model Race Status

- Canonical directory: `C:\Users\jarve\Documents\Codex\kalshi_weather`
- Tests passed: yes (`python -m pytest`, 162 passed)
- Ruff passed: yes (`python -m ruff check .`)
- CLI help passed: yes (`python -m kalshi_weather.cli --help`)
- Live trading enabled: false
- Live order endpoint present: false
- Fake-money only: true
- Separate entry/exit intervals implemented: yes
- Exit monitor command implemented: yes
- No-bid entry filter implemented: yes
- Conservative open P/L implemented: yes
- Missing-bid exit handling implemented: yes
- Model spread block implemented: yes
- Outlier block implemented: yes
- Cooldown after stop implemented: yes
- High-price filter implemented: yes
- Manual flatten implemented: yes

## Latest Fake Model Race Summary

The model-race engine now monitors exits on a fast loop while refreshing heavy model estimates only on the entry/model-refresh loop. Missing executable bids produce `open P/L n/a` and `no exit bid`; they do not display positive open profit. New entries can be blocked by no bid, wide spread, model disagreement, outlier status, stop-loss cooldown, penny-contract settings, or high entry price.

Latest bounded fake-money command output showed new entries blocked because model spread was above 4F, with exit-monitor ticks continuing to manage positions. The `safety_test` race was flattened afterward; no open positions remained.

## Commands Run

- `python -m pytest tests\test_model_race.py -q`
- `python -m pytest`
- `python -m ruff check .`
- `python -m kalshi_weather.cli --help`
- `python -m pip show kalshi-weather`
- `kalshi-weather paper-model-race-report --series KXHIGHLAX --station KLAX --race-id default`
- `kalshi-weather paper-model-race-exit-monitor --series KXHIGHLAX --station KLAX --race-id default --interval-seconds 1 --max-iterations 2`
- `kalshi-weather paper-model-race-once --series KXHIGHLAX --station KLAX --race-id default --starting-cash-per-model 100`
- `kalshi-weather paper-model-race-run --series KXHIGHLAX --station KLAX --race-id safety_test --starting-cash-per-model 100 --entry-interval-seconds 900 --exit-interval-seconds 1 --max-entry-iterations 1 --max-exit-iterations 2 --force-flat-at-end`
- `kalshi-weather paper-model-race-flatten --series KXHIGHLAX --station KLAX --race-id safety_test --confirm`
- `rg -n "create-order|orders|real order|live order|KALSHI_ENABLE_REAL_ORDERS|private_key|api_key|trade_api|submit|place_order|CreateOrder|requests.post|httpx.post|submit_order|send_order" src tests README.md docs config scripts .env.example`

## Known Limitations

- Top-of-book size filtering only applies when size fields are present in the probability/orderbook row shape.
- Exit monitor uses latest stored model estimates/probabilities, so users should run an entry/model-refresh tick before relying on exit-only monitoring.
- Interrupted fake races can still leave open fake positions; use `paper-model-race-flatten --race-id <race_id> --confirm`.

## Next Recommended Work

- Collect several full settled days with unique race IDs.
- Review stop-loss cooldown, spread, and high-price thresholds against realized fake fills.
- Add richer orderbook depth/size parsing if Kalshi exposes stable depth quantities in read-only responses.
