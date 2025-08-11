from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = collect_submodules('qfluentwidgets')
datas = [('assets', 'assets')]  # kompletter assets-Ordner


block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# WICHTIG: One-File: EXE erhält ALLES, KEIN COLLECT!
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='DIM-Creator',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    icon='assets/images/logo/x128.ico',
    runtime_tmpdir=None,   # typisch für onefile-spec
)
