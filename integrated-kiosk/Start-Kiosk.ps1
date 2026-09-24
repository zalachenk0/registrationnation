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
