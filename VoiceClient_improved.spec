# -*- mode: python ; coding: utf-8 -*-
# Improved PyInstaller spec for VoiceClient
# Build with: pyinstaller VoiceClient_improved.spec

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect hidden imports for all dependencies
hiddenimports = [
    'sounddevice',
    'numpy',
    'websockets',
    'tkinter',
    'asyncio',
    'wave',
    'json',
    'logging',
    'threading',
    'datetime',
    'pathlib',
    'urllib.request',
    'urllib.error',
    'ctypes',
    'ctypes.wintypes',
    # Sounddevice dependencies
    '_sounddevice',
    'cffi',
    # Websockets dependencies
    'websockets.legacy',
    'websockets.legacy.client',
    'websockets.legacy.server',
    # Asyncio dependencies
    'asyncio.selector_events',
    'asyncio.windows_events',
    'asyncio.proactor_events',
]

# Collect data files
datas = []
if sys.platform == 'win32':
    datas.append(('app.ico', '.'))

a = Analysis(
    ['client.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'PIL',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'scipy',
        'pandas',
        'IPython',
        'jupyter',
        'notebook',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='VoiceClient',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico' if sys.platform == 'win32' else None,
    version='version_info.txt' if sys.platform == 'win32' else None,
    uac_admin=False,  # Don't require admin rights
    uac_uiaccess=False,
)
