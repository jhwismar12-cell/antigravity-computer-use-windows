"""Window-scoped Windows desktop input and screenshot CLI. No server or API key."""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import traceback
import uuid

DRIVER_VERSION = "2.0.0"
MAX_AGE = 90
KEYUP, UNICODE, EXTENDED = 2, 4, 1
KEYS = {
    "CTRL": 0x11, "CONTROL": 0x11, "ALT": 0x12, "SHIFT": 0x10,
    "ENTER": 0x0D, "RETURN": 0x0D, "TAB": 9, "ESC": 0x1B,
    "ESCAPE": 0x1B, "SPACE": 0x20, "BACKSPACE": 8, "DELETE": 0x2E,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "INSERT": 0x2D, **{f"F{i}": 0x6F + i for i in range(1, 13)},
}
EXTENDED_KEYS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


class DesktopError(RuntimeError):
    pass


class DesktopContextMismatch(DesktopError):
    code = "DESKTOP_CONTEXT_MISMATCH"

    def __init__(self, thread_desktop, input_desktop):
        self.context = {"thread_desktop": thread_desktop, "input_desktop": input_desktop}
        super().__init__(f"This process is attached to desktop {thread_desktop!r}, but the interactive "
                         f"desktop is {input_desktop!r}. Use the configured MCP desktop integration; "
                         "do not switch this process out of its isolated desktop.")


def exception_details(exc):
    return {"type": type(exc).__name__, "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=6))}


def win32_error_message(code):
    return C.FormatError(code).strip() if code else "No extended error was reported."


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]


class INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", W.DWORD), ("data", INPUTUNION)]


def key_event(vk=0, scan=0, flags=0):
    return INPUT(type=1, ki=KEYBDINPUT(vk, scan, flags, 0, 0))


def key_sequence(chord):
    names = [s.strip().upper() for s in chord.split("+")]
    codes = []
    for name in names:
        if name in KEYS:
            codes.append(KEYS[name])
        elif len(name) == 1 and name.isascii() and name.isalnum():
            codes.append(ord(name))
        else:
            raise DesktopError(f"Unsupported key: {name!r}. Use letters, digits or named keys.")
    if not codes or len(set(codes)) != len(codes) or len(codes) > 5:
        raise DesktopError("Invalid or duplicate keys in chord.")
    events = [key_event(vk=c, flags=EXTENDED if c in EXTENDED_KEYS else 0) for c in codes]
    events += [key_event(vk=c, flags=KEYUP | (EXTENDED if c in EXTENDED_KEYS else 0))
               for c in reversed(codes)]
    return events


