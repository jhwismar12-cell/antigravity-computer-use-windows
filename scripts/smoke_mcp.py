"""Read-only MCP handshake, discovery and desktop-context smoke check."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(server_path):
    params = StdioServerParameters(command=sys.executable, args=[str(server_path)])
    with anyio.fail_after(30):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as client:
                initialized = await client.initialize()
                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                assert {"desktop_diagnose", "desktop_windows", "desktop_observe", "desktop_click"} <= names
                for tool in tools.tools:
                    if tool.name in ("desktop_diagnose", "desktop_windows", "desktop_observe"):
                        assert tool.annotations.readOnlyHint is True
                diagnostic = await client.call_tool("desktop_diagnose", {})
                data = diagnostic.structuredContent or json.loads(diagnostic.content[0].text)
                rejected = await client.call_tool("desktop_key", {"state_id": "nonexistent-smoke-state", "keys": "A"})
                assert rejected.isError
                return {"protocol_ok": True, "server": initialized.serverInfo.name,
                        "tool_count": len(names), "desktop_ready": data.get("desktop_check", {}).get("ok"),
                        "window_count": data.get("window_count"), "runtime": data.get("runtime"),
                        "code": data.get("code"), "input_performed": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=Path(__file__).with_name("mcp_server.py"))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(args.server.resolve())), ensure_ascii=True, indent=2))
