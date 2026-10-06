"""Offline integration checks for the actual bundled UI, browser, TLS and video."""
from pathlib import Path
import json
import platform
import ssl
import sys
import urllib.request

from PySide6.QtCore import QTimer, qVersion
from PySide6.QtWidgets import QApplication
from app import MainWindow, PreviewDialog, PreviewLevel, ShopItem, ShopResult


def run(report_path: Path, software: bool) -> int:
    application = QApplication([sys.argv[0]])
    window = MainWindow()
    checks = {}
    preview = None
    done = False
    context = ssl.create_default_context()
    checks['tls_verification'] = (
        context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        and hasattr(urllib.request, 'HTTPSHandler'))

    # Feed synthetic data through the existing production display methods.
    sample = ShopItem('배포 검사', 1000, None)
    window.on_shop_loaded(ShopResult([sample], 3600, [sample], 7200, [sample], 1234, 56))
    checks['three_shop_tabs'] = window.shop_tabs.count() == 3
    checks['shop_cards'] = all(grid.count() == 1 for grid in (
        window.cards_grid, window.night_market_grid, window.bundles_grid))
    checks['wallet_display'] = '1,234' in window.vp_label.text() and '56' in window.rp_label.text()
    window.logout()
    checks['logout_clears_data'] = all(grid.count() == 0 for grid in (
        window.cards_grid, window.night_market_grid, window.bundles_grid)) and '—' in window.vp_label.text()

    def finish(reason: str) -> None:
        nonlocal done
        if done:
            return
        done = True
        if preview is not None:
            video_path = Path(preview.video_file.fileName()) if preview.video_file else None
            preview.cleanup(0)
            checks['video_cleanup'] = video_path is not None and not video_path.exists()
        ok = all(checks.values()) and checks.get('browser_javascript', False) and checks.get('video_frame', False)
        report = dict(ok=ok, reason=reason, checks=checks,
                      frozen=bool(getattr(sys, 'frozen', False)), python=sys.version.split()[0],
                      qt=qVersion(), openssl=ssl.OPENSSL_VERSION,
                      software_rendering=software, machine=platform.machine())
        try:
            report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        finally:
            application.exit(0 if ok else 1)

    def maybe_finish() -> None:
        if checks.get('browser_javascript') and checks.get('video_frame'):
            QTimer.singleShot(0, lambda: finish('Offline UI, TLS, WebEngine and MP4 decoding completed'))

    def javascript(result) -> None:
        if result == 42:
            checks['browser_javascript'] = True
            maybe_finish()

    def loaded(ok: bool) -> None:
        if ok:
            window.web_page.runJavaScript(
                'document.getElementById("result") ? Number(document.getElementById("result").textContent) : null',
                javascript)

    def video_frame(frame) -> None:
        if frame.isValid():
            checks['video_frame'] = True
            maybe_finish()

    class OfflinePreview(PreviewDialog):
        def load_levels(self) -> None:
            # Avoid requesting the skin catalogue; test the real video-loaded path.
            self.levels_loaded([PreviewLevel('Local test', None)])

    def start() -> None:
        nonlocal preview
        window.web_view.loadFinished.connect(loaded)
        window.pages.setCurrentWidget(window.login_page)
        window.web_view.setHtml('<html><body><p id="result">42</p></body></html>')
        preview = OfflinePreview(sample, window)
        preview.audio.setMuted(True)
        preview.video.videoSink().videoFrameChanged.connect(video_frame)
        preview.player.errorOccurred.connect(lambda *_: finish('MP4 decoder reported an error'))
        preview.video_loaded((Path(__file__).parent / 'test-assets' / 'smoke-test.mp4').read_bytes())

    QTimer.singleShot(0, start)
    QTimer.singleShot(30_000, lambda: finish('Offline test timed out'))
    return application.exec()
