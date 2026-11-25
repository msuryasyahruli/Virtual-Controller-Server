"""Virtual controller backends for VControllers (Windows ViGEmBus & macOS 15+)."""

from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

VIGEMBUS_RELEASE_URL = "https://github.com/nefarius/ViGEmBus/releases"
MIN_MACOS_MAJOR = 15

if sys.platform == "win32":
    try:
        import vgamepad

        _VGAMEPAD_IMPORT_ERROR: Optional[Exception] = None
    except Exception as exc:  # pragma: no cover - depends on Windows driver
        vgamepad = None  # type: ignore[assignment]
        _VGAMEPAD_IMPORT_ERROR = exc
else:
    vgamepad = None  # type: ignore[assignment]
    _VGAMEPAD_IMPORT_ERROR = None

BUTTON_ORDER: Tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "START",
    "SELECT",
    "GUIDE",
    "L3",
    "R3",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
    "TOUCHPAD",
    "SHARE",
    "CAPTURE",
    "M1",
    "M2",
    "M3",
    "M4",
    "CUSTOM_1",
    "CUSTOM_2",
    "CUSTOM_3",
    "CUSTOM_4",
    "CUSTOM_5",
    "CUSTOM_6",
    "CUSTOM_7",
    "CUSTOM_8",
)

STANDARD_XUSB_BUTTONS: Tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "START",
    "SELECT",
    "GUIDE",
    "L3",
    "R3",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
)

BindingValue = Union[str, List[str]]

DEFAULT_CUSTOM_BINDINGS: Dict[str, BindingValue] = {
    "GUIDE": "xusb:GUIDE",
    "TOUCHPAD": "xusb:SELECT",
    "SHARE": "key:F12",
    "CAPTURE": "shortcut:META+ALT+G",
    "M1": "xusb:A",
    "M2": "xusb:B",
    "M3": "xusb:X",
    "M4": "xusb:Y",
    "CUSTOM_1": "key:F13",
    "CUSTOM_2": "key:F14",
    "CUSTOM_3": "key:F15",
    "CUSTOM_4": "key:F16",
    "CUSTOM_5": "key:F17",
    "CUSTOM_6": "key:F18",
    "CUSTOM_7": "key:F19",
    "CUSTOM_8": "key:F20",
}

AXIS_DISPLAY_THRESHOLD = 0.05
TRIGGER_DISPLAY_THRESHOLD = 0.01
STICK_KEY_THRESHOLD = 0.5
TRIGGER_KEY_THRESHOLD = 0.5

# macOS virtual keycodes (Carbon / CoreGraphics HIToolbox Events.h)
_MACOS_BUTTON_KEYCODES: Dict[str, int] = {
    "A": 49,           # Space
    "B": 53,           # Escape
    "X": 15,           # R
    "Y": 3,            # F
    "LB": 12,          # Q
    "RB": 14,          # E
    "START": 36,       # Return
    "SELECT": 48,      # Tab
    "GUIDE": 115,      # Home
    "L3": 56,          # Left Shift
    "R3": 9,           # V
    "DPAD_UP": 126,    # Up Arrow
    "DPAD_DOWN": 125,  # Down Arrow
    "DPAD_LEFT": 123,  # Left Arrow
    "DPAD_RIGHT": 124, # Right Arrow
}
_MACOS_KEY_W = 13
_MACOS_KEY_A = 0
_MACOS_KEY_S = 1
_MACOS_KEY_D = 2
_MACOS_KEY_Z = 6
_MACOS_KEY_X = 7

PcActionCallback = Callable[[Dict[str, Any]], Any]


class GamepadBackendError(RuntimeError):
    """Exception raised when the virtual controller backend cannot be initialized."""


# Backward-compatible alias
ViGEmBusError = GamepadBackendError


def load_custom_bindings(config_path: Optional[str] = None) -> Dict[str, BindingValue]:
    """Loads custom button bindings from custom_buttons.json merged over defaults."""
    bindings: Dict[str, BindingValue] = dict(DEFAULT_CUSTOM_BINDINGS)
    candidate_paths: List[str] = []
    if config_path:
        candidate_paths.append(config_path)
    else:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidate_paths.append(os.path.join(meipass, "custom_buttons.json"))
        if getattr(sys, "frozen", False):
            candidate_paths.append(
                os.path.join(os.path.dirname(sys.executable), "custom_buttons.json")
            )
        candidate_paths.append(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_buttons.json")
        )

    for path in candidate_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if not isinstance(key, str):
                        continue
                    if isinstance(value, str) or (
                        isinstance(value, list)
                        and all(isinstance(item, str) for item in value)
                    ):
                        bindings[key.strip().upper()] = value
        except (OSError, json.JSONDecodeError):
            pass

    return bindings


