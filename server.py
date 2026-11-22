"""VControllers Server untuk Windows menggunakan WebSocket dan ViGEmBus."""

from __future__ import annotations

import asyncio
import json
import math
import socket
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

try:
    import vgamepad
except ImportError as exc:
    print("[ERROR] Library 'vgamepad' is not installed.")
    print("        Run   : pip install -r requirements.txt")
    print(f"        Detail: {exc}")
    sys.exit(1)
except Exception as exc:
    print("[ERROR] Failed to load 'vgamepad' library or ViGEmBus driver.")
    print("        Make sure ViGEmBus is installed on Windows:")
    print("        https://github.com/nefarius/ViGEmBus/releases")
    print(f"        Detail: {exc}")
    sys.exit(1)

SERVER_NAME = "VControllers"
HOST = "0.0.0.0"
PORT = 8765

BUTTON_MAP: Dict[str, Any] = {
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

gamepad: Optional[vgamepad.VX360Gamepad] = None
connected_clients: Set[Any] = set()
active_client: Optional[Any] = None


def log(message: str) -> None:
    """Menampilkan log singkat dengan timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def clamp_float(value: Any, min_val: float, max_val: float, default: float = 0.0) -> float:
    """Mengonversi nilai ke float yang aman dalam rentang [min_val, max_val]."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return max(min_val, min(max_val, num))


def get_local_ip() -> str:
    """Mendapatkan alamat IP lokal utama mesin Windows."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        sock.close()


def get_all_local_ips() -> List[str]:
    """Mendapatkan daftar alamat IPv4 lokal non-loopback yang tersedia."""
    primary = get_local_ip()
    ips: List[str] = []
    if primary and not primary.startswith("127."):
        ips.append(primary)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr and not addr.startswith("127.") and addr not in ips:
                ips.append(addr)
    except OSError:
        pass

    return ips or [primary]


def format_client_addr(websocket: Any) -> str:
    """Memformat alamat IP:port client WebSocket untuk log."""
    remote = getattr(websocket, "remote_address", None)
    if isinstance(remote, tuple) and len(remote) >= 2:
        return f"{remote[0]}:{remote[1]}"
    return str(remote) if remote else "unknown"


def init_gamepad() -> vgamepad.VX360Gamepad:
    """Menginisialisasi virtual Xbox 360 controller melalui ViGEmBus sekali saat startup."""
    try:
        return vgamepad.VX360Gamepad()
    except Exception as exc:
        raise RuntimeError(
            "Failed to create virtual Xbox 360 controller. "
            "Make sure the ViGEmBus driver is installed on Windows "
            "(https://github.com/nefarius/ViGEmBus/releases)."
        ) from exc


def reset_gamepad() -> None:
    """Mereset semua tombol dan axis virtual gamepad ke posisi netral."""
    if gamepad is None:
        return

    for xusb_button in BUTTON_MAP.values():
        gamepad.release_button(button=xusb_button)

    gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
    gamepad.right_joystick_float(x_value_float=0.0, y_value_float=0.0)
    gamepad.left_trigger_float(value_float=0.0)
    gamepad.right_trigger_float(value_float=0.0)
    gamepad.update()


def apply_gamepad_state(data: Dict[str, Any]) -> None:
    """Memetakan payload JSON dari aplikasi Android ke input vgamepad."""
    if gamepad is None or data.get("type") != "gamepad":
        return

    buttons = data.get("buttons")
    if isinstance(buttons, dict):
        for name, xusb_button in BUTTON_MAP.items():
            if name in buttons:
                if bool(buttons[name]):
                    gamepad.press_button(button=xusb_button)
                else:
                    gamepad.release_button(button=xusb_button)

    axes = data.get("axes")
    if isinstance(axes, dict):
        lx = clamp_float(axes.get("LX", 0.0), -1.0, 1.0)
        ly = clamp_float(axes.get("LY", 0.0), -1.0, 1.0)
        rx = clamp_float(axes.get("RX", 0.0), -1.0, 1.0)
        ry = clamp_float(axes.get("RY", 0.0), -1.0, 1.0)
        lt = clamp_float(axes.get("LT", 0.0), 0.0, 1.0)
        rt = clamp_float(axes.get("RT", 0.0), 0.0, 1.0)

        gamepad.left_joystick_float(x_value_float=lx, y_value_float=ly)
        gamepad.right_joystick_float(x_value_float=rx, y_value_float=ry)
        gamepad.left_trigger_float(value_float=lt)
        gamepad.right_trigger_float(value_float=rt)

    gamepad.update()


async def handle_client(websocket: Any) -> None:
    """Menangani koneksi WebSocket client Android dan meneruskan input ke vgamepad."""
    global active_client

    client_addr = format_client_addr(websocket)
    previous_active = active_client
    connected_clients.add(websocket)
    active_client = websocket

    if previous_active is not None and previous_active is not websocket:
        reset_gamepad()
        log(
            f"Client connected: {client_addr} "
            f"(took over active control; total clients: {len(connected_clients)})"
        )
    else:
        log(f"Client connected: {client_addr} (total clients: {len(connected_clients)})")

    try:
        async for message in websocket:
            if not isinstance(message, (str, bytes, bytearray)):
                continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                log(f"Invalid JSON message from {client_addr}, ignored.")
                continue

            if not isinstance(payload, dict):
                continue

            if active_client is None:
                active_client = websocket

            if websocket is active_client:
                apply_gamepad_state(payload)
    except ConnectionClosed as exc:
        reason = f"code={exc.code}" if exc.code is not None else "closed"
        log(f"Client disconnected: {client_addr} ({reason})")
    except Exception as exc:
        log(f"Connection error ({client_addr}): {exc}")
    finally:
        connected_clients.discard(websocket)
        was_active = websocket is active_client

        if was_active:
            reset_gamepad()
            active_client = next(iter(connected_clients), None)
            if active_client is not None:
                next_addr = format_client_addr(active_client)
                log(
                    f"Inputs reset to neutral. Active control transferred to {next_addr} "
                    f"(remaining clients: {len(connected_clients)})"
                )
            else:
                log("Inputs reset to neutral. No clients connected.")
        else:
            log(
                f"Inactive client disconnected: {client_addr} "
                f"(remaining clients: {len(connected_clients)})"
            )


async def main() -> None:
    """Initializes the virtual controller and starts the WebSocket server."""
    global gamepad

    gamepad = init_gamepad()
    reset_gamepad()

    local_ips = get_all_local_ips()
    primary_ip = local_ips[0]

    print("=" * 56)
    print(f"  {SERVER_NAME}")
    print("=" * 56)
    print("  Virtual Xbox 360 Controller : Active (ViGEmBus)")
    print(f"  Listening                   : {HOST}:{PORT}")
    print(f"  Local IP (enter on phone)   : {primary_ip}")
    print(f"  WebSocket URL               : ws://{primary_ip}:{PORT}")
    if len(local_ips) > 1:
        extra_ips = ", ".join(local_ips[1:])
        print(f"  Alternative IPs             : {extra_ips}")
    print("-" * 56)
    print("  Press Ctrl+C to stop the server.")
    print("=" * 56, flush=True)

    try:
        async with websockets.serve(
            handle_client,
            HOST,
            PORT,
            ping_interval=20,
            ping_timeout=20,
        ):
            await asyncio.Future()
    finally:
        reset_gamepad()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        log("Server stopped by user.")
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(
            f"\n[ERROR] Failed to start server on port {PORT}: {exc}",
            file=sys.stderr,
        )
        print(
            "        Make sure port 8765 is not already in use by another app.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:
        print(f"\n[ERROR] Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
