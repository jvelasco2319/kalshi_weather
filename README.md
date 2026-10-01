# KLAX Swarm Setup — Offline

This branch is the clean historical-test package for the three retained methods:

1. **V5B** — the original calibrated HRRR/GEFS probabilities plus the strict NO-side trade filter.
2. **V8** — V5B with the frozen six-bracket confusion-matrix probability repair.
3. **V10** — V8 with the frozen KLAX-versus-KDAG pressure-flow adjustment.

It never connects to a current market and never places an order. Every run writes a JSON result and a visual HTML report.

## 1. Clone this branch

```powershell
git clone --branch swarm_setup_offline --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd kalshi_weather
```

Requirements: Windows, Git, and Python 3.11 or newer.

## 2. Set it up — no data required

Run this from the cloned folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1
```

That single command creates the Python environment, verifies V5B/V8/V10, runs the automated tests, loads the bundled 329-date public-derived history, runs all three methods, and creates the graphic. No account, API key, paid subscription, or separate dataset is required.

Open:

- `artifacts\offline\report.html` — visual comparison
- `artifacts\offline\results.json` — complete machine-readable results

The bundled data compares forecast quality only. It contains no historical order-book prices, so trade return is correctly displayed as unavailable.

## 3. Optional: add the fuller local research record

If the full research folder is available on this computer:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1 `
  -ResearchRoot 'C:\Users\darks\Documents\Codex\kalshi\_weather\_llm'
```

If the history is on the shared computer, use its network path:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1 `
  -ResearchRoot '\\192.168.1.193\Kalshi\_weather\_llm'
```

This replaces the bundled input with the richer local research record. The import keeps two evidence sets separate:

- **329 common forecast dates:** exact causal walk-forward V5B, V8, and V10 probabilities.
- **64 execution dates:** historical quote evidence for V5B and V8. V10 is shown as unavailable because its pressure history does not overlap these dates.

The 64-date execution record stays outside this public Git repository. The paid/raw source files and credentials are never copied into Git.

You can also supply an already normalized file:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1 `
  -HistoryFile 'C:\path\to\history.jsonl'
```

To check the program before copying real history:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1 -UseSample
```

## 4. Run all three methods again

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_offline.ps1
```

The browser report opens automatically. Results are saved here:

- `artifacts\offline\report.html` — the graphic
- `artifacts\offline\results.json` — every probability, decision, and score

## 5. Read the output correctly

The graphic shows each model's selected trades, win rate, net profit, realized return, common-date Brier score, latest action, pressure regime, and evidence grade. It ranks forecast accuracy only on dates shared by all three methods. It ranks simulated return only for methods with execution evidence.

Only rows marked **Grade A** represent complete historical order-book evidence. Grade B/B+ results are useful sensitivity checks, not verified fills. V10 was fitted on exposed development data and still needs genuinely new confirmation.

The imported history is exposed development evidence. A positive result is a research lead for a future forward test; it does not prove that the return will continue.

## Input and troubleshooting

- Historical file format: [docs/HISTORY_FORMAT.md](docs/HISTORY_FORMAT.md)
- Bundled-data provenance: [docs/DATA_PROVENANCE.md](docs/DATA_PROVENANCE.md)
- Prompt for another ChatGPT/Codex session: [docs/CHATGPT_SETUP_PROMPT.md](docs/CHATGPT_SETUP_PROMPT.md)
- The current-data version is the `swarm_setup_online` branch.

If setup says Python is missing, install 64-bit Python 3.11 or newer from python.org and select **Add Python to PATH**, then rerun the same command. If a corporate network blocks package installation, connect to a normal internet connection for the first setup; later historical runs use local files only.

