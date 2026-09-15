import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from imv.store import VaultStore


def _tool_data(result):
    """Return a FastMCP tool's JSON-compatible response across SDK versions."""
    if result.structuredContent is not None:
        return result.structuredContent.get("result", result.structuredContent)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    raise AssertionError("tool returned neither structured content nor JSON text")


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_save_review_and_verified_search(self):
        with tempfile.TemporaryDirectory() as vault:
            env = os.environ.copy()
            env["IMV_VAULT"] = vault
            env.pop("IMV_KNOWLEDGE_DB", None)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "imv.server"],
                env=env,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )

            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertEqual(
                        tools,
                        {
                            "save_memory",
                            "search_memory",
                            "list_memory",
                            "get_memory",
                            "approve_memory",
                            "reject_memory",
                            "search_chunks",
                            "get_chunk",
                            "imv_status",
                        },
                    )

                    saved = _tool_data(await session.call_tool(
                        "save_memory",
                        {"title": "Database choice", "content": "Use SQLite FTS5", "tags": ["architecture"]},
                    ))
                    memory_id = saved["saved"]["id"]
                    self.assertEqual(saved["saved"]["q_state"], "needs_review")

                    hidden = _tool_data(await session.call_tool(
                        "search_memory", {"query": "SQLite"}
                    ))
                    self.assertEqual(hidden["results"], [])

                    reviewer = VaultStore(vault)
                    try:
                        reviewer.set_state(memory_id, "verified", "human:integration-test")
                    finally:
                        reviewer.db.close()

                    visible = _tool_data(await session.call_tool(
                        "search_memory", {"query": "SQLite"}
                    ))
                    self.assertEqual([item["id"] for item in visible["results"]], [memory_id])

                    locked = _tool_data(await session.call_tool(
                        "reject_memory", {"memory_id": memory_id}
                    ))
                    self.assertIn("human-only", locked["error"])

                    # An unconfigured knowledge base must announce itself as
                    # disconnected, never as "no evidence found".
                    unconfigured = _tool_data(await session.call_tool(
                        "search_chunks", {"query": "SQLite"}
                    ))
                    self.assertFalse(unconfigured["knowledge_connected"])
                    self.assertNotIn("results", unconfigured)
                    self.assertIn("IMV_KNOWLEDGE_DB", unconfigured["error"])

                    by_id = _tool_data(await session.call_tool(
                        "get_chunk", {"chunk_id": 1}
                    ))
                    self.assertFalse(by_id["knowledge_connected"])

                    status = _tool_data(await session.call_tool("imv_status", {}))
                    self.assertEqual(
                        status["memory_vault"]["memories_by_q_state"]["verified"], 1)
                    self.assertFalse(status["memory_vault"]["agent_review_allowed"])
                    self.assertFalse(status["knowledge_base"]["connected"])


class HostileWorkingDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_starts_when_cwd_holds_a_foreign_env_file(self):
        """The cwd belongs to the host that launches us. A .env sitting there
        is not ours: it must not crash startup (non-UTF-8) and must not be
        able to rewrite our settings (FASTMCP_* keys)."""
        with tempfile.TemporaryDirectory() as cwd:
            # UTF-16 with a BOM, exactly like the stray file that broke this.
            (Path(cwd) / ".env").write_bytes(
                "GEMINI_API_KEY=xyz\nFASTMCP_PORT=1\n".encode("utf-16"))
            vault = Path(cwd) / "vault"
            env = os.environ.copy()
            env["IMV_VAULT"] = str(vault)
            env.pop("IMV_KNOWLEDGE_DB", None)
            # cwd is the hostile directory, so imv has to be found some other way.
            env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))

            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "imv.server"],
                env=env,
                cwd=cwd,
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {t.name for t in (await session.list_tools()).tools}
                    self.assertIn("imv_status", tools)


if __name__ == "__main__":
    unittest.main()
