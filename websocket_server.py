"""Server WebSocket asyncio untuk menerima input gamepad dari aplikasi Android."""

from __future__ import annotations

import asyncio
import errno
import json
from typing import Any, Callable, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed

ConnectCallback = Callable[[str], None]
DisconnectCallback = Callable[[], None]
InputCallback = Callable[[Dict[str, Any]], None]
MessageCallback = Callable[[str], None]


class WebSocketServer:
    """Server WebSocket single-client aktif dengan callback thread-safe."""

    def __init__(
        self,
        port: int,
        on_connect: Optional[ConnectCallback],
        on_disconnect: Optional[DisconnectCallback],
        on_input: Optional[InputCallback],
        on_log: Optional[MessageCallback] = None,
        on_error: Optional[MessageCallback] = None,
    ) -> None:
        """Menyimpan konfigurasi port dan callback event koneksi/input."""
        self.port: int = int(port)
        self.on_connect: Optional[ConnectCallback] = on_connect
        self.on_disconnect: Optional[DisconnectCallback] = on_disconnect
        self.on_input: Optional[InputCallback] = on_input
        self.on_log: Optional[MessageCallback] = on_log
        self.on_error: Optional[MessageCallback] = on_error

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._server: Any = None
        self._active_client: Any = None
        self._stop_requested: bool = False

    @staticmethod
    def _extract_ip(websocket: Any) -> str:
        """Mengambil alamat IP client dari objek koneksi WebSocket."""
        remote = getattr(websocket, "remote_address", None)
        if isinstance(remote, tuple) and len(remote) >= 1:
            return str(remote[0])
        return str(remote) if remote else "unknown"

    def _emit_log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)

    async def start(self) -> None:
        """Menjalankan server WebSocket pada 0.0.0.0:<port> hingga stop() dipanggil."""
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        if self._stop_requested:
            self._stop_event.set()
            return

        try:
            async with websockets.serve(
                self.handle_client,
                "0.0.0.0",
                self.port,
                ping_interval=20,
                ping_timeout=20,
            ) as server:
                self._server = server
                self._emit_log(f"Server started on port {self.port}...")
                await self._stop_event.wait()
                await self._close_active_client()
        except OSError as exc:
            is_in_use = (
                exc.errno == errno.EADDRINUSE
                or getattr(exc, "winerror", None) == 10048
            )
            if is_in_use:
                self._emit_error(
                    f"Port {self.port} is already in use by another application."
                )
            else:
                self._emit_error(
                    f"Failed to start server on port {self.port}: {exc}"
                )
        except Exception as exc:
            self._emit_error(f"WebSocket server error: {exc}")
        finally:
            self._server = None
            self._active_client = None
            self._loop = None

    async def handle_client(self, websocket: Any) -> None:
        """Menangani koneksi WebSocket; menolak client kedua jika sudah ada yang aktif."""
        client_ip = self._extract_ip(websocket)

        if self._active_client is not None:
            self._emit_log(
                f"Connection rejected ({client_ip}): another client is already connected."
            )
            try:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": "another client is already connected",
                        }
                    )
                )
            except Exception:
                pass
            try:
                await websocket.close(
                    code=1008,
                    reason="another client is already connected",
                )
            except Exception:
                pass
            return

        self._active_client = websocket
        if self.on_connect is not None:
            self.on_connect(client_ip)

        try:
            async for message in websocket:
                if not isinstance(message, (str, bytes, bytearray)):
                    continue

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    self._emit_log(
                        f"Invalid JSON message from {client_ip}, ignored."
                    )
                    continue

                if isinstance(data, dict) and data.get("type") == "gamepad":
                    if self.on_input is not None:
                        self.on_input(data)
        except ConnectionClosed:
            pass
        except Exception as exc:
            self._emit_log(f"Connection error ({client_ip}): {exc}")
        finally:
            if self._active_client is websocket:
                self._active_client = None
                if self.on_disconnect is not None:
                    self.on_disconnect()

    async def _close_active_client(self) -> None:
        """Closes the active client connection safely when the server stops."""
        client = self._active_client
        if client is not None:
            try:
                await client.close(code=1001, reason="Server stopped")
            except Exception:
                pass

    def _trigger_stop_on_loop(self) -> None:
        """Memicu penghentian server di dalam asyncio event loop."""
        if self._stop_event is not None and not self._stop_event.is_set():
            self._stop_event.set()

    def stop(self) -> None:
        """Menghentikan server WebSocket secara aman dari thread mana pun."""
        self._stop_requested = True
        loop = self._loop
        if loop is not None and loop.is_running() and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._trigger_stop_on_loop)
            except RuntimeError:
                pass
