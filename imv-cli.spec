# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("mcp")
a = Analysis(["scripts/imv_cli_entry.py"], pathex=["."], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="imv", console=True)
