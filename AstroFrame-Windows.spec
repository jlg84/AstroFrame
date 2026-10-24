from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = collect_submodules("astropy")
timezone_data = collect_data_files("tzdata")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=timezone_data + [
        ("assets/AstroFrame_1024.png", "assets"),
        ("data/equipment_catalog_v1.json", "data"),
        ("VERSION", "."),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AstroFrame",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon="assets/AstroFrame.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="AstroFrame",
)


