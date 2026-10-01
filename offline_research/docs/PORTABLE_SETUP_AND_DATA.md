# Portable setup and data restoration

The `swarm` branch is intentionally code-only. Large raw archives, generated run records, paid Probalytics extracts, the local model, and the Python environment are excluded from Git. The scripts in this guide rebuild the environment, restore exact local artifacts when the original computer is reachable, and download the remaining reproducible historical inputs.

## One command on the other Windows computer

Clone the `swarm` branch, enter the offline project, and run:

```powershell
git clone --branch swarm --single-branch https://github.com/jvelasco2319/kalshi_weather.git
cd .\kalshi_weather\offline_research
powershell -ExecutionPolicy Bypass -File .\scripts\setup_research_machine.ps1 -Mode Auto -MirrorPath '\\192.168.1.193\Kalshi\_weather\_llm' -Workers 8
```

`Auto` first copies missing files from the original computer's network share. It then creates `.venv`, installs the pinned dependencies, audits each source, and resumes only incomplete public downloads. Re-running the same command is supported: verified files are skipped and partial downloaders resume from their saved caches.

The two computers must be on the same private network and the original computer must be awake. If its address changes, replace `192.168.1.193` with the original computer's current IPv4 address. Windows may ask for the share credentials that were created on the original computer.

## Paid Probalytics depth

If the exact paid archive did not copy from the network share, add your own Probalytics ClickHouse username:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_research_machine.ps1 -Mode Auto -MirrorPath '\\192.168.1.193\Kalshi\_weather\_llm' -Workers 8 -IncludeProbalytics -ProbalyticsUsername 'YOUR_USERNAME'
```

The existing bounded downloader prompts privately for the password. The password is not placed on the command line, written to a file, or added to Git. A normal website login cookie is not enough for this downloader; use the ClickHouse credentials shown by the paid data service. Only the 50 preregistered historical dates and the 17:55–18:06 UTC windows are queried.

## Optional local research model

The data and deterministic evaluations do not require the local language model. To copy or download the pinned model and llama.cpp runtime too, add `-IncludeLocalModel`. This adds roughly 13 GB. If the network mirror already contains it, the audit skips the download.

## Other modes

```powershell
# Audit without downloading source data.
.\scripts\setup_research_machine.ps1 -Mode Status

# Copy the exact local archive and run records, then stop.
.\scripts\setup_research_machine.ps1 -Mode MirrorOnly -MirrorPath '\\192.168.1.193\Kalshi\_weather\_llm'

# Ignore the LAN mirror and reconstruct everything available from public sources.
.\scripts\setup_research_machine.ps1 -Mode DownloadOnly -Workers 8
```

The source audit covers archived CLILAX reports, HRRR/GEFS, KLAX-area observations, Kalshi contract metadata, hourly and one-minute candles, public trades, GFS/NAM/NBM, V10 meteorological inputs, paid Probalytics depth, and the optional local model/runtime.

Some frozen V7–V10 artifacts cannot be anonymously re-created from Git alone. They depend on paid exports or immutable registration and recovery records that were generated before the experiments ran. `DownloadOnly` reports these as blocked prerequisites. `Auto` resolves them by copying the exact `data/` and `runs/` trees from the network share before filling public-source gaps.

The launcher never starts a research campaign, opens a protected holdout, downloads settlement labels, connects to a current/live feed, or places paper or live orders. After setup, inspect `data/manifests/portable_bootstrap_state.json`. A `COMPLETE` result means every selected stage passed its local coverage check; `PARTIAL` lists the exact missing prerequisite or credential.