def text_sequence(text, multiline=False):
    if not text or len(text) > 8000:
        raise DesktopError("Text must contain 1 to 8000 characters; split longer documents.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise DesktopError("Unsupported control character in text.")
    if not multiline and any(c in text for c in "\n\t"):
        raise DesktopError("Newlines/tabs require --multiline and an observed multiline editor.")
    events = []
    for char in text:
        if char in "\n\t":
            events.extend(key_sequence("ENTER" if char == "\n" else "TAB"))
        else:
            encoded = char.encode("utf-16-le")
            for (unit,) in struct.iter_unpack("<H", encoded):
                events.extend((key_event(scan=unit, flags=UNICODE),
                               key_event(scan=unit, flags=UNICODE | KEYUP)))
    return events


class Win32:
    def __init__(self):
        if os.name != "nt":
            raise DesktopError("This helper requires Windows 10/11 and an unlocked interactive session.")
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        declarations = {
            "IsWindow": ([W.HWND], W.BOOL),
            "IsWindowVisible": ([W.HWND], W.BOOL),
            "IsIconic": ([W.HWND], W.BOOL),
            "GetWindowTextLengthW": ([W.HWND], C.c_int),
            "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            "GetClassNameW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            "GetForegroundWindow": ([], W.HWND),
            "GetThreadDesktop": ([W.DWORD], W.HANDLE),
            "GetProcessWindowStation": ([], W.HANDLE),
            "GetAncestor": ([W.HWND, W.UINT], W.HWND),
            "WindowFromPoint": ([W.POINT], W.HWND),
            "GetCursorPos": ([C.POINTER(W.POINT)], W.BOOL),
            "GetAsyncKeyState": ([C.c_int], W.SHORT),
            "GetSystemMetrics": ([C.c_int], C.c_int),
            "SetForegroundWindow": ([W.HWND], W.BOOL),
            "ShowWindow": ([W.HWND, C.c_int], W.BOOL),
            "SendInput": ([W.UINT, C.POINTER(INPUT), C.c_int], W.UINT),
            "OpenInputDesktop": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            "CloseDesktop": ([W.HANDLE], W.BOOL),
            "GetUserObjectInformationW": ([W.HANDLE, C.c_int, W.LPVOID, W.DWORD,
                                          C.POINTER(W.DWORD)], W.BOOL),
        }
        for name, (args, result) in declarations.items():
            fn = getattr(self.u, name)
            fn.argtypes, fn.restype = args, result
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.GetCurrentThreadId.argtypes, self.k.GetCurrentThreadId.restype = [], W.DWORD
        self.k.GetCurrentProcess.argtypes, self.k.GetCurrentProcess.restype = [], W.HANDLE
        self.k.ProcessIdToSessionId.argtypes = [W.DWORD, C.POINTER(W.DWORD)]
        self.k.ProcessIdToSessionId.restype = W.BOOL
        # Physical pixels, including monitors with different DPI settings.
        self.u.SetThreadDpiAwarenessContext.argtypes = [C.c_void_p]
        self.u.SetThreadDpiAwarenessContext.restype = C.c_void_p
        if not self.u.SetThreadDpiAwarenessContext(C.c_void_p(-4)):
            raise DesktopError("Cannot enable per-monitor DPI awareness.")

    def desktop_ready(self):
        desktop = self.u.OpenInputDesktop(0, False, 1)
        if not desktop:
            raise DesktopError("Interactive desktop unavailable: terminal isolation, desktop permissions, "
                               "or a locked/secure session may be responsible. Use the host tool's "
                               "official approval flow if desktop access is restricted. Leave UAC/login to the user.")
        try:
            name, size = C.create_unicode_buffer(256), W.DWORD()
            if not self.u.GetUserObjectInformationW(desktop, 2, name, C.sizeof(name), C.byref(size)):
                raise DesktopError("Cannot inspect the input desktop.")
            if name.value.casefold() != "default":
                raise DesktopError("Secure desktop detected. User interaction is required.")
            thread_name, needed = C.create_unicode_buffer(256), W.DWORD()
            thread_desktop = self.u.GetThreadDesktop(self.k.GetCurrentThreadId())
            if not thread_desktop or not self.u.GetUserObjectInformationW(
                    thread_desktop, 2, thread_name, C.sizeof(thread_name), C.byref(needed)):
                raise DesktopError("Cannot verify the current thread's desktop identity.")
            if thread_name.value.casefold() != name.value.casefold():
                raise DesktopContextMismatch(thread_name.value, name.value)
        finally:
            self.u.CloseDesktop(desktop)

    def geometry(self):
        x, y, width, height = (self.u.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        if width <= 0 or height <= 0:
            raise DesktopError("No interactive monitor detected.")
        return [x, y, x + width, y + height]

    def window(self, hwnd):
        if not self.u.IsWindow(hwnd) or not self.u.IsWindowVisible(hwnd):
            raise DesktopError("Window is gone or hidden; run windows again.")
        title = C.create_unicode_buffer(self.u.GetWindowTextLengthW(hwnd) + 1)
        cls, rect, pid = C.create_unicode_buffer(256), W.RECT(), W.DWORD()
        self.u.GetWindowTextW(hwnd, title, len(title))
        self.u.GetClassNameW(hwnd, cls, len(cls))
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        if not self.u.GetWindowRect(hwnd, C.byref(rect)):
            raise DesktopError("Cannot read window bounds.")
        exe = None
        process = self.k.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
                if self.k.QueryFullProcessImageNameW(process, 0, path, C.byref(size)):
                    exe = path.value
            finally:
                self.k.CloseHandle(process)
        return {"hwnd": int(hwnd), "pid": pid.value, "title": title.value,
                "class_name": cls.value, "exe": exe,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
                "minimized": bool(self.u.IsIconic(hwnd))}

    def windows(self):
        result, handles = [], []
        diagnostics = {"enumerated": 0, "visible": 0, "visible_with_title": 0,
                       "visible_without_readable_title": 0, "metadata_errors": 0,
                       "native_result": None, "last_error": None,
                       "last_error_message": None, "callback_errors": [],
                       "native_exception": None, "metadata_error_samples": []}
        # Publish before entering native code so failures never discard diagnostics.
        self.last_enumeration = diagnostics
        callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM, use_last_error=True)

        @callback_type
        def callback(hwnd, _):
            # Keep the callback trivial. Metadata APIs and Python wrappers run only
            # AFTER EnumWindows returns, outside the ctypes callback boundary.
            try:
                diagnostics["enumerated"] += 1
                handles.append(int(hwnd))
                return 1
            except BaseException as exc:
                # Even KeyboardInterrupt must not escape across a C callback.
                diagnostics["callback_errors"].append(exception_details(exc))
                C.set_last_error(31)  # ERROR_GEN_FAILURE, propagated by use_last_error.
                return 0

        self.u.EnumWindows.argtypes = [callback_type, W.LPARAM]
        self.u.EnumWindows.restype = W.BOOL
        C.set_last_error(0)
        try:
            native_result = self.u.EnumWindows(callback, 0)
            last_error = C.get_last_error()  # Capture BEFORE any other Windows API.
            diagnostics["native_result"] = bool(native_result)
            diagnostics["last_error"] = last_error
        except Exception as exc:
            diagnostics["last_error"] = C.get_last_error()
            diagnostics["native_exception"] = exception_details(exc)
        if diagnostics["native_result"] is not True or diagnostics["callback_errors"]:
            diagnostics["last_error_message"] = win32_error_message(diagnostics["last_error"])
            # An incomplete enumeration is never presented as selectable windows.
            return []

        for hwnd in handles:
            stage = "IsWindowVisible"
            try:
                if not self.u.IsWindowVisible(hwnd):
                    continue
                diagnostics["visible"] += 1
                stage = "GetWindowTextLengthW"
                if not self.u.GetWindowTextLengthW(hwnd):
                    diagnostics["visible_without_readable_title"] += 1
                    continue
                diagnostics["visible_with_title"] += 1
                stage = "window_metadata"
                result.append(self.window(hwnd))
            except Exception as exc:
                # A vanishing window or one unreadable property must not cancel
                # enumeration of all the other windows.
                diagnostics["metadata_errors"] += 1
                if len(diagnostics["metadata_error_samples"]) < 5:
                    diagnostics["metadata_error_samples"].append(
                        {"hwnd": hwnd, "stage": stage, **exception_details(exc)})
        return result

    def runtime_context(self):
        """Read-only process/desktop identity; never switch desktops or elevate."""
        info = {"driver_version": DRIVER_VERSION, "script": str(Path(__file__).resolve()),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "python": sys.executable, "python_version": sys.version,
                "pointer_bits": C.sizeof(C.c_void_p) * 8, "process_id": os.getpid(),
                "parent_process_id": os.getppid()}

        def field(name, query):
            try:
                info[name] = query()
            except Exception as exc:
                info[name] = {"error": exception_details(exc)}

        def object_name(handle):
            if not handle:
                raise C.WinError(C.get_last_error())
            name, needed = C.create_unicode_buffer(512), W.DWORD()
            if not self.u.GetUserObjectInformationW(handle, 2, name, C.sizeof(name), C.byref(needed)):
                raise C.WinError(C.get_last_error())
            return name.value

        def input_desktop_name():
            handle = self.u.OpenInputDesktop(0, False, 1)
            if not handle:
                raise C.WinError(C.get_last_error())
            try:
                return object_name(handle)
            finally:
                self.u.CloseDesktop(handle)

        def session_id():
            session = W.DWORD()
            if not self.k.ProcessIdToSessionId(os.getpid(), C.byref(session)):
                raise C.WinError(C.get_last_error())
            return session.value

        def app_container():
            advapi = C.WinDLL("advapi32", use_last_error=True)
            advapi.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
            advapi.OpenProcessToken.restype = W.BOOL
            advapi.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD,
                                                  C.POINTER(W.DWORD)]
            advapi.GetTokenInformation.restype = W.BOOL
            token, value, needed = W.HANDLE(), W.DWORD(), W.DWORD()
            if not advapi.OpenProcessToken(self.k.GetCurrentProcess(), 8, C.byref(token)):
                raise C.WinError(C.get_last_error())
            try:
                if not advapi.GetTokenInformation(token, 29, C.byref(value), C.sizeof(value), C.byref(needed)):
                    raise C.WinError(C.get_last_error())
                return bool(value.value)
            finally:
                self.k.CloseHandle(token)

        field("session_id", session_id)
        field("thread_desktop", lambda: object_name(self.u.GetThreadDesktop(self.k.GetCurrentThreadId())))
        field("input_desktop", input_desktop_name)
        field("window_station", lambda: object_name(self.u.GetProcessWindowStation()))
        field("is_app_container", app_container)
        return info

    def foreground(self):
        return int(self.u.GetForegroundWindow() or 0)

    def focus(self, hwnd):
        self.desktop_ready()
        info = self.window(hwnd)
        if info["minimized"]:
            self.u.ShowWindow(hwnd, 9)
        self.u.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if self.foreground() != hwnd:
            raise DesktopError("Windows refused focus. Select the window manually, then observe again.")

    def abort_check(self):
        self.desktop_ready()
        if self.u.GetAsyncKeyState(0x1B) & 0x8000:
            raise DesktopError("Stopped: Escape is held.")
        point = W.POINT()
        if not self.u.GetCursorPos(C.byref(point)):
            raise DesktopError("Cannot read pointer position.")
        # Corners of the primary monitor are easy for the user to reach.
        width, height = self.u.GetSystemMetrics(0), self.u.GetSystemMetrics(1)
        if min(abs(point.x), abs(point.x - (width - 1))) <= 2 and \
                min(abs(point.y), abs(point.y - (height - 1))) <= 2:
            raise DesktopError("Stopped: pointer at a primary-monitor corner. Move it away to resume.")

    def top_at(self, x, y):
        hwnd = self.u.WindowFromPoint(W.POINT(x, y))
        return int(self.u.GetAncestor(hwnd, 2) or 0)

    def send(self, events):
        if not events:
            return
        array = (INPUT * len(events))(*events)
        sent = self.u.SendInput(len(events), array, C.sizeof(INPUT))
        if sent != len(events):
            # Release synthetic keys/buttons even after partial injection.
            releases = [key_event(e.ki.wVk, e.ki.wScan, e.ki.dwFlags | KEYUP)
                        for e in events if e.type == 1 and not e.ki.dwFlags & KEYUP]
            releases.append(INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 0x14, 0, 0)))
            cleanup = (INPUT * len(releases))(*releases)
            self.u.SendInput(len(releases), cleanup, C.sizeof(INPUT))
            raise DesktopError(f"SendInput injected {sent}/{len(events)} events. Outcome unknown; "
                               "observe before retrying. Elevated apps may block input (UIPI).")

    def move(self, x, y):
        left, top, right, bottom = self.geometry()
        dx = round((x - left) * 65535 / max(1, right - left - 1))
        dy = round((y - top) * 65535 / max(1, bottom - top - 1))
        self.send([INPUT(type=0, mi=MOUSEINPUT(dx, dy, 0, 0xC001, 0, 0))])

    def mouse(self, flags, data=0):
        self.send([INPUT(type=0, mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0))])


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def enumeration_result(api):
    windows = api.windows()
    result = {"ok": bool(windows), "windows": windows,
              "diagnostics": {**getattr(api, "last_enumeration", {}),
                              "foreground_hwnd": api.foreground()}}
    diagnostics = result["diagnostics"]
    if diagnostics.get("callback_errors"):
        result.update({"ok": False, "code": "WINDOW_ENUMERATION_CALLBACK_FAILED",
                       "error": "The enumeration callback failed; inspect diagnostics.callback_errors.",
                       "recovery": "Report the callback exception. No desktop action has been performed."})
    elif diagnostics.get("native_result") is False or diagnostics.get("native_exception"):
        result.update({"ok": False, "code": "WINDOW_ENUMERATION_NATIVE_FAILED",
                       "error": "EnumWindows failed; native error and partial counters are preserved.",
                       "recovery": "Run diagnose in the same approved execution context; inspect last_error, "
                                   "native_exception and runtime desktop/session identity."})
    elif not windows:
        result.update({
            "code": "DESKTOP_ENUMERATION_UNAVAILABLE",
            "error": "No selectable titled windows were returned; desktop access has not been established.",
            "possible_causes": ["terminal isolation or restricted metadata access",
                                "different/noninteractive desktop or no titled windows"],
            "recovery": "Use the host tool's official approval flow to run this exact read-only windows "
                        "command in the user's interactive host session. Do not change global sandbox "
                        "settings or circumvent a denial. See references/permissions.md.",
        })
    return result


