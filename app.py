"""VALORANT 상점·야시장·세트상품과 보유 VP/RP 조회용 애플리케이션.

전체 데이터 흐름은 다음과 같다.

1. 내장 WebView에서 Riot 공식 로그인 페이지를 연다.
2. 로그인 후 리디렉션 URL에서 임시 access token을 추출한다.
3. 토큰에서 사용자 ID를 읽고 Riot Entitlements token을 발급받는다.
4. 두 토큰으로 Riot 게임 클라이언트용 상점 서버에 요청한다.
5. 상품 UUID를 공개 에셋 API의 이름/이미지와 결합하고 wallet에서 잔액을 읽는다.
6. 처리한 결과를 PySide6 카드 UI로 보여준다.

아이디와 비밀번호는 Riot 로그인 페이지가 직접 처리한다. 이 프로그램은 access
token을 실행 중 메모리에만 보관하며 파일이나 별도 제작자 서버에 저장하지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QTemporaryFile, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QTabWidget,
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
RP_CURRENCY_ID = "e59aa87c-4cbf-517a-5983-6e81511be9b7"

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
    price: int | None
    image: bytes | None
    original_price: int | None = None
    discount_percent: int | None = None
    detail: str = ""
    remaining_seconds: int | None = None
    skin_level_id: str | None = None


@dataclass
class PreviewLevel:
    name: str
    video_url: str | None


@lru_cache(maxsize=1)
def skin_catalog() -> list[dict[str, Any]]:
    """상세 창을 처음 열 때만 스킨 전체 목록을 받아 메모리에 보관한다."""
    return request_json(f"{VALORANT_API}/weapons/skins?language=ko-KR").get("data") or []


def fetch_preview_levels(level_id: str) -> list[PreviewLevel]:
    """상점의 레벨 UUID로 원본 스킨을 찾아 모든 업그레이드 단계를 반환한다."""
    for skin in skin_catalog():
        levels = skin.get("levels") or []
        if not any(level.get("uuid") == level_id for level in levels):
            continue
        result = []
        for index, level in enumerate(levels, 1):
            url = level.get("streamedVideo")
            # 공개 영상 주소만 재생하며 인증 토큰은 전달하지 않는다.
            if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != "https":
                url = None
            name = level.get("displayName") or f"레벨 {index}"
            result.append(PreviewLevel(f"레벨 {index} · {name}", url))
        return result
    raise ApiError("이 스킨의 업그레이드 정보가 아직 등록되지 않았습니다.")


def fetch_preview_video(url: str) -> bytes:
    """HTTPS로 영상을 받아 Qt 자체 네트워크/TLS 구현 차이를 피한다."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ApiError("지원하지 않는 영상 주소입니다.")
    request = urllib.request.Request(url, headers={"User-Agent": "VShopPersonal/1.0"})
    limit = 128 * 1024 * 1024
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            chunks = []
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ApiError("미리보기 영상이 너무 큽니다. (최대 128 MB)")
                chunks.append(chunk)
            if not size:
                raise ApiError("영상 파일이 비어 있습니다.")
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ApiError("영상을 다운로드하지 못했습니다. 연결을 확인하고 다시 시도해 주세요.") from exc


@dataclass
class ShopResult:
    """상점·야시장·세트상품과 로그인 계정의 보유 VP/RP."""

    daily_items: list[ShopItem]
    daily_remaining_seconds: int
    night_market_items: list[ShopItem]
    night_market_remaining_seconds: int
    bundles: list[ShopItem]
    vp_balance: int | None
    rp_balance: int | None
    wallet_error: str = ""


def format_remaining(seconds: int, label: str) -> str:
    """서버 조회 시점의 남은 시간을 읽기 쉬운 일/시간/분으로 표시한다."""
    days, remainder = divmod(max(0, seconds), 86400)
    hours, remainder = divmod(remainder, 3600)
    parts = [f"{days}일"] if days else []
    parts.extend([f"{hours}시간", f"{remainder // 60}분"])
    return f" · {label}까지 약 {' '.join(parts)}"


