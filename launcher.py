"""Configure diagnostics and optional software rendering before importing Qt."""
from __future__ import annotations

import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import sys
import tempfile
import traceback


def configure_logging() -> Path | None:
    logger = logging.getLogger('vshop')
    logger.setLevel(logging.INFO)
    for base in (os.environ.get('LOCALAPPDATA'), tempfile.gettempdir()):
        if not base:
            continue
        try:
            path = Path(base) / 'VShopPersonal' / 'logs' / 'app.log'
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2,
                                          encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            logger.addHandler(handler)
            return path
        except OSError:
            continue
    logger.addHandler(logging.NullHandler())
    return None


def software_rendering() -> bool:
    enabled = '--software-rendering' in sys.argv
    if enabled:
        # Keep Chromium's sandbox enabled. Disable only GPU acceleration.
        flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
        os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = flags + ' --disable-gpu'
        os.environ['QT_OPENGL'] = 'software'
        os.environ['QT_QUICK_BACKEND'] = 'software'
    return enabled


def self_test(report_path: Path, software: bool) -> int:
    from deployment_checks import run
    return run(report_path, software)


def main() -> int:
    log_path = configure_logging()
    logger = logging.getLogger('vshop')
    software = software_rendering()
    logger.info('Startup: OS=%s machine=%s Python=%s frozen=%s software=%s',
                platform.version(), platform.machine(), sys.version.split()[0],
                bool(getattr(sys, 'frozen', False)), software)
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        if software:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
        if '--self-test' in sys.argv:
            index = sys.argv.index('--self-test')
            return self_test(Path(sys.argv[index + 1]), software)
        from app import main as run_app
        # Application options must not leak into Chromium's arguments.
        sys.argv = [arg for arg in sys.argv if arg != '--software-rendering']
        return run_app()
    except Exception as exc:
        # Exception values / local variables can contain tokens; omit both.
        logger.error('Startup failure: %s\n%s', type(exc).__name__,
                     ''.join(traceback.format_tb(exc.__traceback__)))
        if '--self-test' not in sys.argv and '--check-https' not in sys.argv:
            message = ('프로그램을 시작하지 못했습니다. ZIP 전체를 압축 해제했는지 확인하고, '
                       '호환 모드로 다시 실행해 주세요.\n\n오류 종류: ' + type(exc).__name__)
            if log_path:
                message += '\n로그: ' + str(log_path)
            ctypes.windll.user32.MessageBoxW(None, message, 'VShop Personal', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
