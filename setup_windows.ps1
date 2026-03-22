$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $root "frontend"
$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateDir = Join-Path $root ".setup-state"
$installerDir = Join-Path $stateDir "installers"
$logPath = Join-Path $stateDir "windows-setup.log"
$pythonInstallerVersion = "3.12.10"
$pythonInstallerArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
$pythonInstallerName = "python-$pythonInstallerVersion-$pythonInstallerArch.exe"
$pythonInstallerPath = Join-Path $installerDir $pythonInstallerName
$pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonInstallerVersion/$pythonInstallerName"
$nodeInstallerVersion = "24.14.0"
$nodeInstallerArch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
$nodeInstallerName = "node-v$nodeInstallerVersion-$nodeInstallerArch.msi"
$nodeInstallerPath = Join-Path $installerDir $nodeInstallerName
$nodeInstallerUrl = "https://nodejs.org/dist/v$nodeInstallerVersion/$nodeInstallerName"
$ffmpegArchiveName = "ffmpeg-release-essentials.zip"
$ffmpegArchivePath = Join-Path $installerDir $ffmpegArchiveName
$ffmpegArchiveUrl = "https://www.gyan.dev/ffmpeg/builds/$ffmpegArchiveName"
$ffmpegInstallRoot = Join-Path $stateDir "ffmpeg"
$ffmpegCurrentDir = Join-Path $ffmpegInstallRoot "current"
$ffmpegBinDir = Join-Path $ffmpegCurrentDir "bin"
$pythonDepsStamp = Join-Path $stateDir "python-deps.stamp"
$frontendDepsStamp = Join-Path $stateDir "frontend-deps.stamp"
$frontendBuildStamp = Join-Path $stateDir "frontend-build.stamp"

function Show-FailureAndExit($message) {
    Write-Host ""
    Write-Host "Setup failed." -ForegroundColor Red
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    Write-Host "Log file: $logPath"
    [void](Read-Host "Press Enter to close this window")
    exit 1
}

function Refresh-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Add-DirectoryToSessionPath($directory) {
    if (-not $directory -or -not (Test-Path $directory)) {
        return
    }

    $segments = ($env:Path -split ';') | Where-Object { $_ }
    if ($segments -contains $directory) {
        return
    }

    $env:Path = "$directory;$env:Path"
}

function Add-DirectoryToUserPath($directory) {
    if (-not $directory -or -not (Test-Path $directory)) {
        return
    }

    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $segments = ($userPath -split ';') | Where-Object { $_ }
    if ($segments -contains $directory) {
        return
    }

    $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
        $directory
    }
    else {
        "$userPath;$directory"
    }

    [System.Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Add-DirectoryToSessionPath $directory
}

function Test-Command($name) {
    return $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

function New-PythonLaunchSpec($command, $arguments = @()) {
    return [PSCustomObject]@{
        Command = $command
        Arguments = @($arguments)
    }
}

function Test-UsablePython($command, $arguments = @()) {
    try {
        $probeArgs = @($arguments) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)")
        & $command @probeArgs *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-PythonLaunchSpec {
    if (Test-Command "py") {
        if (Test-UsablePython "py" @("-3.12")) {
            return New-PythonLaunchSpec "py" @("-3.12")
        }

        if (Test-UsablePython "py") {
            return New-PythonLaunchSpec "py"
        }
    }

    if (Test-Command "python" -and (Test-UsablePython "python")) {
        return New-PythonLaunchSpec "python"
    }

    $candidatePaths = @(
        (Join-Path $env:LocalAppData "Programs\Python\Python314\python.exe"),
        (Join-Path $env:LocalAppData "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python314\python.exe"),
        (Join-Path $env:ProgramFiles "Python313\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe")
    ) | Where-Object { $_ }

    foreach ($candidate in $candidatePaths) {
        if ((Test-Path $candidate) -and (Test-UsablePython $candidate)) {
            return New-PythonLaunchSpec $candidate
        }
    }

    return $null
}

function Invoke-Python($pythonSpec, $arguments) {
    & $pythonSpec.Command @($pythonSpec.Arguments + $arguments)
}

function Invoke-Download($url, $destinationPath, $label) {
    Write-Host "Downloading $label ..."
    Invoke-WebRequest -Uri $url -OutFile $destinationPath
}

function Install-WithWinget($packageId, $label) {
    if (-not (Test-Command "winget")) {
        throw "winget was not found. Install App Installer from Microsoft Store first, then run launch_app.bat again."
    }

    Write-Host "Installing $label ..."
    winget install --id $packageId --accept-package-agreements --accept-source-agreements --silent
    Refresh-SessionPath
}

function Install-PythonDirectly {
    Ensure-StateDir

    Invoke-Download $pythonInstallerUrl $pythonInstallerPath "Python installer from python.org"

    Write-Host "Running Python installer ..."
    $installerArgs = @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=1",
        "Include_launcher=1",
        "AssociateFiles=0",
        "Shortcuts=0",
        "Include_test=0"
    )
    $process = Start-Process -FilePath $pythonInstallerPath -ArgumentList $installerArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer exited with code $($process.ExitCode)."
    }

    Refresh-SessionPath
}