def bundle_price(bundle: dict[str, Any]) -> int | None:
    """서버의 세트 할인가를 우선 사용한다. 0 VP와 가격 누락을 구분한다."""
    total = bundle.get("TotalDiscountedCost") or {}
    if VP_CURRENCY_ID in total:
        return int(total[VP_CURRENCY_ID])
    offers = bundle.get("ItemOffers") or []
    if offers and all(VP_CURRENCY_ID in (o.get("DiscountedCost") or {}) for o in offers):
        return sum(int(o["DiscountedCost"][VP_CURRENCY_ID]) for o in offers)
    # 구형 응답은 각 구성품의 DiscountedPrice만 제공한다.
    items = bundle.get("Items") or []
    if items and all(
        i.get("CurrencyID") == VP_CURRENCY_ID and i.get("DiscountedPrice") is not None
        for i in items
    ):
        return sum(int(i["DiscountedPrice"]) for i in items)
    return None


def fetch_bundles(storefront: dict[str, Any]) -> list[ShopItem]:
    """판매 중인 세트만 추출하고 공개 API의 한글 이름/이미지와 결합한다."""
    featured = storefront.get("FeaturedBundle") or {}
    entries = list(featured.get("Bundles") or [])
    if featured.get("Bundle"):
        entries.append(featured["Bundle"])
    result = []
    seen = set()
    for bundle in entries:
        asset_id = bundle.get("DataAssetID")
        key = bundle.get("ID") or asset_id
        if not key or key in seen:
            continue
        seen.add(key)
        remaining = bundle.get("DurationRemainingInSeconds")
        if remaining is None:
            remaining = featured.get("BundleRemainingDurationInSeconds")
        if remaining is not None and int(remaining) <= 0:
            continue
        asset = {}
        if asset_id:
            try:
                # Riot 인증 헤더를 공개 에셋 API로 전달하지 않는다.
                asset = request_json(f"{VALORANT_API}/bundles/{asset_id}?language=ko-KR").get("data") or {}
            except ApiError:
                pass  # 신규 세트 에셋이 아직 없어도 가격과 판매 시간은 보여준다.
        image_url = asset.get("displayIcon") or asset.get("displayIcon2")
        result.append(ShopItem(
            name=asset.get("displayName") or f"세트상품 ({asset_id or key})",
            price=bundle_price(bundle),
            image=request_bytes(image_url) if image_url else None,
            original_price=(bundle.get("TotalBaseCost") or {}).get(VP_CURRENCY_ID),
            detail=asset.get("description") or "",
            remaining_seconds=int(remaining) if remaining is not None else None,
        ))
    return result


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


