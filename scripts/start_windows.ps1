param(
    [int]$PreferredPort = 8000,
    [int]$PortSearchLimit = 10
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $scriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
    if (-not $scriptPath) {
        throw "Unable to resolve script path."
    }
    $scriptDir = Split-Path -Parent $scriptPath
    return Split-Path -Parent $scriptDir
}

function Get-PythonCommand {
    $candidates = @(
        (Get-Command python -ErrorAction SilentlyContinue),
        (Get-Command py -ErrorAction SilentlyContinue)
    ) | Where-Object { $_ }

    if ($candidates.Count -gt 0) {
        return @{
            FilePath = $candidates[0].Source
            Arguments = @()
        }
    }

    $knownPaths = @(
        "C:\Users\siemp\AppData\Local\Programs\Python\Python311\python.exe",
        "C:\Python311\python.exe"
    )

    foreach ($path in $knownPaths) {
        if (Test-Path $path) {
            return @{
                FilePath = $path
                Arguments = @()
            }
        }
    }

    throw "Could not find a Python interpreter. Install Python 3.11+ or add it to PATH."
}

function Test-PortAvailable {
    param([int]$Port)

    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

function Get-AvailablePort {
    param(
        [int]$StartPort,
        [int]$MaxAttempts
    )

    for ($offset = 0; $offset -lt $MaxAttempts; $offset++) {
        $candidate = $StartPort + $offset
        if (Test-PortAvailable -Port $candidate) {
            return $candidate
        }
    }

    throw "No open port found between $StartPort and $($StartPort + $MaxAttempts - 1)."
}

$repoRoot = Get-RepoRoot
$backendDir = Join-Path $repoRoot "backend"
$runDir = Join-Path $backendDir "run"
$stdoutLog = Join-Path $runDir "uvicorn.out.log"
$stderrLog = Join-Path $runDir "uvicorn.err.log"
$pidFile = Join-Path $runDir "uvicorn.pid"

New-Item -ItemType Directory -Force $runDir | Out-Null

$python = Get-PythonCommand
$port = Get-AvailablePort -StartPort $PreferredPort -MaxAttempts $PortSearchLimit
$url = "http://127.0.0.1:$port"

Remove-Item $stdoutLog, $stderrLog -ErrorAction SilentlyContinue

$arguments = @($python.Arguments) + @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "$port"
)

$process = Start-Process `
    -FilePath $python.FilePath `
    -ArgumentList $arguments `
    -WorkingDirectory $backendDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content -Path $pidFile -Value $process.Id

$healthUrl = "$url/api/health"
$started = $false

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250

    if ($process.HasExited) {
        $stderr = if (Test-Path $stderrLog) { Get-Content $stderrLog -Raw } else { "" }
        throw "Backend exited during startup with code $($process.ExitCode).`n$stderr"
    }

    try {
        $response = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        if ($response.status -eq "ok") {
            $started = $true
            break
        }
    } catch {
        continue
    }
}

if (-not $started) {
    try {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
    } catch {
    }

    $stderr = if (Test-Path $stderrLog) { Get-Content $stderrLog -Raw } else { "" }
    throw "Backend did not become healthy at $healthUrl.`n$stderr"
}

Write-Output "Backend started successfully."
Write-Output "URL: $url"
Write-Output "Health: $healthUrl"
Write-Output "PID: $($process.Id)"
Write-Output "Logs: $stdoutLog"