function Find-NodeCommand {
    if (Test-Command "npm") {
        return "npm"
    }

    $candidatePaths = @(
        (Join-Path $env:ProgramFiles "nodejs\npm.cmd"),
        (Join-Path ${env:ProgramFiles(x86)} "nodejs\npm.cmd")
    ) | Where-Object { $_ }

    foreach ($candidate in $candidatePaths) {
        if (Test-Path $candidate) {
            Add-DirectoryToSessionPath (Split-Path -Parent $candidate)
            return $candidate
        }
    }

    return $null
}

function Install-NodeDirectly {
    Ensure-StateDir
    Invoke-Download $nodeInstallerUrl $nodeInstallerPath "Node.js installer from nodejs.org"

    Write-Host "Running Node.js installer ..."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", $nodeInstallerPath, "/qn", "/norestart") -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Node.js installer exited with code $($process.ExitCode)."
    }

    Refresh-SessionPath
}

function Find-FfmpegCommand {
    if (Test-Command "ffmpeg") {
        return "ffmpeg"
    }

    $candidatePaths = @(
        (Join-Path $ffmpegBinDir "ffmpeg.exe"),
        (Join-Path $env:ProgramFiles "FFmpeg\bin\ffmpeg.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "FFmpeg\bin\ffmpeg.exe")
    ) | Where-Object { $_ }

    foreach ($candidate in $candidatePaths) {
        if (Test-Path $candidate) {
            Add-DirectoryToSessionPath (Split-Path -Parent $candidate)
            return $candidate
        }
    }

    return $null
}

function Install-FfmpegDirectly {
    Ensure-StateDir
    if (-not (Test-Path $ffmpegInstallRoot)) {
        New-Item -ItemType Directory -Path $ffmpegInstallRoot | Out-Null
    }

    Invoke-Download $ffmpegArchiveUrl $ffmpegArchivePath "FFmpeg package"

    $extractDir = Join-Path $ffmpegInstallRoot "extract"
    if (Test-Path $extractDir) {
        Remove-Item $extractDir -Recurse -Force
    }

    Write-Host "Extracting FFmpeg ..."
    Expand-Archive -Path $ffmpegArchivePath -DestinationPath $extractDir -Force

    $packageDir = Get-ChildItem -Path $extractDir -Directory | Where-Object { Test-Path (Join-Path $_.FullName "bin\ffmpeg.exe") } | Select-Object -First 1
    if ($null -eq $packageDir) {
        throw "Could not find ffmpeg.exe after extracting the FFmpeg package."
    }

    if (Test-Path $ffmpegCurrentDir) {
        Remove-Item $ffmpegCurrentDir -Recurse -Force
    }

    Move-Item -Path $packageDir.FullName -Destination $ffmpegCurrentDir
    Add-DirectoryToUserPath $ffmpegBinDir
    Refresh-SessionPath
}

function Ensure-Python {
    $pythonSpec = Find-PythonLaunchSpec
    if ($null -ne $pythonSpec) {
        return $pythonSpec
    }

    if (Test-Command "winget") {
        try {
            Install-WithWinget "Python.Python.3.12" "Python"
            $pythonSpec = Find-PythonLaunchSpec
            if ($null -ne $pythonSpec) {
                return $pythonSpec
            }
        }
        catch {
            Write-Warning "winget Python install failed: $($_.Exception.Message)"
        }
    }
    else {
        Write-Warning "winget was not found. Falling back to a direct Python installer download."
    }

    try {
        Install-PythonDirectly
    }
    catch {
        throw "Could not install Python automatically. Download Python 3.12+ from https://www.python.org/downloads/windows/ and then run launch_app.bat again. Details: $($_.Exception.Message)"
    }

    $pythonSpec = Find-PythonLaunchSpec
    if ($null -eq $pythonSpec) {
        throw "Python installation finished but Python 3.12+ is still unavailable. Install it manually from https://www.python.org/downloads/windows/ and then run launch_app.bat again."
    }

    return $pythonSpec
}

function Ensure-Node {
    $nodeCommand = Find-NodeCommand
    if ($null -ne $nodeCommand) {
        return $nodeCommand
    }

    if (Test-Command "winget") {
        try {
            Install-WithWinget "OpenJS.NodeJS.LTS" "Node.js"
            $nodeCommand = Find-NodeCommand
            if ($null -ne $nodeCommand) {
                return $nodeCommand
            }
        }
        catch {
            Write-Warning "winget Node.js install failed: $($_.Exception.Message)"
        }
    }
    else {
        Write-Warning "winget was not found. Falling back to a direct Node.js installer download."
    }

    Install-NodeDirectly
    $nodeCommand = Find-NodeCommand
    if ($null -eq $nodeCommand) {
        throw "Node.js installation finished but npm is still unavailable."
    }

    return $nodeCommand
}

function Ensure-Ffmpeg {
    $ffmpegCommand = Find-FfmpegCommand
    if ($null -ne $ffmpegCommand) {
        return $ffmpegCommand
    }

    if (Test-Command "winget") {
        try {
            Install-WithWinget "Gyan.FFmpeg" "FFmpeg"
            $ffmpegCommand = Find-FfmpegCommand
            if ($null -ne $ffmpegCommand) {
                return $ffmpegCommand
            }
        }
        catch {
            Write-Warning "winget FFmpeg install failed: $($_.Exception.Message)"
        }
    }
    else {
        Write-Warning "winget was not found. Falling back to a direct FFmpeg download."
    }

    Install-FfmpegDirectly
    $ffmpegCommand = Find-FfmpegCommand
    if ($null -eq $ffmpegCommand) {
        throw "FFmpeg installation finished but ffmpeg is still unavailable."
    }

    return $ffmpegCommand
}

function Ensure-StateDir {
    if (-not (Test-Path $stateDir)) {
        New-Item -ItemType Directory -Path $stateDir | Out-Null
    }

    if (-not (Test-Path $installerDir)) {
        New-Item -ItemType Directory -Path $installerDir | Out-Null
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

function Invoke-Setup {
    Set-Location $root

    Write-Host "Preparing Miscoshorts AI for Windows..."
    Refresh-SessionPath
    Ensure-StateDir
    $pythonSpec = Ensure-Python
    $null = Ensure-Node
    $null = Ensure-Ffmpeg

    if (-not (Test-Path $venvPython)) {
        Write-Host "Creating virtual environment ..."
        Invoke-Python $pythonSpec @("-m", "venv", ".venv")
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
        try {
            npm install
        }
        finally {
            Pop-Location
        }
        Update-Stamp $frontendDepsStamp
    }
    else {
        Write-Host "Frontend packages are already installed. Skipping npm install."
    }

    if (Test-Stale $frontendBuildStamp @((Join-Path $frontendDir "src"), (Join-Path $frontendDir "index.html"), (Join-Path $frontendDir "package.json"), (Join-Path $frontendDir "vite.config.ts"))) {
        Write-Host "Building frontend ..."
        Push-Location $frontendDir
        try {
            npm run build
        }
        finally {
            Pop-Location
        }
        Update-Stamp $frontendBuildStamp
    }
    else {
        Write-Host "Frontend build is up to date. Skipping build."
    }

    Write-Host "Launching app ..."
    & $venvPython app_launcher.py
}

try {
    Ensure-StateDir
    if (Test-Path $logPath) {
        Remove-Item $logPath -Force
    }

    Start-Transcript -Path $logPath -Force | Out-Null
    Invoke-Setup
}
catch {
    $errorMessage = $_.Exception.Message
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace -ForegroundColor DarkYellow
    }
    Show-FailureAndExit $errorMessage
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
}