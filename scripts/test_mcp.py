"""MCP safety and config tests. No live app input is performed."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import desktop
import configure_mcp
import mcp_server as server


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(server, "_root", self.root).start()
        patch.object(server, "_states", {}).start()

    def test_screenshot_returned_inline_with_opaque_state_id(self):
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=")
        image = self.root / "sample.png"
        image.write_bytes(png)
        state = self.root / "opaque-id.json"
        result = server.response({"ok": True, "state": str(state), "screenshot": str(image)})
        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["state_id"], "opaque-id")
        self.assertNotIn("state", result.structuredContent)
        self.assertEqual(base64.b64decode(result.content[1].data), png)
        self.assertEqual(server._states["opaque-id"], str(state))

    @patch.object(server.desktop, "perform")
    @patch.object(server.desktop, "Win32")
    def test_unknown_state_cannot_be_used_as_arbitrary_file_path(self, native, perform):
        result = server.dispatch("key", state_id="C:\\unrelated\\file.json", keys="A")
        self.assertTrue(result.isError)
        self.assertIn("Unknown state_id", result.structuredContent["error"])
        perform.assert_not_called()

    @patch.object(server.desktop, "perform")
    @patch.object(server.desktop, "Win32")
    def test_isolated_desktop_rejected_before_any_window_or_input_action(self, native, perform):
        native.return_value.desktop_ready.side_effect = desktop.DesktopContextMismatch("exebox-test", "Default")
        result = server.dispatch("focus", hwnd=42)
        self.assertTrue(result.isError)
        self.assertEqual(result.structuredContent["code"], "DESKTOP_CONTEXT_MISMATCH")
        native.return_value.windows.assert_not_called()
        native.return_value.focus.assert_not_called()
        perform.assert_not_called()

    @patch.object(server.desktop, "perform")
    @patch.object(server.desktop, "Win32")
    def test_stop_blocks_new_input(self, native, perform):
        self.assertTrue(server.desktop_stop().structuredContent["stopped"])
        self.assertTrue(server.dispatch("key", state_id="anything", keys="A").isError)
        perform.assert_not_called()

    def test_native_desktop_guard_detects_mismatch_and_closes_only_input_handle(self):
        api = desktop.Win32.__new__(desktop.Win32)
        api.u, api.k = MagicMock(), MagicMock()
        api.u.OpenInputDesktop.return_value = 10
        api.u.GetThreadDesktop.return_value = 20

        def name(handle, index, buffer, size, needed):
            buffer.value = "Default" if handle == 10 else "exebox-test"
            return 1

        api.u.GetUserObjectInformationW.side_effect = name
        with self.assertRaises(desktop.DesktopContextMismatch):
            api.desktop_ready()
        api.u.CloseDesktop.assert_called_once_with(10)

    def prepare_config(self):
        skill = self.root / "skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "mcp_server.py").touch()
        python = self.root / "python.exe"
        python.touch()
        config = self.root / "mcp_config.json"
        return skill, python, config

    def test_config_merge_preserves_other_servers_settings_and_exact_backup(self):
        skill, python, config = self.prepare_config()
        original = {"mcpServers": {"existing": {"command": "native-app", "env": {"KEEP": "value"}}},
                    "otherSetting": {"enabled": True}}
        raw = json.dumps(original).encode("utf-8")
        config.write_bytes(raw)
        result = configure_mcp.configure(config, skill, python)
        changed = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(changed["mcpServers"]["existing"], original["mcpServers"]["existing"])
        self.assertEqual(changed["otherSetting"], original["otherSetting"])
        self.assertEqual(Path(result["backup"]).read_bytes(), raw)
        self.assertNotIn("permissions", changed)
        self.assertFalse(configure_mcp.configure(config, skill, python)["changed"])

    def test_existing_unrelated_server_name_is_not_overwritten(self):
        skill, python, config = self.prepare_config()
        raw = json.dumps({"mcpServers": {"computer-use-windows": {"command": "different"}}})
        config.write_text(raw, encoding="utf-8")
        with self.assertRaises(ValueError):
            configure_mcp.configure(config, skill, python)
        self.assertEqual(config.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
