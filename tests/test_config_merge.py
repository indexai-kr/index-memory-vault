import json
from datetime import datetime

import pytest

from imv.installer.config_merge import InvalidClaudeConfig, merge_claude_config
from imv.installer.windows import resolve_claude_config_targets


def _set_roots(monkeypatch, tmp_path):
    appdata = tmp_path / "Roaming"
    localappdata = tmp_path / "Local"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    return appdata, localappdata


def test_t1_claude_msix_package_is_included(monkeypatch, tmp_path):
    appdata, localappdata = _set_roots(monkeypatch, tmp_path)
    package = localappdata / "Packages" / "Claude_abc123"
    package.mkdir(parents=True)
    assert resolve_claude_config_targets() == [
        appdata / "Claude" / "claude_desktop_config.json",
        package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json",
    ]


def test_t2_no_package_returns_legacy_only(monkeypatch, tmp_path):
    appdata, _ = _set_roots(monkeypatch, tmp_path)
    assert resolve_claude_config_targets() == [
        appdata / "Claude" / "claude_desktop_config.json"
    ]


def test_t3_multiple_msix_packages_are_all_included(monkeypatch, tmp_path):
    appdata, localappdata = _set_roots(monkeypatch, tmp_path)
    packages = localappdata / "Packages"
    claude = packages / "Claude_x"
    anthropic = packages / "Anthropic.ClaudeDesktop_y"
    ignored = packages / "OtherClaude_z"
    for package in (claude, anthropic, ignored):
        package.mkdir(parents=True)
    targets = resolve_claude_config_targets()
    assert len(targets) == 3
    assert targets[0] == appdata / "Claude" / "claude_desktop_config.json"
    assert {target.parents[3] for target in targets[1:]} == {claude, anthropic}


def test_missing_config_is_created(tmp_path):
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    result = merge_claude_config(config, tmp_path / "imv-server.exe", tmp_path / "vault")
    assert result.mode == "created"
    data = json.loads(config.read_text("utf-8"))
    assert data["mcpServers"]["memory-vault"]["command"].endswith("imv-server.exe")


def test_t4_existing_msix_config_preserves_other_keys(tmp_path):
    config = tmp_path / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir(parents=True)
    original = {"theme": "dark", "mcpServers": {"github": {"command": "gh-mcp"}}}
    config.write_text(json.dumps(original), encoding="utf-8")
    result = merge_claude_config(config, tmp_path / "imv-server.exe", tmp_path / "vault")
    data = json.loads(config.read_text("utf-8"))
    assert result.mode == "merged"
    assert data["theme"] == "dark"
    assert data["mcpServers"]["github"] == original["mcpServers"]["github"]
    assert "memory-vault" in data["mcpServers"]


def test_t5_broken_json_is_backed_up_recreated_and_logged(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    broken = '{"mcpServers": '
    config.write_text(broken, encoding="utf-8")
    logs = []
    result = merge_claude_config(
        config, tmp_path / "imv-server.exe", tmp_path / "vault",
        now=datetime(2026, 7, 6, 6, 29, 30), emit=logs.append,
    )
    backup = tmp_path / "claude_desktop_config.json.bak-20260706-062930"
    assert result.mode == "backup_recreated"
    assert result.backup == backup
    assert backup.read_text("utf-8") == broken
    assert "memory-vault" in json.loads(config.read_text("utf-8"))["mcpServers"]
    assert logs == [f"parse_failed_backup path={config} backup={backup}"]


def test_t6_written_config_has_no_utf8_bom(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    merge_claude_config(config, tmp_path / "imv-server.exe", tmp_path / "vault")
    assert config.read_bytes()[:3] != b"\xef\xbb\xbf"


def test_non_object_root_is_rejected_without_rewrite(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    config.write_text("[]", encoding="utf-8")
    with pytest.raises(InvalidClaudeConfig, match="root"):
        merge_claude_config(config, tmp_path / "imv-server.exe", tmp_path / "vault")
    assert config.read_text("utf-8") == "[]"


def test_non_object_mcp_servers_is_rejected_without_rewrite(tmp_path):
    config = tmp_path / "claude_desktop_config.json"
    original = '{"mcpServers": []}'
    config.write_text(original, encoding="utf-8")
    with pytest.raises(InvalidClaudeConfig, match="mcpServers"):
        merge_claude_config(config, tmp_path / "imv-server.exe", tmp_path / "vault")
    assert config.read_text("utf-8") == original