# 이 코드 단락은 인증 토큰과 지역 정보로 오늘의 상점과 야시장을 가져와 화면용 형태로 가공함
def fetch_shop(access_token: str, region: str) -> ShopResult:
    """인증 토큰으로 상점·야시장·세트와 보유 재화를 조회한다."""

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

    # 잔액은 동일한 로그인 계정의 wallet에서 읽는다. 실패와 0 잔액은 다르다.
    balances = {}
    wallet_error = ""
    try:
        wallet = request_json(
            f"https://pd.{region}.a.pvp.net/store/v1/wallet/{user_id}",
            headers=game_headers,
        )
        balances = wallet.get("Balances") or {}
    except ApiError as exc:
        wallet_error = str(exc)

    # 5) 상점 JSON에서 오늘의 개별 스킨 제안만 꺼낸다.
    # 각 OfferID는 스킨 레벨을 가리키는 UUID다.
    layout = storefront.get("SkinsPanelLayout") or {}
    offers = layout.get("SingleItemStoreOffers") or []
    if not offers:
        # 일부 응답은 가격이 없는 UUID 목록만 제공할 수 있어 이를 보조 처리한다.
        offer_ids = layout.get("SingleItemOffers") or []
        offers = [{"OfferID": offer_id, "Cost": {}} for offer_id in offer_ids]

    # 오늘의 상점과 야시장에 같은 스킨이 있을 때 에셋 API를 중복 호출하지 않는다.
    asset_cache: dict[str, dict[str, Any]] = {}

    def get_skin_asset(item_id: str) -> dict[str, Any]:
        if item_id not in asset_cache:
            asset_response = request_json(
                f"{VALORANT_API}/weapons/skinlevels/{item_id}?language=ko-KR"
            )
            asset_cache[item_id] = asset_response.get("data") or {}
        return asset_cache[item_id]

    daily_items: list[ShopItem] = []
    for offer in offers:
        offer_id = offer.get("OfferID")
        if not offer_id:
            continue

        # Cost 딕셔너리에서 VP UUID에 해당하는 숫자를 가격으로 사용한다.
        cost = offer.get("Cost") or {}
        price = int(cost.get(VP_CURRENCY_ID) or next(iter(cost.values()), 0))

        # 6) UUID만으로는 사람이 읽기 어려우므로 공개 에셋 API에서 한글 이름과
        # 이미지 URL을 찾는다. 이 요청에는 Riot 인증 토큰을 넣지 않는다.
        asset = get_skin_asset(offer_id)
        name = asset.get("displayName") or "알 수 없는 스킨"
        image_url = asset.get("displayIcon")
        # 실제 이미지 파일은 displayIcon이 가리키는 CDN에서 내려받는다.
        image = request_bytes(image_url) if image_url else None
        daily_items.append(ShopItem(name=name, price=price, image=image, skin_level_id=offer_id))

    if not daily_items:
        raise ApiError("상점 응답에서 오늘의 상품을 찾지 못했습니다.")

    # 야시장이 열려 있으면 BonusStore에 할인 상품이 들어온다. 야시장 기간이
    # 아닐 때는 BonusStore 자체가 없으므로 빈 목록을 정상 상태로 취급한다.
    bonus_store = storefront.get("BonusStore") or {}
    night_market_items: list[ShopItem] = []
    for bonus_offer in bonus_store.get("BonusStoreOffers") or []:
        offer = bonus_offer.get("Offer") or {}
        rewards = offer.get("Rewards") or []
        item_id = rewards[0].get("ItemID") if rewards else None
        if not item_id:
            continue

        original_costs = offer.get("Cost") or {}
        discounted_costs = bonus_offer.get("DiscountCosts") or {}
        original_price = int(
            original_costs.get(VP_CURRENCY_ID)
            or next(iter(original_costs.values()), 0)
        )
        discounted_price = int(
            discounted_costs.get(VP_CURRENCY_ID)
            or next(iter(discounted_costs.values()), 0)
        )
        discount_percent = int(bonus_offer.get("DiscountPercent") or 0)

        asset = get_skin_asset(item_id)
        name = asset.get("displayName") or "알 수 없는 스킨"
        image_url = asset.get("displayIcon")
        image = request_bytes(image_url) if image_url else None
        night_market_items.append(
            ShopItem(
                name=name,
                price=discounted_price,
                image=image,
                original_price=original_price,
                discount_percent=discount_percent,
                skin_level_id=item_id,
            )
        )

    # 서버가 초 단위로 준 남은 시간을 UI에서 시간/분으로 다시 표시한다.
    daily_remaining = int(
        layout.get("SingleItemOffersRemainingDurationInSeconds") or 0
    )
    night_market_remaining = int(
        bonus_store.get("BonusStoreRemainingDurationInSeconds") or 0
    )
    return ShopResult(
        daily_items=daily_items,
        daily_remaining_seconds=daily_remaining,
        night_market_items=night_market_items,
        night_market_remaining_seconds=night_market_remaining,
        bundles=fetch_bundles(storefront),
        vp_balance=int(balances[VP_CURRENCY_ID]) if VP_CURRENCY_ID in balances else None,
        rp_balance=int(balances[RP_CURRENCY_ID]) if RP_CURRENCY_ID in balances else None,
        wallet_error=wallet_error,
    )


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
            # 개발 중 확인할 수 있도록 예상 밖 오류의 종류와 코드 위치를 로그에 기록한다.
            exc_type, _, exc_tb = sys.exc_info()
            logging.getLogger('vshop').error(
                'Worker failure: %s\n%s',
                exc_type.__name__ if exc_type else 'unknown',
                ''.join(traceback.format_tb(exc_tb)),
            )
            self.signals.failed.emit("예상하지 못한 오류가 발생했습니다.")


