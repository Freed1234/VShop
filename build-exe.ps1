[CmdletBinding()]
param(
    [Alias('PythonPath')][string]$Python = '',
    [string]$VenvDir = '',
    [string]$WorkDir = '',
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectDir = $PSScriptRoot
if (-not $VenvDir) { $VenvDir = Join-Path $ProjectDir '.build-venv' }
if (-not $WorkDir) { $WorkDir = Join-Path $ProjectDir 'build' }
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    if ($Python) {
        & $Python -m venv $VenvDir
    } else {
        & py -3.12 -m venv $VenvDir
    }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.12 x64 on the BUILD PC, or pass -Python with its full path.' }
}
& $VenvPython -c "import sys, platform; assert sys.version_info[:2] == (3,12) and platform.machine().upper() == 'AMD64' and sys.maxsize > 2**32, 'Use Python 3.12 x64 to build this release'"
if ($LASTEXITCODE -ne 0) { throw 'Invalid build environment. Use a new -VenvDir with Python 3.12 x64.' }

Push-Location $ProjectDir
$PreviousCache = $env:PYINSTALLER_CONFIG_DIR
try {
    Write-Host '[1/4] Install pinned build dependencies'
    if (-not $SkipInstall) {
        & $VenvPython -m pip --disable-pip-version-check --no-cache-dir install -r (Join-Path $ProjectDir 'requirements-build.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & $VenvPython -c "import PySide6,PyInstaller; assert PySide6.__version__ == '6.11.2' and PyInstaller.__version__ == '6.22.2'"
    if ($LASTEXITCODE -ne 0) { throw 'Pinned build dependencies are missing. Run without -SkipInstall.' }

    Write-Host '[2/4] Build Windows x64 folder bundle'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $WorkDir 'cache'
    & $VenvPython -m PyInstaller --noconfirm --clean --distpath (Join-Path $ProjectDir 'dist') --workpath $WorkDir (Join-Path $ProjectDir 'VShopPersonal.spec')
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }

    Write-Host '[3/4] Verify bundled runtime files'
    $BundleDir = Join-Path $ProjectDir 'dist\VShopPersonal'
    $BundleFiles = @(Get-ChildItem -LiteralPath $BundleDir -Recurse -File)
    if ($BundleFiles | Where-Object Name -EQ 'icuuc.dll') { throw 'Unexpected bundled ICU DLL. Windows 11 provides ICU; check build search paths.' }
    foreach ($Required in @('VShopPersonal.exe', 'python312.dll', 'QtWebEngineProcess.exe', 'Qt6WebEngineCore.dll', 'qwindows.dll', 'icudtl.dat', 'qtwebengine_resources.pak', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
        if (-not ($BundleFiles | Where-Object Name -EQ $Required)) { throw "Missing runtime file: $Required" }
    }
    if (-not ($BundleFiles | Where-Object { $_.Directory.Name -eq 'qtwebengine_locales' -and $_.Extension -eq '.pak' })) { throw 'Missing WebEngine locales.' }
    & $VenvPython (Join-Path $ProjectDir 'validate-bundle.py') $BundleDir
    if ($LASTEXITCODE -ne 0) { throw 'SSL/multimedia bundle validation failed.' }
    foreach ($UserFile in @('Run-Compatibility.cmd', '사용안내.txt')) {
        Copy-Item -LiteralPath (Join-Path $ProjectDir $UserFile) -Destination $BundleDir -Force
    }

    Write-Host '[4/4] Create portable ZIP and checksum'
    $ReleaseDir = Join-Path $ProjectDir 'release'
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    $Archive = Join-Path $ReleaseDir 'VShop-main-Windows11-x64.zip'
    Compress-Archive -LiteralPath $BundleDir -DestinationPath $Archive -Force
    $Hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($Archive + '.sha256') -Value ($Hash + '  ' + (Split-Path $Archive -Leaf)) -Encoding ascii
    Write-Host "Ready: $Archive"
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $PreviousCache
    Pop-Location
}
