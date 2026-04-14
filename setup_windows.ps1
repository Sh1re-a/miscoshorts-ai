param(
    [switch]$SkipLaunch,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"
$PSDefaultParameterValues["Set-Content:Encoding"] = "utf8"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$windowsDataRoot = if (-not [string]::IsNullOrWhiteSpace($env:MISCOSHORTS_DATA_DIR)) { $env:MISCOSHORTS_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "MiscoshortsAI" }
$env:MISCOSHORTS_INTERNAL_DIR = if (-not [string]::IsNullOrWhiteSpace($env:MISCOSHORTS_INTERNAL_DIR)) { $env:MISCOSHORTS_INTERNAL_DIR } else { Join-Path $windowsDataRoot "internal" }
$env:MISCOSHORTS_OUTPUTS_DIR = if (-not [string]::IsNullOrWhiteSpace($env:MISCOSHORTS_OUTPUTS_DIR)) { $env:MISCOSHORTS_OUTPUTS_DIR } else { Join-Path $windowsDataRoot "outputs" }
$appDir = Join-Path $root "app"
$frontendDir = Join-Path $root "frontend"
$frontendDistDir = Join-Path $frontendDir "dist"
$frontendEntry = Join-Path $frontendDistDir "index.html"
$projectCompatInternalDir = Join-Path $root ".miscoshorts"
$projectCompatOutputsDir = Join-Path $root "outputs"
$internalDir = $env:MISCOSHORTS_INTERNAL_DIR
$runtimeDir = Join-Path $internalDir "runtime"
$modelCacheDir = Join-Path $runtimeDir "model-cache"
$setupDir = Join-Path $internalDir "setup"
$outputsDir = $env:MISCOSHORTS_OUTPUTS_DIR
$venvDir = Join-Path $runtimeDir "venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$stateDir = $setupDir
$installerDir = Join-Path $stateDir "installers"
$logPath = Join-Path $stateDir "windows-setup.log"
$doctorReportPath = Join-Path $stateDir "doctor-report.json"
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
    if (Test-Path $doctorReportPath) {
        Write-Host "Doctor report: $doctorReportPath"
    }
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
    Write-Host "Project files can stay anywhere, including an external SSD." -ForegroundColor DarkGray
    Write-Host "Private runtime files live in $internalDir and are ignored by Git/GitHub." -ForegroundColor DarkGray
    Write-Host "Speech models are cached locally in $modelCacheDir." -ForegroundColor DarkGray
    Write-Host "Rendered outputs are stored in $outputsDir." -ForegroundColor DarkGray
    Write-Host "If something fails, send the setup log and doctor report from $setupDir." -ForegroundColor DarkGray
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

# Run a console command (exe/cmd/bat) while showing an animated spinner.
# On failure the last 25 lines of captured output are included in the error.
function Invoke-WithSpinner {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$ArgList    = @(),
        [string]$WorkDir      = "",
        [int[]]$OkExitCodes   = @(0)
    )

    # .cmd / .bat files must be launched through cmd.exe
    $actualExe  = $FilePath
    $actualArgs = $ArgList
    if ($FilePath.ToLower().EndsWith('.cmd') -or $FilePath.ToLower().EndsWith('.bat')) {
        $actualExe  = 'cmd.exe'
        $actualArgs = @('/c', $FilePath) + $ArgList
    }

    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()

    $procParams = @{
        FilePath               = $actualExe
        NoNewWindow            = $true
        PassThru               = $true
        RedirectStandardOutput = $outFile
        RedirectStandardError  = $errFile
    }
    if ($WorkDir -and (Test-Path $WorkDir -ErrorAction SilentlyContinue)) {
        $procParams.WorkingDirectory = $WorkDir
    }
    if ($actualArgs.Count -gt 0) {
        $procParams.ArgumentList = $actualArgs
    }

    $proc   = Start-Process @procParams
    $frames = @('|', '/', '-', '\')
    $n      = 0
    $t0     = [DateTime]::Now

    while (-not $proc.HasExited) {
        $sec = [math]::Floor(([DateTime]::Now - $t0).TotalSeconds)
        [Console]::Write("`r  $($frames[$n % 4])  $Label  ($sec s)   ")
        Start-Sleep -Milliseconds 150
        $n++
    }

    $pad = ' ' * ([Math]::Max(60, $Label.Length + 24))
    [Console]::Write("`r$pad`r")

    $exitCode = $proc.ExitCode
    $stdOut   = [string](Get-Content $outFile -Raw -ErrorAction SilentlyContinue)
    $stdErr   = [string](Get-Content $errFile -Raw -ErrorAction SilentlyContinue)
    Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue

    if ($OkExitCodes -notcontains $exitCode) {
        $combined = ($stdErr.Trim() + "`n" + $stdOut.Trim()).Trim()
        $lines    = $combined -split "`r?`n"
        $snip     = ($lines | Select-Object -Last 25) -join "`n"
        throw "$Label failed (exit code $exitCode).`n$snip"
    }
}

function Invoke-Download($url, $destinationPath, $label) {
    # Probe remote file size once for the progress counter
    $expectedBytes = 0
    try {
        $hr = [System.Net.HttpWebRequest]::Create($url)
        $hr.Method = 'HEAD'
        $hr.AllowAutoRedirect = $true
        $hr.Timeout = 6000
        $resp = $hr.GetResponse()
        $expectedBytes = $resp.ContentLength
        $resp.Dispose()
    } catch {}

    $sizeLabel = if ($expectedBytes -gt 0) { ' (' + (Format-ByteSize $expectedBytes) + ')' } else { '' }
    Write-SetupAction "Downloading $label$sizeLabel"

    $maxAttempts  = 3
    $delaySeconds = 3

    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        if (Test-Path $destinationPath) {
            Remove-Item $destinationPath -Force -ErrorAction SilentlyContinue
        }

        # Run download in a background job so the main thread can show progress
        $dlJob = Start-Job -ScriptBlock {
            param($u, $d)
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -Uri $u -OutFile $d -UseBasicParsing
        } -ArgumentList $url, $destinationPath

        $frames = @('|', '/', '-', '\')
        $n  = 0
        $t0 = [DateTime]::Now

        while ($dlJob.State -eq 'Running') {
            $sec = [math]::Floor(([DateTime]::Now - $t0).TotalSeconds)
            $pctStr = ''
            if ($expectedBytes -gt 0 -and (Test-Path $destinationPath)) {
                $got  = (Get-Item $destinationPath -ErrorAction SilentlyContinue)
                $got  = if ($null -ne $got) { $got.Length } else { 0 }
                $pct  = [math]::Min(99, [int]($got / $expectedBytes * 100))
                $gotM = [math]::Round($got / 1MB, 1)
                $totM = [math]::Round($expectedBytes / 1MB, 1)
                $pctStr = "  $pct%  ($gotM / $totM MB)"
            }
            [Console]::Write("`r  $($frames[$n % 4])  $label$pctStr  ($sec s)   ")
            Start-Sleep -Milliseconds 300
            $n++
        }

        $pad = ' ' * ([Math]::Max(80, $label.Length + 40))
        [Console]::Write("`r$pad`r")

        $jobErrors = Receive-Job $dlJob 2>&1
        $jobState  = $dlJob.State
        Remove-Job $dlJob

        if ($jobState -eq 'Completed') {
            Write-SetupDone "Downloaded $label."
            return
        }

        $errMsg = if ($null -ne $jobErrors) { ($jobErrors | Out-String).Trim() } else { 'Unknown network error' }
        if ($attempt -lt $maxAttempts) {
            Write-SetupInfo "Download attempt $attempt failed: $errMsg. Retrying in ${delaySeconds}s ..."
            Start-Sleep -Seconds $delaySeconds
            $delaySeconds *= 2
        } else {
            throw "Could not download $label after $maxAttempts attempts. Check your internet connection and try running launch_app.bat again. Details: $errMsg"
        }
    }
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

    $installerArgs = @(
        '/quiet',
        'InstallAllUsers=0',
        "TargetDir=`"$pythonCurrentDir`"",
        'PrependPath=0',
        'Include_launcher=0',
        'InstallLauncherAllUsers=0',
        'Include_pip=1',
        'Include_venv=1',
        'AssociateFiles=0',
        'Shortcuts=0',
        'Include_test=0'
    )
    Invoke-WithSpinner "Installing Python $pythonInstallerVersion" $pythonInstallerPath $installerArgs

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

    # 0 = success, 1641/3010 = success + reboot pending (non-critical for our use)
    Invoke-WithSpinner "Installing Node.js $nodeInstallerVersion" 'msiexec.exe' @("/i", "`"$nodeInstallerPath`"", '/qn', '/norestart') -OkExitCodes @(0, 1641, 3010)
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
    foreach ($directory in @($internalDir, $runtimeDir, $modelCacheDir, $setupDir, $installerDir, $outputsDir)) {
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

function Ensure-CompatibilityJunction($linkPath, $targetPath, $label) {
    $resolvedLink = [System.IO.Path]::GetFullPath($linkPath)
    $resolvedTarget = [System.IO.Path]::GetFullPath($targetPath)

    if ($resolvedLink -eq $resolvedTarget) {
        return
    }

    if (Test-Path $linkPath) {
        try {
            if ((Resolve-Path $linkPath).Path -eq (Resolve-Path $targetPath).Path) {
                Write-SetupReuse "$label compatibility path already points to $targetPath"
                return
            }
        }
        catch {
            Write-SetupInfo "$label compatibility path already exists at $linkPath."
            return
        }

        Write-SetupInfo "$label compatibility path already exists at $linkPath."
        return
    }

    try {
        New-Item -ItemType Junction -Path $linkPath -Target $targetPath | Out-Null
        Write-SetupDone "$label compatibility link created at $linkPath"
    }
    catch {
        Write-SetupInfo "Could not create $label compatibility link at $linkPath. Old scripts should use the real path in $targetPath instead."
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

function Write-DoctorSnapshot {
    if (-not (Test-Path $venvPython)) {
        return
    }

    try {
        $reportText = & $venvPython -m app.doctor --json 2>&1
        $reportBody = if ($reportText -is [System.Array]) { $reportText -join [Environment]::NewLine } else { [string]$reportText }
        Set-Content -Path $doctorReportPath -Value $reportBody
        Write-SetupInfo "Doctor report: $doctorReportPath"
    }
    catch {
        Write-SetupInfo "Could not refresh doctor report automatically. You can run it later with: $venvPython -m app.doctor"
    }
}

function Assert-RenderReadyFromDoctorReport {
    if (-not (Test-Path $doctorReportPath)) {
        Write-SetupInfo "Doctor snapshot is missing. Continuing with launch."
        return
    }

    try {
        $report = Get-Content -Path $doctorReportPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-SetupInfo "Doctor report could not be read (corrupted?). Continuing with launch."
        return
    }
    $blockingChecks = @($report.blockingChecks)
    $warningChecks = @($report.warningChecks)

    if ($report.renderReady) {
        if ($warningChecks.Count -gt 0) {
            Write-SetupInfo "Render readiness: READY WITH WARNINGS"
            foreach ($check in $warningChecks | Select-Object -First 3) {
                Write-SetupInfo "OPTIONAL: $($check.name): $($check.message)"
            }
        }
        else {
            Write-SetupInfo "Render readiness: READY"
        }
        return
    }

    Write-SetupInfo "Render readiness: BLOCKED"
    foreach ($check in $blockingChecks) {
        Write-SetupInfo "REQUIRED: $($check.name): $($check.message)"
        if ($check.fix) {
            Write-SetupInfo "Fix: $($check.fix)"
        }
    }
    throw "Blocking setup checks are still failing. Fix the required items above, then run launch_app.bat again."
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
    Ensure-CompatibilityJunction $projectCompatInternalDir $internalDir "Internal runtime"
    Ensure-CompatibilityJunction $projectCompatOutputsDir $outputsDir "Outputs"

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
        Write-SetupInfo "Installing Python runtime packages (first run: up to a few hundred MB)..."
        Invoke-WithSpinner "Installing Python core packages" $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "--no-input", "--progress-bar", "off", "--quiet", "-r", "requirements.txt")
        Write-StateValue $pythonCoreStamp $pythonCoreSignature
        Write-SetupDone "Python core packages are up to date."
    }
    else {
        Write-SetupReuse "Python core packages already installed"
    }

    $shouldInstallOptional = Test-ShouldInstallOptionalPythonDeps
    if ($shouldInstallOptional) {
        if (-not (Test-StateMatch $pythonOptionalStamp $pythonOptionalSignature)) {
            Write-SetupInfo "Installing optional diarization add-ons (heavier bundle, only needed for advanced speaker detection)..."
            Invoke-WithSpinner "Installing optional Python add-ons" $venvPython @("-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", "--no-input", "--progress-bar", "off", "--quiet", "-r", "requirements-optional.txt")
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
        Write-SetupInfo "Downloading the speech model on first run (distil-large-v3 ~1.5 GB, large-v3 ~3 GB as fallback). This step is skipped on repeat runs."
        $env:HF_HOME = Join-Path $modelCacheDir "huggingface"
        $env:XDG_CACHE_HOME = Join-Path $modelCacheDir "xdg"
        $env:WHISPER_MODEL_CACHE_DIR = Join-Path $modelCacheDir "whisper"
        Invoke-WithSpinner "Downloading and caching Whisper speech model" $venvPython @("-m", "app.preflight")
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
            Write-SetupInfo "Installing frontend packages (only needed when building the dashboard locally)..."
            Invoke-WithSpinner "Installing frontend packages" $nodeCommand @("ci", "--silent") -WorkDir $frontendDir
            Write-StateValue $frontendDepsStamp $frontendDepsSignature
            Write-SetupDone "Frontend packages are up to date."
        }
        else {
            Write-SetupReuse "frontend packages already installed"
        }

        if (-not (Test-StateMatch $frontendBuildStamp $frontendBuildSignature)) {
            Invoke-WithSpinner "Building app interface" $nodeCommand @("run", "build") -WorkDir $frontendDir -OkExitCodes @(0)
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
    Write-SetupAction "Refreshing support diagnostics snapshot ..."
    Write-DoctorSnapshot
    Assert-RenderReadyFromDoctorReport
    $elapsed = New-TimeSpan -Start $script:SetupStartedAt -End (Get-Date)
    Write-SetupDone "Local setup is complete."
    Write-SetupInfo "Internal setup files are stored outside the project folder so external SSD installs stay more stable."
    Write-SetupInfo "Outputs are stored in $outputsDir."
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
    Write-SetupInfo "Need support later? Run: $venvPython -m app.doctor"
    Write-SetupInfo "Doctor report: $doctorReportPath"
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
