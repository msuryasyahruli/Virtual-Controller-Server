"""Kontroler virtual Xbox 360 berbasis ViGEmBus untuk VControllers."""

from __future__ import annotations

import math
import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    import vgamepad

    _VGAMEPAD_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - bergantung pada driver Windows
    vgamepad = None  # type: ignore[assignment]
    _VGAMEPAD_IMPORT_ERROR = exc

VIGEMBUS_RELEASE_URL = "https://github.com/nefarius/ViGEmBus/releases"

BUTTON_ORDER: Tuple[str, ...] = (
    "A",
    "B",
    "X",
    "Y",
    "LB",
    "RB",
    "START",
    "SELECT",
    "L3",
    "R3",
    "DPAD_UP",
    "DPAD_DOWN",
    "DPAD_LEFT",
    "DPAD_RIGHT",
)

AXIS_DISPLAY_THRESHOLD = 0.05
TRIGGER_DISPLAY_THRESHOLD = 0.01


class ViGEmBusError(RuntimeError):
    """Exception ketika library vgamepad atau driver ViGEmBus tidak tersedia."""


class GamepadController:
    """Mengelola virtual Xbox 360 controller dan pemetaan input JSON ke vgamepad."""

    def __init__(self) -> None:
        """Menginisialisasi vgamepad.VX360Gamepad() sekali saat startup."""
        self._lock = threading.Lock()
        self._gamepad: Any = None
        self._button_map: Dict[str, Any] = {}

        if vgamepad is None:
            detail = (
                f" ({_VGAMEPAD_IMPORT_ERROR})" if _VGAMEPAD_IMPORT_ERROR else ""
            )
            raise ViGEmBusError(
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
            raise ViGEmBusError(
                "ViGEmBus driver not found or failed to initialize "
                f"({exc}). Download and install ViGEmBus from "
                f"{VIGEMBUS_RELEASE_URL}"
            ) from exc

        self.reset()

    @staticmethod
    def _clamp_float(
        value: Any,
        min_val: float,
        max_val: float,
        default: float = 0.0,
    ) -> float:
        """Mengonversi nilai numerik ke float aman dalam rentang [min_val, max_val]."""
        try:
            num = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(num):
            return default
        return max(min_val, min(max_val, num))

    def apply_input(self, data: Optional[Dict[str, Any]]) -> None:
        """Memetakan dict JSON input dari Android ke virtual Xbox 360 controller."""
        if not isinstance(data, dict) or data.get("type") != "gamepad":
            return

        buttons = data.get("buttons")
        axes = data.get("axes")

        with self._lock:
            if self._gamepad is None:
                return

            if isinstance(buttons, dict):
                for name, xusb_button in self._button_map.items():
                    if name in buttons:
                        if bool(buttons[name]):
                            self._gamepad.press_button(button=xusb_button)
                        else:
                            self._gamepad.release_button(button=xusb_button)

            if isinstance(axes, dict):
                lx = self._clamp_float(axes.get("LX", 0.0), -1.0, 1.0)
                ly = self._clamp_float(axes.get("LY", 0.0), -1.0, 1.0)
                rx = self._clamp_float(axes.get("RX", 0.0), -1.0, 1.0)
                ry = self._clamp_float(axes.get("RY", 0.0), -1.0, 1.0)
                lt = self._clamp_float(axes.get("LT", 0.0), 0.0, 1.0)
                rt = self._clamp_float(axes.get("RT", 0.0), 0.0, 1.0)

                self._gamepad.left_joystick_float(
                    x_value_float=lx,
                    y_value_float=ly,
                )
                self._gamepad.right_joystick_float(
                    x_value_float=rx,
                    y_value_float=ry,
                )
                self._gamepad.left_trigger_float(value_float=lt)
                self._gamepad.right_trigger_float(value_float=rt)

            self._gamepad.update()

    def reset(self) -> None:
        """Mereset seluruh tombol ke False dan seluruh axis ke 0.0."""
        with self._lock:
            if self._gamepad is None:
                return

            for xusb_button in self._button_map.values():
                self._gamepad.release_button(button=xusb_button)

            self._gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
            self._gamepad.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
            self._gamepad.left_trigger_float(value_float=0.0)
            self._gamepad.right_trigger_float(value_float=0.0)
            self._gamepad.update()

    def get_active_inputs(self, data: Optional[Dict[str, Any]]) -> str:
        """Mengembalikan ringkasan string input yang sedang aktif untuk GUI."""
        if not isinstance(data, dict):
            return "-"

        active_parts: List[str] = []

        buttons = data.get("buttons")
        if isinstance(buttons, dict):
            for name in BUTTON_ORDER:
                if bool(buttons.get(name, False)):
                    active_parts.append(name)

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
