$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $root "frontend"
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

function Refresh-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Test-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function Install-WithWinget($packageId, $label) {
    if (-not (Test-Command "winget")) {
        throw "winget was not found. Install App Installer from Microsoft Store first, then run launch_app.bat again."
    }

    Write-Host "Installing $label ..."
    winget install --id $packageId --accept-package-agreements --accept-source-agreements --silent
    Refresh-SessionPath
}

function Ensure-Python {
    if (Test-Command "py") { return }
    if (Test-Command "python") { return }

    Install-WithWinget "Python.Python.3.12" "Python"

    if (-not (Test-Command "py") -and -not (Test-Command "python")) {
        throw "Python installation finished but the command is still unavailable. Close this window and run launch_app.bat again."
    }
}

function Ensure-Node {
    if (Test-Command "npm") { return }

    Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js"

    if (-not (Test-Command "npm")) {
        throw "Node.js installation finished but npm is still unavailable. Close this window and run launch_app.bat again."
    }
}

function Ensure-Ffmpeg {
    if (Test-Command "ffmpeg") { return }

    Install-WithWinget "Gyan.FFmpeg" "FFmpeg"

    if (-not (Test-Command "ffmpeg")) {
        throw "FFmpeg installation finished but ffmpeg is still unavailable. Close this window and run launch_app.bat again."
    }
}

function Get-PythonCommand {
    if (Test-Command "py") { return "py" }
    return "python"
}

Set-Location $root

Write-Host "Preparing Miscoshorts AI for Windows..."
Refresh-SessionPath
Ensure-Python
Ensure-Node
Ensure-Ffmpeg

$pythonCommand = Get-PythonCommand

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment ..."
    & $pythonCommand -m venv .venv
}

Write-Host "Installing Python packages ..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "Installing frontend packages ..."
    Push-Location $frontendDir
    npm install
    Pop-Location
}

Write-Host "Building frontend ..."
Push-Location $frontendDir
npm run build
Pop-Location

Write-Host "Launching app ..."
& $venvPython app_launcher.py