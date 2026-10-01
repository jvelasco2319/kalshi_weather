# Weather daily-high forecast pipeline

Given a city and one or more local calendar dates, this project downloads the
required NOAA forecast GRIB2 files, calculates each model's predicted daily
high temperature, turns each high into a probability distribution, and writes
one JSON file per target date.

Supported models are GFS, GFS Seamless, NAM (12 km Grid 218), and NBM.

## Prerequisites

- Internet access: city lookup and model downloads are online.
- Python 3.12 or later.
- Free disk space. GRIB2 files are large; a multi-day run can require many
  gigabytes.

The code uses `pygrib`, which depends on the ecCodes native library. The
Ubuntu and WSL instructions below are the most reliable setup paths.

## Ubuntu

From the project root:

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev libeccodes0 libeccodes-dev

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run a single target date:

```bash
python scripts/generate_dataset.py \
  --city "Los Angeles, CA" \
  --date 2026-07-01
```

Run an inclusive range, producing a separate JSON file for every date:

```bash
python scripts/generate_dataset.py \
  --city "Los Angeles, CA" \
  --start-date 2026-07-01 \
  --end-date 2026-07-07
```

When finished, leave the virtual environment with:

```bash
deactivate
```

## Windows: recommended WSL setup

`pygrib` and ecCodes are substantially easier to install under Linux. On
Windows 10 or 11, install Ubuntu through WSL, then follow the Ubuntu section
inside the Ubuntu terminal.

```powershell
wsl --install -d Ubuntu
```

After restarting and opening **Ubuntu**, clone or open the project under your
Linux home directory, then use the Ubuntu commands above. From Windows
Explorer, WSL files are available at `\\wsl$\Ubuntu\home\<your-user>`.

## Windows: native Conda setup

If WSL is not an option, install Miniconda or Miniforge, open an **Anaconda
Prompt**, and run this from the project directory:

```powershell
conda create -n weather-pipeline -c conda-forge python=3.12 pygrib eccodes
conda activate weather-pipeline
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the pipeline using the same commands, replacing `python` if necessary
with the Python from the activated Conda environment:

```powershell
python scripts\generate_dataset.py --city "Los Angeles, CA" --date 2026-07-01
```

If `pip install -r requirements.txt` cannot build `pygrib`, retain the
Conda-installed `pygrib` and install the remaining Python packages with your
environment's package manager. WSL is the preferred fallback.

## Command-line options

```text
--city CITY                 City name, for example "Los Angeles, CA"
--date YYYY-MM-DD           Process one target local date
--start-date YYYY-MM-DD     First date in an inclusive range
--end-date YYYY-MM-DD       Last date in that range; required with --start-date
--init YYYY-MM-DDTHH        Exact UTC model initialization for one --date
--init-hour HOUR            UTC initialization hour for every range date (default: 0)
--strict                    Fail if any model is unavailable instead of writing a partial result
```

For a range, every target day uses the selected `--init-hour` on that same
UTC date. For a single day, `--init` lets you choose a run issued before the
target day:

```bash
python scripts/generate_dataset.py \
  --city "Los Angeles, CA" \
  --date 2026-07-01 \
  --init 2026-06-30T12
```

## Progress and output

The console reports city resolution, model/file counts, cache hits, download
progress in bytes and percent, calculated highs, and output paths. Existing
non-empty GRIB2 files are reused. Downloads are first written as `.part`
files and become cached GRIB2 files only after they finish.

Raw GRIB2 files are stored under:

```text
data/raw/<model>/<initialization-date>/t<UTC-hour>z/
```

JSON files are written under:

```text
data/processed/forecasts/<city>_<target-date>.json
```

Each JSON output includes the resolved location, model initialization, each
available model's daily high and distribution, and a weighted ensemble
distribution. If a source archive does not contain a requested model run, the
default behavior is to write a partial result and record the error in
`metadata.unavailable_models`. Use `--strict` when all four models are
required.

## How highs are calculated

- GFS and GFS Seamless: maximum of the downloaded six-hour, 2 m `tmax`
  fields covering the local day.
- NAM Grid 218: maximum of the nearest grid point's three-hourly 2 m
  temperature values within the local day.
- NBM: maximum of the nearest grid point's hourly 2 m temperature values
  within the local day.

The probability distribution is currently a Gaussian centered on that model
high with a fixed 2°F standard deviation. It is suitable as an initial
forecast representation, but it is not yet calibrated from historical error
data.
