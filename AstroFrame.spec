from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('astropy')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/AstroFrame_1024.png', 'assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='AstroFrame', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, console=False,
    icon='assets/AstroFrame.icns',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='AstroFrame')
app = BUNDLE(
    coll,
    name='AstroFrame.app',
    icon='assets/AstroFrame.icns',
    bundle_identifier='com.astroframe.app',
    info_plist={
        'CFBundleName': 'AstroFrame',
        'CFBundleDisplayName': 'AstroFrame',
        'CFBundleShortVersionString': '1.0 RC16',
        'CFBundleVersion': '1',
        'NSHighResolutionCapable': True,
    },
)
