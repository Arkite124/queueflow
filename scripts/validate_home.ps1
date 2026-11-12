param(
    [string]$PythonExe = 'python',
    [switch]$SkipRecovery,
    [switch]$SkipBenchmark
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $taskDockerBin = 'C:/Program Files/Docker/Docker\resources\bin'
    if (Test-Path -LiteralPath (Join-Path $taskDockerBin 'docker.exe')) {
        $env:Path = $taskDockerBin + ';' + $env:Path
    }
}
function Invoke-Checked {
    param([string]$Executable, [string[]]$CommandArguments)
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Executable $($CommandArguments -join ' ')" }
}
Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')
Invoke-Checked 'docker' @('compose', 'config', '--quiet')
Invoke-Checked 'docker' @('compose', '-f', 'docker-compose.test.yml', 'config', '--quiet')
Invoke-Checked 'docker' @('compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.production.yml', 'config', '--quiet')
try {
    Invoke-Checked 'docker' @('compose', '-f', 'docker-compose.test.yml', 'up', '--build', '--abort-on-container-exit', '--exit-code-from', 'tests')
} finally {
    & docker compose -f docker-compose.test.yml down
}
Invoke-Checked 'npm.cmd' @('--prefix', 'frontend', 'ci')
Invoke-Checked 'npm.cmd' @('--prefix', 'frontend', 'run', 'lint')
Invoke-Checked 'npm.cmd' @('--prefix', 'frontend', 'run', 'build')
if (-not $SkipRecovery) {
    Invoke-Checked $PythonExe @('scripts/dev.py', 'scripts.compose_recovery', '--output', 'output/home-recovery.json')
}
if (-not $SkipBenchmark) {
    Invoke-Checked $PythonExe @('scripts/dev.py', 'scripts.run_benchmark', '--mode', 'processor', '--repeats', '3', '--output', 'output/home-processor.json')
}
Write-Output 'Home-PC checks completed. Real API worker CPU/RAM benchmarking remains a separate step.'
