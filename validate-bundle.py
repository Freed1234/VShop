"""Check the DLLs that caused previous cross-PC failures before shipping."""
from pathlib import Path
import hashlib
import _ssl
import sys

root = Path(sys.argv[1]) / '_internal'
source = Path(_ssl.__file__).parent
for name in ('_ssl.pyd', 'libssl-3-x64.dll', 'libcrypto-3-x64.dll'):
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f'Expected exactly one {name}, got {len(matches)}')
    if hashlib.sha256(matches[0].read_bytes()).digest() != hashlib.sha256((source / name).read_bytes()).digest():
        raise RuntimeError(f'Python library mismatch: {name}')
for pattern in ('Qt6Multimedia.dll', 'Qt6MultimediaWidgets.dll', 'ffmpegmediaplugin.dll',
                'avcodec-*.dll', 'avformat-*.dll', 'avutil-*.dll', 'smoke-test.mp4'):
    if not list(root.rglob(pattern)):
        raise RuntimeError(f'Missing multimedia component: {pattern}')
print('Python OpenSSL hashes and multimedia components verified.')