def diagnostic_result(api):
    # Capture native results first: context probes must not overwrite GetLastError.
    result = enumeration_result(api)
    result["window_count"] = len(result.pop("windows"))
    result["runtime"] = api.runtime_context()
    try:
        api.desktop_ready()
        result["desktop_check"] = {"ok": True}
    except Exception as exc:
        result["desktop_check"] = {"ok": False, "error": exception_details(exc)}
        if isinstance(exc, DesktopContextMismatch):
            result.update({"ok": False, "enumeration_code": result.get("code"), "code": exc.code,
                           "desktop_context": exc.context, "error": str(exc),
                           "recovery": "Use the configured local MCP integration. Do not change desktop "
                                       "bindings or repeatedly request the same terminal permission."})
    result["note"] = "Read-only diagnostics: no focus, screenshots, mouse or keyboard input."
    return result


def observe(api, hwnd, out):
    from PIL import ImageGrab
    api.desktop_ready()
    info, virtual = api.window(hwnd), api.geometry()
    if info["minimized"]:
        raise DesktopError("Window minimized; focus it first.")
    rect = info["rect"]
    crop = [max(rect[0], virtual[0]), max(rect[1], virtual[1]),
            min(rect[2], virtual[2]), min(rect[3], virtual[3])]
    if crop[2] <= crop[0] or crop[3] <= crop[1]:
        raise DesktopError("Window is outside the visible desktop.")
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    png, state_path = out / f"{token}.png", out / f"{token}.json"
    image = ImageGrab.grab(bbox=tuple(crop), all_screens=True, include_layered_windows=True)
    if list(image.size) != [crop[2] - crop[0], crop[3] - crop[1]]:
        raise DesktopError("Unexpected screenshot scaling; refusing ambiguous coordinates.")
    image.save(png)
    state = {"schema": 1, "captured_at": time.time(), "window": info,
             "capture_rect": crop, "virtual_rect": virtual,
             "image_size": list(image.size), "screenshot": str(png),
             "foreground_hwnd": api.foreground()}
    save_json(state_path, state)
    return {"ok": True, "state": str(state_path), **state}


