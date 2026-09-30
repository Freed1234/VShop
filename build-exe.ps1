param([string]$PythonPath, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
$VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
function Test-Python([string]$Candidate) {
    if (-not $Candidate) { return $false }
    try {
        & $Candidate -c "import sys; sys.exit(0 if sys.version_info >= (3,10) and sys.maxsize > 2**32 else 1)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
if (-not (Test-Python $VenvPython)) {
    $Candidates = @($PythonPath)
    foreach ($CommandName in @('python', 'py')) {
        $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
        if ($Command) { $Candidates += $Command.Source }
    }
    $BasePython = $Candidates | Where-Object { Test-Python $_ } | Select-Object -First 1
    if (-not $BasePython) {
        throw 'No working 64-bit Python 3.10+ found. Install Python or use -PythonPath C:\path\python.exe. The built EXE does not require Python.'
    }
    $VenvDir = Join-Path $ProjectDir '.venv'
    if (Test-Path -LiteralPath $VenvDir) {
        Rename-Item -LiteralPath $VenvDir -NewName ('.venv.backup-' + [guid]::NewGuid().ToString('N'))
    }
    & $BasePython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
Push-Location $ProjectDir
try {
    if (-not $SkipInstall) {
        & $VenvPython -m pip install -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    & $VenvPython -c "import ssl, PySide6.QtWebEngineWidgets, PySide6.QtMultimedia, PyInstaller; print(ssl.OPENSSL_VERSION)"
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies are missing. Run again without -SkipInstall.' }
    & $VenvPython -m PyInstaller --noconfirm --clean --distpath (Join-Path $ProjectDir 'dist') --workpath (Join-Path $ProjectDir 'build') (Join-Path $ProjectDir 'VShopPersonal.spec')
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
    Write-Host "Created: $(Join-Path $ProjectDir 'dist\VShopPersonal.exe')"
} finally { Pop-Location }
exit 0
