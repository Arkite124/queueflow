param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$qfRoot = Split-Path -Parent $PSScriptRoot
$qfPython = Join-Path $qfRoot '.venv\Scripts\pythonw.exe'
$qfRuntime = Join-Path $PSScriptRoot 'scheduled_runtime.py'
$qfState = Join-Path $qfRoot 'output\server-runtime'
$qfIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ((Get-TimeZone).Id -ne 'Korea Standard Time') { throw 'Korea Standard Time is required.' }
foreach ($qfRequired in @($qfPython, $qfRuntime, (Join-Path $qfRoot '.env'), (Join-Path $qfState 'tunnel-token'), (Join-Path $qfState 'cloudflared.exe'))) {
    if (-not (Test-Path -LiteralPath $qfRequired)) { throw ('Missing runtime prerequisite: ' + (Split-Path -Leaf $qfRequired)) }
}
if (-not $Apply) {
    Write-Output 'Preview: QueueFlow-Run at user logon and 07:00; QueueFlow-Drain at 23:57. Runs as the current interactive user; automatic Windows login is required. No PC shutdown or portfolio changes.'
    return
}
$qfPrincipal = New-ScheduledTaskPrincipal -UserId $qfIdentity.User.Value -LogonType Interactive -RunLevel Limited
$qfSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$qfStopSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
foreach ($qfName in @('QueueFlow-Run', 'QueueFlow-Drain')) {
    $qfExisting = Get-ScheduledTask -TaskName $qfName -ErrorAction SilentlyContinue
    if ($qfExisting) { throw "Task $qfName already exists; inspect it before replacing." }
}
Copy-Item -LiteralPath $qfRuntime -Destination (Join-Path $qfState 'scheduled_runtime.py')
foreach ($qfFile in @('docker-compose.yml', 'docker-compose.production.yml')) {
    Copy-Item -LiteralPath (Join-Path $qfRoot $qfFile) -Destination (Join-Path $qfState $qfFile)
}
$qfInstalledRuntime = Join-Path $qfState 'scheduled_runtime.py'
$qfRun = New-ScheduledTaskAction -Execute $qfPython -Argument ('"' + $qfInstalledRuntime + '" run') -WorkingDirectory $qfRoot
$qfStop = New-ScheduledTaskAction -Execute $qfPython -Argument ('"' + $qfInstalledRuntime + '" signal-stop') -WorkingDirectory $qfRoot
Register-ScheduledTask -TaskName 'QueueFlow-Run' -Action $qfRun -Principal $qfPrincipal -Settings $qfSettings `
    -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $qfIdentity.Name), (New-ScheduledTaskTrigger -Daily -At '07:00')) `
    -Description 'QueueFlow only: Docker Desktop and dedicated Tunnel; 07:00-23:57 KST; requires automatic login.' | Out-Null
Register-ScheduledTask -TaskName 'QueueFlow-Drain' -Action $qfStop -Principal $qfPrincipal -Settings $qfStopSettings `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '23:57') `
    -Description 'QueueFlow only: signal drain/stop; never shuts down Windows; no missed-event replay.' | Out-Null
Write-Output 'Registered QueueFlow-Run and QueueFlow-Drain. Existing portfolio tasks and services were not changed.'
