"""VALORANT 개인 상점 조회용 데스크톱 애플리케이션.

전체 데이터 흐름은 다음과 같다.

1. 내장 WebView에서 Riot 공식 로그인 페이지를 연다.
2. 로그인 후 리디렉션 URL에서 임시 access token을 추출한다.
3. 토큰에서 사용자 ID를 읽고 Riot Entitlements token을 발급받는다.
4. 두 토큰으로 Riot 게임 클라이언트용 상점 서버에 요청한다.
5. 상점이 반환한 상품 UUID를 공개 에셋 API의 이름/이미지와 결합한다.
6. 처리한 결과를 PySide6 카드 UI로 보여준다.

아이디와 비밀번호는 Riot 로그인 페이지가 직접 처리한다. 이 프로그램은 access
token을 실행 중 메모리에만 보관하며 파일이나 별도 제작자 서버에 저장하지 않는다.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# Riot가 사용하는 로그인 주소다. 로그인이 성공하면 redirect_uri로 이동하면서
# URL의 fragment 또는 query에 access_token을 넣어 준다.
LOGIN_URL = (
    "https://auth.riotgames.com/authorize?"
    "redirect_uri=https%3A%2F%2Fplayvalorant.com%2Fopt_in&"
    "client_id=play-valorant-web-prod&response_type=token%20id_token&"
    "nonce=1&scope=account%20openid"
)
# Riot 게임 플레이 권한을 나타내는 특별한 토큰(Entitlements token)을 발급해주는 서버 주소다.
ENTITLEMENTS_URL = "https://entitlements.auth.riotgames.com/api/token/v1/"

# valorant-api.com은 Riot 인증 서버가 아닌 공개 커뮤니티 에셋 API다.
# 여기에는 Riot 토큰을 보내지 않고 버전, 스킨 이름, 이미지 주소만 요청한다.
VALORANT_API = "https://valorant-api.com/v1"

# Riot 응답의 Cost는 "화폐 UUID: 가격" 형태다. 아래 UUID가 VP를 뜻한다.
VP_CURRENCY_ID = "85ad13f7-3d1b-5128-9eb2-7cd8ee0b5741"

# 게임 서비스는 요청한 클라이언트의 플랫폼 정보도 확인한다.
# 이 Base64로 디코딩된 문자열은 Windows PC 플랫폼 정보를 나타낸다.
CLIENT_PLATFORM = (
    "eyJwbGF0Zm9ybVR5cGUiOiJQQyIsInBsYXRmb3JtT1MiOiJXaW5kb3dzIiw"
    "icGxhdGZvcm1PU1ZlcnNpb24iOiIxMC4wLjE5MDQyLjEuMjU2LjY0Yml0Ii"
    "wicGxhdGZvcm1DaGlwc2V0IjoiVW5rbm93biJ9"
)


# 이 코드 단락은 네트워크나 인증 과정에서 발생할 수 있는 오류를 위한 사용자 정의 예외를 정의함
class ApiError(RuntimeError):
    """사용자에게 그대로 표시해도 되는 네트워크/API 오류."""

    pass


# 이 코드 단락은 상점 상품 정보를 화면에 표시하기 위해 필요한 데이터를 간단하게 담는 구조체를 정의함
@dataclass
class ShopItem:
    """화면의 상품 카드 하나를 만드는 데 필요한 가공 완료 데이터."""

    name: str
    price: int
    image: bytes | None


@dataclass
class ShopResult:
    """오늘의 상품 목록과 다음 상점 교체까지 남은 시간."""

    items: list[ShopItem]
    remaining_seconds: int


# 이 코드 단락은 HTTP 요청을 보내고 JSON 응답을 파싱해 프로그램에서 사용할 수 있는 형태로 변환함
def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """HTTPS 요청을 보내고 JSON 응답을 Python 딕셔너리로 변환한다.

    인증 헤더는 호출하는 쪽에서 명시적으로 전달한다. 따라서 공개 에셋 API를
    호출할 때 Riot access token이 실수로 붙는 것을 방지할 수 있다.
    """

    # POST 본문이 있으면 Python 딕셔너리를 UTF-8 JSON 바이트로 직렬화한다.
    body = None if data is None else json.dumps(data).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "VShopPersonal/1.0",
        **(headers or {}),
    }
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method
    )
    try:
        # 요청이 무한히 대기하지 않도록 25초 제한을 둔다.
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        host = urllib.parse.urlsplit(url).hostname or "server"
        if exc.code in (401, 403):
            raise ApiError(
                f"{host} 인증에 실패했습니다. 다시 로그인해 주세요. (HTTP {exc.code})"
            ) from exc
        raise ApiError(f"{host} 요청에 실패했습니다. (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"네트워크 연결에 실패했습니다: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ApiError("서버가 올바른 JSON 응답을 반환하지 않았습니다.") from exc


# 이 코드 단락은 스킨 이미지 파일을 직접 내려받아 메모리에서 사용할 수 있게 함
def request_bytes(url: str) -> bytes | None:
    """스킨 이미지를 바이트로 다운로드한다.

    이미지 하나가 실패해도 나머지 상점 정보는 보여줄 수 있으므로 예외 대신
    None을 반환하고 UI에서 '이미지 없음'으로 처리한다.
    """

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "VShopPersonal/1.0", "Accept": "image/*"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError):
        return None


# 이 코드 단락은 로그인 토큰 안에 포함된 사용자 식별자를 추출함
def jwt_subject(access_token: str) -> str:
    """JWT payload의 ``sub`` 값을 읽어 Riot 사용자 ID를 얻는다.

    JWT는 ``header.payload.signature`` 구조다. 여기서는 payload를 로컬에서
    디코딩만 하며 서명을 직접 검증하지 않는다. 토큰의 실제 유효성은 이후 Riot
    서버가 인증 요청을 받을 때 검증한다.
    """

    try:
        # 두 번째 조각이 Base64 URL-safe 형식의 payload다.
        payload = access_token.split(".")[1]
        # Base64 디코더가 요구하는 길이에 맞게 '=' 패딩을 복원한다.
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        subject = decoded.get("sub")
        if not subject:
            raise ValueError("missing sub")
        return str(subject)
    except (IndexError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ApiError("로그인 토큰에서 사용자 ID를 확인하지 못했습니다.") from exc


# 이 코드 단락은 인증 토큰과 지역 정보를 이용해 오늘의 상점 데이터를 가져와 화면용 형태로 가공함
def fetch_shop(access_token: str, region: str) -> ShopResult:
    """인증 토큰을 사용해 오늘의 상점을 조회하고 UI용 데이터로 가공한다."""

    # 1) 게임 서비스 요청에 필요한 최신 Riot 클라이언트 버전을 확인한다.
    version_response = request_json(f"{VALORANT_API}/version")
    client_version = version_response.get("data", {}).get("riotClientVersion")
    if not client_version:
        raise ApiError("현재 VALORANT 클라이언트 버전을 확인하지 못했습니다.")

    # Bearer 토큰은 '이 사용자가 로그인했다'는 임시 증명서 역할을 한다.
    # 이 헤더 묶음은 Riot 소유 서버에만 전달한다.
    shared_headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Riot-ClientVersion": client_version,
        "X-Riot-ClientPlatform": CLIENT_PLATFORM,
    }
    # 2) access token으로 게임 이용 권한을 나타내는 Entitlements token을 받는다.
    entitlement_response = request_json(
        ENTITLEMENTS_URL,
        method="POST",
        headers=shared_headers,
        data={},
    )
    entitlement_token = entitlement_response.get("entitlements_token")
    if not entitlement_token:
        raise ApiError("Entitlements 토큰을 받지 못했습니다.")

    # 3) access token의 sub 값에서 상점 소유자의 사용자 ID를 얻는다.
    user_id = jwt_subject(access_token)
    game_headers = {
        **shared_headers,
        "X-Riot-Entitlements-JWT": str(entitlement_token),
    }
    # 4) 선택 지역의 Riot 게임 서비스에서 이 사용자의 개인 상점을 요청한다.
    # 예: 한국은 pd.kr.a.pvp.net, 아시아 태평양은 pd.ap.a.pvp.net이다.
    storefront_url = f"https://pd.{region}.a.pvp.net/store/v3/storefront/{user_id}"
    storefront = request_json(
        storefront_url,
        method="POST",
        headers=game_headers,
        data={},
    )

    # 5) 상점 JSON에서 오늘의 개별 스킨 제안만 꺼낸다.
    # 각 OfferID는 스킨 레벨을 가리키는 UUID다.
    layout = storefront.get("SkinsPanelLayout") or {}
    offers = layout.get("SingleItemStoreOffers") or []
    if not offers:
        # 일부 응답은 가격이 없는 UUID 목록만 제공할 수 있어 이를 보조 처리한다.
        offer_ids = layout.get("SingleItemOffers") or []
        offers = [{"OfferID": offer_id, "Cost": {}} for offer_id in offer_ids]

    items: list[ShopItem] = []
    for offer in offers:
        offer_id = offer.get("OfferID")
        if not offer_id:
            continue

        # Cost 딕셔너리에서 VP UUID에 해당하는 숫자를 가격으로 사용한다.
        cost = offer.get("Cost") or {}
        price = int(cost.get(VP_CURRENCY_ID) or next(iter(cost.values()), 0))

        # 6) UUID만으로는 사람이 읽기 어려우므로 공개 에셋 API에서 한글 이름과
        # 이미지 URL을 찾는다. 이 요청에는 Riot 인증 토큰을 넣지 않는다.
        asset_response = request_json(
            f"{VALORANT_API}/weapons/skinlevels/{offer_id}?language=ko-KR"
        )
        asset = asset_response.get("data") or {}
        name = asset.get("displayName") or "알 수 없는 스킨"
        image_url = asset.get("displayIcon")
        # 실제 이미지 파일은 displayIcon이 가리키는 CDN에서 내려받는다.
        image = request_bytes(image_url) if image_url else None
        items.append(ShopItem(name=name, price=price, image=image))

    if not items:
        raise ApiError("상점 응답에서 오늘의 상품을 찾지 못했습니다.")

    # 서버가 초 단위로 준 남은 시간을 UI에서 시간/분으로 다시 표시한다.
    remaining = int(
        layout.get("SingleItemOffersRemainingDurationInSeconds") or 0
    )
    return ShopResult(items=items, remaining_seconds=remaining)


# 이 코드 단락은 백그라운드 작업의 결과를 메인 UI 스레드로 전달하기 위한 신호를 정의함
class WorkerSignals(QObject):
    """백그라운드 작업 결과를 메인 UI 스레드에 전달하는 Qt 신호."""

    finished = Signal(object)
    failed = Signal(str)


# 이 코드 단락은 네트워크 작업을 별도 스레드에서 실행하도록 래퍼 클래스를 구성함
class Worker(QRunnable):
    """네트워크 요청 중 창이 멈추지 않도록 함수를 작업 스레드에서 실행한다."""

    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            # 성공하면 ShopResult를 finished 신호에 실어 보낸다.
            self.signals.finished.emit(self.function())
        except ApiError as exc:
            # 예상 가능한 오류는 정리된 메시지만 UI로 전달한다.
            self.signals.failed.emit(str(exc))
        except Exception:
            # 개발 중 확인할 수 있도록 예상 밖 오류는 콘솔에도 기록한다.
            traceback.print_exc()
            self.signals.failed.emit("예상하지 못한 오류가 발생했습니다.")


# 이 코드 단락은 상점 상품 하나를 카드 형태의 위젯으로 그려 화면에 표시함
class ShopCard(QFrame):
    """스킨 이미지, 이름, VP 가격을 보여주는 상품 카드 위젯."""

    def __init__(self, item: ShopItem) -> None:
        super().__init__()
        self.setObjectName("shopCard")
        self.setMinimumSize(330, 250)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        image_label = QLabel("이미지 없음")
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumHeight(150)
        if item.image:
            # 다운로드한 바이트를 Qt 이미지로 읽고 카드 크기에 맞춰 축소한다.
            pixmap = QPixmap()
            if pixmap.loadFromData(item.image):
                image_label.setPixmap(
                    pixmap.scaled(
                        300,
                        150,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

        name_label = QLabel(item.name)
        name_label.setObjectName("itemName")
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        price_label = QLabel(f"{item.price:,} VP" if item.price else "가격 정보 없음")
        price_label.setObjectName("price")
        price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(image_label, 1)
        layout.addWidget(name_label)
        layout.addWidget(price_label)


# 이 코드 단락은 앱의 메인 창과 화면 전환, 사용자 이벤트를 모두 관리함
class MainWindow(QMainWindow):
    """로그인·로딩·상점 화면과 인증 상태를 관리하는 메인 창."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VShop Personal")
        self.resize(920, 720)
        self.setMinimumSize(760, 600)
        # UI 이벤트 처리는 메인 스레드가 담당하고, HTTP 요청만 이 풀에서 실행한다.
        self.thread_pool = QThreadPool.globalInstance()
        self.worker: Worker | None = None

        # access token은 파일에 저장하지 않고 이 메모리 변수에만 보관한다.
        self.access_token: str | None = None
        self.region = "kr"

        # 리디렉션 이벤트가 여러 번 발생해 상점을 중복 요청하는 것을 막는다.
        self.auth_handled = False

        # 한 창 안에서 홈 → 로그인 → 로딩 → 상점 페이지를 교체한다.
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.home_page = self.build_home_page()
        self.login_page = self.build_login_page()
        self.loading_page = self.build_loading_page()
        self.shop_page = self.build_shop_page()
        for page in (
            self.home_page,
            self.login_page,
            self.loading_page,
            self.shop_page,
        ):
            self.pages.addWidget(page)

        self.apply_style()
        self.pages.setCurrentWidget(self.home_page)

    # 이 코드 단락은 앱의 첫 화면을 구성해 지역 선택과 로그인 진입점을 제공함
    def build_home_page(self) -> QWidget:
        """지역을 선택하고 로그인을 시작하는 첫 화면을 만든다."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(80, 70, 80, 70)
        layout.setSpacing(18)

        title = QLabel("VShop Personal")
        title.setObjectName("title")
        subtitle = QLabel("내 PC에서만 실행되는 VALORANT 오늘의 상점 뷰어")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        notice = QLabel(
            "Riot 공식 앱이 아니며, 비공개 게임 서비스의 변경에 따라 언제든 "
            "작동하지 않을 수 있습니다. 비밀번호와 토큰은 파일에 저장하지 않습니다."
        )
        notice.setObjectName("notice")
        notice.setWordWrap(True)

        region_row = QHBoxLayout()
        region_label = QLabel("계정 지역")
        self.region_combo = QComboBox()
        self.region_combo.addItem("한국 (KR)", "kr")
        self.region_combo.addItem("아시아 태평양 (AP)", "ap")
        self.region_combo.addItem("북미 (NA)", "na")
        self.region_combo.addItem("유럽 (EU)", "eu")
        region_row.addWidget(region_label)
        region_row.addWidget(self.region_combo, 1)

        login_button = QPushButton("Riot 계정으로 로그인")
        login_button.setObjectName("primaryButton")
        login_button.setMinimumHeight(48)
        login_button.clicked.connect(self.start_login)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(16)
        layout.addLayout(region_row)
        layout.addWidget(login_button)
        layout.addWidget(notice)
        layout.addStretch(2)
        return page

    # 이 코드 단락은 Riot 로그인 페이지를 내장 웹뷰 안에 보여주기 위한 화면을 구성함
    def build_login_page(self) -> QWidget:
        """Riot 공식 로그인 사이트를 표시하는 내장 브라우저를 만든다."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)

        top = QHBoxLayout()
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.cancel_login)
        heading = QLabel("Riot 공식 로그인 페이지")
        heading.setObjectName("pageTitle")
        top.addWidget(cancel)
        top.addWidget(heading)
        top.addStretch()

        self.web_view = QWebEngineView()

        # 저장 이름이 없는 QWebEngineProfile은 off-the-record 프로필이다.
        # 쿠키와 캐시는 실행 중 인증에는 쓰이지만 영구 프로필로 지정하지 않는다.
        self.web_profile = QWebEngineProfile(self.web_view)

        # Riot 로그인 페이지가 일반 Windows 브라우저 환경으로 렌더링되게 한다.
        self.web_profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        self.web_page = QWebEnginePage(self.web_profile, self.web_view)
        self.web_view.setPage(self.web_page)
        # 로그인 성공 후 주소가 바뀌면 access_token 포함 여부를 검사한다.
        self.web_view.urlChanged.connect(self.on_login_url_changed)

        layout.addLayout(top)
        layout.addWidget(self.web_view, 1)
        return page

    # 이 코드 단락은 상점 정보를 가져오는 동안 사용자에게 잠시 기다리는 화면을 보여줌
    def build_loading_page(self) -> QWidget:
        """백그라운드 상점 조회 중 표시할 화면을 만든다."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label = QLabel("상점 정보를 가져오는 중…")
        self.loading_label.setObjectName("pageTitle")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("로그인 토큰은 메모리에서만 사용됩니다.")
        detail.setObjectName("subtitle")
        layout.addWidget(self.loading_label)
        layout.addWidget(detail)
        return page

    # 이 코드 단락은 상점 상품 목록과 새로고침, 로그아웃 버튼을 담은 화면을 구성함
    def build_shop_page(self) -> QWidget:
        """상품 카드, 새로고침, 로그아웃 버튼이 있는 상점 화면을 만든다."""

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        top = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading = QLabel("오늘의 상점")
        heading.setObjectName("titleSmall")
        self.shop_meta = QLabel("")
        self.shop_meta.setObjectName("subtitle")
        heading_box.addWidget(heading)
        heading_box.addWidget(self.shop_meta)

        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_shop)
        logout = QPushButton("로그아웃")
        logout.clicked.connect(self.logout)
        top.addLayout(heading_box)
        top.addStretch()
        top.addWidget(refresh)
        top.addWidget(logout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setSpacing(16)
        scroll.setWidget(self.cards_host)

        outer.addLayout(top)
        outer.addWidget(scroll, 1)
        return page

    # 이 코드 단락은 앱 전체의 색상과 위젯 모양을 일관되게 꾸미기 위한 스타일을 적용함
    def apply_style(self) -> None:
        """Qt 스타일시트로 전체 앱의 색상과 위젯 모양을 지정한다."""

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #10141f; color: #f4f5f7; }
            QLabel#title { font-size: 40px; font-weight: 800; color: #ff4655; }
            QLabel#titleSmall { font-size: 30px; font-weight: 800; color: #ff4655; }
            QLabel#pageTitle { font-size: 20px; font-weight: 700; }
            QLabel#subtitle { font-size: 14px; color: #aeb6c6; }
            QLabel#notice {
                padding: 14px; border-radius: 8px; background: #181e2b;
                color: #bdc5d5;
            }
            QLabel#itemName { font-size: 17px; font-weight: 700; }
            QLabel#price { color: #f6d365; font-size: 16px; font-weight: 700; }
            QFrame#shopCard {
                background: #181e2b; border: 1px solid #283043; border-radius: 12px;
            }
            QPushButton {
                background: #293247; border: 0; border-radius: 7px;
                padding: 9px 15px; font-weight: 700;
            }
            QPushButton:hover { background: #35415b; }
            QPushButton#primaryButton { background: #ff4655; color: white; }
            QPushButton#primaryButton:hover { background: #ff5d69; }
            QComboBox {
                background: #181e2b; border: 1px solid #35415b;
                border-radius: 7px; padding: 10px;
            }
            QScrollArea { background: transparent; }
            """
        )

    @Slot()
    # 이 코드 단락은 사용자가 선택한 지역과 로그인 상태를 초기화한 뒤 로그인 화면으로 전환함
    def start_login(self) -> None:
        """선택 지역을 기억하고 Riot 로그인 페이지를 연다."""

        self.region = str(self.region_combo.currentData())
        self.access_token = None
        self.auth_handled = False
        self.pages.setCurrentWidget(self.login_page)
        self.web_view.load(QUrl(LOGIN_URL))

    @Slot()
    # 이 코드 단락은 로그인 과정을 중단하고 홈 화면으로 돌아가며 웹뷰 내용을 정리함
    def cancel_login(self) -> None:
        """로그인을 중단하고 WebView 내용을 비운 뒤 홈으로 돌아간다."""

        self.web_view.setUrl(QUrl("about:blank"))
        self.pages.setCurrentWidget(self.home_page)

    @Slot(QUrl)
    # 이 코드 단락은 로그인 후 리디렉션된 URL에서 토큰을 찾고 상점 조회를 시작함
    def on_login_url_changed(self, url: QUrl) -> None:
        """리디렉션 URL에서 access token을 발견하면 상점 조회를 시작한다."""

        if self.auth_handled:
            return
        token = self.extract_access_token(url.toString())
        if not token:
            return
        # 토큰 문자열은 디스크가 아니라 MainWindow 객체의 메모리에만 둔다.
        self.auth_handled = True
        self.access_token = token

        # 민감한 토큰이 포함된 리디렉션 URL을 화면에 계속 남기지 않는다.
        self.web_view.setUrl(QUrl("about:blank"))
        self.pages.setCurrentWidget(self.loading_page)
        self.load_shop()

    @staticmethod
    # 이 코드 단락은 URL의 fragment나 query에서 access token을 안전하게 추출함
    def extract_access_token(url: str) -> str | None:
        """URL의 ``#fragment`` 또는 ``?query``에서 access_token을 찾는다."""

        parsed = urllib.parse.urlsplit(url)

        # OAuth 구현에 따라 토큰 위치가 달라질 수 있어 두 영역을 모두 검사한다.
        for part in (parsed.fragment, parsed.query):
            token = urllib.parse.parse_qs(part).get("access_token")
            if token:
                return token[0]
        return None

    # 이 코드 단락은 현재 토큰과 지역을 이용해 백그라운드 작업으로 상점 조회를 시작함
    def load_shop(self) -> None:
        """현재 토큰과 지역으로 상점 조회 Worker를 시작한다."""

        if not self.access_token:
            self.show_error("로그인이 필요합니다.")
            return
        self.loading_label.setText("상점 정보를 가져오는 중…")
        token = self.access_token
        region = self.region
        # fetch_shop은 네트워크 I/O를 하므로 메인 UI 스레드에서 직접 호출하지 않는다.
        self.worker = Worker(lambda: fetch_shop(token, region))
        self.worker.signals.finished.connect(self.on_shop_loaded)
        self.worker.signals.failed.connect(self.show_error)
        self.thread_pool.start(self.worker)

    @Slot()
    # 이 코드 단락은 저장된 인증 정보로 상점 정보를 다시 불러와 화면을 갱신함
    def refresh_shop(self) -> None:
        """메모리에 있는 동일 토큰으로 최신 상점 정보를 다시 요청한다."""

        self.pages.setCurrentWidget(self.loading_page)
        self.load_shop()

    @Slot(object)
    # 이 코드 단락은 백그라운드에서 받아온 상점 데이터를 화면에 맞게 배치하고 표시함
    def on_shop_loaded(self, result: ShopResult) -> None:
        """백그라운드 조회 결과를 받아 기존 카드를 새 카드로 교체한다."""

        # 새로고침할 때 이전 카드 위젯이 겹치지 않도록 모두 제거한다.
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # 행/열 인덱스를 계산해 상품을 두 열로 배치한다.
        for index, shop_item in enumerate(result.items):
            self.cards_grid.addWidget(ShopCard(shop_item), index // 2, index % 2)

        # 초 단위 남은 시간을 사람이 읽기 쉬운 시간/분으로 바꾼다.
        hours, remainder = divmod(result.remaining_seconds, 3600)
        minutes = remainder // 60
        remaining_text = (
            f" · 교체까지 약 {hours}시간 {minutes}분"
            if result.remaining_seconds
            else ""
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.shop_meta.setText(f"{now} 기준{remaining_text}")
        self.pages.setCurrentWidget(self.shop_page)
        self.worker = None

    @Slot(str)
    # 이 코드 단락은 조회 실패 시 사용자에게 오류 메시지를 보여주고 적절한 화면으로 복귀함
    def show_error(self, message: str) -> None:
        """백그라운드 작업 오류를 메시지 상자로 보여준다."""

        self.worker = None
        QMessageBox.critical(self, "상점 조회 실패", message)
        if self.access_token:
            self.pages.setCurrentWidget(self.shop_page)
        else:
            self.pages.setCurrentWidget(self.home_page)

    @Slot()
    # 이 코드 단락은 인증 정보를 지우고 웹 세션을 정리한 뒤 홈 화면으로 돌아감
    def logout(self) -> None:
        """인증 관련 메모리, 쿠키, 캐시를 지우고 홈 화면으로 돌아간다."""

        # access token 참조를 제거해 더 이상 API 요청에 사용할 수 없게 한다.
        self.access_token = None
        self.auth_handled = False
        self.web_view.setUrl(QUrl("about:blank"))
        # Riot 로그인 세션이 남지 않도록 WebView의 쿠키와 캐시를 명시적으로 삭제한다.
        self.web_profile.cookieStore().deleteAllCookies()
        self.web_profile.clearHttpCache()
        self.pages.setCurrentWidget(self.home_page)


# 이 코드 단락은 Qt 애플리케이션을 실행하고 메인 창을 표시함
def main() -> int:
    """Qt 이벤트 루프를 시작하고 메인 창을 표시한다."""

    app = QApplication(sys.argv)
    app.setApplicationName("VShop Personal")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
