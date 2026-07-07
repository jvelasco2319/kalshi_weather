# Canonical Output Paths

Trading, debug, journal, and archive artifacts use one canonical location.

## Roots

Canonical repo root:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather
```

Canonical artifact root:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent
```

Canonical debug root:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\debug
```

Canonical archive root:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\archives
```

Every run lives under:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\debug\<run_id>
```

Archives live under:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\archives
```

## Files To Review

Each run folder may contain:

- `latest.json`
- `decisions.jsonl`
- `candidates.csv`
- `diagnostic.sqlite`
- `terminal_output.txt`
- `market_lifecycle.jsonl`
- `profile_decisions.jsonl`
- `settlement_scenarios.json`
- `settlement_report.json`
- `paper_settlement_report.json`
- `clv_report.json`
- `final_results.json`
- `bot_trust_report.json`
- `run_metadata.json`
- `effective_config.json`
- `pytest_output.txt`

The easiest review artifact is the generated complete ZIP package. It includes
the raw debug files, journal, final results, trust report, settlement reports,
CLV report, configs, metadata, and a `package_manifest.json` when those files
exist.

Preferred command for the latest run:

```powershell
.\scripts\package_debug_run.ps1 -Latest
```

Preferred command for a specific run:

```powershell
.\scripts\package_debug_run.ps1 -RunId "<run_id>"
```

The resulting ZIP is written under:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\archives
```

It is named:

```text
<run_id>_complete_review_package_<timestamp>.zip
```

That ZIP is the only file needed for outside review.

The latest-run pointer is:

```text
C:\Users\jarve\Documents\Codex\kalshi_weather\reports\trader_agent\debug\_LATEST_RUN.txt
```

It records the current `run_id`, `run_dir`, `journal_path`, and UTC timestamps.

## Safety

These paths are for fake-money research artifacts only. The trader commands remain fake-money-only and do not add real Kalshi order placement.
