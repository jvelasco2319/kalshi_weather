# Kalshi History Charts Status

Generated: 2026-06-20T18:16:41.0372484-07:00

## Summary

- Canonical directory: C:\Users\jarve\Documents\Codex\kalshi_weather
- Tests passed: yes (108 passed; final output captured in esults_for_chatgpt/COMMAND_OUTPUTS/pytest.txt)
- Ruff passed: yes
- CLI help passed: yes
- Live trading enabled: false
- Live order endpoint present: false
- Kalshi candlestick client implemented: yes
- Historical markets client implemented: yes
- History discovery implemented: yes
- History backfill implemented: yes
- Trend table implemented: yes
- Chart generation implemented: yes
- Dashboard implemented: yes
- Microtrade trend replay implemented: yes
- Storage tables created: yes (kalshi_candlesticks, kalshi_trend_artifacts)

## Latest Live Read-Only Run

- Series: KXHIGHLAX
- Station: KLAX
- Latest backfill date range: 2026-06-19 to 2026-06-20
- Markets found: 12
- Candles fetched: 7256
- Candles stored in latest run: 7256
- Total stored Kalshi candlesticks: 7256
- Total stored trend artifacts: 17
- Official outcomes count: 1
- Joined outcomes count: 174
- Paper fills count: 0

## Latest Chart Paths

- eports/kalshi_trends/2026-06-20/price_by_bracket.png
- eports/kalshi_trends/2026-06-20/favorite_bracket_over_time.png
- eports/kalshi_trends/2026-06-20/volume_open_interest.png
- eports/kalshi_trends/2026-06-20/model_vs_market.png
- eports/kalshi_trends/2026-06-20/edge_over_time.png
- eports/kalshi_trends/2026-06-20/observed_high_and_model_estimate.png
- eports/kalshi_trends/2026-06-20/microtrade_candidate_windows.png
- Dashboard path: eports/kalshi_trends/2026-06-20/dashboard.html
- Microtrade replay chart: eports/kalshi_trends/2026-06-20/microtrade_replay.png

## Commands Run

- python -m pytest
- python -m ruff check .
- python -m kalshi_weather.cli --help
- python -m pip show kalshi-weather
- kalshi-weather kalshi-history-discover --series KXHIGHLAX --start-date 2026-06-19 --end-date 2026-06-20 --json --output reports/latest_kalshi_history_discover.json
- kalshi-weather kalshi-history-backfill --series KXHIGHLAX --start-date 2026-06-19 --end-date 2026-06-20 --period-interval 1 --store --json --output reports/latest_kalshi_history_backfill.json
- kalshi-weather kalshi-trends --series KXHIGHLAX --station KLAX --date 2026-06-20 --backfill-if-missing
- kalshi-weather kalshi-trend-chart --series KXHIGHLAX --station KLAX --date 2026-06-20 --backfill-if-missing --output-dir reports/kalshi_trends
- kalshi-weather kalshi-trend-dashboard --series KXHIGHLAX --station KLAX --date 2026-06-20 --backfill-if-missing --output-dir reports/kalshi_trends
- kalshi-weather microtrade-trend-replay --series KXHIGHLAX --station KLAX --date 2026-06-20 --chart --output reports/latest_microtrade_trend_replay.json

## Known Limitations

- Kalshi candle replay is approximate and should not be treated as proof of exact fills.
- Model joins use nearest timestamps; missing model predictions leave model/edge fields blank instead of fabricating edges.
- The latest Git status command resolves to a parent-level repository on this machine, so the results package records command output but does not rely on it for project file scope.
- Official outcome coverage remains limited to rows already recorded in SQLite.
- Generated reports include runtime market data, but the upload package intentionally excludes SQLite databases, snapshots, .env, API keys, and private keys.

## Next Recommended Work

- Add more settled historical dates and official outcomes for stronger calibration.
- Validate candlestick endpoint behavior over older archived market windows.
- Add richer dashboard annotations once more outcome/model history exists.
- Keep microtrade replay separate from paper trading unless ChatGPT explicitly requests integration.
