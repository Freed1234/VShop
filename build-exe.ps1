$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "가상환경이 없습니다. 먼저 'py -V:3.14 -m venv .venv'를 실행하세요."
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
        --onefile `
        --windowed `
        --name VShopPersonal `
        --distpath (Join-Path $ProjectDir "dist") `
        --workpath (Join-Path $ProjectDir "build") `
        --specpath $ProjectDir `
        (Join-Path $ProjectDir "app.py")

    if ($LASTEXITCODE -ne 0) {
        throw "EXE 빌드에 실패했습니다."
    }

    Write-Host ""
    Write-Host "완료: $(Join-Path $ProjectDir 'dist\VShopPersonal.exe')"
}
finally {
    Pop-Location
}
