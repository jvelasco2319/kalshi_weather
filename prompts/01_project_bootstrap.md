# Prompt 01 — Project bootstrap

Implement the initial Python project skeleton.

## Tasks

1. Confirm `pyproject.toml` installs the package with `pip install -e ".[dev]"`.
2. Ensure `kalshi-weather --help` works.
3. Implement `src/kalshi_weather/config.py` to load:
   - `.env`
   - `config/settings.example.yaml`
   - environment overrides
4. Implement `src/kalshi_weather/logging_utils.py` with a simple Rich/logging setup.
5. Implement enough CLI commands to show placeholders:
   - `markets`
   - `weather-snapshot`
   - `predict-once`
   - `paper-once`
   - `run-paper`
6. All placeholder commands must print a clear “not implemented yet” message rather than crashing.

## Acceptance criteria

Run:

```powershell
pip install -e ".[dev]"
kalshi-weather --help
pytest
```

Expected:

```text
All tests pass.
CLI displays all command names.
```

## Do not do yet

Do not call external APIs in this prompt.
Do not add authenticated Kalshi order code.
