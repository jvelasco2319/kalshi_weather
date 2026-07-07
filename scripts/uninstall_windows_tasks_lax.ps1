[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = "Stop"
$TaskNames = @(
    "KalshiWeather_Collect_LAX",
    "KalshiWeather_AfterSettlement_LAX",
    "KalshiWeather_ModelHealth_LAX"
)

foreach ($TaskName in $TaskNames) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -ne $Task -and $PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Output "Removed $TaskName"
    }
    elseif ($null -eq $Task) {
        Write-Output "Task not found: $TaskName"
    }
}
