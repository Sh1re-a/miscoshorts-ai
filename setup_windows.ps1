$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $root "frontend"
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateDir = Join-Path $root ".setup-state"
$pythonDepsStamp = Join-Path $stateDir "python-deps.stamp"
$frontendDepsStamp = Join-Path $stateDir "frontend-deps.stamp"
$frontendBuildStamp = Join-Path $stateDir "frontend-build.stamp"

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

function Ensure-StateDir {
    if (-not (Test-Path $stateDir)) {
        New-Item -ItemType Directory -Path $stateDir | Out-Null
    }
}

function Update-Stamp($stampPath) {
    if (Test-Path $stampPath) {
        (Get-Item $stampPath).LastWriteTimeUtc = [DateTime]::UtcNow
        return
    }

    New-Item -ItemType File -Path $stampPath | Out-Null
}

function Test-Stale($stampPath, $paths) {
    if (-not (Test-Path $stampPath)) {
        return $true
    }

    $stampTime = (Get-Item $stampPath).LastWriteTimeUtc

    foreach ($path in $paths) {
        if (-not (Test-Path $path)) {
            return $true
        }

        $item = Get-Item $path
        if ($item.PSIsContainer) {
            $newestChild = Get-ChildItem -Path $path -Recurse -File | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            if ($null -ne $newestChild -and $newestChild.LastWriteTimeUtc -gt $stampTime) {
                return $true
            }
        }
        elseif ($item.LastWriteTimeUtc -gt $stampTime) {
            return $true
        }
    }

    return $false
}

Set-Location $root

Write-Host "Preparing Miscoshorts AI for Windows..."
Refresh-SessionPath
Ensure-StateDir
Ensure-Python
Ensure-Node
Ensure-Ffmpeg

$pythonCommand = Get-PythonCommand

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment ..."
    & $pythonCommand -m venv .venv
}

if (Test-Stale $pythonDepsStamp @((Join-Path $root "requirements.txt"), $venvPython)) {
    Write-Host "Installing Python packages ..."
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r requirements.txt
    Update-Stamp $pythonDepsStamp
}
else {
    Write-Host "Python packages are already installed. Skipping reinstall."
}

if (Test-Stale $frontendDepsStamp @((Join-Path $frontendDir "package.json"), (Join-Path $frontendDir "package-lock.json"))) {
    Write-Host "Installing frontend packages ..."
    Push-Location $frontendDir
    npm install
    Pop-Location
    Update-Stamp $frontendDepsStamp
}
else {
    Write-Host "Frontend packages are already installed. Skipping npm install."
}

if (Test-Stale $frontendBuildStamp @((Join-Path $frontendDir "src"), (Join-Path $frontendDir "index.html"), (Join-Path $frontendDir "package.json"), (Join-Path $frontendDir "vite.config.ts"))) {
    Write-Host "Building frontend ..."
    Push-Location $frontendDir
    npm run build
    Pop-Location
    Update-Stamp $frontendBuildStamp
}
else {
    Write-Host "Frontend build is up to date. Skipping build."
}

Write-Host "Launching app ..."
& $venvPython app_launcher.py