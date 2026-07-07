from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


class InvalidClaudeConfig(ValueError):
    """Raised when a parsed Claude Desktop config has an invalid structure."""


@dataclass(frozen=True)
class ConfigWriteResult:
    mode: str
    backup: Path | None = None


def memory_vault_entry(server_exe: str | Path, vault: str | Path) -> dict:
    return {
        "command": str(Path(server_exe).resolve()),
        "env": {"IMV_VAULT": str(Path(vault).resolve())},
    }


def merge_claude_config(
    config_path: str | Path,
    server_exe: str | Path,
    vault: str | Path,
    *,
    now: datetime | None = None,
    emit: Callable[[str], None] | None = None,
) -> ConfigWriteResult:
    """Merge only mcpServers.memory-vault, preserving all other keys."""
    config = Path(config_path)
    backup = None
    mode = "created"
    if config.exists():
        try:
            original = config.read_text(encoding="utf-8")
            data = json.loads(original)
            mode = "merged"
        except (json.JSONDecodeError, UnicodeDecodeError):
            stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
            backup = config.with_name(f"{config.name}.bak-{stamp}")
            shutil.copy2(config, backup)
            if emit:
                emit(f"parse_failed_backup path={config} backup={backup}")
            data = {}
            mode = "backup_recreated"
        if not isinstance(data, dict):
            raise InvalidClaudeConfig("Claude config root must be a JSON object")
    else:
        data = {}
        config.parent.mkdir(parents=True, exist_ok=True)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InvalidClaudeConfig("mcpServers must be a JSON object")
    servers["memory-vault"] = memory_vault_entry(server_exe, vault)
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    config.write_bytes(rendered.encode("utf-8"))
    return ConfigWriteResult(mode=mode, backup=backup)
