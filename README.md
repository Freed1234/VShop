# VShop-main — Windows 11 배포본

Downloads의 VShop-main 소스를 별도 폴더에 복사해 만든 프로젝트입니다.
오늘의 상점·야시장·세트상품·VP/RP 잔액·업그레이드 영상 미리보기 기능을 유지합니다.

## 실행과 배포

release/VShop-main-Windows11-x64.zip을 **전체 압축 해제**하고
VShopPersonal/VShopPersonal.exe를 실행하세요. 실행 PC에는 Python 설치가 필요 없습니다.
EXE 옆의 _internal 폴더도 함께 배포해야 합니다.
Intel/AMD 기반 Windows 11 x64를 대상으로 합니다. ARM은 미검증입니다.
인터넷 연결과 Riot 계정이 필요합니다.

계정 지역을 선택하고 로그인하면 세 가지 상점 탭과 VP/RP 잔액을 볼 수 있습니다.
새로고침 버튼은 상점과 잔액을 함께 다시 조회합니다. 실시간 자동 갱신은 아닙니다.
야시장 기간이 아니거나 데이터가 없으면 해당 탭에 안내가 나옵니다.
스킨 카드의 업그레이드 미리보기에서는 레벨 선택, 재생/일시정지, 탐색, 음량 조절을 지원합니다.
영상은 공개 API에서 임시 다운로드하며 단계 전환/닫기 시 정리합니다.

로그인 화면이 비거나 그래픽 문제가 있으면 앱을 닫고 Run-Compatibility.cmd를 실행합니다.
내장 브라우저의 GPU 가속을 끄는 모드이며 인증서 검사나 브라우저 보안 격리를 끄지 않습니다.
영상 코덱이나 모든 그래픽 문제를 해결한다고 보장하지는 않습니다.

## 변경 내용

- 누락된 VShopPersonal.spec 복구 및 Git의 *.spec 제외 제거.
- Windows 폴더형 EXE, ZIP 및 SHA-256 체크섬 생성.
- 빌드 DLL 검색 경로 제한으로 다른 앱의 ICU DLL 유입 방지.
- Python의 _ssl.pyd와 일치하는 OpenSSL DLL 쌍을 명시적으로 포함하고 해시 검사.
- Qt WebEngine 및 Qt Multimedia/FFmpeg 구성 요소 포함 검사.
- 호환 모드, 시작 환경/오류 종류 로그, 로그인 페이지 실패 안내 추가.
- 배포 EXE 자체에서 UI·WebEngine·TLS·MP4 디코딩을 검사하는 오프라인 모드 추가.
- 기존 --check-https --report 공개 API 검사 기능 유지.

상점 API 요청·로그인·영상 다운로드 및 재생 로직은 원본을 유지합니다.
기존 REPAIR-NOTES.md는 제공된 소스에 들어 있던 과거 기록입니다.
이번 작업의 검사 결과는 validation 폴더를 확인하세요.

## 개발 PC에서 빌드

빌드하는 PC에만 Python 3.12 x64가 필요합니다.

    powershell -ExecutionPolicy Bypass -File .\build-exe.ps1

Python 경로를 직접 지정할 수도 있습니다.

    .\build-exe.ps1 -PythonPath 'C:\Path\To\Python312\python.exe'

-VenvDir와 -WorkDir로 가상환경/중간 파일 경로를 지정할 수 있습니다.
기본값은 .build-venv와 build입니다. 기존 환경에 고정 버전이 설치되어 있으면 -SkipInstall을 사용할 수 있습니다.
다시 빌드하면 이 복사본의 dist/release 산출물을 교체합니다.
버전: Python 3.12, PySide6 6.11.2, PyInstaller 6.22.2, hooks-contrib 2026.7.

소스 실행:

    .\.build-venv\Scripts\python.exe launcher.py
    .\.build-venv\Scripts\python.exe launcher.py --software-rendering

## 검증

    .\test-portable.ps1

ZIP을 새 경로에 풀고 PATH에서 Python을 제외하며 Python/Qt 환경 변수를 정리한 후
일반/호환 모드에서 실제 메인 화면, 모의 상점·잔액, 로그아웃, 내장 브라우저,
인증서 검증 설정, 1초짜리 합성 MP4의 프레임 디코딩과 임시 파일 정리를 확인합니다.
계정이나 인터넷을 사용하지 않습니다. 테스트 폴더와 JSON 결과는 보존됩니다.

공개 API HTTPS 검사는 별도로 수행합니다. 보고서의 ok/frozen이 모두 true여야 합니다.

    $p = Start-Process .\dist\VShopPersonal\VShopPersonal.exe -ArgumentList '--check-https --report https-check.json' -Wait -PassThru -WindowStyle Hidden
    Get-Content https-check.json

test-assets/smoke-test.mp4는 자체 생성한 1초짜리 파란 화면과 무음의 H.264/AAC 테스트 파일입니다.
실제 Riot 스킨 영상이나 계정 데이터를 포함하지 않습니다.

## 로그와 확인 범위

로그 위치: %LOCALAPPDATA%\VShopPersonal\logs\app.log.
쓸 수 없으면 임시 폴더 아래에 저장합니다. 로그에는 인증 토큰·비밀번호·로그인 URL·예외 값을 저장하지 않습니다.
오류 코드 위치에는 사용자 폴더 경로가 포함될 수 있습니다.
OS가 Python 자체를 불러오기 전에 발생하는 오류는 앱 로그에 기록되지 않을 수 있습니다.

Riot 비공개 서비스의 변경/접근 제한은 패키징으로 해결되지 않습니다.
실제 로그인·2단계 인증·개인 상점·잔액 및 실제 스킨 영상은 사용자의 계정/네트워크로 확인해야 합니다.
다른 물리 PC나 깨끗한 Windows 가상 머신에서의 검사는 이번 로컬 검사와 별개입니다.
배포 EXE는 코드 서명되지 않았습니다.

## 이번 빌드의 검사 상태

HTTPS·상점 UI·영상 디코딩 검사는 통과했지만, 내장 브라우저 검사는 현재 실행 환경에서
보안 프로세스 생성 오류(49)로 완료하지 못했습니다. 이전 배포본도 같은 환경에서 같은 오류가
발생했습니다. 전체 검증 성공을 의미하지 않습니다. 자세한 내용은 validation/검증결과.md를 보세요.
