"""Register this server in the official MCP config, preserving other entries and permissions."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

SERVER_ID = "computer-use-windows"


def configure(config_path, skill_path, python_path):
    config_path, skill_path, python_path = (Path(p).resolve() for p in (config_path, skill_path, python_path))
    server_script = skill_path / "scripts" / "mcp_server.py"
    if not python_path.is_file() or not server_script.is_file():
        raise ValueError("Runtime Python or MCP server script is missing.")
    original = config_path.read_bytes() if config_path.exists() else None
    data = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError("Unexpected MCP config structure; no configuration was written.")
    servers = data.setdefault("mcpServers", {})
    existing = servers.get(SERVER_ID)
    if existing:
        old_args = existing.get("args", []) if isinstance(existing, dict) else []
        if not any(isinstance(a, str) and Path(a).resolve() == server_script for a in old_args):
            raise ValueError("A different server already owns this server ID; refusing to overwrite it.")
    entry = {"command": str(python_path),
             "args": [str(server_script), "--startup-report", str(skill_path / "runtime" / "mcp-startup.json")],
             "cwd": str(skill_path)}
    if existing == entry:
        return {"ok": True, "changed": False, "server_id": SERVER_ID, "config": str(config_path)}
    servers[SERVER_ID] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if original is not None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = config_path.with_name(f"{config_path.name}.computer-use-backup-{stamp}-{uuid.uuid4().hex[:8]}")
        with backup.open("xb") as stream:
            stream.write(original)
    pending = config_path.with_name(f".{config_path.name}.{uuid.uuid4().hex}.tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if (config_path.read_bytes() if config_path.exists() else None) != original:
        raise RuntimeError("MCP config changed during preparation; refusing to overwrite it.")
    os.replace(pending, config_path)
    return {"ok": True, "changed": True, "server_id": SERVER_ID, "config": str(config_path),
            "backup": str(backup) if backup else None,
            "note": "No permission policy, allowlist, sandbox or other server was changed."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--skill", required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    print(json.dumps(configure(args.config, args.skill, args.python), ensure_ascii=True, indent=2))
