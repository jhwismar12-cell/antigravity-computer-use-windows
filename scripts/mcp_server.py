"""Official stdio MCP transport for the Windows desktop helper. No TCP listener."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import tempfile
import threading
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

import desktop

mcp = FastMCP("computer-use-windows", instructions=(
    "Use desktop_diagnose first, then desktop_windows to choose a real HWND. "
    "Inspect each returned screenshot before one action using its state_id. "
    "User task authorization and the host's MCP permissions apply to every tool. "
    "Never retry input blindly or alter desktop/security boundaries."))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
FOCUS = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
INPUT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
_lock = threading.Lock()
_root_guard = threading.Lock()
_states: dict[str, str] = {}
_root: Path | None = None


def state_root():
    global _root
    with _root_guard:
        if _root is None:
            _root = Path(tempfile.mkdtemp(prefix="antigravity-computer-use-"))
    return _root


def response(result):
    result["driver_version"] = desktop.DRIVER_VERSION
    screenshot = result.get("screenshot")
    if result.get("state"):
        state_id = Path(result["state"]).stem
        _states[state_id] = result.pop("state")
        result["state_id"] = state_id
    content = [TextContent(type="text", text=json.dumps(result, ensure_ascii=True, indent=2))]
    if screenshot and result.get("ok"):
        content.append(ImageContent(type="image", mimeType="image/png",
                                    data=base64.b64encode(Path(screenshot).read_bytes()).decode("ascii")))
    return CallToolResult(content=content, structuredContent=result, isError=not result.get("ok", False))


def dispatch(operation, **kwargs):
    with _lock:
        try:
            api = desktop.Win32()
            if operation == "diagnose":
                return response(desktop.diagnostic_result(api))
            # Reject an exebox/secure/foreign desktop. Never call SetThreadDesktop,
            # SwitchDesktop or a host launcher to get around this check.
            api.desktop_ready()
            if operation == "windows":
                return response(desktop.enumeration_result(api))
            if (state_root() / "STOP").exists():
                raise desktop.DesktopError("Stopped by user. Resume only after an explicit request and reconnection.")
            if operation in ("observe", "focus"):
                hwnd = kwargs["hwnd"]
                # Accept only a current window returned by this server's enumeration.
                if not any(w["hwnd"] == hwnd for w in api.windows()):
                    raise desktop.DesktopError("HWND not returned by windows; enumerate and select a current window.")
                if operation == "focus":
                    api.abort_check()
                    api.focus(hwnd)
                return response(desktop.observe(api, hwnd, state_root()))
            state_id = kwargs.pop("state_id")
            if state_id not in _states:
                raise desktop.DesktopError("Unknown state_id. Observe this window with this MCP connection first.")
            args = argparse.Namespace(command=operation, state=_states[state_id], **kwargs)
            return response(desktop.perform(api, args))
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__,
                      "recovery": "Observe the current state before retrying any input."}
            if isinstance(exc, desktop.DesktopContextMismatch):
                result.update({"code": exc.code, "desktop_context": exc.context,
                               "recovery": "The MCP host also isolated this process. Reconnect through an "
                                           "authorized host integration; do not switch desktops or evade the boundary."})
            return response(result)


@mcp.tool(annotations=READ)
def desktop_diagnose() -> CallToolResult:
    """Read native errors, Python identity and desktop/session context. Never moves or types."""
    return dispatch("diagnose")


@mcp.tool(annotations=READ)
def desktop_windows() -> CallToolResult:
    """List visible windows with real HWND, PID, title and optional executable path."""
    return dispatch("windows")


@mcp.tool(annotations=READ)
def desktop_observe(hwnd: int) -> CallToolResult:
    """Capture the visible window without changing focus. Inspect the image before one action."""
    return dispatch("observe", hwnd=hwnd)


@mcp.tool(annotations=FOCUS)
def desktop_focus(hwnd: int) -> CallToolResult:
    """Activate a selected window, then return a new screenshot and single-use state_id."""
    return dispatch("focus", hwnd=hwnd)


@mcp.tool(annotations=INPUT)
def desktop_click(state_id: str, x: int, y: int, button: Literal["left", "right"] = "left",
                  double: bool = False) -> CallToolResult:
    """Click once (or double-click) in original screenshot pixels, then return a fresh image."""
    return dispatch("click", state_id=state_id, x=x, y=y, button=button, double=double)


@mcp.tool(annotations=INPUT)
def desktop_key(state_id: str, keys: str) -> CallToolResult:
    """Send one named key/chord such as CTRL+S or ENTER, then refresh. Check user authorization."""
    return dispatch("key", state_id=state_id, keys=keys)


@mcp.tool(annotations=INPUT)
def desktop_type(state_id: str, text: str, multiline: bool = False) -> CallToolResult:
    """Type literal Unicode in an observed focused editor. Multiline sends Enter/Tab; use deliberately."""
    return dispatch("type", state_id=state_id, text=text, text_file=None, multiline=multiline)


@mcp.tool(annotations=INPUT)
def desktop_scroll(state_id: str, x: int, y: int, steps: int, horizontal: bool = False) -> CallToolResult:
    """Scroll at an observed point. Positive up/right, negative down/left; at most 20 steps."""
    return dispatch("scroll", state_id=state_id, x=x, y=y, steps=steps, horizontal=horizontal)


@mcp.tool(annotations=INPUT)
def desktop_drag(state_id: str, x: int, y: int, to_x: int, to_y: int, duration: float = 0.5) -> CallToolResult:
    """Drag within the observed window, release the button, then return a fresh screenshot."""
    return dispatch("drag", state_id=state_id, x=x, y=y, to_x=to_x, to_y=to_y, duration=duration)


@mcp.tool(annotations=FOCUS)
def desktop_move(state_id: str, x: int, y: int) -> CallToolResult:
    """Move the pointer to an observed point and return the resulting screenshot."""
    return dispatch("move", state_id=state_id, x=x, y=y)


@mcp.tool(annotations=FOCUS)
def desktop_stop() -> CallToolResult:
    """Stop future inputs for this MCP connection. Reconnect only after the user requests resumption."""
    # Do not take the action lock: a running drag must be able to notice STOP.
    (state_root() / "STOP").touch()
    return response({"ok": True, "stopped": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup-report", help="Optional local desktop-identity log for connection diagnostics")
    args = parser.parse_args()
    if args.startup_report:
        try:
            api = desktop.Win32()
            report = {"runtime": api.runtime_context()}
            try:
                api.desktop_ready()
                report["desktop_ready"] = True
            except Exception as exc:
                report.update({"desktop_ready": False, "error": str(exc)})
            path = Path(args.startup_report).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            desktop.save_json(path, report)
        except Exception as exc:
            import sys
            print(f"Startup diagnostics unavailable: {exc}", file=sys.stderr)
    mcp.run(transport="stdio")
