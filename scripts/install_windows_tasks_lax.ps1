[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$CollectTime = "09:00",
    [string]$AfterSettlementTime = "11:30",
    [string]$ModelHealthTime = "11:45",
    [string]$UserId = $env:USERNAME
)

$ErrorActionPreference = "Stop"
$Root = "C:\Users\jarve\Documents\Codex\kalshi_weather"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

function New-KalshiWeatherTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string]$At
    )

    $Action = New-ScheduledTaskAction `
        -Execute $PowerShell `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $Trigger = New-ScheduledTaskTrigger -Daily -At $At
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)

    if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $Action `
            -Trigger $Trigger `
            -Settings $Settings `
            -Description "Kalshi Weather fake-money operational validation task" `
            -User $UserId `
            -Force | Out-Null
    }
}

New-KalshiWeatherTask `
    -TaskName "KalshiWeather_Collect_LAX" `
    -ScriptPath (Join-Path $Root "scripts\run_collect_session_lax.ps1") `
    -At $CollectTime
New-KalshiWeatherTask `
    -TaskName "KalshiWeather_AfterSettlement_LAX" `
    -ScriptPath (Join-Path $Root "scripts\run_after_settlement_lax.ps1") `
    -At $AfterSettlementTime
New-KalshiWeatherTask `
    -TaskName "KalshiWeather_ModelHealth_LAX" `
    -ScriptPath (Join-Path $Root "scripts\run_model_health_lax.ps1") `
    -At $ModelHealthTime

Write-Output "Scheduled task install script completed. Use -WhatIf to preview without registering tasks."