def check_macos_version() -> Tuple[int, ...]:
    """Validates that the host macOS version is at least 15.0."""
    version_str = platform.mac_ver()[0]
    if not version_str:
        raise GamepadBackendError(
            "Unable to determine macOS version. macOS 15.0 or newer is required."
        )

    parts: List[int] = []
    for token in version_str.split("."):
        try:
            parts.append(int(token))
        except ValueError:
            break

    if not parts or parts[0] < MIN_MACOS_MAJOR:
        raise GamepadBackendError(
            f"Unsupported macOS version ({version_str}). "
            f"VControllers requires macOS {MIN_MACOS_MAJOR}.0 or newer."
        )

    return tuple(parts)


class _MacOSKeyboardBackend:
    """Emulates gamepad inputs on macOS 15+ via CoreGraphics CGEvent keystrokes."""

    _KCG_HID_EVENT_TAP = 0

    def __init__(self) -> None:
        check_macos_version()

        try:
            cg = ctypes.CDLL(
                "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
            )
            cf = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )
        except OSError as exc:
            raise GamepadBackendError(
                f"Failed to load macOS CoreGraphics framework: {exc}"
            ) from exc

        cg.CGEventCreateKeyboardEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint16,
            ctypes.c_bool,
        ]
        cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGEventPost.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRelease.restype = None

        self._cg = cg
        self._cf = cf
        self._pressed_keys: Set[int] = set()

    def _send_key(self, keycode: int, is_down: bool) -> None:
        event = self._cg.CGEventCreateKeyboardEvent(None, keycode, is_down)
        if not event:
            return
        try:
            self._cg.CGEventPost(self._KCG_HID_EVENT_TAP, event)
        finally:
            self._cf.CFRelease(event)

    def _sync_keys(self, desired_keys: Set[int]) -> None:
        for keycode in list(self._pressed_keys - desired_keys):
            self._send_key(keycode, False)
            self._pressed_keys.discard(keycode)

        for keycode in desired_keys - self._pressed_keys:
            self._send_key(keycode, True)
            self._pressed_keys.add(keycode)

    def apply(
        self,
        pressed_buttons: Set[str],
        lx: float,
        ly: float,
        lt: float,
        rt: float,
    ) -> None:
        desired: Set[int] = set()

        for name in pressed_buttons:
            keycode = _MACOS_BUTTON_KEYCODES.get(name)
            if keycode is not None:
                desired.add(keycode)

        if lx <= -STICK_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_A)
        elif lx >= STICK_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_D)

        if ly >= STICK_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_W)
        elif ly <= -STICK_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_S)

        if lt >= TRIGGER_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_Z)
        if rt >= TRIGGER_KEY_THRESHOLD:
            desired.add(_MACOS_KEY_X)

        self._sync_keys(desired)

    def reset(self) -> None:
        self._sync_keys(set())


