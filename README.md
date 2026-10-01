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

## 2. Set it up and import history

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

The setup creates `.venv`, installs the package, verifies all three frozen configurations, and converts the verified historical cache into `data\history.jsonl`. The paid/raw source files stay outside Git.

To check the program before copying real history:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_offline.ps1 -UseSample
```

## 3. Run all three methods

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_offline.ps1
```

The browser report opens automatically. Results are saved here:

- `artifacts\offline\report.html` — the graphic
- `artifacts\offline\results.json` — every probability, decision, and score

## 4. Read the output correctly

The graphic shows each model's selected trades, win rate, net profit, realized return, Brier score, latest action, pressure regime, and evidence grade. A positive historical result is evidence for a future confirmation test; it is not proof that the return will continue.

Only rows marked **Grade A** represent complete historical order-book evidence. Grade B/B+ results are useful sensitivity checks, not verified fills. V10 was fitted on exposed development data and still needs genuinely new confirmation.

## Input and troubleshooting

- Historical file format: [docs/HISTORY_FORMAT.md](docs/HISTORY_FORMAT.md)
- Prompt for another ChatGPT/Codex session: [docs/CHATGPT_SETUP_PROMPT.md](docs/CHATGPT_SETUP_PROMPT.md)
- The current-data version is the `swarm_setup_online` branch.

