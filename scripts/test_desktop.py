"""Read-only tests: all input is recorded by a fake driver, never sent to Windows."""
import argparse
import copy
import ctypes
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import desktop as cu


class FakeAPI:
    def __init__(self):
        self.info = {"hwnd": 42, "pid": 7, "class_name": "Editor", "title": "Demo",
                     "rect": [-100, 20, 500, 420], "minimized": False}
        self.fg = 42
        self.virtual = [-1920, 0, 1920, 1080]
        self.occluded = False
        self.events = []

    def abort_check(self):
        pass

    def window(self, hwnd):
        return copy.deepcopy(self.info)

    def geometry(self):
        return self.virtual

    def foreground(self):
        return self.fg

    def top_at(self, x, y):
        return 99 if self.occluded else 42

    def move(self, x, y):
        self.events.append(("move", x, y))

    def mouse(self, flags, data=0):
        self.events.append(("mouse", flags, data))

    def send(self, events):
        self.events.extend(events)


class NativeEnumerationStub:
    """Exercises the actual ctypes callback without enumerating real windows."""
    def __init__(self, handles=(42, 43), success=True, last_error=0, exception=None):
        self.handles, self.success, self.last_error = handles, success, last_error
        self.exception = exception
        self.inside_callback = False

    def __call__(self, callback, parameter):
        for hwnd in self.handles:
            self.inside_callback = True
            try:
                if not callback(hwnd, parameter):
                    return 0
            finally:
                self.inside_callback = False
        cu.C.set_last_error(self.last_error)
        if self.exception:
            raise self.exception
        return 1 if self.success else 0


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "observation.json"
        self.api = FakeAPI()
        self.state = {"schema": 1, "captured_at": time.time(), "window": self.api.window(42),
                      "capture_rect": [-100, 20, 500, 420], "image_size": [600, 400],
                      "virtual_rect": self.api.geometry()}
        self.write_state()

    def write_state(self):
        self.path.write_text(json.dumps(self.state), encoding="utf-8")

    def action(self, command="click", **kwargs):
        return argparse.Namespace(command=command, state=str(self.path), x=110, y=50,
                                  button="left", double=False, **kwargs)

    def test_unicode_and_surrogates_roundtrip_without_clipboard(self):
        text = "Diseño: área, ñ, 東京 🏗"
        events = cu.text_sequence(text)
        encoded = b"".join(e.ki.wScan.to_bytes(2, "little") for e in events[::2])
        self.assertEqual(encoded.decode("utf-16-le"), text)
        self.assertTrue(all(e.ki.wVk == 0 and e.ki.dwFlags == cu.UNICODE for e in events[::2]))
        self.assertTrue(all(e.ki.dwFlags == cu.UNICODE | cu.KEYUP for e in events[1::2]))

    def test_multiline_requires_explicit_option(self):
        with self.assertRaises(cu.DesktopError):
            cu.text_sequence("uno\ndos")
        events = cu.text_sequence("uno\r\ndos\tfin", multiline=True)
        self.assertEqual(sum(e.ki.wVk == 13 and e.ki.dwFlags == 0 for e in events), 1)
        self.assertEqual(sum(e.ki.wVk == 9 and e.ki.dwFlags == 0 for e in events), 1)

    def test_chord_releases_reverse_order_and_handles_extended_keys(self):
        events = cu.key_sequence("CTRL+SHIFT+LEFT")
        self.assertEqual([e.ki.wVk for e in events], [17, 16, 37, 37, 16, 17])
        self.assertEqual(events[2].ki.dwFlags, cu.EXTENDED)
        self.assertEqual(events[3].ki.dwFlags, cu.EXTENDED | cu.KEYUP)

    def test_unknown_duplicate_and_shell_keys_rejected(self):
        for chord in ("CTRL+CTRL", "CTRL+", "WIN+R", "$(anything)"):
            with self.subTest(chord=chord), self.assertRaises(cu.DesktopError):
                cu.key_sequence(chord)

    def test_empty_enumeration_reports_unavailable_without_claiming_a_specific_cause(self):
        self.api.windows = lambda: []
        self.api.last_enumeration = {"enumerated": 12, "visible": 4,
                                     "visible_without_readable_title": 4}
        result = cu.enumeration_result(self.api)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DESKTOP_ENUMERATION_UNAVAILABLE")
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["diagnostics"]["visible_without_readable_title"], 4)
        self.assertEqual(result["diagnostics"]["foreground_hwnd"], 42)
        self.assertNotIn("detected_cause", result)

    def test_missing_executable_path_does_not_block_selectable_window(self):
        self.api.windows = lambda: [{**self.api.info, "exe": None}]
        result = cu.enumeration_result(self.api)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["windows"][0]["exe"])
        self.assertEqual(result["windows"][0]["hwnd"], 42)

    @patch.object(cu, "Win32")
    @patch.object(cu, "parser")
    def test_empty_enumeration_uses_nonzero_exit_status(self, parser, native):
        parser.return_value.parse_args.return_value = argparse.Namespace(command="windows")
        native.return_value.windows.return_value = []
        native.return_value.last_enumeration = {"enumerated": 0}
        native.return_value.foreground.return_value = 0
        with patch("builtins.print"):
            self.assertEqual(cu.main(), 2)

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_callback_only_collects_handles_and_metadata_runs_after_native_return(self):
        native, api = self.native_enumeration()

        def metadata(hwnd):
            self.assertFalse(native.inside_callback)
            cu.C.set_last_error(1234)  # Must not replace the native enumeration error.
            return {**self.api.info, "hwnd": hwnd}

        api.window = metadata
        windows = api.windows()
        self.assertEqual([w["hwnd"] for w in windows], [42, 43])
        self.assertTrue(api.last_enumeration["native_result"])
        self.assertEqual(api.last_enumeration["last_error"], 0)
        self.assertEqual(api.last_enumeration["callback_errors"], [])

    def native_enumeration(self, **kwargs):
        native = NativeEnumerationStub(**kwargs)
        api = cu.Win32.__new__(cu.Win32)
        api.u = SimpleNamespace(EnumWindows=native, IsWindowVisible=lambda hwnd: True,
                                GetWindowTextLengthW=lambda hwnd: 4)
        api.window = lambda hwnd: {**self.api.info, "hwnd": hwnd}
        api.foreground = lambda: 42
        return native, api

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_native_failure_retains_last_error_and_partial_handle_count(self):
        _, api = self.native_enumeration(success=False, last_error=5)
        result = cu.enumeration_result(api)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WINDOW_ENUMERATION_NATIVE_FAILED")
        self.assertEqual(result["windows"], [])
        self.assertEqual(result["diagnostics"]["enumerated"], 2)
        self.assertEqual(result["diagnostics"]["last_error"], 5)
        self.assertTrue(result["diagnostics"]["last_error_message"])

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_native_failure_without_error_does_not_claim_access_denied(self):
        _, api = self.native_enumeration(handles=(), success=False, last_error=0)
        result = cu.enumeration_result(api)
        self.assertEqual(result["code"], "WINDOW_ENUMERATION_NATIVE_FAILED")
        self.assertEqual(result["diagnostics"]["last_error"], 0)
        self.assertEqual(result["diagnostics"]["enumerated"], 0)
        self.assertEqual(result["diagnostics"]["last_error_message"], "No extended error was reported.")

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_unexpected_metadata_exception_is_recorded_and_other_windows_survive(self):
        _, api = self.native_enumeration()

        def metadata(hwnd):
            if hwnd == 42:
                raise ValueError("metadata unreadable")
            return {**self.api.info, "hwnd": hwnd}

        api.window = metadata
        result = cu.enumeration_result(api)
        self.assertTrue(result["ok"])
        self.assertEqual([w["hwnd"] for w in result["windows"]], [43])
        self.assertEqual(result["diagnostics"]["metadata_errors"], 1)
        self.assertEqual(result["diagnostics"]["callback_errors"], [])
        detail = result["diagnostics"]["metadata_error_samples"][0]
        self.assertEqual(detail["hwnd"], 42)
        self.assertEqual(detail["stage"], "window_metadata")
        self.assertEqual(detail["type"], "ValueError")
        self.assertIn("metadata unreadable", detail["traceback"])

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_callback_exception_is_caught_before_crossing_c_boundary(self):
        _, api = self.native_enumeration()
        # Simulate a failure during handle conversion inside the minimal callback.
        with patch.object(cu, "int", side_effect=RuntimeError("callback conversion failed"), create=True), \
                patch.object(cu.sys, "unraisablehook") as unraisable:
            result = cu.enumeration_result(api)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "WINDOW_ENUMERATION_CALLBACK_FAILED")
        self.assertEqual(result["diagnostics"]["enumerated"], 1)
        detail = result["diagnostics"]["callback_errors"][0]
        self.assertEqual(detail["type"], "RuntimeError")
        self.assertIn("callback conversion failed", detail["traceback"])
        unraisable.assert_not_called()

    @unittest.skipUnless(cu.os.name == "nt", "Windows callback ABI")
    def test_exception_from_native_call_preserves_diagnostics(self):
        _, api = self.native_enumeration(last_error=998, exception=OSError("native call failed"))
        result = cu.enumeration_result(api)
        self.assertEqual(result["code"], "WINDOW_ENUMERATION_NATIVE_FAILED")
        self.assertEqual(result["diagnostics"]["enumerated"], 2)
        self.assertEqual(result["diagnostics"]["last_error"], 998)
        self.assertIsNone(result["diagnostics"]["native_result"])
        self.assertEqual(result["diagnostics"]["native_exception"]["type"], "OSError")

    @patch.object(cu, "Win32")
    @patch.object(cu, "parser")
    def test_diagnose_preserves_failed_enumeration_in_report(self, parser, native):
        report = self.path.parent / "diagnostic.json"
        parser.return_value.parse_args.return_value = argparse.Namespace(command="diagnose", report=str(report))
        api = self.api
        api.windows = lambda: []
        api.last_enumeration = {"native_result": False, "last_error": 5, "enumerated": 2}
        api.runtime_context = lambda: {"thread_desktop": "Default", "session_id": 1}
        api.desktop_ready = lambda: None
        native.return_value = api
        with patch("builtins.print"):
            self.assertEqual(cu.main(), 2)
        result = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(result["code"], "WINDOW_ENUMERATION_NATIVE_FAILED")
        self.assertEqual(result["diagnostics"]["last_error"], 5)
        self.assertEqual(result["runtime"]["session_id"], 1)
        self.assertEqual(result["driver_version"], cu.DRIVER_VERSION)
        self.assertNotIn("windows", result)
        self.assertEqual(api.events, [])

    def test_expired_and_future_and_nan_observations_rejected(self):
        for timestamp in (time.time() - 91, time.time() + 60, float("nan")):
            self.state["captured_at"] = timestamp
            self.write_state()
            with self.subTest(timestamp=timestamp), self.assertRaises(cu.DesktopError):
                cu.load_observation(self.api, self.path)

    def test_changed_window_focus_or_geometry_rejected(self):
        for attr, value in (("fg", 77), ("virtual", [0, 0, 1000, 1000])):
            api = FakeAPI()
            setattr(api, attr, value)
            with self.subTest(attr=attr), self.assertRaises(cu.DesktopError):
                cu.load_observation(api, self.path)
        for key, value in (("pid", 20), ("class_name", "Other"), ("rect", [0, 0, 50, 50]),
                           ("minimized", True)):
            api = FakeAPI()
            api.info[key] = value
            with self.subTest(key=key), self.assertRaises(cu.DesktopError):
                cu.load_observation(api, self.path)

    def test_negative_monitor_origin_converts_to_physical_coordinates(self):
        self.assertEqual(cu.point_from_state(self.api, self.state, 10, 15), (-90, 35))

    def test_out_of_bounds_and_occluded_points_rejected_without_input(self):
        for x, y in ((-1, 20), (600, 20), (5, 400)):
            with self.subTest(x=x, y=y), self.assertRaises(cu.DesktopError):
                cu.point_from_state(self.api, self.state, x, y)
        self.api.occluded = True
        with self.assertRaises(cu.DesktopError):
            cu.point_from_state(self.api, self.state, 10, 10)
        self.assertEqual(self.api.events, [])

    def test_stop_file_blocks_input(self):
        (self.path.parent / "STOP").touch()
        with self.assertRaises(cu.DesktopError):
            cu.perform(self.api, self.action())
        self.assertEqual(self.api.events, [])

    @patch.object(cu.time, "sleep")
    @patch.object(cu, "observe", return_value={"ok": True, "state": "new.json"})
    def test_state_consumed_once_and_refresh_returned(self, observe, sleep):
        result = cu.perform(self.api, self.action())
        self.assertTrue(result["input_sent"])
        self.assertEqual(result["state"], "new.json")
        self.assertEqual(self.api.events, [("move", 10, 70), ("mouse", 2, 0), ("mouse", 4, 0)])
        with self.assertRaises(cu.DesktopError):
            cu.perform(self.api, self.action())
        observe.assert_called_once()

    @patch.object(cu.time, "sleep")
    @patch.object(cu, "observe", side_effect=RuntimeError("capture unavailable"))
    def test_failed_refresh_does_not_allow_replaying_action(self, observe, sleep):
        with self.assertRaisesRegex(cu.DesktopError, "Input sent"):
            cu.perform(self.api, self.action())
        self.assertTrue(self.path.with_suffix(".used").exists())
        with self.assertRaises(cu.DesktopError):
            cu.perform(self.api, self.action())

    @patch.object(cu.time, "sleep")
    def test_drag_releases_button_when_interrupted(self, sleep):
        original = self.api.abort_check
        calls = 0

        def stop_mid_drag():
            nonlocal calls
            calls += 1
            if calls >= 3:
                raise cu.DesktopError("Stopped")
            original()

        self.api.abort_check = stop_mid_drag
        with self.assertRaises(cu.DesktopError):
            cu.perform(self.api, self.action("drag", to_x=220, to_y=90, duration=0.2))
        self.assertEqual(self.api.events[-1], ("mouse", 4, 0))

    @unittest.skipUnless(cu.os.name == "nt", "Windows ABI")
    def test_native_input_structure_matches_windows_abi(self):
        self.assertEqual(ctypes.sizeof(cu.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


if __name__ == "__main__":
    unittest.main(verbosity=2)
