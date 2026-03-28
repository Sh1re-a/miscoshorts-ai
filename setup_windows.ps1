param(
    [switch]$SkipLaunch,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = Join-Path $root "app"
$frontendDir = Join-Path $root "frontend"
$frontendDistDir = Join-Path $frontendDir "dist"
$frontendEntry = Join-Path $frontendDistDir "index.html"
$internalDir = Join-Path $root ".miscoshorts"
$runtimeDir = Join-Path $internalDir "runtime"
$modelCacheDir = Join-Path $runtimeDir "model-cache"
$setupDir = Join-Path $internalDir "setup"
$venvDir = Join-Path $runtimeDir "venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateDir = $setupDir
$installerDir = Join-Path $stateDir "installers"
$logPath = Join-Path $stateDir "windows-setup.log"
$pythonInstallRoot = Join-Path $runtimeDir "python"
$pythonCurrentDir = Join-Path $pythonInstallRoot "current"
$pythonManagedExe = Join-Path $pythonCurrentDir "python.exe"
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
$ffmpegInstallRoot = Join-Path $runtimeDir "ffmpeg"
$ffmpegCurrentDir = Join-Path $ffmpegInstallRoot "current"
$ffmpegBinDir = Join-Path $ffmpegCurrentDir "bin"
$pythonCoreStamp = Join-Path $stateDir "python-core.state"
$pythonOptionalStamp = Join-Path $stateDir "python-optional.state"
$frontendDepsStamp = Join-Path $stateDir "frontend-deps.state"
$frontendBuildStamp = Join-Path $stateDir "frontend-build.state"
$whisperModelStamp = Join-Path $stateDir "whisper-model.state"
$script:SetupStep = 0
$script:SetupStartedAt = Get-Date
$whisperDistilLargeV3DownloadBytes = 1536MB
$whisperLargeV3DownloadBytes = 3100MB

function Show-FailureAndExit($message) {
    Write-Host ""
    Write-Host "Setup failed." -ForegroundColor Red
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    Write-Host "Log file: $logPath"
    if (-not $NonInteractive) {
        [void](Read-Host "Press Enter to close this window")
    }
    exit 1
}

function Get-SetupStepCount {
    if ($SkipLaunch) {
        return 4
    }

    return 5
}

function Write-SetupBanner {
    Write-Host ""
    Write-Host "Miscoshorts AI" -ForegroundColor Cyan
    Write-Host "Local setup and launch" -ForegroundColor DarkCyan
    Write-Host ""
    Write-Host "This window keeps the local app alive while it is running." -ForegroundColor DarkGray
    Write-Host "Private runtime files live in .miscoshorts and are ignored by Git/GitHub." -ForegroundColor DarkGray
    Write-Host "Speech models are cached locally in .miscoshorts\runtime\model-cache." -ForegroundColor DarkGray
}

function Start-SetupStep($title) {
    $script:SetupStep += 1
    Write-Host ""
    Write-Host "[$($script:SetupStep)/$(Get-SetupStepCount)] $title" -ForegroundColor Cyan
}

function Write-SetupInfo($message) {
    Write-Host "  $message" -ForegroundColor Gray
}

function Write-SetupReuse($message) {
    Write-Host "  Reusing: $message" -ForegroundColor DarkGreen
}

function Write-SetupAction($message) {
    Write-Host "  $message" -ForegroundColor Yellow
}

function Write-SetupDone($message) {
    Write-Host "  Done: $message" -ForegroundColor Green
}

function Write-SetupSummary($message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Green
}

function Format-ByteSize($bytes) {
    if ($bytes -ge 1GB) {
        return ("{0:N1} GB" -f ($bytes / 1GB))
    }
    if ($bytes -ge 1MB) {
        return ("{0:N0} MB" -f ($bytes / 1MB))
    }
    if ($bytes -ge 1KB) {
        return ("{0:N0} KB" -f ($bytes / 1KB))
    }
    return "$bytes B"
}

function Get-DirectoryHasFiles($path) {
    if (-not (Test-Path $path)) {
        return $false
    }

    return $null -ne (Get-ChildItem -Path $path -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Get-RemoteFileSizeLabel($url) {
    try {
        $request = [System.Net.HttpWebRequest]::Create($url)
        $request.Method = "HEAD"
        $request.AllowAutoRedirect = $true
        $response = $request.GetResponse()
        try {
            if ($response.ContentLength -gt 0) {
                return Format-ByteSize $response.ContentLength
            }
        }
        finally {
            $response.Dispose()
        }
    }
    catch {
    }

    return $null
}

function Hide-InternalDirectory($path) {
    if (-not (Test-Path $path)) {
        return
    }

    try {
        attrib +h $path 2>$null | Out-Null
    }
    catch {
    }
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
    if ((Test-Path $pythonManagedExe) -and (Test-UsablePython $pythonManagedExe)) {
        return New-PythonLaunchSpec $pythonManagedExe
    }

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
    $sizeLabel = Get-RemoteFileSizeLabel $url
    if ($null -ne $sizeLabel) {
        Write-SetupAction "Downloading $label ($sizeLabel) ..."
    }
    else {
        Write-SetupAction "Downloading $label ..."
    }
    Invoke-WebRequest -Uri $url -OutFile $destinationPath
}

function Install-WithWinget($packageId, $label) {
    if (-not (Test-Command "winget")) {
        throw "winget was not found. Install App Installer from Microsoft Store first, then run launch_app.bat again."
    }

    Write-SetupAction "Installing $label ..."
    winget install --id $packageId --accept-package-agreements --accept-source-agreements --silent
    Refresh-SessionPath
}

function Install-PythonDirectly {
    Ensure-StateDir

    if (-not (Test-Path $pythonInstallRoot)) {
        New-Item -ItemType Directory -Path $pythonInstallRoot | Out-Null
    }

    if (Test-Path $pythonCurrentDir) {
        Remove-Item $pythonCurrentDir -Recurse -Force
    }

    Invoke-Download $pythonInstallerUrl $pythonInstallerPath "Python installer from python.org"

    Write-SetupAction "Running Python installer ..."
    $installerArgs = @(
        "/quiet",
        "InstallAllUsers=0",
        "TargetDir=`"$pythonCurrentDir`"",
        "PrependPath=0",
        "Include_launcher=0",
        "InstallLauncherAllUsers=0",
        "Include_pip=1",
        "Include_venv=1",
        "AssociateFiles=0",
        "Shortcuts=0",
        "Include_test=0"
    )
    $process = Start-Process -FilePath $pythonInstallerPath -ArgumentList $installerArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Python installer exited with code $($process.ExitCode)."
    }

    if (-not (Test-Path $pythonManagedExe)) {
        throw "Python installer completed but python.exe was not found in $pythonCurrentDir."
    }

    Add-DirectoryToSessionPath $pythonCurrentDir
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

    Write-SetupAction "Running Node.js installer ..."
    $process = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", "`"$nodeInstallerPath`"", "/qn", "/norestart") -Wait -PassThru
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

    Write-SetupAction "Extracting FFmpeg ..."
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
        throw "Python installation finished but Python 3.12+ is still unavailable."
    }

    return $pythonSpec
}

function Test-UsableVenv {
    if (-not (Test-Path $venvPython)) {
        return $false
    }

    return Test-UsablePython $venvPython
}

function Remove-PythonDependencyStamps {
    foreach ($stampPath in @($pythonCoreStamp, $pythonOptionalStamp)) {
        if (Test-Path $stampPath) {
            Remove-Item $stampPath -Force
        }
    }
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
    foreach ($directory in @($internalDir, $runtimeDir, $modelCacheDir, $setupDir, $installerDir)) {
        if (-not (Test-Path $directory)) {
            New-Item -ItemType Directory -Path $directory | Out-Null
        }
    }

    Hide-InternalDirectory $internalDir

    if (-not (Test-Path $installerDir)) {
        New-Item -ItemType Directory -Path $installerDir | Out-Null
    }

    if (-not (Test-Path $pythonInstallRoot)) {
        New-Item -ItemType Directory -Path $pythonInstallRoot | Out-Null
    }
}

function Get-StringHash($value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha.Dispose()
    }
}

function Get-PathFingerprint($path) {
    if (-not (Test-Path $path)) {
        return "missing"
    }

    $item = Get-Item $path
    if (-not $item.PSIsContainer) {
        return "file:$((Get-FileHash -Algorithm SHA256 -Path $item.FullName).Hash)"
    }

    # Use file sizes + modification times for faster directory fingerprinting
    $root = $item.FullName
    $entries = Get-ChildItem -Path $item.FullName -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relativePath = $_.FullName.Substring($root.Length).TrimStart('\', '/').Replace("\", "/")
        "$relativePath|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
    }

    return "dir:$(Get-StringHash ($entries -join "`n"))"
}

function Get-StateSignature($paths) {
    $parts = foreach ($path in $paths) {
        $resolvedPath = if ([System.IO.Path]::IsPathRooted($path)) { $path } else { Join-Path $root $path }
        "$path=$(Get-PathFingerprint $resolvedPath)"
    }

    return Get-StringHash ($parts -join "`n")
}

function Test-ShouldInstallOptionalPythonDeps {
    return (
        (Test-Path (Join-Path $root "requirements-optional.txt")) -and (
            -not [string]::IsNullOrWhiteSpace($env:PYANNOTE_AUTH_TOKEN) -or
            -not [string]::IsNullOrWhiteSpace($env:HF_TOKEN) -or
            $env:AUTO_INSTALL_PRO_DEPS -eq "1"
        )
    )
}

function Get-WhisperModelSignature {
    $requestedModels = if ([string]::IsNullOrWhiteSpace($env:WHISPER_MODEL)) { "distil-large-v3,large-v3" } else { $env:WHISPER_MODEL }
    $requestedBackend = if ([string]::IsNullOrWhiteSpace($env:WHISPER_BACKEND)) { "auto" } else { $env:WHISPER_BACKEND }
    return Get-StringHash "$requestedModels`n$requestedBackend"
}

function Read-StateValue($statePath) {
    if (-not (Test-Path $statePath)) {
        return $null
    }

    return (Get-Content -Path $statePath -Raw).Trim()
}

function Write-StateValue($statePath, $value) {
    Set-Content -Path $statePath -Value $value -NoNewline
}

function Test-StateMatch($statePath, $expectedValue) {
    $currentValue = Read-StateValue $statePath
    return $null -ne $currentValue -and $currentValue -eq $expectedValue
}

function Invoke-CheckedCommand($command, $arguments, $failureMessage) {
    & $command @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$failureMessage Exit code: $LASTEXITCODE."
    }
}

function Test-FrontendBuildReady {
    return Test-Path $frontendEntry
}

function Invoke-Setup {
    Set-Location $root

    Write-SetupBanner
    Refresh-SessionPath
    Ensure-StateDir

    Start-SetupStep "Checking local tools"
    $pythonSpec = Ensure-Python
    Write-SetupDone "Python is ready."
    $null = Ensure-Ffmpeg
    Write-SetupDone "FFmpeg is ready."
    $pythonCoreSignature = Get-StateSignature @("requirements.txt")
    $optionalDepsMode = "off"
    if (Test-ShouldInstallOptionalPythonDeps) {
        $optionalDepsMode = "on"
    }
    $pythonOptionalSignature = "{0}|optional={1}" -f (Get-StateSignature @("requirements-optional.txt")), $optionalDepsMode
    $whisperModelSignature = Get-WhisperModelSignature
    $frontendDepsSignature = Get-StateSignature @("frontend/package.json", "frontend/package-lock.json")
    $frontendBuildSignature = Get-StateSignature @("frontend/src", "frontend/index.html", "frontend/package.json", "frontend/vite.config.ts")

    Start-SetupStep "Preparing Python environment"
    if ((Test-Path $venvDir) -and (-not (Test-UsableVenv))) {
        Write-SetupAction "Existing Python environment is invalid. Recreating it ..."
        Remove-Item $venvDir -Recurse -Force
        Remove-PythonDependencyStamps
    }

    if (-not (Test-UsableVenv)) {
        Write-SetupAction "Creating local Python environment ..."
        Invoke-CheckedCommand $pythonSpec.Command ($pythonSpec.Arguments + @("-m", "venv", $venvDir)) "Creating the Python virtual environment failed."
        Write-SetupDone "Local Python environment created."
    }
    else {
        Write-SetupReuse "existing local Python environment"
    }

    if (-not (Test-StateMatch $pythonCoreStamp $pythonCoreSignature)) {
        Write-SetupAction "Installing Python core packages ..."
        Write-SetupInfo "This is the app runtime only. The speech model is downloaded on first transcription."
        Write-SetupInfo "Expected first-time app dependency download: usually under a few hundred MB, depending on Windows wheel selection."
        Invoke-CheckedCommand $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "--quiet", "-r", "requirements.txt") "Installing Python dependencies failed."
        Write-StateValue $pythonCoreStamp $pythonCoreSignature
        Write-SetupDone "Python core packages are up to date."
    }
    else {
        Write-SetupReuse "Python core packages already installed"
    }

    $shouldInstallOptional = Test-ShouldInstallOptionalPythonDeps
    if ($shouldInstallOptional) {
        if (-not (Test-StateMatch $pythonOptionalStamp $pythonOptionalSignature)) {
            Write-SetupAction "Installing optional pro diarization add-ons ..."
            Write-SetupInfo "This optional bundle is much heavier than the default setup and is only needed for advanced diarization."
            Invoke-CheckedCommand $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "--quiet", "-r", "requirements-optional.txt") "Installing optional pro dependencies failed."
            Write-StateValue $pythonOptionalStamp $pythonOptionalSignature
            Write-SetupDone "Optional Python add-ons are up to date."
        }
        else {
            Write-SetupReuse "Optional Python add-ons already installed"
        }
    } else {
        Write-SetupReuse "Optional Python add-ons disabled"
    }

    if ((Test-StateMatch $whisperModelStamp $whisperModelSignature) -and (Get-DirectoryHasFiles (Join-Path $modelCacheDir "whisper"))) {
        Write-SetupReuse "Whisper model cache already prepared"
    }
    else {
        Write-SetupAction "Preparing the configured Whisper model before the first render ..."
        Write-SetupInfo "This avoids model-missing failures during transcription."
        $env:HF_HOME = Join-Path $modelCacheDir "huggingface"
        $env:XDG_CACHE_HOME = Join-Path $modelCacheDir "xdg"
        $env:WHISPER_MODEL_CACHE_DIR = Join-Path $modelCacheDir "whisper"
        Invoke-CheckedCommand $venvPython @("-m", "app.preflight") "Preparing the Whisper model failed."
        Write-StateValue $whisperModelStamp $whisperModelSignature
        Write-SetupDone "Whisper model is ready."
    }

    Start-SetupStep "Preparing app interface"
    if (Test-FrontendBuildReady) {
        Write-SetupReuse "prebuilt frontend already included"
        Write-SetupInfo "The app will open using the bundled interface with no extra frontend setup."
    }
    else {
        $frontendPackageJson = Join-Path $frontendDir "package.json"
        if (-not (Test-Path $frontendPackageJson)) {
            throw "The frontend source files were not found. Download the full GitHub ZIP again, extract it completely, and run launch_app.bat from the extracted project folder."
        }

        Write-SetupInfo "A prebuilt frontend was not found, so Windows will prepare the browser app locally."
        $nodeCommand = Ensure-Node
        Write-SetupDone "Node.js is ready."

        if (-not (Test-StateMatch $frontendDepsStamp $frontendDepsSignature)) {
            Write-SetupAction "Installing frontend packages ..."
            Write-SetupInfo "This is only for the local dashboard build and is skipped when frontend/dist is already included."
            Push-Location $frontendDir
            try {
                Invoke-CheckedCommand $nodeCommand @("ci") "Installing frontend packages failed."
            }
            finally {
                Pop-Location
            }
            Write-StateValue $frontendDepsStamp $frontendDepsSignature
            Write-SetupDone "Frontend packages are up to date."
        }
        else {
            Write-SetupReuse "frontend packages already installed"
        }

        if (-not (Test-StateMatch $frontendBuildStamp $frontendBuildSignature)) {
            Write-SetupAction "Building frontend ..."
            Push-Location $frontendDir
            try {
                Invoke-CheckedCommand $nodeCommand @("run", "build") "Building the frontend failed. Check the error output above and the setup log for the real npm or TypeScript error."
            }
            finally {
                Pop-Location
            }
            Write-StateValue $frontendBuildStamp $frontendBuildSignature
            Write-SetupDone "Frontend build is ready."
        }
        else {
            Write-SetupReuse "frontend build already up to date"
        }

        if (-not (Test-FrontendBuildReady)) {
            throw "The frontend build finished without creating frontend/dist/index.html. Check the npm build output above and the setup log for details."
        }
    }

    Start-SetupStep "Final checks"
    $elapsed = New-TimeSpan -Start $script:SetupStartedAt -End (Get-Date)
    Write-SetupDone "Local setup is complete."
    Write-SetupInfo "Internal setup files are stored in .miscoshorts so the project folder stays clean."
    if (Get-DirectoryHasFiles (Join-Path $modelCacheDir "whisper")) {
        Write-SetupReuse "existing local speech-model cache"
    }
    else {
        Write-SetupInfo "First transcription will download the configured Whisper model into the private cache."
        Write-SetupInfo ("Planned model order: distil-large-v3 first ({0}), then large-v3 fallback ({1}) only if needed." -f (Format-ByteSize $whisperDistilLargeV3DownloadBytes), (Format-ByteSize $whisperLargeV3DownloadBytes))
        Write-SetupInfo "If the cache was deleted, the same configured model will be downloaded again automatically."
    }
    Write-SetupSummary "Setup finished in $([math]::Max(1, [int][math]::Round($elapsed.TotalSeconds))) seconds."

    if ($SkipLaunch) {
        Write-SetupSummary "Skipping app launch because -SkipLaunch was requested."
        return
    }

    Start-SetupStep "Starting app"
    Write-SetupInfo "Opening the local app in your browser. Keep this window open while the app runs."
    $env:HF_HOME = Join-Path $modelCacheDir "huggingface"
    $env:XDG_CACHE_HOME = Join-Path $modelCacheDir "xdg"
    $env:WHISPER_MODEL_CACHE_DIR = Join-Path $modelCacheDir "whisper"
    Invoke-CheckedCommand $venvPython @("-m", "app.app_launcher") "The local app failed to start."
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