class GamepadController:
    """Manages virtual controller output across Windows (ViGEmBus) and macOS 15+."""

    def __init__(
        self,
        pc_action_callback: Optional[PcActionCallback] = None,
        custom_bindings: Optional[Dict[str, BindingValue]] = None,
    ) -> None:
        """Initializes the platform-appropriate controller backend and custom bindings."""
        self._lock = threading.Lock()
        self._gamepad: Any = None
        self._macos_backend: Optional[_MacOSKeyboardBackend] = None
        self._button_map: Dict[str, Any] = {}
        self._pc_action_callback: Optional[PcActionCallback] = pc_action_callback
        self._custom_bindings: Dict[str, BindingValue] = (
            dict(custom_bindings)
            if custom_bindings is not None
            else load_custom_bindings()
        )
        self._standard_pressed: Set[str] = set()
        self._custom_pressed: Set[str] = set()
        self._custom_held_keys: Set[str] = set()
        self._last_axes: Tuple[float, float, float, float, float, float] = (
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        )
        self.backend_name: str = "Unknown"

        if sys.platform == "darwin":
            self._macos_backend = _MacOSKeyboardBackend()
            self.backend_name = "macOS Keyboard Bridge"
            self.reset()
            return

        if vgamepad is None:
            detail = (
                f" ({_VGAMEPAD_IMPORT_ERROR})" if _VGAMEPAD_IMPORT_ERROR else ""
            )
            raise GamepadBackendError(
                "vgamepad library or ViGEmBus driver was not found"
                f"{detail}. Please install ViGEmBus from "
                f"{VIGEMBUS_RELEASE_URL}"
            )

        self._button_map = {
            "A": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_A,
            "B": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_B,
            "X": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_X,
            "Y": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "LB": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "RB": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "START": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_START,
            "SELECT": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "GUIDE": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
            "L3": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "R3": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "DPAD_UP": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "DPAD_DOWN": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "DPAD_LEFT": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "DPAD_RIGHT": vgamepad.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
        }

        try:
            self._gamepad = vgamepad.VX360Gamepad()
        except Exception as exc:
            raise GamepadBackendError(
                "ViGEmBus driver not found or failed to initialize "
                f"({exc}). Download and install ViGEmBus from "
                f"{VIGEMBUS_RELEASE_URL}"
            ) from exc

        self.backend_name = "ViGEmBus (Xbox 360)"
        self.reset()

    def set_pc_action_callback(self, callback: Optional[PcActionCallback]) -> None:
        """Sets the callback used to forward custom key/shortcut bindings."""
        with self._lock:
            self._pc_action_callback = callback

    def set_custom_binding(self, button_name: str, target: BindingValue) -> None:
        """Adds or updates a custom button binding at runtime."""
        with self._lock:
            self._custom_bindings[button_name.strip().upper()] = target

    @staticmethod
    def _clamp_float(
        value: Any,
        min_val: float,
        max_val: float,
        default: float = 0.0,
    ) -> float:
        """Converts numeric input to a finite float clamped within [min_val, max_val]."""
        try:
            num = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(num):
            return default
        return max(min_val, min(max_val, num))

    @staticmethod
    def _expand_binding(binding: BindingValue) -> List[str]:
        if isinstance(binding, list):
            return [item.strip() for item in binding if isinstance(item, str) and item.strip()]
        return [binding.strip()] if isinstance(binding, str) and binding.strip() else []

    def _sync_custom_actions(self, next_custom_pressed: Set[str]) -> Set[str]:
        """Synchronizes custom key/shortcut actions and returns active mapped XUSB buttons."""
        newly_pressed = next_custom_pressed - self._custom_pressed
        self._custom_pressed = set(next_custom_pressed)

        extra_xusb: Set[str] = set()
        desired_pc_keys: Set[str] = set()

        for btn_name in self._custom_pressed:
            binding = self._custom_bindings.get(btn_name)
            if binding is None:
                continue
            for action in self._expand_binding(binding):
                lower = action.lower()
                if lower.startswith("xusb:"):
                    xusb_name = action.split(":", 1)[1].strip().upper()
                    if xusb_name in STANDARD_XUSB_BUTTONS:
                        extra_xusb.add(xusb_name)
                elif lower.startswith("key:"):
                    key_name = action.split(":", 1)[1].strip()
                    if key_name:
                        desired_pc_keys.add(key_name)
                elif lower.startswith("shortcut:") and btn_name in newly_pressed:
                    combo = action.split(":", 1)[1].strip()
                    keys = [part.strip() for part in combo.split("+") if part.strip()]
                    if keys and self._pc_action_callback is not None:
                        self._pc_action_callback({"type": "shortcut", "keys": keys})

        if self._pc_action_callback is not None:
            for key_name in list(self._custom_held_keys - desired_pc_keys):
                self._pc_action_callback(
                    {"type": "key", "key": key_name, "pressed": False}
                )
                self._custom_held_keys.discard(key_name)
            for key_name in desired_pc_keys - self._custom_held_keys:
                self._pc_action_callback(
                    {"type": "key", "key": key_name, "pressed": True}
                )
                self._custom_held_keys.add(key_name)

        return extra_xusb

    def _flush_controller(self, extra_xusb: Set[str]) -> None:
        lx, ly, rx, ry, lt, rt = self._last_axes
        effective_buttons = self._standard_pressed | extra_xusb

        if self._macos_backend is not None:
            self._macos_backend.apply(effective_buttons, lx, ly, lt, rt)
            return

        if self._gamepad is None:
            return

        for name, xusb_button in self._button_map.items():
            if name in effective_buttons:
                self._gamepad.press_button(button=xusb_button)
            else:
                self._gamepad.release_button(button=xusb_button)

        self._gamepad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        self._gamepad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        self._gamepad.left_trigger_float(value_float=lt)
        self._gamepad.right_trigger_float(value_float=rt)
        self._gamepad.update()

    def apply_custom_button(self, button_id: str, pressed: bool) -> str:
        """Applies a standalone custom_button event (e.g. CUSTOM_1..CUSTOM_8, M1..M4)."""
        norm_id = button_id.strip().upper()
        with self._lock:
            next_custom = set(self._custom_pressed)
            if pressed:
                next_custom.add(norm_id)
            else:
                next_custom.discard(norm_id)
            extra_xusb = self._sync_custom_actions(next_custom)
            self._flush_controller(extra_xusb)
        return f"{norm_id} {'↓' if pressed else '↑'}"

    def apply_input(self, data: Optional[Dict[str, Any]]) -> None:
        """Maps Android JSON payload (standard + custom buttons) to the active backend."""
        if not isinstance(data, dict) or data.get("type") != "gamepad":
            return

        buttons = data.get("buttons")
        axes = data.get("axes")
        axes_dict = axes if isinstance(axes, dict) else {}

        lx = self._clamp_float(axes_dict.get("LX", 0.0), -1.0, 1.0)
        ly = self._clamp_float(axes_dict.get("LY", 0.0), -1.0, 1.0)
        rx = self._clamp_float(axes_dict.get("RX", 0.0), -1.0, 1.0)
        ry = self._clamp_float(axes_dict.get("RY", 0.0), -1.0, 1.0)
        lt = self._clamp_float(axes_dict.get("LT", 0.0), 0.0, 1.0)
        rt = self._clamp_float(axes_dict.get("RT", 0.0), 0.0, 1.0)

        with self._lock:
            if isinstance(axes, dict):
                self._last_axes = (lx, ly, rx, ry, lt, rt)

            next_custom = set(self._custom_pressed)
            if isinstance(buttons, dict):
                for raw_name, is_down in buttons.items():
                    if not isinstance(raw_name, str):
                        continue
                    name = raw_name.strip().upper()
                    pressed = bool(is_down)
                    if name in STANDARD_XUSB_BUTTONS:
                        if pressed:
                            self._standard_pressed.add(name)
                        else:
                            self._standard_pressed.discard(name)
                    elif name in self._custom_bindings:
                        if pressed:
                            next_custom.add(name)
                        else:
                            next_custom.discard(name)

            extra_xusb = self._sync_custom_actions(next_custom)
            self._flush_controller(extra_xusb)

    def reset(self) -> None:
        """Resets all standard and custom buttons to False and all axes to 0.0."""
        with self._lock:
            self._standard_pressed.clear()
            self._last_axes = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            self._sync_custom_actions(set())
            self._flush_controller(set())

    def get_active_inputs(self, data: Optional[Dict[str, Any]]) -> str:
        """Returns a compact string summary of active inputs for the GUI."""
        if not isinstance(data, dict):
            return "-"

        active_parts: List[str] = []

        buttons = data.get("buttons")
        if isinstance(buttons, dict):
            seen: Set[str] = set()
            for name in BUTTON_ORDER:
                if bool(buttons.get(name, False)):
                    active_parts.append(name)
                    seen.add(name)
            for raw_name, is_down in buttons.items():
                if isinstance(raw_name, str) and bool(is_down):
                    upper = raw_name.strip().upper()
                    if upper not in seen:
                        active_parts.append(upper)
                        seen.add(upper)

        axes = data.get("axes")
        if isinstance(axes, dict):
            for trigger_name in ("LT", "RT"):
                val = self._clamp_float(axes.get(trigger_name, 0.0), 0.0, 1.0)
                if val >= TRIGGER_DISPLAY_THRESHOLD:
                    active_parts.append(f"{trigger_name}={val:.2f}")

            for stick_name in ("LX", "LY", "RX", "RY"):
                val = self._clamp_float(axes.get(stick_name, 0.0), -1.0, 1.0)
                if abs(val) >= AXIS_DISPLAY_THRESHOLD:
                    active_parts.append(f"{stick_name}={val:.2f}")

        return " ".join(active_parts) if active_parts else "-"
