# Windows x64 folder bundle. Keep this file in source control.
# PyInstaller's PySide6 hooks collect Qt plugins, WebEngineProcess, resources,
# locales, and the Python interpreter. Do not copy a developer's .venv.
from pathlib import Path
import os
import sys
import _ssl

# A third-party directory on the build PC's PATH can supply an incompatible
# DLL with the same name as a Windows DLL (notably icuuc.dll). Resolve only
# Python, package hook paths, and the Windows system directories.
windows = Path(os.environ['SystemRoot'])
os.environ['PATH'] = os.pathsep.join(str(p) for p in (
    Path(sys.executable).parent, Path(sys.base_prefix),
    windows / 'System32', windows,
))

project = Path(SPECPATH)
# Match Python's SSL extension with its own OpenSSL binaries, never a DLL
# with the same filename from another application or System32.
ssl_dir = Path(_ssl.__file__).parent
openssl = [ssl_dir / name for name in ('libssl-3-x64.dll', 'libcrypto-3-x64.dll')]
if not all(path.is_file() for path in openssl):
    raise RuntimeError('Python OpenSSL DLL pair was not found beside _ssl.pyd')
a = Analysis(
    [str(project / 'launcher.py')],
    pathex=[str(project)],
    binaries=[(str(path), '.') for path in openssl],
    datas=[(str(project / 'test-assets' / 'smoke-test.mp4'), 'test-assets')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
# Explicitly retain the validated Python DLL pair after dependency analysis.
ssl_names = {path.name.lower() for path in openssl}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in ssl_names]
a.binaries += [(path.name, str(path), 'BINARY') for path in openssl]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='VShopPersonal',
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False,
    upx=False,
    name='VShopPersonal',
)
