# Stdio requires the console bootloader; Electron windowsHide prevents a console.
from pathlib import Path
root = Path(SPECPATH)
asset_names = (
    'manifest.json', 'crew001_ecg.json', 'crew002_ecg.json', 'crew001_bvp.json',
    'ATTRIBUTION.txt', 'QUICKSTART-EN.txt', 'QUICKSTART-KO.txt', 'RUNTIME-NOTICES.txt',
    'PYTHON-LICENSE.txt', 'TCL-LICENSE.txt', 'TK-LICENSE.txt', 'PYINSTALLER-LICENSE.txt',
    'winscat-reference-manifest.json', 'signal-review-v2.json',
    'assistant-protocols-v1.json', 'crew-visuals-v1.json',
)
datas = [(str(root/'assets'/'desktop'/name), 'assets/desktop') for name in asset_names]
datas += [(str(root/'assets'/'presentation'/name), 'assets/presentation')
          for name in ('index.html', 'app.js', 'app.css', 'THIRD-PARTY-NOTICES.txt', 'manifest.json')]
a = Analysis([str(root/'run_webhost.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'numpy', 'scipy', 'sklearn', 'matplotlib', 'pandas', 'PIL', 'pytest',
              'pip', 'setuptools', 'PyInstaller', 'IPython', 'jupyter', 'torch', 'tensorflow'],
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='BioTwinHost', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='BioTwinHost')
