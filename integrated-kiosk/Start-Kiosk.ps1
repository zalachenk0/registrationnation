$ErrorActionPreference = "Stop"

$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$kioskUrl = "http://127.0.0.1:8443/"
$corepack = "C:\Program Files\nodejs\corepack.cmd"
$logFile = Join-Path $projectDirectory "kiosk-ui.log"

if (-not (Test-Path -LiteralPath $corepack)) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "Node.js was not found. Install Node.js, then run Start-Kiosk again.",
        "Registration Nation Kiosk"
    ) | Out-Null
    exit 1
}

# --- Payment/scanner API (Flask) ---------------------------------------
# The UI depends on this for payment, camera/scan, and print requests
# (proxied through Vite's /api route in dev). Started the same way as
# the Node UI below: skip if already running, otherwise launch it
# hidden and wait for its health check.

$flaskUrl = "http://127.0.0.1:5000/api/health"
$flaskLog = Join-Path $projectDirectory "kiosk-api.log"
$flaskErrorLog = Join-Path $projectDirectory "kiosk-api-error.log"

$flaskIsRunning = $false
try {
    $response = Invoke-WebRequest -Uri $flaskUrl -UseBasicParsing -TimeoutSec 2
    $flaskIsRunning = $response.StatusCode -eq 200
} catch {
    $flaskIsRunning = $false
}

if (-not $flaskIsRunning) {
    # Prefer a project-local virtual environment if one exists
    # (created via: python -m venv .venv), otherwise fall back to
    # whatever python/py is on PATH.
    $venvPython = Join-Path $projectDirectory ".venv\Scripts\python.exe"
    $pythonExe = $null
    if (Test-Path -LiteralPath $venvPython) {
        $pythonExe = $venvPython
    } else {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) { $pythonCmd = Get-Command py -ErrorAction SilentlyContinue }
        if ($pythonCmd) { $pythonExe = $pythonCmd.Source }
    }

    if (-not $pythonExe) {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "Python was not found. Install Python 3, run 'pip install -r requirements-scanner.txt', then run Start-Kiosk again.",
            "Registration Nation Kiosk"
        ) | Out-Null
        exit 1
    }

    Start-Process -FilePath $pythonExe `
        -ArgumentList @("scanner_api.py") `
        -WorkingDirectory $projectDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $flaskLog `
        -RedirectStandardError $flaskErrorLog

    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri $flaskUrl -UseBasicParsing -TimeoutSec 2
            $flaskIsRunning = $response.StatusCode -eq 200
        } catch {
            $flaskIsRunning = $false
        }
    } until ($flaskIsRunning -or (Get-Date) -gt $deadline)
}

if (-not $flaskIsRunning) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "The payment/scanner API did not start. Check kiosk-api-error.log in the project folder.",
        "Registration Nation Kiosk"
    ) | Out-Null
    exit 1
}

# --- Kiosk UI (Vite) -----------------------------------------------------

$serverIsRunning = $false
try {
    $response = Invoke-WebRequest -Uri $kioskUrl -UseBasicParsing -TimeoutSec 2
    $serverIsRunning = $response.StatusCode -eq 200
} catch {
    $serverIsRunning = $false
}

if (-not $serverIsRunning) {
    $arguments = @(
        "pnpm",
        "dev",
        "--host",
        "0.0.0.0"
    )
    Start-Process -FilePath $corepack `
        -ArgumentList $arguments `
        -WorkingDirectory $projectDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError (Join-Path $projectDirectory "kiosk-ui-error.log")

    $deadline = (Get-Date).AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -Uri $kioskUrl -UseBasicParsing -TimeoutSec 2
            $serverIsRunning = $response.StatusCode -eq 200
        } catch {
            $serverIsRunning = $false
        }
    } until ($serverIsRunning -or (Get-Date) -gt $deadline)
}

if (-not $serverIsRunning) {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "The kiosk server did not start. Check kiosk-ui-error.log in the project folder.",
        "Registration Nation Kiosk"
    ) | Out-Null
    exit 1
}

$chromePaths = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList @("--new-window", $kioskUrl)
} else {
    Start-Process $kioskUrl
}
