datas = [('assets', 'assets')]

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'scipy', 'matplotlib', 'tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='DIM-Creator',
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/images/logo/favicon.ico',
    runtime_tmpdir=None,
)
