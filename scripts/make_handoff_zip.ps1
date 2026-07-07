$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$ZipPath = Join-Path $Root "kalshi_weather_handoff_latest.zip"
$CheckPath = Join-Path $Root "HANDOFF_ZIP_CHECK.txt"

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path -LiteralPath $CheckPath) {
    Remove-Item -LiteralPath $CheckPath -Force
}

$includeFiles = @(
    "README.md",
    "RUN_LOG.md",
    "FINAL_STATUS.md",
    "TODO.md",
    "PHASE2_STATUS.md",
    "PHASE3_STATUS.md",
    "PHASE4_STATUS.md",
    "PHASE5_STATUS.md",
    "PHASE6_STATUS.md",
    "PHASE7_STATUS.md",
    "POC_FINAL_STATUS.md",
    "OPERATIONAL_VALIDATION_STATUS.md",
    "MODEL_ESTIMATE_COMPARISON_STATUS.md",
    "SIMPLE_OUTPUT_STATUS.md",
    "KALSHI_HISTORY_CHARTS_STATUS.md",
    "DIRECT_NOAA_MODELS_STATUS.md",
    "PAPER_MODEL_RACE_STATUS.md",
    "SAFER_MODEL_RACE_STATUS.md",
    "INDEPENDENT_MODEL_RACE_STATUS.md",
    "SYNTHETIC_EDGE_CASE_STATUS.md",
    "LLM_TRADE_ADVISOR_STATUS.md",
    "POC_ACCEPTANCE_REPORT.md",
    "pyproject.toml",
    ".env.example",
    ".gitignore"
)

$includeDirs = @(
    "src",
    "tests",
    "docs",
    "prompts",
    "config",
    "scripts"
)

$excludeParts = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".test-artifacts",
    "kalshi_weather.egg-info"
)

$tempDir = Join-Path $Root ".handoff_tmp"
if (Test-Path -LiteralPath $tempDir) {
    Remove-Item -LiteralPath $tempDir -Recurse -Force
}
New-Item -ItemType Directory -Path $tempDir | Out-Null

try {
    foreach ($file in $includeFiles) {
        $src = Join-Path $Root $file
        if (Test-Path -LiteralPath $src) {
            $dst = Join-Path $tempDir $file
            New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
            Copy-Item -LiteralPath $src -Destination $dst -Force
        }
    }

    foreach ($dir in $includeDirs) {
        $srcDir = Join-Path $Root $dir
        if (-not (Test-Path -LiteralPath $srcDir)) {
            continue
        }
        Get-ChildItem -LiteralPath $srcDir -Recurse -File -Force | ForEach-Object {
            $rel = $_.FullName.Substring($Root.Path.Length + 1)
            $parts = $rel -split '[\\/]'
            $skip = $false
            if ($parts[0] -eq "data") {
                $skip = $true
            }
            if ($parts | Where-Object { $_ -in $excludeParts }) {
                $skip = $true
            }
            if ($_.Name -eq ".env" -or $_.Extension -in @(".pyc", ".pyo", ".sqlite", ".db", ".pem", ".key")) {
                $skip = $true
            }
            if (-not $skip) {
                $dst = Join-Path $tempDir $rel
                New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
                Copy-Item -LiteralPath $_.FullName -Destination $dst -Force
            }
        }
    }

    Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $ZipPath -CompressionLevel Optimal
}
finally {
    if (Test-Path -LiteralPath $tempDir) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force
    }
}

$required = @(
    "PHASE3_STATUS.md",
    "POC_FINAL_STATUS.md",
    "tests/",
    "tests/test_model_race.py",
    "tests/test_operational_validation.py",
    "tests/test_time_phase3.py",
    "src/kalshi_weather/data/kalshi_client.py",
    "src/kalshi_weather/data/market_discovery.py",
    "src/kalshi_weather/data/nws_client.py",
    "src/kalshi_weather/data/open_meteo_client.py",
    "src/kalshi_weather/data/outcomes.py",
    "src/kalshi_weather/data/herbie_client.py",
    "src/kalshi_weather/data/kalshi_history.py",
    "src/kalshi_weather/data/storage.py",
    "src/kalshi_weather/advisor/decision_schema.py",
    "src/kalshi_weather/advisor/trade_quality.py",
    "src/kalshi_weather/advisor/llm_trade_advisor.py",
    "src/kalshi_weather/advisor/risk_validator.py",
    "src/kalshi_weather/trading/model_race.py",
    "src/kalshi_weather/synthetic/scenarios.py",
    "src/kalshi_weather/synthetic/providers.py",
    "SAFER_MODEL_RACE_STATUS.md",
    "INDEPENDENT_MODEL_RACE_STATUS.md",
    "SYNTHETIC_EDGE_CASE_STATUS.md",
    "src/kalshi_weather/time_utils.py",
    "src/kalshi_weather/cli.py",
    "src/kalshi_weather/model/model_estimates.py",
    "src/kalshi_weather/model/probability.py",
    "src/kalshi_weather/model/lax_high_temp.py",
    "src/kalshi_weather/model/calibration.py",
    "src/kalshi_weather/backtest/replay.py",
    "src/kalshi_weather/reporting.py",
    "src/kalshi_weather/validation.py",
    "scripts/run_collect_session_lax.ps1",
    "scripts/run_after_settlement_lax.ps1",
    "scripts/run_model_health_lax.ps1",
    "scripts/run_kalshi_history_backfill_lax.ps1",
    "scripts/run_kalshi_trend_dashboard_lax.ps1",
    "scripts/install_direct_noaa_models.ps1",
    "scripts/install_windows_tasks_lax.ps1",
    "scripts/uninstall_windows_tasks_lax.ps1",
    "docs/HOW_TO_READ_RESULTS.md",
    "docs/06_CLI_REFERENCE.md",
    "docs/SYNTHETIC_EDGE_CASES.md",
    "docs/KALSHI_HISTORY_AND_CHARTS.md",
    "docs/MODEL_ESTIMATE_COMPARISON.md",
    "docs/PAPER_MODEL_RACE.md",
    "docs/LLM_TRADE_ADVISOR.md",
    "prompts/LLM_TRADE_ADVISOR_SYSTEM_PROMPT.md",
    "config/settings.example.yaml",
    "LLM_TRADE_ADVISOR_STATUS.md",
    "DIRECT_NOAA_MODELS_STATUS.md",
    "PAPER_MODEL_RACE_STATUS.md",
    "OPERATIONAL_VALIDATION_STATUS.md",
    "MODEL_ESTIMATE_COMPARISON_STATUS.md",
    "SIMPLE_OUTPUT_STATUS.md",
    "KALSHI_HISTORY_CHARTS_STATUS.md",
    "README.md",
    "RUN_LOG.md",
    "TODO.md",
    "FINAL_STATUS.md",
    "POC_FINAL_STATUS.md"
)

$contents = tar -tf $ZipPath
"ZIP: $ZipPath" | Out-File -LiteralPath $CheckPath -Encoding UTF8
"FILE_COUNT: $($contents.Count)" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
foreach ($path in $required) {
    if ($path.EndsWith("/")) {
        $status = if ($contents | Where-Object { $_.StartsWith($path) }) { "PRESENT" } else { "MISSING" }
    }
    else {
        $status = if ($contents -contains $path) { "PRESENT" } else { "MISSING" }
    }
    "$($path): $status" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
}
"--- CONTENTS ---" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
$contents | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8

Write-Output "Created $ZipPath"
Write-Output "Wrote $CheckPath"