def load_observation(api, path):
    path = Path(path).resolve()
    state = json.loads(path.read_text(encoding="utf-8"))
    age = time.time() - state["captured_at"]
    if state.get("schema") != 1 or not math.isfinite(age) or age < 0 or age > MAX_AGE:
        raise DesktopError("Observation expired or invalid; observe again.")
    if path.with_suffix(".used").exists():
        raise DesktopError("Observation already consumed; inspect a fresh screenshot.")
    if (path.parent / "STOP").exists():
        raise DesktopError("Stopped by STOP file. Resume only after the user requests it.")
    api.abort_check()
    expected = state["window"]
    current = api.window(expected["hwnd"])
    if any(current[k] != expected[k] for k in ("pid", "class_name", "rect")) or current["minimized"]:
        raise DesktopError("Window changed; observe again.")
    if api.geometry() != state["virtual_rect"]:
        raise DesktopError("Display geometry changed; observe again.")
    if api.foreground() != expected["hwnd"]:
        raise DesktopError("Target lost focus or a dialog opened; list windows and observe the correct one.")
    return path, state


def point_from_state(api, state, x, y):
    width, height = state["image_size"]
    if not (0 <= x < width and 0 <= y < height):
        raise DesktopError("Point outside screenshot. Use original screenshot pixels, without resizing.")
    left, top, _, _ = state["capture_rect"]
    point = (left + x, top + y)
    if api.top_at(*point) != state["window"]["hwnd"]:
        raise DesktopError("Point is covered by another window; observe the intended window again.")
    return point


