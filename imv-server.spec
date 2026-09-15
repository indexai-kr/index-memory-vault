# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

# mcp.cli is the `mcp` command-line tool; it imports typer at module scope and
# is not reachable from this server's entry point. Collecting it would drag a
# build-only dependency into a server binary that never calls it.
datas = collect_data_files("mcp")
binaries = collect_dynamic_libs("mcp")
hiddenimports = collect_submodules(
    "mcp", filter=lambda name: not name.startswith("mcp.cli")
)

a = Analysis(["scripts/imv_server_entry.py"], pathex=["."], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="imv-server", console=False)
