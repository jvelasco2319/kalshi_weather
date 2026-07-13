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
    "tests/test_time_phase3.py",
    "src/kalshi_weather/data/kalshi_client.py",
    "src/kalshi_weather/data/market_discovery.py",
    "src/kalshi_weather/data/nws_client.py",
    "src/kalshi_weather/data/open_meteo_client.py",
    "src/kalshi_weather/data/outcomes.py",
    "src/kalshi_weather/data/storage.py",
    "src/kalshi_weather/time_utils.py",
    "src/kalshi_weather/cli.py",
    "src/kalshi_weather/reporting.py"
)

$contents = tar -tf $ZipPath
"ZIP: $ZipPath" | Out-File -LiteralPath $CheckPath -Encoding UTF8
"FILE_COUNT: $($contents.Count)" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
foreach ($path in $required) {
    $status = if ($contents -contains $path) { "PRESENT" } else { "MISSING" }
    "$($path): $status" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
}
"--- CONTENTS ---" | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8
$contents | Out-File -LiteralPath $CheckPath -Append -Encoding UTF8

Write-Output "Created $ZipPath"
Write-Output "Wrote $CheckPath"
