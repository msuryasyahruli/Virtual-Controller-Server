"""Cross-platform PC Mouse & Keyboard input controller (Windows & macOS 15+)."""

from __future__ import annotations

import ctypes
import math
import sys
import threading
from typing import Any, Dict, Iterable, List, Optional, Set

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# ---------------------------------------------------------------------------
# Windows SendInput structures & virtual-key constants
# ---------------------------------------------------------------------------
if IS_WINDOWS:
    from ctypes import wintypes

    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1

    _MOUSEEVENTF_MOVE = 0x0001
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004
    _MOUSEEVENTF_RIGHTDOWN = 0x0008
    _MOUSEEVENTF_RIGHTUP = 0x0010
    _MOUSEEVENTF_MIDDLEDOWN = 0x0020
    _MOUSEEVENTF_MIDDLEUP = 0x0040
    _MOUSEEVENTF_WHEEL = 0x0800
    _MOUSEEVENTF_HWHEEL = 0x01000

    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002

    _ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", _ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", _MOUSEINPUT),
            ("ki", _KEYBDINPUT),
            ("hi", _HARDWAREINPUT),
        ]

    class _INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUT_UNION),
        ]

    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _USER32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _USER32.SendInput.restype = wintypes.UINT

    _WIN_VK_MAP: Dict[str, int] = {
        "ESC": 0x1B,
        "BACKSPACE": 0x08,
        "TAB": 0x09,
        "CAPS_LOCK": 0x14,
        "ENTER": 0x0D,
        "SPACE": 0x20,
        "SHIFT": 0x10,
        "CTRL": 0x11,
        "ALT": 0x12,
        "META": 0x5B,  # Left Windows key
        "ARROW_UP": 0x26,
        "UP": 0x26,
        "ARROW_DOWN": 0x28,
        "DOWN": 0x28,
        "ARROW_LEFT": 0x25,
        "LEFT": 0x25,
        "ARROW_RIGHT": 0x27,
        "RIGHT": 0x27,
        "VOLUME_DOWN": 0xAE,
        "VOLUME_UP": 0xAF,
        "MIC_MUTE": 0xAD,  # Volume Mute fallback on Windows
        "MEDIA_PLAY_PAUSE": 0xB3,
        "MEDIA_NEXT": 0xB0,
        "MEDIA_PREV": 0xB1,
        "PRINT_SCREEN": 0x2C,
        "INSERT": 0x2D,
        "DELETE": 0x2E,
        "HOME": 0x24,
        "END": 0x23,
        "PAGE_UP": 0x21,
        "PAGE_DOWN": 0x22,
        "−": 0xBD,  # U+2212 minus
        "-": 0xBD,
        "=": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        ";": 0xBA,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
    }
    for _i in range(26):
        _WIN_VK_MAP[chr(ord("A") + _i)] = 0x41 + _i
    for _i in range(10):
        _WIN_VK_MAP[str(_i)] = 0x30 + _i
    for _i in range(1, 25):
        _WIN_VK_MAP[f"F{_i}"] = 0x6F + _i

    _WIN_EXTENDED_VKS: Set[int] = {
        0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B,
    }


