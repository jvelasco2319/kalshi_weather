param(
    [int]$PollSeconds = 30,
    [int]$MaximumAcquisitionAttempts = 3,
    [switch]$RunIdentityFixtureTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $projectRoot 'runs\weather_v3_bulk.pid'
$normalizerPidPath = Join-Path $projectRoot 'runs\weather_v3_normalize.pid'
$progressPath = Join-Path $projectRoot 'data\manifests\v3_weather_bulk_progress.json'
$statePath = Join-Path $projectRoot 'data\manifests\v3_weather_handoff_status.json'
$attemptsPath = Join-Path $projectRoot 'data\manifests\v3_weather_acquisition_attempts.json'
$launchIntentPath = Join-Path $projectRoot 'runs\weather_v3_bulk.launch-intent.json'
$normalizerIdentityPath = Join-Path $projectRoot 'runs\weather_v3_normalize.process.json'
$watcherLeasePath = Join-Path $projectRoot 'runs\weather_v3_handoff.watcher-lease.json'
$watcherPidPath = Join-Path $projectRoot 'runs\weather_v3_handoff.pid'
$bulkStdoutPath = Join-Path $projectRoot 'runs\weather_v3_bulk.stdout.log'
$bulkStderrPath = Join-Path $projectRoot 'runs\weather_v3_bulk.stderr.log'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$rawModule = 'klax_lab.acquire_weather_v3'
$rawCommand = 'registered-bulk'
$normalizerModule = 'klax_lab.weather_normalize_v3'

function Write-AtomicText {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value,
        [ValidateSet('ascii', 'utf8')][string]$Encoding = 'utf8'
    )
    $directory = Split-Path -Parent $LiteralPath
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $tempPath = "$LiteralPath.$([Guid]::NewGuid().ToString('N')).tmp"
    try {
        Set-Content -LiteralPath $tempPath -Value $Value -Encoding $Encoding
        Move-Item -LiteralPath $tempPath -Destination $LiteralPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
}

function Write-AtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 12
    )
    Write-AtomicText -LiteralPath $LiteralPath -Value ($Value | ConvertTo-Json -Depth $Depth) -Encoding utf8
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    try {
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = $algorithm.ComputeHash($stream)
            return ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
        }
        finally {
            $algorithm.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Write-HandoffState {
    param([string]$Status, [string]$Detail)
    $body = [ordered]@{
        schema_version = 1
        component = 'weather_download_to_normalization_handoff'
        status = $Status
        detail = $Detail
        protected_final_read = $false
        network_used_by_handoff = $false
        updated_at_utc = [DateTime]::UtcNow.ToString('o')
    }
    Write-AtomicJson -LiteralPath $statePath -Value $body -Depth 4
}

function Get-Sha256Text {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-HandoffWatcherMutexName {
    param([Parameter(Mandatory = $true)][string]$Root)
    $rootIdentity = (Get-Sha256Text -Value $Root.ToLowerInvariant()).Substring(0, 32)
    return "Local\klax-weather-v3-handoff-$rootIdentity"
}

function Enter-HandoffWatcherLease {
    param(
        [Parameter(Mandatory = $true)][string]$MutexName,
        [Parameter(Mandatory = $true)][string]$LeasePath,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$LegacyPidPath
    )
    $mutex = [System.Threading.Mutex]::new($false, $MutexName)
    $ownsMutex = $false
    try {
        try {
            $ownsMutex = $mutex.WaitOne(0)
        }
        catch [System.Threading.AbandonedMutexException] {
            $ownsMutex = $true
        }
        if (-not $ownsMutex) {
            $mutex.Dispose()
            return [pscustomobject]@{
                acquired = $false
                reason = 'named_mutex_owned_by_live_watcher'
                mutex = $null
                lease = $null
            }
        }

        $takeover = $null
        $legacyPidObservation = $null
        if (Test-Path -LiteralPath $LeasePath) {
            $previousHash = Get-Sha256Hex -LiteralPath $LeasePath
            try {
                $previous = Get-Content -LiteralPath $LeasePath -Raw | ConvertFrom-Json
            }
            catch {
                throw 'Existing watcher lease is unreadable; refusing an unauditable takeover.'
            }
            if ($previous.schema_version -ne 1 -or [int]$previous.watcher_pid -le 0 -or
                    $null -eq $previous.watcher_started_at_utc -or
                    -not [string]::Equals([string]$previous.project_root, $Root, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw 'Existing watcher lease has an invalid identity; refusing an unauditable takeover.'
            }
            $previousProcess = Get-Process -Id ([int]$previous.watcher_pid) -ErrorAction SilentlyContinue
            if ($null -ne $previousProcess -and
                    (Test-StartTimeBinding -ObservedStartUtc $previousProcess.StartTime.ToUniversalTime() `
                        -RecordedStartUtc $previous.watcher_started_at_utc)) {
                $mutex.ReleaseMutex()
                $ownsMutex = $false
                $mutex.Dispose()
                return [pscustomobject]@{
                    acquired = $false
                    reason = 'lease_names_same_live_process'
                    mutex = $null
                    lease = $previous
                }
            }
            $takeoverReason = if ($null -eq $previousProcess) { 'previous_process_dead' } else { 'pid_start_mismatch' }
            $takeover = [ordered]@{
                prior_lease_sha256 = $previousHash
                prior_watcher_pid = [int]$previous.watcher_pid
                prior_watcher_started_at_utc = ConvertTo-UtcRoundtrip -Value $previous.watcher_started_at_utc
                reason = $takeoverReason
                taken_over_at_utc = [DateTime]::UtcNow.ToString('o')
            }
        }
        elseif (-not [string]::IsNullOrWhiteSpace($LegacyPidPath) -and (Test-Path -LiteralPath $LegacyPidPath)) {
            $legacyPidFile = Get-Item -LiteralPath $LegacyPidPath
            $legacyPid = [int](Get-Content -LiteralPath $LegacyPidPath -Raw)
            $legacyProcess = Get-Process -Id $legacyPid -ErrorAction SilentlyContinue
            $legacyStartMatches = $false
            if ($null -ne $legacyProcess) {
                $legacyStartMatches = [Math]::Abs(
                    ($legacyProcess.StartTime.ToUniversalTime() - $legacyPidFile.LastWriteTimeUtc).TotalSeconds) -le 5
            }
            $legacyDisposition = if ($null -eq $legacyProcess) {
                'previous_process_dead'
            }
            elseif ($legacyPid -eq $PID) {
                'current_watcher_pid'
            }
            elseif ($legacyStartMatches) {
                'same_live_legacy_watcher'
            }
            else {
                'pid_start_mismatch'
            }
            $legacyPidObservation = [ordered]@{
                path = $LegacyPidPath
                pid = $legacyPid
                pid_file_written_at_utc = $legacyPidFile.LastWriteTimeUtc.ToString('o')
                observed_process_started_at_utc = $(if ($null -ne $legacyProcess) {
                    $legacyProcess.StartTime.ToUniversalTime().ToString('o')
                } else { $null })
                disposition = $legacyDisposition
            }
            if ($legacyDisposition -eq 'same_live_legacy_watcher') {
                $mutex.ReleaseMutex()
                $ownsMutex = $false
                $mutex.Dispose()
                return [pscustomobject]@{
                    acquired = $false
                    reason = 'legacy_pid_names_same_live_watcher'
                    mutex = $null
                    lease = $null
                }
            }
        }

        $self = Get-Process -Id $PID -ErrorAction Stop
        $scriptResolved = (Resolve-Path -LiteralPath $ScriptPath).Path
        $lease = [pscustomobject][ordered]@{
            schema_version = 1
            component = 'weather_v3_handoff_watcher_lease'
            status = 'ACTIVE'
            mutex_name = $MutexName
            watcher_pid = $PID
            watcher_started_at_utc = $self.StartTime.ToUniversalTime().ToString('o')
            watcher_executable_path = [string]$self.Path
            project_root = $Root
            script_path = $scriptResolved
            script_sha256 = Get-Sha256Hex -LiteralPath $scriptResolved
            command_identity = [ordered]@{
                entrypoint = 'continue_weather_v3.ps1'
                command_line_sha256 = Get-Sha256Text -Value ([Environment]::CommandLine)
                poll_seconds = $PollSeconds
                maximum_acquisition_attempts = $MaximumAcquisitionAttempts
                raw_module = $rawModule
                raw_command = $rawCommand
                normalizer_module = $normalizerModule
            }
            takeover = $takeover
            legacy_pid_observation = $legacyPidObservation
            acquired_at_utc = [DateTime]::UtcNow.ToString('o')
            released_at_utc = $null
        }
        Write-AtomicJson -LiteralPath $LeasePath -Value $lease -Depth 8
        return [pscustomobject]@{
            acquired = $true
            reason = 'acquired'
            mutex = $mutex
            lease = $lease
        }
    }
    catch {
        if ($ownsMutex) {
            try { $mutex.ReleaseMutex() } catch { }
        }
        $mutex.Dispose()
        throw
    }
}

function Exit-HandoffWatcherLease {
    param(
        [Parameter(Mandatory = $true)]$Handle,
        [Parameter(Mandatory = $true)][string]$LeasePath
    )
    if ($Handle.acquired -ne $true -or $null -eq $Handle.mutex) {
        return
    }
    try {
        if (Test-Path -LiteralPath $LeasePath) {
            $lease = Get-Content -LiteralPath $LeasePath -Raw | ConvertFrom-Json
            $self = Get-Process -Id $PID -ErrorAction SilentlyContinue
            if ($null -ne $self -and [int]$lease.watcher_pid -eq $PID -and
                    (Test-StartTimeBinding -ObservedStartUtc $self.StartTime.ToUniversalTime() `
                        -RecordedStartUtc $lease.watcher_started_at_utc)) {
                $lease.status = 'RELEASED'
                $lease.released_at_utc = [DateTime]::UtcNow.ToString('o')
                Write-AtomicJson -LiteralPath $LeasePath -Value $lease -Depth 8
            }
        }
    }
    finally {
        try { $Handle.mutex.ReleaseMutex() } finally { $Handle.mutex.Dispose() }
    }
}

function Test-CommandLineBinding {
    param(
        [Parameter(Mandatory = $true)][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$Command
    )
    $modulePattern = '(?i)(?:^|\s)-m\s+(?:"' + [regex]::Escape($Module) + '"|' + [regex]::Escape($Module) + ')(?=\s|$)'
    $rootPattern = '(?i)(?:^|\s)--root\s+(?:"' + [regex]::Escape($Root) + '"|' + [regex]::Escape($Root) + ')(?=\s|$)'
    if ($CommandLine -notmatch $modulePattern -or $CommandLine -notmatch $rootPattern) {
        return $false
    }
    if (-not [string]::IsNullOrWhiteSpace($Command)) {
        $commandPattern = '(?i)(?:^|\s)(?:"' + [regex]::Escape($Command) + '"|' + [regex]::Escape($Command) + ')(?=\s|$)'
        if ($CommandLine -notmatch $commandPattern) {
            return $false
        }
    }
    return $true
}

function Test-StartTimeBinding {
    param(
        [Parameter(Mandatory = $true)][DateTime]$ObservedStartUtc,
        [Parameter(Mandatory = $true)]$RecordedStartUtc,
        [double]$ToleranceSeconds = 2
    )
    $recorded = ConvertTo-UtcDateTime -Value $RecordedStartUtc
    return [Math]::Abs(($ObservedStartUtc.ToUniversalTime() - $recorded).TotalSeconds) -le $ToleranceSeconds
}

function ConvertTo-UtcDateTime {
    param([Parameter(Mandatory = $true)]$Value)
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToUniversalTime()
    }
    return [DateTime]::Parse(
        [string]$Value,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
}

function ConvertTo-UtcRoundtrip {
    param([Parameter(Mandatory = $true)]$Value)
    return (ConvertTo-UtcDateTime -Value $Value).ToString('o')
}

function Get-BoundPythonProcess {
    param(
        [int]$ProcessId,
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$Command,
        $RecordedStartUtc
    )
    $candidate = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $candidate) {
        return $null
    }
    if (-not [string]::Equals(
            [string]$candidate.Path,
            [string]$pythonPath,
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "PID $ProcessId exists but is not the registered project Python runtime."
    }
    try {
        $record = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
    }
    catch {
        throw "Cannot read the command line for PID $ProcessId; process identity cannot be proven. $($_.Exception.Message)"
    }
    if ($null -eq $record -or [string]::IsNullOrWhiteSpace([string]$record.CommandLine)) {
        throw "PID $ProcessId exists but has no readable command line; process identity cannot be proven."
    }
    $commandLine = [string]$record.CommandLine
    if (-not (Test-CommandLineBinding -CommandLine $commandLine -Module $Module -Root $Root -Command $Command)) {
        throw "PID $ProcessId command line is not bound to module $Module and project root $Root."
    }
    $startedUtc = $candidate.StartTime.ToUniversalTime()
    if ($null -ne $RecordedStartUtc -and -not [string]::IsNullOrWhiteSpace([string]$RecordedStartUtc)) {
        if (-not (Test-StartTimeBinding -ObservedStartUtc $startedUtc -RecordedStartUtc $RecordedStartUtc)) {
            throw "PID $ProcessId start time does not match its durable process record; refusing a stale or reused PID."
        }
    }
    $candidate | Add-Member -NotePropertyName VerifiedCommandLine -NotePropertyValue $commandLine -Force
    $candidate | Add-Member -NotePropertyName VerifiedStartedAtUtc -NotePropertyValue ($startedUtc.ToString('o')) -Force
    return $candidate
}

function Get-TrackedRawProcess {
    param([int]$ProcessId)
    $journal = Read-AcquisitionJournal
    $attempt = @($journal.attempts | Where-Object { [int]$_.pid -eq $ProcessId }) | Select-Object -Last 1
    $candidate = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $candidate) {
        return $null
    }
    if ($null -eq $attempt -or [string]::IsNullOrWhiteSpace([string]$attempt.started_at_utc)) {
        throw "Live raw PID $ProcessId has no durable journal identity."
    }
    return Get-BoundPythonProcess -ProcessId $ProcessId -Module $rawModule -Root $projectRoot `
        -Command $rawCommand -RecordedStartUtc $attempt.started_at_utc
}

function Get-TrackedNormalizerProcess {
    param([int]$ProcessId)
    $candidate = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $candidate) {
        return $null
    }
    $recordedStartUtc = $null
    $identity = $null
    if (Test-Path -LiteralPath $normalizerIdentityPath) {
        $identity = Get-Content -LiteralPath $normalizerIdentityPath -Raw | ConvertFrom-Json
        if ([int]$identity.pid -eq $ProcessId) {
            $recordedStartUtc = $identity.started_at_utc
        }
    }
    if ($null -eq $recordedStartUtc -or [string]::IsNullOrWhiteSpace([string]$recordedStartUtc)) {
        $pidFile = Get-Item -LiteralPath $normalizerPidPath
        $observedStart = $candidate.StartTime.ToUniversalTime()
        if ([Math]::Abs(($observedStart - $pidFile.LastWriteTimeUtc).TotalSeconds) -gt 5) {
            throw "Normalizer PID $ProcessId has no durable start record and the PID-file timestamp does not match process start."
        }
        $bound = Get-BoundPythonProcess -ProcessId $ProcessId -Module $normalizerModule -Root $projectRoot
        $identity = [ordered]@{
            schema_version = 1
            component = 'weather_v3_rolling_normalizer_process_identity'
            pid = $ProcessId
            started_at_utc = [string]$bound.VerifiedStartedAtUtc
            executable_path = $pythonPath
            module = $normalizerModule
            project_root = $projectRoot
            command_line_sha256 = Get-Sha256Text -Value ([string]$bound.VerifiedCommandLine)
            identity_source = 'pid_file_timestamp_and_live_command_line'
            recorded_at_utc = [DateTime]::UtcNow.ToString('o')
        }
        Write-AtomicJson -LiteralPath $normalizerIdentityPath -Value $identity
        $recordedStartUtc = [string]$identity.started_at_utc
    }
    if ([int]$identity.pid -ne $ProcessId -or
            [string]$identity.module -ne $normalizerModule -or
            -not [string]::Equals([string]$identity.project_root, $projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Normalizer PID $ProcessId does not match its durable process identity record."
    }
    $bound = Get-BoundPythonProcess -ProcessId $ProcessId -Module $normalizerModule -Root $projectRoot `
        -RecordedStartUtc $recordedStartUtc
    if ((Get-Sha256Text -Value ([string]$bound.VerifiedCommandLine)) -ne [string]$identity.command_line_sha256) {
        throw "Normalizer PID $ProcessId command line changed from its durable process identity record."
    }
    return $bound
}

function Read-AcquisitionJournal {
    if (-not (Test-Path -LiteralPath $attemptsPath)) {
        throw "Missing acquisition-attempt journal: $attemptsPath"
    }
    $journal = Get-Content -LiteralPath $attemptsPath -Raw | ConvertFrom-Json
    if ($journal.schema_version -notin @(2, 3) -or
            $journal.recovery_policy.maximum_process_attempts -ne $MaximumAcquisitionAttempts -or
            $journal.recovery_policy.scope_change_allowed -ne $false -or
            $journal.recovery_policy.byte_cap_change_allowed -ne $false -or
            $journal.recovery_policy.protected_final_read_allowed -ne $false) {
        throw 'Acquisition recovery policy differs from the persisted finite journal.'
    }
    return $journal
}

function Test-AdministrativeReplacementEligible {
    param($Journal)
    if ($Journal.schema_version -ne 3 -or
            $Journal.recovery_policy.maximum_pretransfer_environment_replacement_launches -ne 1 -or
            $Journal.recovery_policy.maximum_total_process_launches -ne ($MaximumAcquisitionAttempts + 1) -or
            @($Journal.attempts).Count -ne $MaximumAcquisitionAttempts) {
        return $false
    }
    $failed = @($Journal.attempts)[-1]
    if ($failed.failure_class -ne 'PermissionError.WinError10013' -or
            $failed.failure_context -ne 'local_socket_permission_denied' -or
            $failed.network_requests -ne 0 -or
            $failed.new_transfer_bytes -ne 0 -or
            $failed.administrative_replacement_eligible -ne $true) {
        return $false
    }
    $amendment = $Journal.administrative_replacement_amendment
    if ($null -eq $amendment -or [string]::IsNullOrWhiteSpace([string]$amendment.path) -or
            [string]::IsNullOrWhiteSpace([string]$amendment.sha256)) {
        return $false
    }
    $amendmentPath = Join-Path $projectRoot ([string]$amendment.path)
    return ((Test-Path -LiteralPath $amendmentPath) -and
        (Get-Sha256Hex -LiteralPath $amendmentPath) -eq [string]$amendment.sha256)
}

function Write-AcquisitionJournal {
    param($Journal)
    $Journal.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-AtomicJson -LiteralPath $attemptsPath -Value $Journal -Depth 12
}

function Get-TransientAcquisitionFailure {
    if (-not (Test-Path -LiteralPath $bulkStderrPath)) {
        return $false
    }
    $stderr = Get-Content -LiteralPath $bulkStderrPath -Raw
    return $stderr -match 'requests\.exceptions\.(ReadTimeout|ConnectTimeout|ConnectionError|ChunkedEncodingError)|urllib3\.exceptions\.(ReadTimeoutError|ProtocolError|NewConnectionError)'
}

function Complete-AcquisitionAttempt {
    param(
        [int]$ProcessId,
        [string]$TerminalStatus,
        [switch]$RecoveryExhausted
    )
    $journal = Read-AcquisitionJournal
    $attempt = $journal.attempts | Where-Object { [int]$_.pid -eq $ProcessId } | Select-Object -Last 1
    if ($null -eq $attempt) {
        throw "Acquisition journal does not contain PID $ProcessId."
    }
    $currentStatus = [string]$attempt.terminal_status
    $isActive = ($currentStatus -eq 'LAUNCH_RESERVED_NOT_DISPATCHED' -or
        $currentStatus -like 'RUNNING*')
    if (-not $isActive) {
        if ($currentStatus -eq $TerminalStatus) {
            return
        }
        throw "Acquisition attempt PID $ProcessId is already terminal as $currentStatus; refusing rewrite to $TerminalStatus."
    }
    $progress = if (Test-Path -LiteralPath $progressPath) {
        Get-Content -LiteralPath $progressPath -Raw | ConvertFrom-Json
    } else { $null }
    $attempt.terminal_status = $TerminalStatus
    $attempt | Add-Member -NotePropertyName ended_at_utc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    $attempt | Add-Member -NotePropertyName committed_days -NotePropertyValue $(if ($null -ne $progress) { [int]$progress.days_complete } else { 0 }) -Force
    $lastCommittedDate = $null
    if ($null -ne $progress) {
        $lastCommittedDate = [string]$progress.last_complete_date
        if ([string]::IsNullOrWhiteSpace($lastCommittedDate) -and @($progress.days).Count -gt 0) {
            $lastCommittedDate = [string]$progress.days[-1].date
        }
    }
    if ($null -ne $progress) {
        $attempt | Add-Member -NotePropertyName network_requests -NotePropertyValue ([int](
            @($progress.days | ForEach-Object { [int]$_.network_requests } | Measure-Object -Sum).Sum)) -Force
        [int64]$dailyTransferBytes = 0
        foreach ($day in @($progress.days)) {
            $dailyTransferBytes += [int64]$day.new_transfer_bytes
        }
        $attempt | Add-Member -NotePropertyName new_transfer_bytes -NotePropertyValue $dailyTransferBytes -Force
    }
    $attempt | Add-Member -NotePropertyName last_committed_date -NotePropertyValue $lastCommittedDate -Force
    if (Test-Path -LiteralPath $bulkStdoutPath) {
        $attempt | Add-Member -NotePropertyName stdout_sha256 -NotePropertyValue (Get-Sha256Hex -LiteralPath $bulkStdoutPath) -Force
    }
    if (Test-Path -LiteralPath $bulkStderrPath) {
        $attempt | Add-Member -NotePropertyName stderr_sha256 -NotePropertyValue (Get-Sha256Hex -LiteralPath $bulkStderrPath) -Force
        $stderr = Get-Content -LiteralPath $bulkStderrPath -Raw
        if ($stderr -match 'WinError 10013') {
            $attempt | Add-Member -NotePropertyName failure_class -NotePropertyValue 'PermissionError.WinError10013' -Force
            $attempt | Add-Member -NotePropertyName failure_context -NotePropertyValue 'local_socket_permission_denied' -Force
        }
    }
    if ($RecoveryExhausted) {
        $attempt | Add-Member -NotePropertyName recovery_exhausted -NotePropertyValue $true -Force
        $attempt | Add-Member -NotePropertyName recovery_exhaustion_reason -NotePropertyValue 'registered_process_attempt_ceiling_reached' -Force
    }
    Write-AcquisitionJournal -Journal $journal
}

function Assert-LaunchIntentBinding {
    param($Intent)
    if ($null -eq $Intent -or $Intent.schema_version -ne 1 -or
            [string]$Intent.module -ne $rawModule -or
            [string]$Intent.command -ne $rawCommand -or
            -not [string]::Equals([string]$Intent.project_root, $projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals([string]$Intent.executable_path, $pythonPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Durable raw-acquisition launch intent is not bound to this project, module, command, and runtime.'
    }
}

function Write-LaunchIntent {
    param($Intent)
    $Intent.updated_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-AtomicJson -LiteralPath $launchIntentPath -Value $Intent -Depth 8
}

function Add-ReservedAttemptFromIntent {
    param($Intent)
    $journal = Read-AcquisitionJournal
    $existing = @($journal.attempts | Where-Object { [string]$_.launch_id -eq [string]$Intent.launch_id }) | Select-Object -Last 1
    if ($null -ne $existing) {
        return
    }
    if (@($journal.attempts).Count + 1 -ne [int]$Intent.attempt) {
        throw 'Launch-intent attempt number does not follow the durable attempt journal.'
    }
    if ([int]$Intent.attempt -gt $MaximumAcquisitionAttempts) {
        if ([int]$Intent.attempt -ne ($MaximumAcquisitionAttempts + 1) -or
                $Intent.administrative_replacement -ne $true -or
                -not (Test-AdministrativeReplacementEligible -Journal $journal)) {
            throw 'Launch intent exceeds the registered process ceiling or lacks the exact eligible administrative replacement.'
        }
    }
    elseif ($Intent.administrative_replacement -eq $true) {
        throw 'Launch intent marks an ordinary attempt as an administrative replacement.'
    }
    $reserved = [pscustomobject][ordered]@{
        attempt = [int]$Intent.attempt
        pid = 0
        started_at_utc = $null
        terminal_status = 'LAUNCH_RESERVED_NOT_DISPATCHED'
        launch_id = [string]$Intent.launch_id
        launch_intent_path = 'runs/weather_v3_bulk.launch-intent.json'
        stdout_path = 'runs/weather_v3_bulk.stdout.log'
        stderr_path = 'runs/weather_v3_bulk.stderr.log'
    }
    if ($Intent.administrative_replacement -eq $true) {
        $reserved | Add-Member -NotePropertyName administrative_replacement -NotePropertyValue $true
        $reserved | Add-Member -NotePropertyName replacement_for_attempt -NotePropertyValue $MaximumAcquisitionAttempts
    }
    $journal.attempts = @($journal.attempts) + $reserved
    Write-AcquisitionJournal -Journal $journal
}

function Register-IdentifiedLaunch {
    param($Intent)
    Assert-LaunchIntentBinding -Intent $Intent
    if ([int]$Intent.pid -le 0 -or [string]::IsNullOrWhiteSpace([string]$Intent.started_at_utc)) {
        throw 'Identified launch intent is missing PID or process start time.'
    }
    $journal = Read-AcquisitionJournal
    $attempt = @($journal.attempts | Where-Object { [string]$_.launch_id -eq [string]$Intent.launch_id }) | Select-Object -Last 1
    if ($null -eq $attempt) {
        throw 'Identified process has no pre-launch reservation in the attempt journal.'
    }
    $attempt.pid = [int]$Intent.pid
    $attempt.started_at_utc = ConvertTo-UtcRoundtrip -Value $Intent.started_at_utc
    $attempt.terminal_status = 'RUNNING_RESUME_FROM_VERIFIED_CACHE'
    $attempt | Add-Member -NotePropertyName executable_path -NotePropertyValue $pythonPath -Force
    $attempt | Add-Member -NotePropertyName module -NotePropertyValue $rawModule -Force
    $attempt | Add-Member -NotePropertyName command -NotePropertyValue $rawCommand -Force
    $attempt | Add-Member -NotePropertyName project_root -NotePropertyValue $projectRoot -Force
    $attempt | Add-Member -NotePropertyName command_line_sha256 -NotePropertyValue ([string]$Intent.command_line_sha256) -Force
    Write-AcquisitionJournal -Journal $journal
    $Intent.status = 'JOURNALED_PROCESS_IDENTITY'
    Write-LaunchIntent -Intent $Intent
    Write-AtomicText -LiteralPath $pidPath -Value ([string]$Intent.pid) -Encoding ascii
    $Intent.status = 'REGISTERED'
    $Intent.pid_file_registered = $true
    Write-LaunchIntent -Intent $Intent
    return [int]$Intent.pid
}

function Find-LaunchIntentCandidates {
    param($Intent)
    $notBefore = (ConvertTo-UtcDateTime -Value $Intent.dispatch_authorized_at_utc).AddSeconds(-2)
    try {
        $records = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe'" -ErrorAction Stop)
    }
    catch {
        throw "Cannot enumerate Python command lines to recover the durable launch intent. $($_.Exception.Message)"
    }
    $matches = @()
    foreach ($record in $records) {
        if ([string]::IsNullOrWhiteSpace([string]$record.CommandLine) -or
                -not (Test-CommandLineBinding -CommandLine ([string]$record.CommandLine) -Module $rawModule -Root $projectRoot -Command $rawCommand)) {
            continue
        }
        $process = Get-Process -Id ([int]$record.ProcessId) -ErrorAction SilentlyContinue
        if ($null -ne $process -and $process.StartTime.ToUniversalTime() -ge $notBefore -and
                [string]::Equals([string]$process.Path, $pythonPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            $process | Add-Member -NotePropertyName VerifiedCommandLine -NotePropertyValue ([string]$record.CommandLine) -Force
            $matches += $process
        }
    }
    return @($matches)
}

function Resolve-DurableLaunchIntent {
    if (-not (Test-Path -LiteralPath $launchIntentPath)) {
        return $null
    }
    $intent = Get-Content -LiteralPath $launchIntentPath -Raw | ConvertFrom-Json
    Assert-LaunchIntentBinding -Intent $intent
    if ([string]$intent.status -eq 'PREPARED') {
        Add-ReservedAttemptFromIntent -Intent $intent
        $intent.status = 'JOURNAL_SLOT_RESERVED'
        Write-LaunchIntent -Intent $intent
        return Invoke-ReservedAcquisitionLaunch -Intent $intent
    }
    if ([string]$intent.status -eq 'JOURNAL_SLOT_RESERVED') {
        return Invoke-ReservedAcquisitionLaunch -Intent $intent
    }
    if ([string]$intent.status -eq 'DISPATCH_AUTHORIZED') {
        $matches = @(Find-LaunchIntentCandidates -Intent $intent)
        if ($matches.Count -ne 1) {
            throw "Launch intent $($intent.launch_id) was dispatch-authorized but has $($matches.Count) matching live processes; refusing to launch again or guess a PID."
        }
        $matched = $matches[0]
        $intent.pid = [int]$matched.Id
        $intent.started_at_utc = $matched.StartTime.ToUniversalTime().ToString('o')
        $intent.command_line_sha256 = Get-Sha256Text -Value ([string]$matched.VerifiedCommandLine)
        $intent.status = 'PROCESS_IDENTIFIED'
        Write-LaunchIntent -Intent $intent
    }
    if ([string]$intent.status -in @('PROCESS_IDENTIFIED', 'JOURNALED_PROCESS_IDENTITY')) {
        $live = Get-Process -Id ([int]$intent.pid) -ErrorAction SilentlyContinue
        if ($null -ne $live) {
            $bound = Get-BoundPythonProcess -ProcessId ([int]$intent.pid) -Module $rawModule -Root $projectRoot `
                -Command $rawCommand -RecordedStartUtc $intent.started_at_utc
            if ((Get-Sha256Text -Value ([string]$bound.VerifiedCommandLine)) -ne [string]$intent.command_line_sha256) {
                throw 'Recovered raw-process command line differs from its durable launch intent.'
            }
        }
        return Register-IdentifiedLaunch -Intent $intent
    }
    if ([string]$intent.status -eq 'REGISTERED') {
        $journal = Read-AcquisitionJournal
        $attempt = @($journal.attempts | Where-Object { [string]$_.launch_id -eq [string]$intent.launch_id }) | Select-Object -Last 1
        if ($null -eq $attempt -or [int]$attempt.pid -ne [int]$intent.pid -or
                (ConvertTo-UtcRoundtrip -Value $attempt.started_at_utc) -ne (ConvertTo-UtcRoundtrip -Value $intent.started_at_utc)) {
            throw 'Registered launch intent differs from its durable attempt-journal identity.'
        }
        Write-AtomicText -LiteralPath $pidPath -Value ([string]$intent.pid) -Encoding ascii
        return [int]$intent.pid
    }
    throw "Unknown durable launch-intent status: $($intent.status)"
}

function Invoke-ReservedAcquisitionLaunch {
    param($Intent)
    Assert-LaunchIntentBinding -Intent $Intent
    if ([string]$Intent.status -ne 'JOURNAL_SLOT_RESERVED') {
        throw 'Raw acquisition can only launch from a durable reserved journal slot.'
    }
    $Intent.status = 'DISPATCH_AUTHORIZED'
    $Intent.dispatch_authorized_at_utc = [DateTime]::UtcNow.ToString('o')
    Write-LaunchIntent -Intent $Intent
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    $arguments = @('-m', $rawModule, $rawCommand, '--root', $projectRoot)
    $process = Start-Process -FilePath $pythonPath -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $bulkStdoutPath -RedirectStandardError $bulkStderrPath `
        -WindowStyle Hidden -PassThru
    $bound = Get-BoundPythonProcess -ProcessId ([int]$process.Id) -Module $rawModule -Root $projectRoot -Command $rawCommand
    $Intent.pid = [int]$process.Id
    $Intent.started_at_utc = [string]$bound.VerifiedStartedAtUtc
    $Intent.command_line_sha256 = Get-Sha256Text -Value ([string]$bound.VerifiedCommandLine)
    $Intent.status = 'PROCESS_IDENTIFIED'
    Write-LaunchIntent -Intent $Intent
    return Register-IdentifiedLaunch -Intent $Intent
}

function Start-ResumedAcquisition {
    if (Test-Path -LiteralPath $launchIntentPath) {
        $pending = Get-Content -LiteralPath $launchIntentPath -Raw | ConvertFrom-Json
        Assert-LaunchIntentBinding -Intent $pending
        if ([string]$pending.status -eq 'PREPARED') {
            Add-ReservedAttemptFromIntent -Intent $pending
            $pending.status = 'JOURNAL_SLOT_RESERVED'
            Write-LaunchIntent -Intent $pending
        }
        if ([string]$pending.status -eq 'JOURNAL_SLOT_RESERVED') {
            return Invoke-ReservedAcquisitionLaunch -Intent $pending
        }
        if ([string]$pending.status -ne 'REGISTERED') {
            return Resolve-DurableLaunchIntent
        }
    }

    $journal = Read-AcquisitionJournal
    $attemptNumber = @($journal.attempts).Count + 1
    $administrativeReplacement = $false
    if ($attemptNumber -gt $MaximumAcquisitionAttempts) {
        if (-not (Test-AdministrativeReplacementEligible -Journal $journal)) {
            throw "Registered acquisition process-attempt ceiling of $MaximumAcquisitionAttempts is exhausted."
        }
        $administrativeReplacement = $true
    }
    $previousAttempt = $attemptNumber - 1
    $archivedStdoutRelative = "runs/weather_v3_bulk.attempt-$previousAttempt.stdout.log"
    $archivedStderrRelative = "runs/weather_v3_bulk.attempt-$previousAttempt.stderr.log"
    $archivedStdout = Join-Path $projectRoot $archivedStdoutRelative
    $archivedStderr = Join-Path $projectRoot $archivedStderrRelative
    Copy-Item -LiteralPath $bulkStdoutPath -Destination $archivedStdout -Force
    Copy-Item -LiteralPath $bulkStderrPath -Destination $archivedStderr -Force
    $previous = @($journal.attempts)[-1]
    if ((Get-Sha256Hex -LiteralPath $archivedStdout) -ne $previous.stdout_sha256 -or
            (Get-Sha256Hex -LiteralPath $archivedStderr) -ne $previous.stderr_sha256) {
        throw 'Archived acquisition-attempt log differs from its terminal hash.'
    }
    $previous.stdout_path = $archivedStdoutRelative
    $previous.stderr_path = $archivedStderrRelative
    Write-AcquisitionJournal -Journal $journal

    $intent = [pscustomobject][ordered]@{
        schema_version = 1
        component = 'weather_v3_raw_acquisition_launch_intent'
        status = 'PREPARED'
        launch_id = [Guid]::NewGuid().ToString('N')
        attempt = $attemptNumber
        administrative_replacement = $administrativeReplacement
        executable_path = $pythonPath
        module = $rawModule
        command = $rawCommand
        project_root = $projectRoot
        prepared_at_utc = [DateTime]::UtcNow.ToString('o')
        dispatch_authorized_at_utc = $null
        updated_at_utc = [DateTime]::UtcNow.ToString('o')
        pid = 0
        started_at_utc = $null
        command_line_sha256 = $null
        pid_file_registered = $false
    }
    Write-LaunchIntent -Intent $intent
    Add-ReservedAttemptFromIntent -Intent $intent
    $intent.status = 'JOURNAL_SLOT_RESERVED'
    Write-LaunchIntent -Intent $intent
    return Invoke-ReservedAcquisitionLaunch -Intent $intent
}

function Invoke-IdentityFixtureTests {
    $fixtureRoot = 'C:\fixture root\weather'
    $validRaw = '"C:\fixture root\weather\.venv\Scripts\python.exe" -m klax_lab.acquire_weather_v3 registered-bulk --root "C:\fixture root\weather"'
    $validNormalizer = '"C:\fixture root\weather\.venv\Scripts\python.exe" -m klax_lab.weather_normalize_v3 --root "C:\fixture root\weather"'
    if (-not (Test-CommandLineBinding -CommandLine $validRaw -Module $rawModule -Root $fixtureRoot -Command $rawCommand)) {
        throw 'Identity fixture rejected a valid raw acquisition command line.'
    }
    if (-not (Test-CommandLineBinding -CommandLine $validNormalizer -Module $normalizerModule -Root $fixtureRoot)) {
        throw 'Identity fixture rejected a valid normalizer command line.'
    }
    if (Test-CommandLineBinding -CommandLine $validRaw -Module $rawModule -Root 'C:\other' -Command $rawCommand) {
        throw 'Identity fixture accepted the wrong project root.'
    }
    if (Test-CommandLineBinding -CommandLine $validRaw -Module 'klax_lab.weather_normalize_v3' -Root $fixtureRoot) {
        throw 'Identity fixture accepted the wrong module.'
    }
    if (Test-CommandLineBinding -CommandLine ($validRaw -replace 'registered-bulk', 'other-command') `
            -Module $rawModule -Root $fixtureRoot -Command $rawCommand) {
        throw 'Identity fixture accepted the wrong acquisition command.'
    }
    $fixtureStart = [DateTime]::Parse('2026-09-26T12:03:02.1231528Z').ToUniversalTime()
    if (-not (Test-StartTimeBinding -ObservedStartUtc $fixtureStart -RecordedStartUtc '2026-09-26T12:03:02.1231528Z')) {
        throw 'Identity fixture rejected a matching process start time.'
    }
    if (Test-StartTimeBinding -ObservedStartUtc $fixtureStart -RecordedStartUtc '2026-09-26T12:13:02.1231528Z') {
        throw 'Identity fixture accepted a stale or reused PID start time.'
    }
    $jsonRoundTripStart = ('{"started_at_utc":"2026-09-26T12:03:02.1231528Z"}' | ConvertFrom-Json).started_at_utc
    if (-not (Test-StartTimeBinding -ObservedStartUtc $fixtureStart -RecordedStartUtc $jsonRoundTripStart)) {
        throw 'Identity fixture lost UTC semantics after a JSON DateTime round trip.'
    }
    $intentFixture = [pscustomobject]@{
        schema_version = 1
        module = $rawModule
        command = $rawCommand
        project_root = $projectRoot
        executable_path = $pythonPath
    }
    Assert-LaunchIntentBinding -Intent $intentFixture
    $intentFixture.project_root = Join-Path $projectRoot 'wrong'
    $rejected = $false
    try { Assert-LaunchIntentBinding -Intent $intentFixture } catch { $rejected = $true }
    if (-not $rejected) {
        throw 'Identity fixture accepted a launch intent for the wrong project root.'
    }

    $fixtureId = [Guid]::NewGuid().ToString('N')
    $fixtureMutexName = "Local\klax-weather-v3-handoff-fixture-$fixtureId"
    $fixtureLeasePath = Join-Path ([System.IO.Path]::GetTempPath()) "klax-weather-v3-handoff-fixture-$fixtureId.json"
    $fixtureLegacyPidPath = Join-Path ([System.IO.Path]::GetTempPath()) "klax-weather-v3-handoff-fixture-$fixtureId.pid"
    $fixtureLegacyLeasePath = Join-Path ([System.IO.Path]::GetTempPath()) "klax-weather-v3-handoff-fixture-$fixtureId.legacy.json"
    $firstHandle = $null
    $takeoverHandle = $null
    $legacyHandle = $null
    $legacyFixtureProcess = $null
    try {
        $firstHandle = Enter-HandoffWatcherLease -MutexName $fixtureMutexName -LeasePath $fixtureLeasePath `
            -Root $projectRoot -ScriptPath $PSCommandPath
        if ($firstHandle.acquired -ne $true -or -not (Test-Path -LiteralPath $fixtureLeasePath)) {
            throw 'Watcher-lease fixture could not establish the first owner.'
        }
        $leaseBeforeContender = Get-Sha256Hex -LiteralPath $fixtureLeasePath
        $powershellPath = (Get-Process -Id $PID).Path
        $escapedMutexName = $fixtureMutexName.Replace("'", "''")
        $contenderCommand = @"
`$m = [System.Threading.Mutex]::new(`$false, '$escapedMutexName')
try {
    try { `$acquired = `$m.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { `$acquired = `$true }
    if (`$acquired) { try { `$m.ReleaseMutex() } catch { }; exit 41 }
    exit 0
}
finally { `$m.Dispose() }
"@
        & $powershellPath -NoProfile -NonInteractive -Command $contenderCommand
        if ($LASTEXITCODE -ne 0) {
            throw 'Watcher-lease fixture allowed a second live owner.'
        }
        if ((Get-Sha256Hex -LiteralPath $fixtureLeasePath) -ne $leaseBeforeContender) {
            throw 'Refused watcher contender modified the active lease.'
        }
        Exit-HandoffWatcherLease -Handle $firstHandle -LeasePath $fixtureLeasePath
        $firstHandle = $null

        $staleLease = Get-Content -LiteralPath $fixtureLeasePath -Raw | ConvertFrom-Json
        $selfStart = (Get-Process -Id $PID).StartTime.ToUniversalTime().AddMinutes(10)
        $staleLease.status = 'ACTIVE'
        $staleLease.watcher_started_at_utc = $selfStart.ToString('o')
        $staleLease.released_at_utc = $null
        Write-AtomicJson -LiteralPath $fixtureLeasePath -Value $staleLease -Depth 8
        $takeoverHandle = Enter-HandoffWatcherLease -MutexName $fixtureMutexName -LeasePath $fixtureLeasePath `
            -Root $projectRoot -ScriptPath $PSCommandPath
        if ($takeoverHandle.acquired -ne $true -or
                [string]$takeoverHandle.lease.takeover.reason -ne 'pid_start_mismatch') {
            throw 'Watcher-lease fixture did not permit the recorded stale-PID takeover.'
        }

        $legacyFixtureProcess = Start-Process -FilePath $powershellPath `
            -ArgumentList @('-NoProfile', '-NonInteractive', '-Command', 'Start-Sleep -Seconds 30') `
            -WindowStyle Hidden -PassThru
        Write-AtomicText -LiteralPath $fixtureLegacyPidPath -Value ([string]$legacyFixtureProcess.Id) -Encoding ascii
        (Get-Item -LiteralPath $fixtureLegacyPidPath).LastWriteTimeUtc = $legacyFixtureProcess.StartTime.ToUniversalTime()
        $legacyHandle = Enter-HandoffWatcherLease `
            -MutexName "Local\klax-weather-v3-handoff-legacy-fixture-$fixtureId" `
            -LeasePath $fixtureLegacyLeasePath -Root $projectRoot -ScriptPath $PSCommandPath `
            -LegacyPidPath $fixtureLegacyPidPath
        if ($legacyHandle.acquired -eq $true -or
                [string]$legacyHandle.reason -ne 'legacy_pid_names_same_live_watcher' -or
                (Test-Path -LiteralPath $fixtureLegacyLeasePath)) {
            throw 'Watcher-lease fixture did not refuse the same live legacy watcher before writing a lease.'
        }
    }
    finally {
        if ($null -ne $firstHandle) {
            Exit-HandoffWatcherLease -Handle $firstHandle -LeasePath $fixtureLeasePath
        }
        if ($null -ne $takeoverHandle) {
            Exit-HandoffWatcherLease -Handle $takeoverHandle -LeasePath $fixtureLeasePath
        }
        if ($null -ne $legacyHandle -and $legacyHandle.acquired -eq $true) {
            Exit-HandoffWatcherLease -Handle $legacyHandle -LeasePath $fixtureLegacyLeasePath
        }
        if ($null -ne $legacyFixtureProcess -and -not $legacyFixtureProcess.HasExited) {
            Stop-Process -Id $legacyFixtureProcess.Id -Force
            $legacyFixtureProcess.WaitForExit()
        }
        foreach ($fixturePath in @($fixtureLeasePath, $fixtureLegacyPidPath, $fixtureLegacyLeasePath)) {
            if (Test-Path -LiteralPath $fixturePath) {
                Remove-Item -LiteralPath $fixturePath -Force
            }
        }
    }
    Write-Output 'continue_weather_v3 identity fixtures passed'
}

if ($RunIdentityFixtureTests) {
    Invoke-IdentityFixtureTests
    exit 0
}

if ($PollSeconds -lt 5 -or $PollSeconds -gt 300) {
    throw 'PollSeconds must be between 5 and 300.'
}
if ($MaximumAcquisitionAttempts -lt 2 -or $MaximumAcquisitionAttempts -gt 3) {
    throw 'MaximumAcquisitionAttempts must be two or three.'
}
if (-not (Test-Path -LiteralPath $pidPath)) {
    throw "Missing raw-weather PID file: $pidPath"
}
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing project Python runtime: $pythonPath"
}

$watcherMutexName = Get-HandoffWatcherMutexName -Root $projectRoot
$watcherLeaseHandle = Enter-HandoffWatcherLease -MutexName $watcherMutexName -LeasePath $watcherLeasePath `
    -Root $projectRoot -ScriptPath $PSCommandPath -LegacyPidPath $watcherPidPath
if ($watcherLeaseHandle.acquired -ne $true) {
    Write-Output "Handoff watcher not started: $($watcherLeaseHandle.reason)."
    exit 12
}

try {
$recoveredPid = Resolve-DurableLaunchIntent
$rawPid = if ($null -ne $recoveredPid) {
    [int]$recoveredPid
}
else {
    [int](Get-Content -LiteralPath $pidPath -Raw)
}
while ($true) {
    Write-HandoffState -Status 'WAITING_FOR_FINITE_RAW_ACQUISITION' -Detail "Watching PID $rawPid."
    $restartRequired = $false
    while ($true) {
        $progress = $null
        if (Test-Path -LiteralPath $progressPath) {
            try {
                $progress = Get-Content -LiteralPath $progressPath -Raw | ConvertFrom-Json
            }
            catch {
                $progress = $null
            }
        }
        if ($null -ne $progress -and
                $progress.status -eq 'FULL_RAW_RANGE_ACQUISITION_COMPLETE' -and
                $progress.coverage_complete -eq $true -and
                $progress.days_complete -eq 543 -and
                $progress.days_unavailable -eq 0 -and
                $progress.protected_final_read -eq $false) {
            break
        }

        $process = Get-TrackedRawProcess -ProcessId $rawPid
        if ($null -eq $process) {
            $journal = Read-AcquisitionJournal
            $transientFailure = Get-TransientAcquisitionFailure
            $replacementEligible = Test-AdministrativeReplacementEligible -Journal $journal
            if ($transientFailure -and (
                    @($journal.attempts).Count -lt $MaximumAcquisitionAttempts -or
                    $replacementEligible)) {
                Complete-AcquisitionAttempt -ProcessId $rawPid -TerminalStatus 'TRANSIENT_NETWORK_FAILURE'
                Write-HandoffState -Status 'RAW_ACQUISITION_TRANSIENT_RESTART' -Detail "PID $rawPid exited after a transient network failure; resuming the identical finite acquisition from verified cache."
                $rawPid = Start-ResumedAcquisition
                $restartRequired = $true
                break
            }
            $currentAttempt = $journal.attempts | Where-Object { [int]$_.pid -eq $rawPid } | Select-Object -Last 1
            if ($null -ne $currentAttempt -and [string]$currentAttempt.terminal_status -like 'RUNNING*') {
                $terminalStatus = if ($transientFailure) { 'TRANSIENT_NETWORK_FAILURE' } else { 'NONTRANSIENT_FAILURE' }
                Complete-AcquisitionAttempt -ProcessId $rawPid -TerminalStatus $terminalStatus -RecoveryExhausted
            }
            Write-HandoffState -Status 'RAW_ACQUISITION_INCOMPLETE' -Detail 'Downloader exited before complete 543-day coverage; failure was non-transient or the bounded attempt ceiling was exhausted.'
            exit 2
        }
        Start-Sleep -Seconds $PollSeconds
    }
    if ($restartRequired) {
        continue
    }
    while ($null -ne (Get-TrackedRawProcess -ProcessId $rawPid)) {
        Start-Sleep -Seconds 1
    }
    Complete-AcquisitionAttempt -ProcessId $rawPid -TerminalStatus 'COMPLETE_543_DAYS_CACHE_VERIFIED'
    break
}

if (Test-Path -LiteralPath $normalizerPidPath) {
    $normalizerPid = [int](Get-Content -LiteralPath $normalizerPidPath -Raw)
    $normalizerProcess = Get-TrackedNormalizerProcess -ProcessId $normalizerPid
    if ($null -ne $normalizerProcess) {
        Write-HandoffState -Status 'WAITING_FOR_ROLLING_NORMALIZER' -Detail "Raw coverage is complete; waiting for rolling normalizer PID $normalizerPid to exit before the final normalization pass."
        while ($null -ne (Get-TrackedNormalizerProcess -ProcessId $normalizerPid)) {
            Start-Sleep -Seconds $PollSeconds
        }
    }
}

Write-HandoffState -Status 'NORMALIZATION_STARTED' -Detail 'Raw 543-day coverage verified; starting cache-only normalization.'
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = 'src'
& $pythonPath -m klax_lab.weather_normalize_v3 --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'NORMALIZATION_FAILED' -Detail "Cache-only normalizer exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$normalized = Get-Content -LiteralPath (Join-Path $projectRoot 'data\manifests\v3_weather_normalization_progress.json') -Raw | ConvertFrom-Json
if ($normalized.status -ne 'COMPLETE_COMPONENTS_PUBLISHED' -or
        $normalized.coverage_complete -ne $true -or
        $normalized.normalized_days -ne 543 -or
        $normalized.protected_final_read -ne $false -or
        $normalized.network_used -ne $false) {
    Write-HandoffState -Status 'NORMALIZATION_INCOMPLETE' -Detail 'Normalizer exited without publishing complete audited components.'
    exit 3
}

Write-HandoffState -Status 'SOURCE_FINALIZATION_STARTED' -Detail 'Normalized weather coverage verified; binding the exact source artifacts and hashes.'
& $pythonPath -m klax_lab.substantive_readiness_v3 finalize-source --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'SOURCE_FINALIZATION_FAILED' -Detail "Source-feasibility finalizer exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$sourceFeasibility = Get-Content -LiteralPath (Join-Path $projectRoot 'data\manifests\v3_source_feasibility.json') -Raw | ConvertFrom-Json
if ($sourceFeasibility.status -ne 'COMPLETE_FINITE_BULK_AND_NORMALIZED_COVERAGE_AUDITED' -or
        $sourceFeasibility.readiness_component_pass -ne $true -or
        $sourceFeasibility.completion_audit.days_complete -ne 543 -or
        $sourceFeasibility.completion_audit.days_unavailable -ne 0 -or
        $sourceFeasibility.completion_audit.protected_final_read -ne $false -or
        $sourceFeasibility.completion_audit.offline_normalization_verified -ne $true) {
    Write-HandoffState -Status 'SOURCE_FINALIZATION_INCOMPLETE' -Detail 'Finalizer exited without publishing a complete audited source set.'
    exit 4
}

Write-HandoffState -Status 'DATASET_FREEZE_STARTED' -Detail 'Finalized sources verified; freezing the offline training, calibration, and scored-development dataset.'
& $pythonPath -m klax_lab.freeze_pipeline_v3 --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'DATASET_FREEZE_FAILED' -Detail "V3 development freeze exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}

$dataset = Get-Content -LiteralPath (Join-Path $projectRoot 'data\manifests\v3_dataset.json') -Raw | ConvertFrom-Json
$folds = Get-Content -LiteralPath (Join-Path $projectRoot 'data\manifests\v3_five_fold_split.json') -Raw | ConvertFrom-Json
if ($dataset.status -ne 'TRAINING_CALIBRATION_EVALUATION_FROZEN' -or
        $dataset.protected_final_read -ne $false -or
        $dataset.network_used -ne $false -or
        $folds.status -ne 'EVALUATION_FOLDS_FROZEN' -or
        $folds.protected_final_read -ne $false -or
        $folds.network_used -ne $false -or
        $dataset.dataset_id -ne $folds.dataset_id -or
        $folds.folds.Count -ne 5) {
    Write-HandoffState -Status 'DATASET_FREEZE_INCOMPLETE' -Detail 'Freeze command exited without canonical verified dataset and five-fold components.'
    exit 5
}

Write-HandoffState -Status 'CAPABILITY_REFRESH_STARTED' -Detail 'Frozen development data verified; refreshing deterministic fixtures and binding the saved actual local V3 worker probe.'
& $pythonPath -m klax_lab.cli v3-fixtures --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'CAPABILITY_REFRESH_FAILED' -Detail "V3 component fixture publication exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}
& $pythonPath -m klax_lab.substantive_readiness_v3 publish-worker-probe --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'WORKER_BINDING_FAILED' -Detail "Saved actual V3 worker-probe binding exited with code $LASTEXITCODE. No new inference was requested."
    exit $LASTEXITCODE
}
& $pythonPath -m klax_lab.substantive_readiness_v3 probes --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'CAPABILITY_PROBES_FAILED' -Detail "V3 replication, critic, or actual-worker capability verification exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}
& $pythonPath -m klax_lab.cli v3-status --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'IMPLEMENTATION_INVENTORY_FAILED' -Detail "V3 component inventory exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}

Write-HandoffState -Status 'SUBSTANTIVE_READINESS_VALIDATION_STARTED' -Detail 'Validating every frozen component, hash, capability probe, and protected-final boundary before ticket issuance.'
& $pythonPath -m klax_lab.substantive_readiness_v3 validate --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'SUBSTANTIVE_READINESS_FAILED' -Detail "V3 readiness validation exited with code $LASTEXITCODE. No campaign ticket was issued."
    exit $LASTEXITCODE
}

$readinessPath = Join-Path $projectRoot 'data\manifests\v3_readiness.json'
$ticketPath = Join-Path $projectRoot 'runs\v3_offline_campaign_ticket.json'
$readinessExists = Test-Path -LiteralPath $readinessPath
$ticketExists = Test-Path -LiteralPath $ticketPath
if ($readinessExists -ne $ticketExists) {
    Write-HandoffState -Status 'READINESS_TICKET_PAIR_AMBIGUOUS' -Detail 'Exactly one of the readiness and campaign-ticket files exists; refusing to issue or run.'
    exit 6
}

if (-not $readinessExists) {
    $campaignId = 'v3-offline-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    Write-HandoffState -Status 'READINESS_TICKET_ISSUE_STARTED' -Detail "All prerequisites passed; issuing the one-use offline campaign ticket for $campaignId."
    & $pythonPath -m klax_lab.substantive_readiness_v3 issue --root $projectRoot --campaign-id $campaignId
    if ($LASTEXITCODE -ne 0) {
        Write-HandoffState -Status 'READINESS_TICKET_ISSUE_FAILED' -Detail "V3 readiness/ticket issuance exited with code $LASTEXITCODE."
        exit $LASTEXITCODE
    }
}
else {
    $ticket = Get-Content -LiteralPath $ticketPath -Raw | ConvertFrom-Json
    $campaignId = [string]$ticket.campaign_id
    if ([string]::IsNullOrWhiteSpace($campaignId)) {
        Write-HandoffState -Status 'CAMPAIGN_TICKET_INVALID' -Detail 'Existing V3 ticket has no campaign identity.'
        exit 7
    }
}

$campaignIdPath = Join-Path $projectRoot 'runs\weather_v3_campaign.id'
Set-Content -LiteralPath $campaignIdPath -Value $campaignId -Encoding ascii
$campaignRoot = Join-Path $projectRoot ("runs\campaigns_v3\" + $campaignId)
$campaignSummaryPath = Join-Path $campaignRoot 'summary.json'
$campaignRecoveryPath = Join-Path $campaignRoot 'recovery-state.json'

if (-not (Test-Path -LiteralPath $campaignSummaryPath)) {
    if (Test-Path -LiteralPath $campaignRecoveryPath) {
        Write-HandoffState -Status 'OFFLINE_CAMPAIGN_RESUME_STARTED' -Detail "Resuming the same bounded V3 campaign $campaignId from its verified recovery state."
        & $pythonPath -m klax_lab.orchestrator_v3 resume --root $projectRoot
    }
    else {
        Write-HandoffState -Status 'OFFLINE_CAMPAIGN_STARTED' -Detail "Starting bounded offline V3 campaign $campaignId. Protected-final data remains sealed."
        & $pythonPath -m klax_lab.orchestrator_v3 start --root $projectRoot
    }
    if ($LASTEXITCODE -ne 0) {
        Write-HandoffState -Status 'OFFLINE_CAMPAIGN_FAILED' -Detail "Bounded V3 campaign exited with code $LASTEXITCODE; inspect its recovery state before resuming."
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath $campaignSummaryPath)) {
    Write-HandoffState -Status 'OFFLINE_CAMPAIGN_INCOMPLETE' -Detail 'Campaign process exited without a canonical summary.'
    exit 8
}
& $pythonPath -m klax_lab.orchestrator_v3 status --root $projectRoot
if ($LASTEXITCODE -ne 0) {
    Write-HandoffState -Status 'OFFLINE_CAMPAIGN_ARTIFACT_VERIFICATION_FAILED' -Detail "Completed V3 campaign artifact verification exited with code $LASTEXITCODE."
    exit $LASTEXITCODE
}
$campaignSummary = Get-Content -LiteralPath $campaignSummaryPath -Raw | ConvertFrom-Json
$finalAuthorizationPath = Join-Path $campaignRoot 'protected-final-authorization.json'
if (Test-Path -LiteralPath $finalAuthorizationPath) {
    Write-HandoffState -Status 'CAMPAIGN_COMPLETE_CHAMPION_AUTHORIZED_FOR_PROTECTED_FINAL' -Detail "Campaign $campaignId completed with a gate-passing champion. The protected final remains unopened pending the isolated final evaluator."
}
else {
    $conclusion = [string]$campaignSummary.scientific_conclusion
    Write-HandoffState -Status 'CAMPAIGN_COMPLETE_NO_CHAMPION_PROTECTED_FINAL_SEALED' -Detail "Campaign $campaignId completed with conclusion $conclusion. No protected-final authorization exists."
}
}
finally {
    Exit-HandoffWatcherLease -Handle $watcherLeaseHandle -LeasePath $watcherLeasePath
}
