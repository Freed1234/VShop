# 2026-09-30 실행 환경 복구

- 최신 소스: C:\Users\minwo\VShop\VShop (원본 변경 없음)
- 기존 Codex 사본은 삭제된 Python314를 가리켜 실행 불가.
- 최신 프로젝트에는 가상환경과 EXE가 없었음.
- Python 3.12.14 / PySide6 6.11.1 / PyInstaller 6.21.0으로 새 가상환경과 EXE 생성.
- 이 PC에 이미 있던 패키지를 복사해 사용했으며 새 다운로드는 없음.
- build-exe.ps1: Python 3.14 고정 안내 제거, Python 경로 지정 지원,
  없는 가상환경 생성 및 손상된 환경 백업, 오프라인 빌드용 -SkipInstall 추가.
- 기존 앱의 상점·야시장·세트·잔액·영상 기능 소스는 변경하지 않음.
- GUI 생성, 3개 탭, 모의 잔액·상품 표시, 로그아웃 검증 통과.
- 공개 스킨 버전 API와 Riot 로그인 URL은 HTTP 200 확인.
- 실제 계정 로그인, 개인 상점/잔액 조회와 영상 재생은 이번 검사에서 미검증.

실행: dist\VShopPersonal.exe (별도 Python 설치 불필요).
소스 실행: .\.venv\Scripts\python.exe app.py
현재 환경 재빌드: powershell -ExecutionPolicy Bypass -File .\build-exe.ps1 -SkipInstall

가상환경은 이 PC의 Python 런타임에 의존하므로 다른 PC로 옮기면 새로 만드세요.
EXE는 가상환경 경로에 의존하지 않습니다.

빌드 방식 참고: https://www.pyinstaller.org/en/stable/usage.html

## 추가 수정: EXE의 HTTPS 오류

기존 EXE는 System32의 libcrypto-3-x64.dll과 libssl-3-x64.dll을 포함했습니다.
이 libcrypto에는 Python _ssl.pyd가 요구하는 X509_STORE_get1_objects가 없어
SSL 모듈 로딩이 실패하고 urllib가 HTTPSHandler를 만들지 못했습니다.
Python 자체 DLL 쌍에서는 모든 필수 함수가 확인되었습니다.

VShopPersonal.spec으로 Python OpenSSL DLL 쌍을 고정했습니다.
계정 정보 없이 배포 EXE를 검사하는 --check-https --report 옵션을 추가했습니다.
앞선 소스 환경의 HTTP 200 검사는 배포 EXE의 동작을 보증하지 못했습니다.

재빌드 검증에서 PATH에 있던 Poppler의 icuuc.dll이 Qt용으로 수집되는 문제도
발견했습니다. 이 DLL은 함수명 규칙이 달라 QtCore 로딩이 실패했습니다.
해당 DLL은 번들에서 제외하고 이 PC의 Windows ICU를 사용하도록 수정했습니다.

최종 검증: 배포 EXE가 Qt 모듈을 정상 로딩한 뒤 HTTPS 검사 모드로 실행되어
종료 코드 0을 반환했습니다. OpenSSL 3.5.8, HTTPSHandler 존재, 공개 API HTTP 200,
frozen=true, ok=true를 확인했습니다. 번들 안의 _ssl.pyd와 OpenSSL DLL 2개는
Python 원본과 SHA-256이 모두 일치합니다. 결과는 https-check.json에 보관했습니다.
