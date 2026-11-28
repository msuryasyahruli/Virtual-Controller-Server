"""VControllers CLI Server for Windows & macOS 15+ over WebSocket."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from datetime import datetime
from typing import Any, Dict, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed

from bluetooth_server import BluetoothServer
from gamepad_controller import GamepadBackendError, GamepadController
from pc_input_controller import PcInputController

SERVER_NAME = "VControllers"
APP_VERSION = "1.1.0"
HOST = "0.0.0.0"
PORT = 8765

controller: Optional[GamepadController] = None
pc_input: PcInputController = PcInputController()
connected_clients: Set[Any] = set()
active_client: Optional[Any] = None


def log(message: str) -> None:
    """Prints a timestamped log line."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def get_local_ip() -> str:
    """Returns the primary local IPv4 address."""
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


def get_all_local_ips() -> list[str]:
    """Returns a list of available non-loopback local IPv4 addresses."""
    primary = get_local_ip()
    ips: list[str] = []
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
    """Formats client IP:port for logs."""
    remote = getattr(websocket, "remote_address", None)
    if isinstance(remote, tuple) and len(remote) >= 2:
        return f"{remote[0]}:{remote[1]}"
    return str(remote) if remote else "unknown"


def reset_gamepad() -> None:
    """Resets controller and PC inputs to neutral."""
    if controller is not None:
        controller.reset()
    pc_input.reset()


def apply_gamepad_state(data: Dict[str, Any]) -> None:
    """Applies JSON gamepad, custom_button, or PC mouse/keyboard payload."""
    msg_type = data.get("type")
    if msg_type == "gamepad":
        if controller is not None:
            controller.apply_input(data)
    elif msg_type == "custom_button":
        btn_id = str(data.get("id", data.get("button", ""))).strip()
        pressed = bool(data.get("pressed", False))
        if btn_id and controller is not None:
            controller.apply_custom_button(btn_id, pressed)
    else:
        pc_input.apply_message(data)


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
    """Initializes the controller backend and starts the WebSocket server."""
    global controller

    try:
        controller = GamepadController(pc_action_callback=pc_input.apply_message)
    except GamepadBackendError as exc:
        raise RuntimeError(str(exc)) from exc

    reset_gamepad()

    local_ips = get_all_local_ips()
    primary_ip = local_ips[0]

    print("=" * 56)
    print(f"  {SERVER_NAME} v{APP_VERSION}")
    print("=" * 56)
    print(f"  Controller Backend          : {controller.backend_name}")
    print(f"  Listening                   : {HOST}:{PORT}")
    print(f"  Local IP (enter on phone)   : {primary_ip}")
    print(f"  WebSocket URL               : ws://{primary_ip}:{PORT}")
    if len(local_ips) > 1:
        extra_ips = ", ".join(local_ips[1:])
        print(f"  Alternative IPs             : {extra_ips}")
    print("-" * 56)
    print("  Press Ctrl+C to stop the server.")
    print("=" * 56, flush=True)

    bt_server = BluetoothServer(
        on_connect=lambda addr: log(f"Bluetooth client connected: {addr}"),
        on_disconnect=lambda: (reset_gamepad(), log("Bluetooth client disconnected.")),
        on_input=apply_gamepad_state,
        on_log=log,
        on_error=lambda err: log(f"Bluetooth error: {err}"),
        is_busy_fn=lambda: active_client is not None,
    )
    bt_server.start_background()

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
        bt_server.stop()
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