# ---------------------------------------------------------------------------
# macOS CoreGraphics constants & virtual keycodes
# ---------------------------------------------------------------------------
if IS_MACOS:
    class _CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    _KCG_HID_EVENT_TAP = 0
    _KCG_EVENT_LEFT_MOUSE_DOWN = 1
    _KCG_EVENT_LEFT_MOUSE_UP = 2
    _KCG_EVENT_RIGHT_MOUSE_DOWN = 3
    _KCG_EVENT_RIGHT_MOUSE_UP = 4
    _KCG_EVENT_MOUSE_MOVED = 5
    _KCG_EVENT_LEFT_MOUSE_DRAGGED = 6
    _KCG_EVENT_RIGHT_MOUSE_DRAGGED = 7
    _KCG_EVENT_OTHER_MOUSE_DOWN = 25
    _KCG_EVENT_OTHER_MOUSE_UP = 26
    _KCG_EVENT_OTHER_MOUSE_DRAGGED = 27

    _KCG_MOUSE_BUTTON_LEFT = 0
    _KCG_MOUSE_BUTTON_RIGHT = 1
    _KCG_MOUSE_BUTTON_CENTER = 2
    _KCG_SCROLL_EVENT_UNIT_PIXEL = 0

    _KCG_EVENT_FLAG_MASK_SHIFT = 0x00020000
    _KCG_EVENT_FLAG_MASK_CONTROL = 0x00040000
    _KCG_EVENT_FLAG_MASK_ALTERNATE = 0x00080000
    _KCG_EVENT_FLAG_MASK_COMMAND = 0x00100000

    _MACOS_KEYCODE_MAP: Dict[str, int] = {
        "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7,
        "C": 8, "V": 9, "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15,
        "Y": 16, "T": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
        "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "−": 27, "8": 28,
        "0": 29, "]": 30, "O": 31, "U": 32, "[": 33, "I": 34, "P": 35,
        "ENTER": 36, "L": 37, "J": 38, "K": 40, ";": 41, ",": 43, "/": 44,
        "N": 45, "M": 46, ".": 47, "TAB": 48, "SPACE": 49, "BACKSPACE": 51,
        "ESC": 53, "META": 55, "SHIFT": 56, "CAPS_LOCK": 57, "ALT": 58,
        "CTRL": 59,
        "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
        "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
        "F13": 105, "F14": 107, "F15": 113, "F16": 106, "F17": 64, "F18": 79,
        "F19": 80, "F20": 90,
        "DELETE": 117, "HOME": 115, "END": 119, "PAGE_UP": 116, "PAGE_DOWN": 121,
        "ARROW_LEFT": 123, "LEFT": 123,
        "ARROW_RIGHT": 124, "RIGHT": 124,
        "ARROW_DOWN": 125, "DOWN": 125,
        "ARROW_UP": 126, "UP": 126,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if math.isfinite(num) else default


class PcInputController:
    """Handles mouse, trackpad, scroll, pinch-zoom, keyboard, and shortcut events."""

    SUPPORTED_TYPES: Set[str] = {
        "mouse_move",
        "mouse_button",
        "mouse_scroll",
        "pinch_zoom",
        "key",
        "shortcut",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dx_rem = 0.0
        self._dy_rem = 0.0
        self._scroll_x_rem = 0.0
        self._scroll_y_rem = 0.0
        self._zoom_rem = 0.0
        self._pressed_mouse_buttons: Set[str] = set()
        self._pressed_keys: Set[str] = set()

        self._cg: Any = None
        self._cf: Any = None
        if IS_MACOS:
            try:
                cg = ctypes.CDLL(
                    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
                )
                cf = ctypes.CDLL(
                    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
                )
                cg.CGEventCreate.argtypes = [ctypes.c_void_p]
                cg.CGEventCreate.restype = ctypes.c_void_p
                cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
                cg.CGEventGetLocation.restype = _CGPoint
                cg.CGEventCreateMouseEvent.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    _CGPoint,
                    ctypes.c_uint32,
                ]
                cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
                cg.CGEventCreateScrollWheelEvent.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_int32,
                    ctypes.c_int32,
                ]
                cg.CGEventCreateScrollWheelEvent.restype = ctypes.c_void_p
                cg.CGEventCreateKeyboardEvent.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint16,
                    ctypes.c_bool,
                ]
                cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
                cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
                cg.CGEventSetFlags.restype = None
                cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
                cg.CGEventPost.restype = None
                cf.CFRelease.argtypes = [ctypes.c_void_p]
                cf.CFRelease.restype = None
                self._cg = cg
                self._cf = cf
            except OSError:
                self._cg = None
                self._cf = None

    def apply_message(self, data: Dict[str, Any]) -> Optional[str]:
        """Processes a PC mouse/keyboard JSON payload and returns a short GUI summary."""
        msg_type = data.get("type")
        if msg_type not in self.SUPPORTED_TYPES:
            return None

        with self._lock:
            if msg_type == "mouse_move":
                dx = _safe_float(data.get("dx", 0.0))
                dy = _safe_float(data.get("dy", 0.0))
                self._move_mouse(dx, dy)
                return f"Mouse ({dx:+.0f}, {dy:+.0f})"

            if msg_type == "mouse_button":
                button = str(data.get("button", "left")).lower()
                pressed = bool(data.get("pressed", False))
                self._set_mouse_button(button, pressed)
                state = "Down" if pressed else "Up"
                return f"Mouse {button.title()} {state}"

            if msg_type == "mouse_scroll":
                dx = _safe_float(data.get("dx", 0.0))
                dy = _safe_float(data.get("dy", 0.0))
                self._scroll_mouse(dx, dy)
                return f"Scroll ({dx:+.0f}, {dy:+.0f})"

            if msg_type == "pinch_zoom":
                delta = _safe_float(data.get("delta", 0.0))
                self._pinch_zoom(delta)
                return f"Zoom {delta:+.2f}"

            if msg_type == "key":
                key = str(data.get("key", "")).strip()
                pressed = bool(data.get("pressed", False))
                raw_mods = data.get("modifiers")
                mods = (
                    [str(m).upper() for m in raw_mods]
                    if isinstance(raw_mods, list)
                    else []
                )
                if key:
                    self._handle_key(key, pressed, mods)
                    mod_prefix = "+".join(mods) + "+" if mods else ""
                    return f"Key {mod_prefix}{key} {'↓' if pressed else '↑'}"
                return None

            if msg_type == "shortcut":
                raw_keys = data.get("keys")
                if isinstance(raw_keys, list) and raw_keys:
                    keys = [str(k).strip() for k in raw_keys if str(k).strip()]
                    if keys:
                        self._send_shortcut(keys)
                        return f"Shortcut {'+'.join(keys)}"
                return None

        return None

    def reset(self) -> None:
        """Releases any held mouse buttons or keyboard keys."""
        with self._lock:
            for button in list(self._pressed_mouse_buttons):
                self._set_mouse_button(button, False)
            for key in list(self._pressed_keys):
                self._send_single_key(key, False, set())
            self._pressed_mouse_buttons.clear()
            self._pressed_keys.clear()
            self._dx_rem = 0.0
            self._dy_rem = 0.0
            self._scroll_x_rem = 0.0
            self._scroll_y_rem = 0.0
            self._zoom_rem = 0.0

    # ------------------------------------------------------------------
    # Mouse & Scroll helpers
    # ------------------------------------------------------------------
    def _move_mouse(self, dx: float, dy: float) -> None:
        self._dx_rem += dx
        self._dy_rem += dy
        ix = int(round(self._dx_rem))
        iy = int(round(self._dy_rem))
        self._dx_rem -= ix
        self._dy_rem -= iy
        if ix == 0 and iy == 0:
            return

        if IS_WINDOWS:
            inp = _INPUT(type=_INPUT_MOUSE)
            inp.union.mi = _MOUSEINPUT(
                dx=ix, dy=iy, mouseData=0, dwFlags=_MOUSEEVENTF_MOVE, time=0, dwExtraInfo=0
            )
            _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        elif IS_MACOS and self._cg is not None:
            pos = self._macos_cursor_pos()
            new_pos = _CGPoint(pos.x + ix, pos.y + iy)
            if "left" in self._pressed_mouse_buttons:
                ev_type, btn = _KCG_EVENT_LEFT_MOUSE_DRAGGED, _KCG_MOUSE_BUTTON_LEFT
            elif "right" in self._pressed_mouse_buttons:
                ev_type, btn = _KCG_EVENT_RIGHT_MOUSE_DRAGGED, _KCG_MOUSE_BUTTON_RIGHT
            elif "middle" in self._pressed_mouse_buttons:
                ev_type, btn = _KCG_EVENT_OTHER_MOUSE_DRAGGED, _KCG_MOUSE_BUTTON_CENTER
            else:
                ev_type, btn = _KCG_EVENT_MOUSE_MOVED, _KCG_MOUSE_BUTTON_LEFT
            ev = self._cg.CGEventCreateMouseEvent(None, ev_type, new_pos, btn)
            if ev:
                try:
                    self._cg.CGEventPost(_KCG_HID_EVENT_TAP, ev)
                finally:
                    self._cf.CFRelease(ev)

    def _set_mouse_button(self, button: str, pressed: bool) -> None:
        if pressed:
            self._pressed_mouse_buttons.add(button)
        else:
            self._pressed_mouse_buttons.discard(button)

        if IS_WINDOWS:
            flag_map = {
                ("left", True): _MOUSEEVENTF_LEFTDOWN,
                ("left", False): _MOUSEEVENTF_LEFTUP,
                ("right", True): _MOUSEEVENTF_RIGHTDOWN,
                ("right", False): _MOUSEEVENTF_RIGHTUP,
                ("middle", True): _MOUSEEVENTF_MIDDLEDOWN,
                ("middle", False): _MOUSEEVENTF_MIDDLEUP,
            }
            flag = flag_map.get((button, pressed))
            if flag is None:
                return
            inp = _INPUT(type=_INPUT_MOUSE)
            inp.union.mi = _MOUSEINPUT(
                dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0
            )
            _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        elif IS_MACOS and self._cg is not None:
            ev_map = {
                ("left", True): (_KCG_EVENT_LEFT_MOUSE_DOWN, _KCG_MOUSE_BUTTON_LEFT),
                ("left", False): (_KCG_EVENT_LEFT_MOUSE_UP, _KCG_MOUSE_BUTTON_LEFT),
                ("right", True): (_KCG_EVENT_RIGHT_MOUSE_DOWN, _KCG_MOUSE_BUTTON_RIGHT),
                ("right", False): (_KCG_EVENT_RIGHT_MOUSE_UP, _KCG_MOUSE_BUTTON_RIGHT),
                ("middle", True): (_KCG_EVENT_OTHER_MOUSE_DOWN, _KCG_MOUSE_BUTTON_CENTER),
                ("middle", False): (_KCG_EVENT_OTHER_MOUSE_UP, _KCG_MOUSE_BUTTON_CENTER),
            }
            mapped = ev_map.get((button, pressed))
            if mapped is None:
                return
            pos = self._macos_cursor_pos()
            ev = self._cg.CGEventCreateMouseEvent(None, mapped[0], pos, mapped[1])
            if ev:
                try:
                    self._cg.CGEventPost(_KCG_HID_EVENT_TAP, ev)
                finally:
                    self._cf.CFRelease(ev)

    def _scroll_mouse(self, dx: float, dy: float) -> None:
        if IS_WINDOWS:
            self._scroll_x_rem += dx * 20.0
            self._scroll_y_rem += dy * 20.0
            ix = int(round(self._scroll_x_rem))
            iy = int(round(self._scroll_y_rem))
            self._scroll_x_rem -= ix
            self._scroll_y_rem -= iy
            if iy != 0:
                inp = _INPUT(type=_INPUT_MOUSE)
                inp.union.mi = _MOUSEINPUT(
                    dx=0, dy=0, mouseData=iy & 0xFFFFFFFF, dwFlags=_MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0
                )
                _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            if ix != 0:
                inp = _INPUT(type=_INPUT_MOUSE)
                inp.union.mi = _MOUSEINPUT(
                    dx=0, dy=0, mouseData=ix & 0xFFFFFFFF, dwFlags=_MOUSEEVENTF_HWHEEL, time=0, dwExtraInfo=0
                )
                _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        elif IS_MACOS and self._cg is not None:
            self._scroll_x_rem += dx
            self._scroll_y_rem += dy
            ix = int(round(self._scroll_x_rem))
            iy = int(round(self._scroll_y_rem))
            self._scroll_x_rem -= ix
            self._scroll_y_rem -= iy
            if ix == 0 and iy == 0:
                return
            ev = self._cg.CGEventCreateScrollWheelEvent(
                None, _KCG_SCROLL_EVENT_UNIT_PIXEL, 2, iy, ix
            )
            if ev:
                try:
                    self._cg.CGEventPost(_KCG_HID_EVENT_TAP, ev)
                finally:
                    self._cf.CFRelease(ev)

    def _pinch_zoom(self, delta: float) -> None:
        self._zoom_rem += delta
        step = 0.08
        while abs(self._zoom_rem) >= step:
            zoom_in = self._zoom_rem > 0
            self._zoom_rem -= step if zoom_in else -step
            mod = "META" if IS_MACOS else "CTRL"
            key = "=" if zoom_in else "-"
            self._send_shortcut([mod, key])

    def _macos_cursor_pos(self) -> Any:
        ev = self._cg.CGEventCreate(None)
        if not ev:
            return _CGPoint(0.0, 0.0)
        try:
            return self._cg.CGEventGetLocation(ev)
        finally:
            self._cf.CFRelease(ev)

    # ------------------------------------------------------------------
    # Keyboard & Shortcut helpers
    # ------------------------------------------------------------------
    def _handle_key(self, key: str, pressed: bool, modifiers: Iterable[str]) -> None:
        mod_set = set(modifiers)
        upper_key = key.upper()
        is_modifier = upper_key in {"SHIFT", "CTRL", "ALT", "META"}

        if is_modifier:
            self._send_single_key(upper_key, pressed, set())
            return

        if pressed:
            extra_mods = [m for m in mod_set if m not in self._pressed_keys]
            for m in extra_mods:
                self._send_single_key(m, True, set())
            self._send_single_key(key, True, mod_set)
            for m in reversed(extra_mods):
                self._send_single_key(m, False, set())
        else:
            self._send_single_key(key, False, mod_set)

    def _send_shortcut(self, keys: List[str]) -> None:
        mods = {k.upper() for k in keys if k.upper() in {"SHIFT", "CTRL", "ALT", "META"}}
        for k in keys:
            self._send_single_key(k, True, mods)
        for k in reversed(keys):
            self._send_single_key(k, False, mods)

    def _send_single_key(self, key: str, pressed: bool, active_mods: Set[str]) -> None:
        norm = key.upper() if len(key) > 1 or key.isalpha() else key
        if pressed:
            self._pressed_keys.add(norm)
        else:
            self._pressed_keys.discard(norm)

        if IS_WINDOWS:
            vk = _WIN_VK_MAP.get(norm)
            if vk is None:
                return
            flags = 0 if pressed else _KEYEVENTF_KEYUP
            if vk in _WIN_EXTENDED_VKS:
                flags |= _KEYEVENTF_EXTENDEDKEY
            inp = _INPUT(type=_INPUT_KEYBOARD)
            inp.union.ki = _KEYBDINPUT(
                wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0
            )
            _USER32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        elif IS_MACOS and self._cg is not None:
            keycode = _MACOS_KEYCODE_MAP.get(norm)
            if keycode is None:
                return
            ev = self._cg.CGEventCreateKeyboardEvent(None, keycode, pressed)
            if not ev:
                return
            try:
                flags = 0
                effective_mods = active_mods | (self._pressed_keys & {"SHIFT", "CTRL", "ALT", "META"})
                if "SHIFT" in effective_mods:
                    flags |= _KCG_EVENT_FLAG_MASK_SHIFT
                if "CTRL" in effective_mods:
                    flags |= _KCG_EVENT_FLAG_MASK_CONTROL
                if "ALT" in effective_mods:
                    flags |= _KCG_EVENT_FLAG_MASK_ALTERNATE
                if "META" in effective_mods:
                    flags |= _KCG_EVENT_FLAG_MASK_COMMAND
                if flags:
                    self._cg.CGEventSetFlags(ev, flags)
                self._cg.CGEventPost(_KCG_HID_EVENT_TAP, ev)
            finally:
                self._cf.CFRelease(ev)
