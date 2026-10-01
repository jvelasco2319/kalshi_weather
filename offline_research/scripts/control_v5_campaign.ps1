[CmdletBinding()]
param(
    [ValidateSet('Start', 'Resume', 'Status')]
    [string]$Action = 'Status',
    [string]$ProjectRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$project = [System.IO.Path]::GetFullPath($ProjectRoot)

function Resolve-ProjectPath {
    param([string]$Root, [string]$Value)
    $rootPath = [System.IO.Path]::GetFullPath($Root)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $rootPath $Value))
    $prefix = $rootPath.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith(
            $prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes V5 project root: $Value"
    }
    return $candidate
}

function Read-JsonOrNull {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
}

function Write-AtomicJson {
    param([string]$Path, [object]$Value)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $pending = $Path + '.pending'
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText(
        $pending, $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $pending -Destination $Path -Force
}

function Get-LiveRecordedProcess {
    param([object]$State)
    if ($null -eq $State -or $null -eq $State.pid -or
        [string]::IsNullOrWhiteSpace(
            [string]$State.process_started_at_utc)) {
        return $null
    }
    try {
        $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        $actual = $process.StartTime.ToUniversalTime()
        $recorded = [DateTimeOffset]::Parse(
            [string]$State.process_started_at_utc,
            [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
        if ([Math]::Abs(($actual - $recorded).TotalSeconds) -gt 2) {
            return $null
        }
        return $process
    }
    catch {
        return $null
    }
}

$python = Resolve-ProjectPath $project '.venv/Scripts/python.exe'
$module = Resolve-ProjectPath $project 'v5/orchestrator.py'
$readinessPath = Resolve-ProjectPath $project 'data/manifests/v5_readiness.json'
$ticketPath = Resolve-ProjectPath $project 'runs/v5_offline_campaign_ticket.json'
$claimPath = $ticketPath + '.claimed.json'
$processStatePath = Resolve-ProjectPath $project 'runs/v5_campaign.process.json'
$pidPath = Resolve-ProjectPath $project 'runs/v5_campaign.pid'
$stdoutPath = Resolve-ProjectPath $project 'runs/v5_campaign.stdout.log'
$stderrPath = Resolve-ProjectPath $project 'runs/v5_campaign.stderr.log'

$state = Read-JsonOrNull $processStatePath
$live = Get-LiveRecordedProcess $state
$ticket = Read-JsonOrNull $ticketPath
$claim = Read-JsonOrNull $claimPath
$campaignId = if ($null -ne $claim) {
    [string]$claim.campaign_id
}
elseif ($null -ne $ticket) {
    [string]$ticket.campaign_id
}
elseif ($null -ne $state) {
    [string]$state.campaign_id
}
else {
    $null
}
$campaignRoot = if ([string]::IsNullOrWhiteSpace($campaignId)) {
    $null
}
else {
    Resolve-ProjectPath $project ("runs/campaigns_v5/" + $campaignId)
}
$summary = if ($null -eq $campaignRoot) {
    $null
}
else {
    Read-JsonOrNull (Join-Path $campaignRoot 'summary.json')
}
$verification = if ($null -eq $campaignRoot) {
    $null
}
else {
    Read-JsonOrNull (Join-Path $campaignRoot 'scheduler-verification.json')
}
$manifest = if ($null -eq $campaignRoot) {
    $null
}
else {
    Read-JsonOrNull (Join-Path $campaignRoot 'campaign-artifacts.json')
}
$terminal = (
    $null -ne $summary -and
    $null -ne $verification -and
    $verification.status -eq 'PASS' -and
    $null -ne $manifest -and
    $summary.campaign_id -eq $campaignId -and
    $manifest.campaign_id -eq $campaignId
)

if ($Action -eq 'Status') {
    [pscustomobject][ordered]@{
        controller_version = 'klax-v5-background-controller-v1'
        status = if ($terminal) {
            'COMPLETE'
        }
        elseif ($null -ne $live) {
            'RUNNING'
        }
        elseif ($null -ne $claim) {
            'INCOMPLETE_EXPLICIT_RESUME_REQUIRED'
        }
        elseif ($null -ne $ticket) {
            'TICKET_READY_NOT_STARTED'
        }
        else {
            'NOT_READY_OR_NOT_TICKETED'
        }
        process_live = $null -ne $live
        pid = if ($null -eq $live) { $null } else { $live.Id }
        campaign_id = $campaignId
        campaign_root = $campaignRoot
        scientific_conclusion = if ($terminal) {
            $summary.scientific_conclusion
        }
        else {
            $null
        }
        maximum_wall_seconds = 43200
        protected_confirmation_labels_read = if ($terminal) {
            $summary.protected_confirmation_labels_read
        }
        else {
            $false
        }
        actual_orders_placed = if ($terminal) {
            $summary.actual_orders_placed
        }
        else {
            $false
        }
        stdout_path = $stdoutPath
        stderr_path = $stderrPath
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($terminal) {
    throw "V5 campaign $campaignId is already complete."
}
if ($null -ne $live) {
    throw "V5 campaign process $($live.Id) is already running."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python runtime is missing: $python"
}
if (-not (Test-Path -LiteralPath $module -PathType Leaf)) {
    throw "V5 orchestrator is missing: $module"
}
if (-not (Test-Path -LiteralPath $readinessPath -PathType Leaf)) {
    throw "V5 readiness is missing: $readinessPath"
}
if ($Action -eq 'Start' -and $null -eq $ticket) {
    throw 'V5 start requires an unclaimed one-use ticket.'
}
if ($Action -eq 'Resume' -and $null -eq $claim) {
    throw 'V5 resume requires a claimed incomplete campaign.'
}

$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = (
    (Resolve-ProjectPath $project 'src') + ';' + $project)
try {
    $startParameters = @{
        FilePath = $python
        ArgumentList = @(
            '-m', 'v5.orchestrator', $Action.ToLowerInvariant(),
            '--root', ('"' + $project + '"')
        )
        WorkingDirectory = $project
        WindowStyle = 'Hidden'
        RedirectStandardOutput = $stdoutPath
        RedirectStandardError = $stderrPath
        PassThru = $true
    }
    $process = Start-Process @startParameters
}
finally {
    $env:PYTHONPATH = $priorPythonPath
}

$record = [pscustomobject][ordered]@{
    controller_version = 'klax-v5-background-controller-v1'
    status = 'LAUNCHED'
    action = $Action.ToLowerInvariant()
    campaign_id = $campaignId
    pid = $process.Id
    process_started_at_utc = $process.StartTime.ToUniversalTime().ToString('o')
    launched_at_utc = [DateTime]::UtcNow.ToString('o')
    maximum_wall_seconds = 43200
    readiness_path = $readinessPath
    stdout_path = $stdoutPath
    stderr_path = $stderrPath
    protected_confirmation_labels_read = $false
    live_or_paper_orders_authorized = $false
    actual_orders_placed = $false
}
Write-AtomicJson $processStatePath $record
[System.IO.File]::WriteAllText(
    $pidPath, [string]$process.Id + [Environment]::NewLine,
    [System.Text.Encoding]::ASCII)
$record | ConvertTo-Json -Depth 8