# 이 코드 단락은 상점 상품 하나를 카드 형태의 위젯으로 그려 화면에 표시함
class PreviewDialog(QDialog):
    """공개 영상 URL을 Qt Multimedia로 재생하는 업그레이드 상세 창."""

    def __init__(self, item: ShopItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{item.name} · 업그레이드 미리보기")
        self.resize(900, 650)
        self.setMinimumSize(640, 480)
        self.item = item
        self.closed = False
        self.worker: Worker | None = None
        self.video_worker: Worker | None = None
        self.video_file: QTemporaryFile | None = None
        layout = QVBoxLayout(self)
        heading = QLabel(item.name)
        heading.setObjectName("pageTitle")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.levels = QComboBox()
        self.levels.setEnabled(False)
        self.levels.currentIndexChanged.connect(self.select_level)
        layout.addWidget(self.levels)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(240)
        layout.addWidget(self.video, 1)
        self.status = QLabel("업그레이드 정보를 가져오는 중…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        layout.addWidget(self.seek)
        controls = QHBoxLayout()
        self.play_button = QPushButton("재생")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_button)
        controls.addStretch()
        controls.addWidget(QLabel("음량"))
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(50)
        volume.setMaximumWidth(130)
        controls.addWidget(volume)
        self.retry_button = QPushButton("다시 불러오기")
        self.retry_button.clicked.connect(self.load_levels)
        controls.addWidget(self.retry_button)
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        controls.addWidget(close)
        layout.addLayout(controls)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda duration: self.seek.setRange(0, duration))
        self.player.positionChanged.connect(self.update_position)
        self.player.seekableChanged.connect(self.seek.setEnabled)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(self.media_error)
        self.finished.connect(self.cleanup)
        self.load_levels()

    def load_levels(self) -> None:
        if self.worker or self.video_worker or self.closed:
            return
        self.player.stop()
        self.levels.setEnabled(False)
        self.play_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.status.setText("업그레이드 정보를 가져오는 중…")
        level_id = self.item.skin_level_id or ""
        self.worker = Worker(lambda: fetch_preview_levels(level_id))
        self.worker.signals.finished.connect(self.levels_loaded)
        self.worker.signals.failed.connect(self.load_failed)
        QThreadPool.globalInstance().start(self.worker)

    @Slot(object)
    def levels_loaded(self, levels: list[PreviewLevel]) -> None:
        self.worker = None
        if self.closed:
            return
        self.retry_button.setEnabled(True)
        self.levels.blockSignals(True)
        self.levels.clear()
        for level in levels:
            suffix = "" if level.video_url else " (영상 없음)"
            self.levels.addItem(level.name + suffix, level.video_url)
        self.levels.blockSignals(False)
        self.levels.setEnabled(bool(levels))
        if not levels:
            self.status.setText("등록된 업그레이드 단계가 없습니다.")
            return
        self.select_level(self.levels.currentIndex())

    @Slot(str)
    def load_failed(self, message: str) -> None:
        self.worker = None
        if not self.closed:
            self.status.setText(message)
            self.retry_button.setEnabled(True)

    @Slot(int)
    def select_level(self, index: int) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.clear_video_file()
        self.seek.setValue(0)
        self.seek.setEnabled(False)
        url = self.levels.itemData(index) if index >= 0 else None
        self.play_button.setEnabled(bool(url))
        self.video.setVisible(bool(url))
        if not url:
            self.status.setText("이 단계에는 미리보기 영상이 없습니다.")
            return
        self.status.setText("재생을 누르면 이 단계의 영상을 불러옵니다.")

    def clear_video_file(self) -> None:
        if self.video_file:
            self.video_file.remove()
            self.video_file = None

    def toggle_play(self) -> None:
        if self.player.source().isEmpty():
            url = self.levels.currentData()
            if not url or self.video_worker:
                return
            self.status.setText("미리보기 영상을 다운로드하는 중…")
            self.levels.setEnabled(False)
            self.play_button.setEnabled(False)
            self.retry_button.setEnabled(False)
            self.video_worker = Worker(lambda: fetch_preview_video(url))
            self.video_worker.signals.finished.connect(self.video_loaded)
            self.video_worker.signals.failed.connect(self.video_failed)
            QThreadPool.globalInstance().start(self.video_worker)
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    @Slot(object)
    def video_loaded(self, data: bytes) -> None:
        self.video_worker = None
        if self.closed:
            return
        temporary = QTemporaryFile(self)
        if not temporary.open() or temporary.write(data) != len(data):
            temporary.remove()
            self.video_failed("영상 임시 파일을 만들지 못했습니다. 디스크 공간을 확인해 주세요.")
            return
        temporary.close()
        self.video_file = temporary
        self.levels.setEnabled(True)
        self.play_button.setEnabled(True)
        self.retry_button.setEnabled(True)
        self.player.setSource(QUrl.fromLocalFile(temporary.fileName()))
        self.player.play()

    @Slot(str)
    def video_failed(self, message: str) -> None:
        self.video_worker = None
        if not self.closed:
            self.status.setText(message)
            self.levels.setEnabled(True)
            self.play_button.setEnabled(True)
            self.retry_button.setEnabled(True)

    def update_position(self, position: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

    def update_play_button(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_button.setText("일시정지" if state == QMediaPlayer.PlaybackState.PlayingState else "재생")

    def media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self.closed or self.player.source().isEmpty():
            return
        if status in (QMediaPlayer.MediaStatus.LoadingMedia, QMediaPlayer.MediaStatus.BufferingMedia):
            self.status.setText("영상을 불러오는 중…")
        elif status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self.status.setText("재생·일시정지 버튼으로 영상을 조작할 수 있습니다.")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.status.setText("재생이 끝났습니다. 재생 버튼을 누르면 다시 볼 수 있습니다.")

    def media_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if not self.closed and error != QMediaPlayer.Error.NoError:
            self.status.setText("영상을 재생하지 못했습니다. 연결을 확인한 뒤 다시 불러오세요.")
            self.status.setToolTip(message)
            self.play_button.setEnabled(False)

    def cleanup(self, _result: int) -> None:
        # 창 닫기/ESC 모두 재생과 소리를 중지한다. 늦게 도착한 조회 결과는 무시한다.
        self.closed = True
        self.player.stop()
        self.player.setSource(QUrl())
        self.clear_video_file()


class ShopCard(QFrame):
    """스킨 이미지, 이름, VP 가격과 선택적인 야시장 할인을 보여준다."""

    def __init__(self, item: ShopItem) -> None:
        super().__init__()
        self.item = item
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

        if item.original_price and item.price is not None and item.original_price > item.price:
            price_text = f"{item.original_price:,} VP  →  {item.price:,} VP"
        else:
            price_text = f"{item.price:,} VP" if item.price is not None else "가격 정보 없음"

        price_label = QLabel(price_text)
        price_label.setObjectName("price")
        price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(image_label, 1)
        layout.addWidget(name_label)
        if item.discount_percent:
            discount_label = QLabel(f"{item.discount_percent}% 할인")
            discount_label.setObjectName("discount")
            discount_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(discount_label)
        layout.addWidget(price_label)
        if item.detail:
            detail = QLabel(item.detail)
            detail.setWordWrap(True)
            detail.setObjectName("subtitle")
            detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(detail)
        if item.remaining_seconds is not None:
            remaining = QLabel(format_remaining(item.remaining_seconds, "판매 종료").removeprefix(" · "))
            remaining.setWordWrap(True)
            remaining.setObjectName("subtitle")
            remaining.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(remaining)
        if item.skin_level_id:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            preview = QPushButton("업그레이드 미리보기")
            preview.clicked.connect(self.open_preview)
            layout.addWidget(preview)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.item.skin_level_id:
            self.open_preview()
        super().mouseReleaseEvent(event)

    def open_preview(self) -> None:
        dialog = PreviewDialog(self.item, self.window())
        dialog.exec()
        dialog.deleteLater()


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
        subtitle = QLabel("내 PC에서 확인하는 발로란트 상점")
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
        self.web_view.loadFinished.connect(self.on_login_load_finished)
        self.web_page.renderProcessTerminated.connect(self.on_renderer_terminated)

        layout.addLayout(top)
        layout.addWidget(self.web_view, 1)
        return page

    @Slot(bool)
    def on_login_load_finished(self, ok: bool) -> None:
        if '--self-test' in sys.argv:
            return
        if (not ok and not self.auth_handled
                and self.pages.currentWidget() == self.login_page):
            logging.getLogger('vshop').warning('Login page load failed')
            QMessageBox.warning(
                self, '로그인 페이지 오류',
                '로그인 페이지를 불러오지 못했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요. '
                '빈 화면이 계속되면 앱을 닫고 Run-Compatibility.cmd로 실행해 주세요.',
            )

    def on_renderer_terminated(self, status: Any, exit_code: int) -> None:
        logging.getLogger('vshop').error('WebEngine stopped: status=%s code=%s',
                                       status, exit_code)
        if '--self-test' in sys.argv:
            return
        if self.pages.currentWidget() == self.login_page:
            QMessageBox.warning(
                self, '로그인 화면 종료',
                '내장 브라우저가 종료되었습니다. 앱을 닫고 Run-Compatibility.cmd로 다시 실행해 주세요.',
            )

    # 이 코드 단락은 상점 정보를 가져오는 동안 사용자에게 잠시 기다리는 화면을 보여줌
    def build_loading_page(self) -> QWidget:
        """백그라운드 상점 조회 중 표시할 화면을 만든다."""

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label = QLabel("상점·야시장·세트상품과 보유 VP/RP를 가져오는 중…")
        self.loading_label.setObjectName("pageTitle")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("로그인 토큰은 메모리에서만 사용됩니다.")
        detail.setObjectName("subtitle")
        layout.addWidget(self.loading_label)
        layout.addWidget(detail)
        return page

    # 이 코드 단락은 상점 상품 목록과 새로고침, 로그아웃 버튼을 담은 화면을 구성함
    def build_shop_page(self) -> QWidget:
        """세 가지 상점 탭과 공통 잔액 영역을 만든다."""

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        top = QHBoxLayout()
        heading = QLabel("VALORANT 상점")
        heading.setObjectName("titleSmall")

        refresh = QPushButton("새로고침")
        refresh.clicked.connect(self.refresh_shop)
        logout = QPushButton("로그아웃")
        logout.clicked.connect(self.logout)
        top.addWidget(heading)
        top.addStretch()
        top.addWidget(refresh)
        top.addWidget(logout)

        # 잔액 영역은 탭 바깥에 있어 어느 상점 탭에서도 계속 보인다.
        wallet_row = QHBoxLayout()
        self.vp_label = QLabel("보유 VP: —")
        self.rp_label = QLabel("보유 RP: —")
        for label in (self.vp_label, self.rp_label):
            label.setObjectName("wallet")
            wallet_row.addWidget(label)
        wallet_row.addStretch()
        self.wallet_meta = QLabel("")
        self.wallet_meta.setObjectName("subtitle")
        wallet_row.addWidget(self.wallet_meta)

        # 오늘의 상점 탭
        daily_page = QWidget()
        daily_layout = QVBoxLayout(daily_page)
        daily_layout.setContentsMargins(0, 12, 0, 0)
        self.shop_meta = QLabel("")
        self.shop_meta.setObjectName("subtitle")
        daily_scroll = QScrollArea()
        daily_scroll.setWidgetResizable(True)
        daily_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setSpacing(16)
        daily_scroll.setWidget(self.cards_host)
        daily_layout.addWidget(self.shop_meta)
        daily_layout.addWidget(daily_scroll, 1)

        # 야시장 탭. 야시장이 열리지 않은 기간에도 탭은 유지하고 안내 문구를 보인다.
        night_market_page = QWidget()
        night_market_layout = QVBoxLayout(night_market_page)
        night_market_layout.setContentsMargins(0, 12, 0, 0)
        self.night_market_meta = QLabel("")
        self.night_market_meta.setObjectName("subtitle")
        self.night_market_empty = QLabel(
            "현재 야시장이 열려 있지 않습니다.\n야시장 이벤트가 시작되면 할인 상품이 여기에 표시됩니다."
        )
        self.night_market_empty.setObjectName("emptyState")
        self.night_market_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.night_market_empty.setWordWrap(True)
        night_market_scroll = QScrollArea()
        night_market_scroll.setWidgetResizable(True)
        night_market_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.night_market_cards_host = QWidget()
        self.night_market_grid = QGridLayout(self.night_market_cards_host)
        self.night_market_grid.setSpacing(16)
        night_market_scroll.setWidget(self.night_market_cards_host)
        night_market_layout.addWidget(self.night_market_meta)
        night_market_layout.addWidget(self.night_market_empty)
        night_market_layout.addWidget(night_market_scroll, 1)

        self.shop_tabs = QTabWidget()
        self.shop_tabs.addTab(daily_page, "오늘의 상점")
        self.shop_tabs.addTab(night_market_page, "야시장")

        bundles_page = QWidget()
        bundles_layout = QVBoxLayout(bundles_page)
        bundles_layout.setContentsMargins(0, 12, 0, 0)
        self.bundles_meta = QLabel("")
        self.bundles_meta.setObjectName("subtitle")
        self.bundles_empty = QLabel("현재 판매 중인 세트상품이 없습니다.")
        self.bundles_empty.setObjectName("emptyState")
        self.bundles_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bundles_scroll = QScrollArea()
        bundles_scroll.setWidgetResizable(True)
        bundles_scroll.setFrameShape(QFrame.Shape.NoFrame)
        bundles_host = QWidget()
        self.bundles_grid = QGridLayout(bundles_host)
        self.bundles_grid.setSpacing(16)
        bundles_scroll.setWidget(bundles_host)
        bundles_layout.addWidget(self.bundles_meta)
        bundles_layout.addWidget(self.bundles_empty)
        bundles_layout.addWidget(bundles_scroll, 1)
        self.shop_tabs.addTab(bundles_page, "세트상품")

        outer.addLayout(top)
        outer.addLayout(wallet_row)
        outer.addWidget(self.shop_tabs, 1)
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
            QLabel#wallet {
                background: #181e2b; border-radius: 8px; padding: 10px 16px;
                color: #f6d365; font-size: 16px; font-weight: 700;
            }
            QLabel#discount {
                color: #ff6572; font-size: 15px; font-weight: 700;
            }
            QLabel#emptyState {
                padding: 28px; color: #aeb6c6; font-size: 16px;
            }
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
            QTabWidget::pane { border: 0; }
            QTabBar::tab {
                background: #181e2b; color: #aeb6c6;
                padding: 11px 24px; margin-right: 4px;
                border-top-left-radius: 7px; border-top-right-radius: 7px;
            }
            QTabBar::tab:selected { background: #ff4655; color: white; }
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
        self.loading_label.setText("상점·야시장·세트상품과 보유 VP/RP를 가져오는 중…")
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
        """세 가지 상점 탭과 공통 잔액을 조회 결과로 갱신한다."""

        def clear_grid(grid: QGridLayout) -> None:
            while grid.count():
                layout_item = grid.takeAt(0)
                widget = layout_item.widget()
                if widget:
                    widget.deleteLater()

        # 새로고침할 때 이전 카드 위젯이 겹치지 않도록 각 탭을 비운다.
        clear_grid(self.cards_grid)
        clear_grid(self.night_market_grid)
        clear_grid(self.bundles_grid)

        for index, shop_item in enumerate(result.daily_items):
            self.cards_grid.addWidget(ShopCard(shop_item), index // 2, index % 2)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        daily_remaining = format_remaining(
            result.daily_remaining_seconds, "교체"
        )
        self.shop_meta.setText(f"{now} 기준{daily_remaining}")

        if result.night_market_items:
            self.night_market_empty.hide()
            for index, shop_item in enumerate(result.night_market_items):
                self.night_market_grid.addWidget(
                    ShopCard(shop_item), index // 2, index % 2
                )
            night_remaining = format_remaining(
                result.night_market_remaining_seconds, "종료"
            )
            self.night_market_meta.setText(f"{now} 기준{night_remaining}")
        else:
            self.night_market_empty.show()
            self.night_market_meta.setText(f"{now} 기준 · 현재 야시장 기간이 아닙니다")

        for index, bundle in enumerate(result.bundles):
            self.bundles_grid.addWidget(ShopCard(bundle), index // 2, index % 2)
        self.bundles_empty.setVisible(not result.bundles)
        self.bundles_meta.setText(f"{now} 기준 · 판매 중인 세트 {len(result.bundles)}개")
        for label, currency, value in (
            (self.vp_label, "VP", result.vp_balance),
            (self.rp_label, "RP", result.rp_balance),
        ):
            label.setText(f"보유 {currency}: {value:,}" if value is not None else f"보유 {currency}: 조회 불가")
            label.setToolTip(result.wallet_error or ("" if value is not None else "서버 응답에 잔액이 없습니다."))
        self.wallet_meta.setText(f"{now} 조회")

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
        self.vp_label.setText("보유 VP: —")
        self.rp_label.setText("보유 RP: —")
        self.wallet_meta.clear()
        # 다른 계정으로 로그인하다 실패했을 때 이전 계정 상품이 남지 않게 한다.
        for grid in (self.cards_grid, self.night_market_grid, self.bundles_grid):
            while grid.count():
                item = grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        self.pages.setCurrentWidget(self.home_page)


# 이 코드 단락은 Qt 애플리케이션을 실행하고 메인 창을 표시함
def main() -> int:
    """Qt 이벤트 루프를 시작하고 메인 창을 표시한다."""

    if "--check-https" in sys.argv:
        return check_https()

    app = QApplication(sys.argv)
    app.setApplicationName("VShop Personal")
    window = MainWindow()
    window.show()
    return app.exec()


def check_https() -> int:
    """배포 EXE 자체로 TLS 로딩과 공개 API를 검사한다. 계정 토큰은 사용하지 않는다."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--check-https", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        import ssl
        report["openssl"] = ssl.OPENSSL_VERSION
        report["https_handler"] = hasattr(urllib.request, "HTTPSHandler")
        if not report["https_handler"]:
            raise RuntimeError("HTTPSHandler is unavailable")
        context = ssl.create_default_context()
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        response = request_json(f"{VALORANT_API}/version")
        if response.get("status") != 200 or not response.get("data", {}).get("riotClientVersion"):
            raise RuntimeError("Public version API returned an unexpected response")
        report["public_api_status"] = response["status"]
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
