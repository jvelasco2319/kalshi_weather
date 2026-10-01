[CmdletBinding()]
param(
    [ValidateSet('Start', 'Resume', 'Status')]
    [string]$Action = 'Status',

    [string]$ProjectRoot = '',

    [string]$Readiness = 'data/manifests/v4_1_readiness.json',

    [string]$Ticket = 'runs/v4_1_offline_campaign_ticket.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}

function Resolve-ProjectPath {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter(Mandatory)] [string]$Value
    )
    $rootPath = [System.IO.Path]::GetFullPath($Root)
    $candidate = if ([System.IO.Path]::IsPathRooted($Value)) {
        [System.IO.Path]::GetFullPath($Value)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $rootPath $Value))
    }
    $prefix = $rootPath.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes the V4 project root: $Value"
    }
    return $candidate
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [object]$Value
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $pending = $Path + '.pending'
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText(
        $pending,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::Move($pending, $Path, $true)
}

function Get-Sha256OrNull {
    param([Parameter(Mandatory)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-RecordedProcess {
    param([object]$State)
    if ($null -eq $State -or $null -eq $State.pid -or
        [string]::IsNullOrWhiteSpace([string]$State.process_started_at_utc)) {
        return $null
    }
    try {
        $process = Get-Process -Id ([int]$State.pid) -ErrorAction Stop
        $actual = $process.StartTime.ToUniversalTime()
        # ConvertFrom-Json may materialize an ISO-8601 value as DateTime.  A
        # round-trip through [string] loses its UTC marker and makes the value
        # look like local time, which falsely reports a live process as stale.
        $recordedValue = $State.process_started_at_utc
        $recorded = if ($recordedValue -is [DateTime]) {
            $recordedValue.ToUniversalTime()
        }
        elseif ($recordedValue -is [DateTimeOffset]) {
            $recordedValue.UtcDateTime
        }
        else {
            [DateTimeOffset]::Parse(
                [string]$recordedValue,
                [Globalization.CultureInfo]::InvariantCulture).UtcDateTime
        }
        if ([Math]::Abs(($actual - $recorded).TotalSeconds) -gt 2) {
            return $null
        }
        return $process
    }
    catch {
        return $null
    }
}

function Read-JsonOrNull {
    param([Parameter(Mandatory)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Invalid JSON artifact: $Path"
    }
}

function Get-JsonPropertyOrNull {
    param(
        [object]$Value,
        [Parameter(Mandatory)] [string]$Name
    )
    if ($null -eq $Value) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-V41TerminalCommit {
    param(
        [Parameter(Mandatory)] [string]$CampaignRoot,
        [object]$Summary
    )
    if ($null -eq $Summary) { return $false }
    $verificationPath = Join-Path $CampaignRoot 'scheduler-verification.json'
    $manifestPath = Join-Path $CampaignRoot 'campaign-artifacts.json'
    $verification = Read-JsonOrNull -Path $verificationPath
    $manifest = Read-JsonOrNull -Path $manifestPath
    if ($null -eq $verification -or $null -eq $manifest) { return $false }
    $verificationStatus = [string](Get-JsonPropertyOrNull $verification 'status')
    if (@('PASS', 'FAIL') -notcontains $verificationStatus) { return $false }
    if ((Get-JsonPropertyOrNull $Summary 'report_version') -ne
            'klax-v4.1-offline-campaign-report-v1' -or
        (Get-JsonPropertyOrNull $verification 'verifier_version') -ne
            'klax-v4.1-independent-scheduler-verifier-v1' -or
        (Get-JsonPropertyOrNull $manifest 'manifest_version') -ne
            'klax-v4.1-campaign-artifacts-v1' -or
        (Get-JsonPropertyOrNull $Summary 'scheduler_verification_v4_1') -ne
            $verificationStatus -or
        (Get-JsonPropertyOrNull $manifest 'scheduler_verification_status') -ne
            $verificationStatus -or
        (Get-JsonPropertyOrNull $manifest 'scientific_conclusion') -ne
            (Get-JsonPropertyOrNull $Summary 'scientific_conclusion')) {
        return $false
    }
    $artifacts = @(Get-JsonPropertyOrNull $manifest 'artifacts')
    foreach ($required in @('summary.json', 'scheduler-verification.json',
                             'scheduler-state.json', 'report.md',
                             'recovery-state.json')) {
        $entry = @($artifacts | Where-Object { $_.path -eq $required })
        $path = Join-Path $CampaignRoot $required
        if ($entry.Count -ne 1 -or
            -not (Test-Path -LiteralPath $path -PathType Leaf) -or
            [string]$entry[0].sha256 -ne (Get-Sha256OrNull -Path $path)) {
            return $false
        }
    }
    return $true
}

function Get-CampaignSnapshot {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter(Mandatory)] [string]$TicketPath
    )
    $ticketValue = Read-JsonOrNull -Path $TicketPath
    $campaignId = if ($null -eq $ticketValue) { $null } else { [string]$ticketValue.campaign_id }
    $campaignRoot = if ([string]::IsNullOrWhiteSpace($campaignId)) {
        $null
    }
    else {
        Resolve-ProjectPath -Root $Root -Value ("runs/campaigns_v4_1/" + $campaignId)
    }
    $summaryPath = if ($null -eq $campaignRoot) { $null } else { Join-Path $campaignRoot 'summary.json' }
    $recoveryPath = if ($null -eq $campaignRoot) { $null } else { Join-Path $campaignRoot 'recovery-state.json' }
    $summary = if ($null -eq $summaryPath) { $null } else { Read-JsonOrNull -Path $summaryPath }
    $recovery = if ($null -eq $recoveryPath) { $null } else { Read-JsonOrNull -Path $recoveryPath }
    $terminal = if ($null -eq $campaignRoot) {
        $false
    }
    else {
        Test-V41TerminalCommit -CampaignRoot $campaignRoot -Summary $summary
    }
    return [pscustomobject][ordered]@{
        campaign_id = $campaignId
        campaign_root = $campaignRoot
        summary_path = $summaryPath
        recovery_path = $recoveryPath
        terminal = $terminal
        incomplete = $null -ne $recovery -and -not $terminal
        phase = if ($terminal) { 'COMPLETE' } elseif ($null -ne $recovery) { $recovery.phase } else { $null }
        scientific_conclusion = if (-not $terminal) { $null } else { $summary.scientific_conclusion }
        stopped_reason = if (-not $terminal) { $null } else { $summary.stopped_reason }
    }
}

$project = [System.IO.Path]::GetFullPath($ProjectRoot)
$python = Resolve-ProjectPath -Root $project -Value '.venv/Scripts/python.exe'
$moduleFile = Resolve-ProjectPath -Root $project -Value 'v4/orchestrator_v4_1.py'
$readinessPath = Resolve-ProjectPath -Root $project -Value $Readiness
$ticketPath = Resolve-ProjectPath -Root $project -Value $Ticket
$statePath = Resolve-ProjectPath -Root $project -Value 'runs/v4_1_campaign.process.json'
$pidPath = Resolve-ProjectPath -Root $project -Value 'runs/v4_1_campaign.pid'
$stdoutPath = Resolve-ProjectPath -Root $project -Value 'runs/v4_1_campaign.stdout.log'
$stderrPath = Resolve-ProjectPath -Root $project -Value 'runs/v4_1_campaign.stderr.log'

$state = Read-JsonOrNull -Path $statePath
$liveProcess = Get-RecordedProcess -State $state
$snapshot = Get-CampaignSnapshot -Root $project -TicketPath $ticketPath

if ($Action -eq 'Status') {
    [pscustomobject][ordered]@{
        controller_version = 'klax-v4.1-background-controller-v1'
        status = if ($null -ne $liveProcess) {
            'RUNNING'
        }
        elseif ($snapshot.terminal) {
            'COMPLETE'
        }
        elseif ($snapshot.incomplete) {
            'INCOMPLETE_EXPLICIT_RESUME_REQUIRED'
        }
        elseif (Test-Path -LiteralPath $ticketPath -PathType Leaf) {
            'TICKET_READY_NOT_STARTED'
        }
        else {
            'NOT_READY_OR_NOT_TICKETED'
        }
        process_live = $null -ne $liveProcess
        pid = if ($null -eq $liveProcess) { $null } else { $liveProcess.Id }
        process_started_at_utc = if ($null -eq $liveProcess) {
            $null
        }
        else {
            $liveProcess.StartTime.ToUniversalTime().ToString('o')
        }
        module_available = Test-Path -LiteralPath $moduleFile -PathType Leaf
        readiness_available = Test-Path -LiteralPath $readinessPath -PathType Leaf
        ticket_available = Test-Path -LiteralPath $ticketPath -PathType Leaf
        process_state_path = $statePath
        stdout_path = $stdoutPath
        stderr_path = $stderrPath
        campaign = $snapshot
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ($null -ne $liveProcess) {
    throw "V4.1 campaign process $($liveProcess.Id) is already running; duplicate launch refused."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python runtime is missing: $python"
}
if (-not (Test-Path -LiteralPath $moduleFile -PathType Leaf)) {
    throw "V4 orchestrator is not implemented yet: $moduleFile"
}
if (-not (Test-Path -LiteralPath $readinessPath -PathType Leaf)) {
    throw "V4.1 readiness artifact is missing: $readinessPath"
}
if (-not (Test-Path -LiteralPath $ticketPath -PathType Leaf)) {
    throw "V4.1 one-use ticket is missing: $ticketPath"
}
if ($snapshot.terminal) {
    throw "V4.1 campaign $($snapshot.campaign_id) is already complete; relaunch refused."
}
if ($Action -eq 'Start' -and $snapshot.incomplete) {
    throw "A V4 recovery state already exists; use -Action Resume."
}
if ($Action -eq 'Resume' -and -not $snapshot.incomplete) {
    throw "Resume requires an incomplete V4 recovery state bound to this ticket."
}

$ticketValue = Read-JsonOrNull -Path $ticketPath
if ([string]::IsNullOrWhiteSpace([string]$ticketValue.campaign_id)) {
    throw 'V4 ticket has no campaign identity.'
}

# Keep one active log set. Preserve a prior stopped attempt before an explicit
# recovery so crash diagnostics are not overwritten.
if ($Action -eq 'Resume') {
    $suffix = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    foreach ($logPath in @($stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $logPath -PathType Leaf) {
            Move-Item -LiteralPath $logPath -Destination ($logPath + '.' + $suffix) -Force
        }
    }
}

$moduleAction = $Action.ToLowerInvariant()
$arguments = @(
    '-m', 'v4.orchestrator_v4_1', $moduleAction,
    '--root', ('"' + $project + '"'),
    '--readiness', ('"' + $readinessPath + '"'),
    '--ticket', ('"' + $ticketPath + '"')
)
$priorPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = (Resolve-ProjectPath -Root $project -Value 'src') + ';' + $project
try {
    $startParameters = @{
        FilePath = $python
        ArgumentList = $arguments
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

try {
    $started = $process.StartTime.ToUniversalTime().ToString('o')
}
catch {
    throw "V4.1 process launched but its start identity could not be recorded. PID: $($process.Id)"
}

$record = [pscustomobject][ordered]@{
    controller_version = 'klax-v4.1-background-controller-v1'
    status = 'LAUNCHED'
    action = $moduleAction
    campaign_id = [string]$ticketValue.campaign_id
    pid = $process.Id
    process_started_at_utc = $started
    launched_at_utc = [DateTime]::UtcNow.ToString('o')
    project_root = $project
    python_path = $python
    module = 'v4.orchestrator_v4_1'
    readiness_path = $readinessPath
    readiness_sha256 = Get-Sha256OrNull -Path $readinessPath
    ticket_path = $ticketPath
    ticket_sha256 = Get-Sha256OrNull -Path $ticketPath
    stdout_path = $stdoutPath
    stderr_path = $stderrPath
    protected_final_read = $false
    actual_orders_placed = $false
}
Write-AtomicJson -Path $statePath -Value $record
[System.IO.File]::WriteAllText(
    $pidPath,
    [string]$process.Id + [Environment]::NewLine,
    [System.Text.Encoding]::ASCII)
$record | ConvertTo-Json -Depth 8
