import json
import os
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
