$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $VenvPython) {
    try {
        & $VenvPython -c "import sys; print(sys.executable)" 2>$null
        $VenvUsable = $LASTEXITCODE -eq 0
    } catch {
        $VenvUsable = $false
    }
} else {
    $VenvUsable = $false
}

if (-not $VenvUsable) {
    Write-Host "가상환경 경로가 유효하지 않아 현재 Python으로 다시 생성합니다."
    & py -3.14 -m venv (Join-Path $ProjectDir ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "가상환경을 생성하지 못했습니다. Python 3.14 설치를 확인하세요."
    }
}

Push-Location $ProjectDir
try {
    Write-Host "[1/3] Python 가상환경 확인"
    & $VenvPython --version
    if ($LASTEXITCODE -ne 0) {
        throw "가상환경이 손상되었습니다. .venv를 삭제한 뒤 다시 생성하세요."
    }

    Write-Host "[2/3] 빌드 도구 설치"
    & $VenvPython -m pip install -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) {
        throw "의존성 설치에 실패했습니다."
    }

    Write-Host "[3/3] 단일 EXE 생성"
    & $VenvPython -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath (Join-Path $ProjectDir "dist") `
        --workpath (Join-Path $ProjectDir "build") `
        (Join-Path $ProjectDir "VShopPersonal.spec")

    if ($LASTEXITCODE -ne 0) {
        throw "EXE 빌드에 실패했습니다."
    }

    Write-Host ""
    Write-Host "완료: $(Join-Path $ProjectDir 'dist\VShopPersonal.exe')"
}
finally {
    Pop-Location
}
