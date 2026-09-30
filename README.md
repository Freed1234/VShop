# VShop Personal (Python)

Windows PC에서 개인적으로 실행하는 VALORANT 상점·야시장·세트상품 및 보유 VP/RP 뷰어입니다.

이 프로젝트는 Riot Games의 공식 앱이 아니며, Riot이 공개한 개발자용 상점 API를
사용하지 않습니다. 게임 클라이언트용 비공개 서비스가 변경되거나 접근을 제한하면
예고 없이 작동하지 않을 수 있습니다. Riot의 현재 개발자 정책에서는 온라인 상점
추적 앱이 승인 대상이 아닙니다.

## 보안 원칙

- 로그인 화면은 `auth.riotgames.com`의 Riot 공식 페이지입니다.
- 비밀번호를 애플리케이션 코드에서 읽거나 저장하지 않습니다.
- access token은 실행 중 메모리에서만 사용하고 파일에 저장하지 않습니다.
- 별도 제작자 서버를 사용하지 않습니다.
- 로그아웃하면 WebView 쿠키를 삭제합니다.
- 자동 구매 기능은 포함하지 않습니다.

소스가 공개돼 있어도 다른 사람이 배포한 EXE가 같은 소스로 빌드됐다는 보장은
없습니다. 가능하면 이 폴더의 소스를 직접 실행하세요.

## 설치

Python 3.10~3.14를 설치한 뒤 PowerShell에서 이 폴더로 이동합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 실행

```powershell
.\.venv\Scripts\python.exe app.py
```

1. 계정 지역을 선택합니다. 한국 계정은 `한국 (KR)`을 사용합니다.
2. `Riot 계정으로 로그인`을 누릅니다.
3. Riot 공식 페이지에서 로그인과 2단계 인증을 완료합니다.
4. 상점 상품과 보유 VP/RP가 표시될 때까지 기다립니다.

상점 화면은 `오늘의 상점`, `야시장`, `세트상품` 탭으로 나뉩니다. 야시장이 열려 있으면
할인 전 가격, 할인율, 할인가와 종료까지 남은 시간이 표시됩니다. 야시장 기간이
아니면 해당 탭에 안내 문구가 표시됩니다.

`세트상품` 탭에는 현재 판매 중인 세트들의 이름, 이미지, 서버가 제공한 세트 가격과
판매 종료까지 남은 시간이 표시됩니다. 새 세트가 공개 에셋 API에 아직 등록되지
않았다면 임시 이름과 가격을 표시하며, 가격이 누락되면 `가격 정보 없음`으로 표시합니다.

보유 VP와 RP(레디어나이트 포인트)는 탭 위 공통 영역에서 항상 확인할 수 있습니다.
`새로고침`을 누르면 상품과 잔액을 함께 다시 조회합니다. 시간과 잔액은 표시된 조회
시점 기준이며 실시간 자동 갱신은 아닙니다. 잔액 요청 실패는 `조회 불가`로 표시하고,
마우스를 올리면 오류를 확인할 수 있습니다. 로그아웃하면 잔액과 상품 표시를 비웁니다.

응답 구조 참고: [상점 응답](https://valapidocs.techchrism.me/endpoint/storefront),
[잔액 응답](https://github.com/HeyM1ke/ValorantClientAPI/blob/master/Docs/UserBalance.md).

앱을 다시 실행하면 보안을 위해 다시 로그인해야 합니다.

## 업그레이드 영상 미리보기

오늘의 상점 또는 야시장에서 스킨 카드나 `업그레이드 미리보기` 버튼을 누릅니다.
상세 창에서 레벨을 선택하고 `재생`을 누르면 해당 단계의 영상을 볼 수 있습니다.
재생/일시정지, 재생 위치 이동, 음량 조절을 지원하며 창을 닫으면 소리도 중지됩니다.
영상이 등록되지 않은 단계에는 `영상 없음`을 표시합니다. 조회 또는 재생 오류가
나면 `다시 불러오기`를 누르세요. 세트 카드 자체에는 개별 스킨 미리보기 버튼이 없습니다.

공개 스킨 API의 `levels[].streamedVideo` 주소에서 영상을 임시 파일로 받아 재생합니다.
최초 상세 조회 때 스킨 목록을 메모리에 캐시하며, 영상 요청에는 Riot 인증 토큰을
보내지 않습니다. 단계 전환이나 창 닫기 시 임시 파일을 정리합니다. 영상 파일을
EXE에 포함하지 않으므로 인터넷 연결이 필요합니다. 영상당 최대 128 MB를 지원합니다.

## 단일 EXE 만들기

먼저 `app.py`가 정상 실행되는지 확인한 다음 아래 명령을 실행합니다.

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

빌드가 완료되면 다음 파일이 생성됩니다.

```text
dist\VShopPersonal.exe
```

이 EXE에는 Python, PySide6, Qt WebEngine이 함께 포함되므로 파일이 크고 첫 실행이
느릴 수 있습니다. 개인 빌드에는 코드 서명이 없으므로 Windows SmartScreen이 경고를
표시할 수도 있습니다.

가상환경이 없거나 삭제된 Python을 가리키면 빌드 스크립트가 정상 Python을 찾아
가상환경을 생성합니다. 손상된 환경은 `.venv.backup-*`으로 보존합니다.
Python이 PATH에 등록되어 있지 않으면 실행 파일 경로를 지정하세요.

```powershell
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1 -PythonPath "C:\path\python.exe"
```

## 문제 해결

- `HTTP 401/403`: 로그아웃한 뒤 다시 로그인합니다.
- 클라이언트 버전 오류: 잠시 후 다시 실행합니다.
- 로그인 페이지가 비어 있음: 인터넷 연결과 Riot 로그인 서비스 상태를 확인합니다.
  이 앱은 Qt WebEngine을 사용하며 Edge WebView2를 사용하지 않습니다.
- 상점 응답 오류: Riot의 비공개 엔드포인트가 변경됐을 가능성이 있습니다.

## 파일 구성

- `app.py`: 로그인 WebView, API 요청, 상점 UI
- `requirements.txt`: Python 의존성
- `requirements-build.txt`: EXE 빌드 의존성
- `build-exe.ps1`: 단일 EXE 빌드 스크립트
- `VShopPersonal.spec`: Python과 일치하는 OpenSSL DLL을 명시적으로 포함하는 빌드 설정

## EXE의 HTTPS 통신 검사

PowerShell에서 아래 명령을 실행하면 계정 로그인 없이 공개 버전 API로 검사합니다.
검사 결과의 `ok`와 `frozen`이 모두 `true`이면 EXE 내부 HTTPS 요청이 성공한 것입니다.

```powershell
$check = Start-Process -FilePath .\dist\VShopPersonal.exe -ArgumentList '--check-https --report https-check.json' -Wait -PassThru
Get-Content https-check.json
```

`unknown url type: https` 오류는 이번 빌드에서 호환되지 않는 System32 OpenSSL DLL이
포함되면서 발생했습니다. `.spec`이 Python의 `_ssl.pyd`와 같은 폴더의 OpenSSL DLL을
사용하도록 수정했습니다. 인증서 검증을 끄거나 Windows DLL을 변경하지 않습니다.
빌드 설정 참고: https://pyinstaller.org/en/stable/spec-files.html