def perform(api, args):
    state_path, state = load_observation(api, args.state)
    points, events = [], []
    if args.command in ("click", "move", "scroll", "drag"):
        points.append(point_from_state(api, state, args.x, args.y))
    if args.command == "drag":
        points.append(point_from_state(api, state, args.to_x, args.to_y))
        if not math.isfinite(args.duration) or not 0.1 <= args.duration <= 5:
            raise DesktopError("Drag duration must be 0.1 to 5 seconds.")
    if args.command == "key":
        events = key_sequence(args.keys)
    if args.command == "type":
        content = Path(args.text_file).read_text(encoding="utf-8-sig") if args.text_file else args.text
        events = text_sequence(content, args.multiline)
    if args.command == "scroll" and not -20 <= args.steps <= 20:
        raise DesktopError("Scroll steps must be between -20 and 20.")
    # Exclusive creation also prevents concurrent actions consuming the same state.
    try:
        with state_path.with_suffix(".used").open("x", encoding="utf-8") as used:
            used.write(args.command)
    except FileExistsError as exc:
        raise DesktopError("Observation already consumed by another action.") from exc
    # Nothing retries input automatically. This state stays consumed on any error.
    if api.foreground() != state["window"]["hwnd"]:
        raise DesktopError("Focus changed before input; observe again.")
    api.abort_check()
    if points:
        api.move(*points[0])
    if args.command == "click":
        down, up = (0x02, 0x04) if args.button == "left" else (0x08, 0x10)
        for i in range(2 if args.double else 1):
            if i:
                time.sleep(0.08)
            api.mouse(down)
            api.mouse(up)
    elif args.command == "scroll":
        api.mouse(0x1000 if args.horizontal else 0x0800, args.steps * 120)
    elif args.command == "drag":
        api.mouse(0x02)
        try:
            count = max(2, round(args.duration / 0.025))
            for i in range(1, count + 1):
                api.abort_check()
                if api.foreground() != state["window"]["hwnd"] or (state_path.parent / "STOP").exists():
                    raise DesktopError("Drag interrupted; observe before continuing.")
                x = round(points[0][0] + (points[1][0] - points[0][0]) * i / count)
                y = round(points[0][1] + (points[1][1] - points[0][1]) * i / count)
                if api.top_at(x, y) != state["window"]["hwnd"]:
                    raise DesktopError("Drag crossed an obstructing window; observe again.")
                api.move(x, y)
                time.sleep(args.duration / count)
        finally:
            api.mouse(0x04)
    elif events:
        api.send(events)
    time.sleep(0.3)
    try:
        return {**observe(api, state["window"]["hwnd"], state_path.parent),
                "action": args.command, "input_sent": True}
    except Exception as exc:
        raise DesktopError(f"Input sent, but refresh failed: {exc}. Do not repeat input; observe first.") from exc


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Read-only runtime and desktop checks")
    sub.add_parser("windows", help="List visible windows and HWNDs; no input")
    diagnostic = sub.add_parser("diagnose", help="Read-only native errors and desktop/session diagnostics")
    diagnostic.add_argument("--report", help="Optional JSON report file (also printed to stdout)")
    for name in ("focus", "observe"):
        item = sub.add_parser(name)
        item.add_argument("--hwnd", required=True, type=lambda v: int(v, 0))
        item.add_argument("--out", required=True, help="Session directory for private screenshots/state")
    for name in ("click", "move", "scroll", "drag", "key", "type"):
        item = sub.add_parser(name)
        item.add_argument("--state", required=True, help="Unconsumed JSON from the screenshot you inspected")
        if name in ("click", "move", "scroll", "drag"):
            item.add_argument("--x", required=True, type=int)
            item.add_argument("--y", required=True, type=int)
        if name == "click":
            item.add_argument("--button", choices=("left", "right"), default="left")
            item.add_argument("--double", action="store_true")
        elif name == "scroll":
            item.add_argument("--steps", required=True, type=int, help="Positive up/right; negative down/left")
            item.add_argument("--horizontal", action="store_true")
        elif name == "drag":
            item.add_argument("--to-x", required=True, type=int)
            item.add_argument("--to-y", required=True, type=int)
            item.add_argument("--duration", type=float, default=0.5)
        elif name == "key":
            item.add_argument("--keys", required=True, help="Example: CTRL+S, ALT+F4, ENTER")
        elif name == "type":
            group = item.add_mutually_exclusive_group(required=True)
            group.add_argument("--text")
            group.add_argument("--text-file", help="UTF-8 file, recommended for shell metacharacters")
            item.add_argument("--multiline", action="store_true")
    return p


