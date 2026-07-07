from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from imv.probe import run_probes
from imv.store import VaultStore

from .config_merge import merge_claude_config


def resolve_claude_config_targets() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    localappdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    targets = [appdata / "Claude" / "claude_desktop_config.json"]
    packages = localappdata / "Packages"
    matches: list[Path] = []
    if packages.is_dir():
        for package in packages.iterdir():
            if not package.is_dir():
                continue
            name = package.name
            if (name.startswith("Claude_") or name.startswith("AnthropicClaude")
                    or name.startswith("Anthropic.ClaudeDesktop")):
                matches.append(package)
    for package in sorted(matches, key=lambda path: path.name.casefold()):
        targets.append(package / "LocalCache" / "Roaming" / "Claude"
                       / "claude_desktop_config.json")
    return targets


def _server_waits(server_exe: Path, vault: Path, seconds: float = 5.0) -> None:
    env = os.environ.copy()
    env["IMV_VAULT"] = str(vault)
    process = subprocess.Popen(
        [str(server_exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                error = process.stderr.read().decode(errors="replace")
                raise RuntimeError(f"imv-server exited during startup: {error}")
            time.sleep(0.1)
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def configure_and_diagnose(server_exe: Path, vault: Path, report: Path | None = None) -> None:
    lines: list[str] = []

    def emit(message: str) -> None:
        lines.append(message)
        print(message)

    vault.mkdir(parents=True, exist_ok=True)
    targets = resolve_claude_config_targets()
    if len(targets) == 1:
        emit("msix_not_detected")
    for target in targets:
        result = merge_claude_config(target, server_exe, vault, emit=emit)
        emit(f"config_written path={target} mode={result.mode}")
    _server_waits(server_exe, vault)
    emit("Server startup: OK (waited 5 seconds)")
    store = VaultStore(vault)
    try:
        findings = run_probes(store)
        errors = [item for item in findings if item.severity == "error"]
        warns = [item for item in findings if item.severity == "warn"]
        emit(f"OK: {len(store.list(limit=None))} memories checked")
        emit(f"WARN: {len(warns)}")
        emit(f"ERROR: {len(errors)}")
        if errors:
            raise RuntimeError("doctor found errors")
    finally:
        store.close()
    emit("Restart Claude Desktop completely to load the MCP server.")
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
