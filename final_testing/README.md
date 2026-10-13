# Four-city paper trading and Offline History

This branch packages the saved Los Angeles, New York, Miami and Phoenix models,
their **nine paper trading strategies**, and the tools to download and replay
historical days. It uses fake money and has no exchange order transport.

## Install and start

You need **64-bit Windows, 64-bit Python 3.12, Git and internet access**. Install
Python with the Windows `py` launcher. Use a short folder such as `C:\kw`;
keep the full repository path at **65 characters or fewer** to avoid Windows
path-length errors when restoring the archived files.

Open PowerShell and run:

```powershell
git clone --branch final_testing_temp --single-branch https://github.com/jvelasco2319/kalshi_weather.git C:\kw
cd C:\kw
.\final_testing\setup.ps1
.\final_testing\Start.ps1
```

If PowerShell blocks local scripts, allow them for that window and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Setup creates this clone's own `.venv`, installs the 17 recorded dependency
versions, and restores the original model and data files from verified lossless
asset packs. It checks their hashes. It does not train new models. A missing or
changed asset stops setup instead of using files from the original computer.

If this clone already has the correctly prepared environment, verify and restore
the assets without repeating package installation:

```powershell
.\final_testing\setup.ps1 -SkipInstall
```

The combined page opens at **http://127.0.0.1:9020/?tab=offline**. The online
monitoring server uses **http://127.0.0.1:9015/**. Leave the starter running while
using either page; **Ctrl+C** stops its two servers. To use another pair of ports:

```powershell
.\final_testing\Start.ps1 --online-port 9035 --offline-port 9040
```

Setup and launch scripts work from other folders too when called by their full
paths. The `.venv`, restored assets, caches and new runtime results are local
generated files. Keep the included asset packs; a fresh clone needs them.

## Use Offline History

Open **Offline History**, then select **Download & analyze missing days**. The
history starts on **October 1, 2026** and runs through the date of your click.
Each strategy has its own **$100 wallet**, processed in date order without a
monthly cash reset. These wallets are separate from online monitoring wallets.

The button checks saved inputs, tries the authorized read-only downloads for
missing market and weather data, and updates the figures. It can continue after
the browser tab closes while the server runs. Repeated clicks do not create a
second copy of the same active job.

For new historical order books, use **Connect read-only archive** and enter
**your own Probalytics read-only database login**. A website login alone does
not supply this database connection. Credentials stay in server memory and are
released when it stops. No credentials, connection tokens or author wallet
databases are included in this repository. Public market and weather reads do
not require a trading-enabled Kalshi account.

The page shows balances, daily closed-trade profits, wins and losses, contracts,
fees, pending days, source receipts and saved result exports. **Pending or
unknown is different from a day when the rules chose not to trade.**

## The nine saved strategies

| City | Paper strategies |
| --- | --- |
| Los Angeles | F3 V10; F6 hybrid staged safety; V8D NO at 18 UTC — prospective impossible-only exits |
| New York | Conservative P09; targeted SAFE5 |
| Miami | Selected calibrated model; original RAW model |
| Phoenix | Four-law ensemble quarter-Kelly; V19 temperature-set quarter-Kelly |

Each retains its saved first-buy time, spending limits, fees and whole-contract
quantities. The new online LA exit overlay is described below; saved historical
replays retain their original exit rules. Some use a 5% budget. Phoenix uses its saved
quarter-Kelly sizing capped at 10%. Nine separate wallet simulations do not
prove that all nine orders could fill together against the same market book.

## Read the results correctly

These are **conditional historical simulations**, using the documented archived
timing and execution assumptions. Actual download times remain recorded. Missing
books, uncertain fills and unfinished settlement remain pending or unknown;
they are never replaced with invented trades or prices. A full balance stays
blank where evidence is incomplete, while supported cash is shown separately.

Daily profit is assigned to a closed trade's original entry date. Cash becomes
available again according to the recorded payout timing. A profit graph and
cash available at a calendar cutoff therefore measure different things.

The sources and model release were frozen on **October 12, 2026**, with
registered **October 2026 checkpoints**. Some earlier dates are retrospective
fixed-state comparisons because their checkpoint was saved later. The included
October sources contain gaps; this package does not claim every date or strategy
has a complete result. Dates requiring a later month's unregistered checkpoint
remain pending. The button does not fit a replacement model.

### Weather Company feed and LA impossible-only exits

The current application reads **The Weather Company's public Kalshi portal**
at [weather.com/kalshi](https://weather.com/kalshi) for all four cities when
their actual market rules name that provider. It uses the matching KLAX, KNYC,
KMIA and KPHX station, records receipt times and source hashes, and displays
published hourly Fahrenheit values without inventing temperatures. The climate
day uses local standard time, including the daylight-saving offset. A TWC outage
remains unavailable; it never silently replaces that feed with NWS readings.

**LA checks every 30 seconds while the server runs.** This does not mean the
source publishes new temperatures every 30 seconds. The three LA paper wallets
attempt to sell only a position invalidated by a matching official,
non-preliminary **completed daily report**, and only while the market is open
with positive supported buyer bids. Partial sales are allowed. Unsold contracts
remain open; increasingly unlikely positions are held. The former F3 soft
forecast exit is disabled in this prospective application.

A fixed-range NO is still possible while the day's high can rise above that
range. An hourly reading marked `settled` is settled for its **hour**, not the
daily high. Preliminary observations do not trigger an impossible-position sale.
Daily reports usually arrive after trading closes, so this strict rule **does
not provide a dependable intraday rescue exit**. Even official reports can have
material revisions under the market rules; they are not labeled irrevocable.

The saved weather models still predict the original NWS physical target. A feed
switch does not retrain or validate them against TWC settlement, and the existing
target-equivalence and native fee checks still pause unsupported online entries.
The UI states this explicitly. Monitoring and conditional history remain
available; neither proves future profit or actual historical fills.

Application code is under `final_testing/application`, with new sealed online
and offline releases. Setup restores unchanged native vendor bytes from the
original asset packs into those releases. Original models, source-map identities,
archives and results remain unchanged. The new application's runtime wallets are
separate from those of the original archived application.

## What is portable

The package includes the UI, saved source code, weights, checkpoints, native
trader rules, offline download/replay tools and the current reproducible history
inputs. It does **not** include every old research campaign or the author's
private connections and runtime wallet databases.

Saved source and data bytes stay unchanged. Old workstation path strings are
provenance identities: a deployment mount maps them to verified files inside
this clone. An unexported path fails explicitly. There is **no original-PC
fallback**. The new installation records its own Python identity; this first
portable package targets Windows Python 3.12.

The included files and seals are listed in
`final_testing/assets/asset-manifest.json`; pack checksums are in
`final_testing/asset_packs/manifest.json`. The final package passed 22 setup/path tests, all four native city workers, and
an exact replay of nine wallets across 108 calendar records. Original balances,
fees, contract counts and uncertain-day statuses matched. The combined UI and
online/offline isolation were also checked. See
[evidence/portable-acceptance.json](evidence/portable-acceptance.json) and
[evidence/ny-final-portable-acceptance-v2.json](evidence/ny-final-portable-acceptance-v2.json).

For focused deployment tests after setup:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s final_testing\tests
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_portable_mount.py
```

Use this guide's launcher for the four-city package. The repository's older LA
instructions and experimental launchers remain available for their original
application.