def main():
    args = parser().parse_args()
    api = None
    exit_code = 0
    try:
        api = Win32()
        if args.command != "diagnose":
            api.desktop_ready()
        if args.command == "diagnose":
            result = diagnostic_result(api)
        elif args.command == "doctor":
            import PIL
            result = {"ok": True, "python": sys.executable, "pillow": PIL.__version__,
                      "virtual_rect": api.geometry(), "foreground_hwnd": api.foreground(),
                      "input_structure_bytes": C.sizeof(INPUT), "observation_max_age_seconds": MAX_AGE,
                      "note": "Read-only checks passed; input delivery has not been tested."}
        elif args.command == "windows":
            result = enumeration_result(api)
        elif args.command in ("focus", "observe"):
            if args.command == "focus":
                api.abort_check()
                api.focus(args.hwnd)
            result = observe(api, args.hwnd, args.out)
        else:
            result = perform(api, args)
        exit_code = 0 if result.get("ok") else 2
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__,
                  "exception": exception_details(exc),
                  "diagnostics": getattr(api, "last_enumeration", {}),
                  "recovery": "Inspect current state before retrying any input."}
        if isinstance(exc, DesktopContextMismatch):
            result.update({"code": exc.code, "desktop_context": exc.context,
                           "recovery": "Use the configured local MCP integration; do not switch desktops."})
        exit_code = 1
    result["driver_version"] = DRIVER_VERSION
    if args.command == "diagnose" and args.report:
        try:
            report_path = Path(args.report).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            result["report"] = str(report_path)
            save_json(report_path, result)
        except Exception as exc:
            result["report_error"] = exception_details(exc)
            exit_code = 1
    print(json.dumps(result, ensure_ascii=True, indent=2), file=sys.stderr if exit_code == 1 else sys.stdout)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
