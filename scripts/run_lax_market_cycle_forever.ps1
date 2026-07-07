param(
  [string]$RepoRoot = "C:\Users\jarve\Documents\Codex\kalshi_weather",
  [string]$Series = "KXHIGHLAX",
  [string]$Station = "KLAX",
  [string]$TargetDate = "",
  [switch]$Tomorrow,
  [int]$StartingCash = 1000,
  [int]$RestartDelaySeconds = 60,
  [int]$PackageEveryMinutes = 60,
  [int]$FastModelRefreshSeconds = 60,
  [int]$NoaaModelRefreshSeconds = 900,
  [int]$ObservationRefreshSeconds = 300,
  [switch]$AggressiveAllDay,
  [double]$MaxNoBinProbability = 0.40,
  [double]$MinEdgeCents = 2.0,
  [double]$MinNoEdgeCents = 2.0,
  [double]$MinNoUpsideCents = 2.0,
  [switch]$ModelAuthoritative,
  [switch]$TakerBuy,
  [switch]$NoCachedModels,
  [switch]$ForceModelRecomputeEveryIteration,
  [switch]$AllowCachedModels,
  [bool]$CancelExistingPassiveOrdersOnTakerStart = $true,
  [string]$NoProbabilityFilterMode = "",
  [double]$NoProbabilityPenaltyStart = -1.0,
  [double]$NoProbabilityPenaltyFactor = 0.30,
  [double]$AbsoluteNoBinProbabilityCap = 0.60,
  [switch]$AllowCheapAskYesWithMissingBid,
  [switch]$BlockHighConfidenceNoOnExtremeSpread,
  [switch]$BlockNoOnModelSourceDegraded,
  [switch]$AllWeatherModels,
  [switch]$NoaaOff,
  [switch]$NoaaAfterNoon,
  [string]$NoaaStartLocalTime = "12:00",
  [string]$NoaaTimedModels = "hrrr,nbm,gfs,rap",
  [switch]$ShowModelEstimates,
  [ValidateSet("never", "every", "changed")]
  [string]$ShowSnapshot = "changed",
  [int]$SnapshotEvery = 15,
  [ValidateSet("compact", "table", "full")]
  [string]$SnapshotStyle = "compact"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Set-Location $RepoRoot

if ($RestartDelaySeconds -lt 1) {
  throw "RestartDelaySeconds must be at least 1."
}

if ($PackageEveryMinutes -lt 1) {
  throw "PackageEveryMinutes must be at least 1."
}

foreach ($cadence in @(
  @{ Name = "FastModelRefreshSeconds"; Value = $FastModelRefreshSeconds },
  @{ Name = "NoaaModelRefreshSeconds"; Value = $NoaaModelRefreshSeconds },
  @{ Name = "ObservationRefreshSeconds"; Value = $ObservationRefreshSeconds }
)) {
  if ($cadence.Value -lt 1) {
    throw "$($cadence.Name) must be at least 1."
  }
}

if ($MaxNoBinProbability -lt 0 -or $MaxNoBinProbability -gt 1) {
  throw "MaxNoBinProbability must be between 0 and 1."
}

if (![string]::IsNullOrWhiteSpace($NoProbabilityFilterMode) -and $NoProbabilityFilterMode -notin @("hard", "soft_penalty", "off")) {
  throw "NoProbabilityFilterMode must be hard, soft_penalty, or off."
}

if ($NoProbabilityPenaltyStart -ne -1.0 -and ($NoProbabilityPenaltyStart -lt 0 -or $NoProbabilityPenaltyStart -gt 1)) {
  throw "NoProbabilityPenaltyStart must be between 0 and 1, or leave it unset."
}

if ($NoProbabilityPenaltyFactor -lt 0) {
  throw "NoProbabilityPenaltyFactor must be 0 or greater."
}

if ($AbsoluteNoBinProbabilityCap -lt 0 -or $AbsoluteNoBinProbabilityCap -gt 1) {
  throw "AbsoluteNoBinProbabilityCap must be between 0 and 1."
}

foreach ($threshold in @(
  @{ Name = "MinEdgeCents"; Value = $MinEdgeCents },
  @{ Name = "MinNoEdgeCents"; Value = $MinNoEdgeCents },
  @{ Name = "MinNoUpsideCents"; Value = $MinNoUpsideCents }
)) {
  if ($threshold.Value -lt 0) {
    throw "$($threshold.Name) must be 0 or greater."
  }
}

if ($SnapshotEvery -lt 1) {
  throw "SnapshotEvery must be at least 1."
}

if ($Tomorrow -and -not [string]::IsNullOrWhiteSpace($TargetDate)) {
  throw "Use either -TargetDate or -Tomorrow, not both."
}

if ($NoaaOff -and $NoaaAfterNoon) {
  throw "Use either -NoaaOff or -NoaaAfterNoon, not both."
}

if ($AllWeatherModels -and $NoaaAfterNoon) {
  throw "Use either -AllWeatherModels or -NoaaAfterNoon, not both."
}

$noaaStartTimeOfDay = $null
if ($NoaaAfterNoon) {
  try {
    $noaaStartTimeOfDay = [datetime]::ParseExact($NoaaStartLocalTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture).TimeOfDay
  }
  catch {
    throw "NoaaStartLocalTime must use HH:mm format, for example 12:00."
  }
}

$supervisorLogDir = Join-Path $RepoRoot "reports\trader_agent\supervisor"
New-Item -ItemType Directory -Force -Path $supervisorLogDir | Out-Null

$supervisorLog = Join-Path $supervisorLogDir ("lax_market_cycle_supervisor_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".txt")
$lastPackageAt = $null
$profileMode = if ($AggressiveAllDay) { "fixed_test" } else { "auto" }
$effectiveProbabilityBlendMode = if ($ModelAuthoritative) { "model_only" } else { "blend" }
$effectiveOrderStyle = if ($TakerBuy) { "taker" } else { "passive" }
$cacheForcedOff = ($NoCachedModels -or $ForceModelRecomputeEveryIteration -or $ModelAuthoritative -or $TakerBuy) -and (-not $AllowCachedModels)
$effectiveUseCachedModels = -not $cacheForcedOff
$effectiveForceModelRecompute = [bool]$ForceModelRecomputeEveryIteration
if ($cacheForcedOff) { $effectiveForceModelRecompute = $true }
$effectiveModelRefreshSeconds = if ($cacheForcedOff) { 0 } else { $FastModelRefreshSeconds }
$effectiveNoProbabilityFilterMode = $NoProbabilityFilterMode
if ($ModelAuthoritative -and [string]::IsNullOrWhiteSpace($effectiveNoProbabilityFilterMode)) {
  $effectiveNoProbabilityFilterMode = "soft_penalty"
}
$effectiveNoaaModelMode = "scheduled"
$effectiveShowSnapshot = $ShowSnapshot
$effectiveSnapshotEvery = $SnapshotEvery
$effectiveSnapshotStyle = $SnapshotStyle

if ($ShowModelEstimates) {
  $effectiveShowSnapshot = "every"
  $effectiveSnapshotEvery = 1
  $effectiveSnapshotStyle = "table"
}

if ($AllWeatherModels) {
  $env:ENABLE_DIRECT_NOAA_MODELS = "true"
  $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo,noaa_herbie"
  $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE = "hrrr,nbm,gfs,rap,nam,nam_conus,href_mean,href_p50"
  $env:OPEN_METEO_MODELS = "best_match,gfs_seamless,gfs_global,gfs_global025,gfs_global016,gfs025,gfs013,hrrr,hrrr_conus,nbm,nbm_conus,nam,nam_conus,graphcast,graphcast025,gfs_graphcast,gfs_graphcast025,aigfs,aigfs025,hgefs,hgefs025,ecmwf_ifs,aifs"
  $env:MODEL_ESTIMATE_DEFAULT_MODELS_OPEN_METEO = $env:OPEN_METEO_MODELS
}

function Set-NoaaModeForCycle {
  param(
    [datetime]$NowLocal
  )

  if ($AllWeatherModels) {
    $env:ENABLE_DIRECT_NOAA_MODELS = "true"
    $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo,noaa_herbie"
    $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE = "hrrr,nbm,gfs,rap,nam,nam_conus,href_mean,href_p50"
    return "scheduled"
  }

  if ($NoaaOff -or (!$NoaaAfterNoon -and $env:ENABLE_DIRECT_NOAA_MODELS -eq "false")) {
    $env:ENABLE_DIRECT_NOAA_MODELS = "false"
    if ([string]::IsNullOrWhiteSpace($env:MODEL_ESTIMATE_DEFAULT_PROVIDERS) -or $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS -match "noaa_herbie") {
      $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo"
    }
    return "off"
  }

  if ($NoaaAfterNoon) {
    if ($NowLocal.TimeOfDay -lt $noaaStartTimeOfDay) {
      $env:ENABLE_DIRECT_NOAA_MODELS = "false"
      $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo"
      return "off"
    }

    $env:ENABLE_DIRECT_NOAA_MODELS = "true"
    $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo,noaa_herbie"
    $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE = $NoaaTimedModels
    return "scheduled"
  }

  $env:ENABLE_DIRECT_NOAA_MODELS = "true"
  if ([string]::IsNullOrWhiteSpace($env:MODEL_ESTIMATE_DEFAULT_PROVIDERS)) {
    $env:MODEL_ESTIMATE_DEFAULT_PROVIDERS = "current,open_meteo,noaa_herbie"
  }
  if ([string]::IsNullOrWhiteSpace($env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE)) {
    $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE = "hrrr,nbm,gfs,rap"
  }
  return "scheduled"
}

function Invoke-LatestRunPackage {
  $packageOutput = & .\scripts\package_debug_run.ps1 -Latest 2>&1
  $zipPath = $packageOutput |
    ForEach-Object { "$_" } |
    Where-Object { $_ -match '\.zip\s*$' } |
    Select-Object -Last 1

  if ([string]::IsNullOrWhiteSpace($zipPath)) {
    Write-Host "ZIP package created, but the path was not found in packager output."
    return
  }

  Write-Host "ZIP: $zipPath"
}

Start-Transcript -Path $supervisorLog -Force

try {
  Write-Host "Starting LAX market-cycle supervisor"
  Write-Host "Repo root: $RepoRoot"
  Write-Host "Series: $Series"
  Write-Host "Station: $Station"
  if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    Write-Host "Target date: $TargetDate"
  }
  elseif ($Tomorrow) {
    Write-Host "Target date: tomorrow"
  }
  else {
    Write-Host "Target date: automatic current market date"
  }
  Write-Host "Supervisor log: $supervisorLog"
  Write-Host "Package interval: every $PackageEveryMinutes minute(s)"
  Write-Host "Profile mode: $profileMode"
  Write-Host "Max NO bin probability: $MaxNoBinProbability"
  Write-Host "Min edge thresholds: YES/general ${MinEdgeCents}c | NO ${MinNoEdgeCents}c | NO upside ${MinNoUpsideCents}c"
  Write-Host "Model-trusting NO mode: extreme-spread hard block $([bool]$BlockHighConfidenceNoOnExtremeSpread) | degraded-source hard block $([bool]$BlockNoOnModelSourceDegraded)"
  Write-Host "Model authoritative: $([bool]$ModelAuthoritative) | probability blend: $effectiveProbabilityBlendMode"
  Write-Host "Order style: $effectiveOrderStyle | cached models: $effectiveUseCachedModels | force recompute: $effectiveForceModelRecompute | model refresh: ${effectiveModelRefreshSeconds}s"
  if (![string]::IsNullOrWhiteSpace($effectiveNoProbabilityFilterMode)) {
    Write-Host "NO probability filter: $effectiveNoProbabilityFilterMode | penalty factor $NoProbabilityPenaltyFactor | absolute cap $AbsoluteNoBinProbabilityCap"
  }
  Write-Host "All weather models: $([bool]$AllWeatherModels)"
  Write-Host "Refresh cadence: market 60s | Open-Meteo/current ${FastModelRefreshSeconds}s | NOAA/Herbie ${NoaaModelRefreshSeconds}s | observations ${ObservationRefreshSeconds}s"
  if ($NoaaAfterNoon) {
    Write-Host "NOAA/Herbie timing: off before $NoaaStartLocalTime, then $NoaaTimedModels"
  }
  elseif ($NoaaOff) {
    Write-Host "NOAA/Herbie timing: always off"
  }
  else {
    Write-Host "NOAA/Herbie timing: normal/default"
  }
  Write-Host "Snapshot output: $effectiveShowSnapshot every $effectiveSnapshotEvery cycle(s), style $effectiveSnapshotStyle"
  if ($AllWeatherModels) {
    Write-Host "Open-Meteo models: $env:OPEN_METEO_MODELS"
    Write-Host "NOAA/Herbie models: $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE"
  }
  Write-Host "Press Ctrl+C to stop."

  while ($true) {
    $cycleStartedAt = Get-Date
    $effectiveNoaaModelMode = Set-NoaaModeForCycle -NowLocal $cycleStartedAt
    if ($cacheForcedOff -and $effectiveNoaaModelMode -ne "off") {
      $effectiveNoaaModelMode = "full_recompute_each_iteration"
    }
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "Starting or waiting for next market cycle at $cycleStartedAt"
    Write-Host "NOAA/Herbie mode this cycle: $effectiveNoaaModelMode"
    if ($effectiveNoaaModelMode -ne "off") {
      Write-Host "NOAA/Herbie models this cycle: $env:MODEL_ESTIMATE_DEFAULT_MODELS_NOAA_HERBIE"
    }
    Write-Host "============================================================"

    $cycleArgs = @(
      "trader-market-cycle",
      "--series", $Series,
      "--station", $Station,
      "--cycle-mode", "once",
      "--poll-seconds", "$RestartDelaySeconds",
      "--decision-mode", "rules",
      "--strategy", "hybrid",
      "--order-style", $effectiveOrderStyle,
      "--paper-fill-price-mode", "conservative",
      $(if ($TakerBuy -and $CancelExistingPassiveOrdersOnTakerStart) { "--cancel-existing-passive-orders-on-taker-start" } else { "--no-cancel-existing-passive-orders-on-taker-start" }),
      "--profile-mode", $profileMode,
      "--profile-config", "configs/trader_time_profiles.yaml",
      "--probability-blend-mode", $effectiveProbabilityBlendMode,
      "--probability-blend-config", "configs/probability_blend_defaults.yaml",
      "--market-lifecycle-config", "configs/market_lifecycle.yaml",
      "--starting-cash", "$StartingCash",
      "--min-edge-cents", "$MinEdgeCents",
      "--min-no-edge-cents", "$MinNoEdgeCents",
      "--min-no-upside-cents", "$MinNoUpsideCents",
      "--max-no-bin-probability", "$MaxNoBinProbability",
      "--use-canonical-paths",
      "--no-allow-scale-in",
      $(if ($effectiveUseCachedModels) { "--use-cached-models" } else { "--no-use-cached-models" }),
      $(if ($effectiveForceModelRecompute) { "--force-model-recompute-every-iteration" } else { "--no-force-model-recompute-every-iteration" }),
      "--model-refresh-seconds", "$effectiveModelRefreshSeconds",
      "--market-refresh-seconds", "60",
      "--fast-model-refresh-seconds", "$FastModelRefreshSeconds",
      "--noaa-model-mode", $effectiveNoaaModelMode,
      "--noaa-model-refresh-seconds", "$NoaaModelRefreshSeconds",
      "--observation-refresh-seconds", "$ObservationRefreshSeconds",
      "--model-consensus-enabled",
      "--consensus-method", "family_weighted_iqr",
      "--extreme-spread-no-block-threshold-f", "8",
      "--show-snapshot", $effectiveShowSnapshot,
      "--snapshot-every", "$effectiveSnapshotEvery",
      "--snapshot-style", $effectiveSnapshotStyle,
      "--show-settlement-scenarios",
      "--settlement-scenario-style", "compact",
      "--debug-decision",
      "--explain-hold",
      "--audit-pricing",
      "--audit-portfolio",
      "--audit-data",
      "--show-rejections", "summary"
    )

    if ($ModelAuthoritative) {
      $cycleArgs += @(
        "--model-authoritative",
        "--model-weight", "1.0",
        "--market-weight", "0.0",
        "--use-market-implied-probability-as-prior", "false"
      )
    }

    if (![string]::IsNullOrWhiteSpace($effectiveNoProbabilityFilterMode)) {
      $cycleArgs += @("--no-probability-filter-mode", $effectiveNoProbabilityFilterMode)
    }

    if ($NoProbabilityPenaltyStart -ne -1.0) {
      $cycleArgs += @("--no-probability-penalty-start", "$NoProbabilityPenaltyStart")
    }

    $cycleArgs += @(
      "--no-probability-penalty-factor", "$NoProbabilityPenaltyFactor",
      "--absolute-no-bin-probability-cap", "$AbsoluteNoBinProbabilityCap"
    )

    if ($AllowCheapAskYesWithMissingBid) {
      $cycleArgs += "--allow-cheap-ask-yes-with-missing-bid"
    }

    if ($BlockHighConfidenceNoOnExtremeSpread) {
      $cycleArgs += "--block-high-confidence-no-on-extreme-spread"
    } else {
      $cycleArgs += "--no-block-high-confidence-no-on-extreme-spread"
    }

    if ($BlockNoOnModelSourceDegraded) {
      $cycleArgs += "--block-no-on-model-source-degraded"
    } else {
      $cycleArgs += "--no-block-no-on-model-source-degraded"
    }

    if (![string]::IsNullOrWhiteSpace($TargetDate)) {
      $cycleArgs += @("--target-date", $TargetDate)
    }
    elseif ($Tomorrow) {
      $cycleArgs += "--tomorrow"
    }

    try {
      & kalshi-weather @cycleArgs

      $now = Get-Date
      $packageDue = ($null -eq $lastPackageAt) -or (($now - $lastPackageAt).TotalMinutes -ge $PackageEveryMinutes)
      if ($packageDue) {
        Write-Host ""
        Invoke-LatestRunPackage
        $lastPackageAt = Get-Date
      }
      else {
        $nextPackageAt = $lastPackageAt.AddMinutes($PackageEveryMinutes)
        Write-Host ""
        Write-Host "Next ZIP: $nextPackageAt"
      }
    }
    catch {
      Write-Host ""
      Write-Host "ERROR during market cycle:"
      Write-Host $_
      Write-Host ""
      $now = Get-Date
      $packageDue = ($null -eq $lastPackageAt) -or (($now - $lastPackageAt).TotalMinutes -ge $PackageEveryMinutes)

      if ($packageDue) {
        try {
          Invoke-LatestRunPackage
          $lastPackageAt = Get-Date
        }
        catch {
          Write-Host "Could not package latest run:"
          Write-Host $_
        }
      }
      else {
        $nextPackageAt = $lastPackageAt.AddMinutes($PackageEveryMinutes)
        Write-Host "Skipping error ZIP package until $nextPackageAt."
      }
    }

    Write-Host ""
    Write-Host "Sleeping $RestartDelaySeconds seconds before checking next market..."
    Start-Sleep -Seconds $RestartDelaySeconds
  }
}
finally {
  Stop-Transcript
}
