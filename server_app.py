"""Cross-platform desktop GUI (Windows & macOS 15+) for VControllers."""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from typing import Any, Callable, Dict, Optional

import customtkinter as ctk

from bluetooth_server import BluetoothServer
from gamepad_controller import GamepadBackendError, GamepadController
from pc_input_controller import PcInputController
from websocket_server import WebSocketServer

APP_VERSION = "1.1.0"
IS_MACOS = sys.platform == "darwin"
FONT_UI = "Helvetica Neue" if IS_MACOS else "Tahoma"
FONT_MONO = "Menlo" if IS_MACOS else "Consolas"
FONT_SYMBOL = "Apple Symbols" if IS_MACOS else "Segoe UI"
PROTOCOL_TAG = "MAC15" if IS_MACOS else "XUSB"

# Tema warna Light Retro OS (klasik Windows 98/2000)
COLOR_BG = "#d4d0c8"
COLOR_SURFACE = "#e4e0d8"
COLOR_STATUS_BG = "#ece9e0"
COLOR_INSET_BG = "#ffffff"
COLOR_BORDER = "#808080"
COLOR_BORDER_DARK = "#404040"
COLOR_HIGHLIGHT = "#ffffff"

COLOR_TITLEBAR_BG = "#0a246a"
COLOR_TITLEBAR_BADGE_BG = "#d4d0c8"
COLOR_TITLEBAR_TEXT = "#ffffff"
COLOR_TITLEBAR_SUB = "#b8c9f0"

COLOR_TEXT_PRIMARY = "#111111"
COLOR_TEXT_SECONDARY = "#4a4a55"
COLOR_TEXT_DISABLED = "#7f7b72"

COLOR_STATUS_CONNECTED = "#008040"
COLOR_STATUS_WAITING = "#c47a00"
COLOR_STATUS_ERROR = "#c41e1e"

COLOR_START_BG = "#cfe8cf"
COLOR_START_BORDER = "#2d6a2d"
COLOR_START_HOVER = "#bddbbd"
COLOR_START_TEXT = "#0e3a0e"

COLOR_STOP_BG = "#edd0d0"
COLOR_STOP_BORDER = "#8a2d2d"
COLOR_STOP_HOVER = "#e2bcbc"
COLOR_STOP_TEXT = "#4a1010"

COLOR_BTN_BG = "#e2dfd7"
COLOR_BTN_BORDER = "#5a5a5a"
COLOR_BTN_HOVER = "#eeebe4"
COLOR_BTN_DISABLED_BG = "#d8d4cc"
COLOR_BTN_DISABLED_BORDER = "#9a968e"

COLOR_SCROLLBAR = "#a8a49c"
COLOR_SCROLLBAR_HOVER = "#88847c"

DEFAULT_PORT = 8765
MAX_LOG_LINES = 500


def detect_local_ip() -> str:
    """Mendeteksi alamat IPv4 lokal PC Windows menggunakan socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip_addr = str(sock.getsockname()[0])
        if ip_addr and not ip_addr.startswith("127."):
            return ip_addr
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for info in socket.getaddrinfo(
            socket.gethostname(),
            None,
            socket.AF_INET,
        ):
            addr = str(info[4][0])
            if addr and not addr.startswith("127."):
                return addr
    except OSError:
        pass

    return "127.0.0.1"


class GamepadServerApp:
    """Aplikasi desktop CustomTkinter bergaya Light Retro OS untuk VControllers."""

    def __init__(self) -> None:
        """Inisialisasi jendela CustomTkinter, deteksi IP lokal, dan virtual gamepad."""
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.app: ctk.CTk = ctk.CTk()
        self.app.title(f"VControllers v{APP_VERSION}")
        self.app.geometry("400x520")
        self.app.resizable(False, False)
        self.app.configure(fg_color=COLOR_BG)

        self._main_thread_id: int = threading.get_ident()
        self._is_closing: bool = False
        self._is_running: bool = False
        self._had_server_error: bool = False
        self._client_connected: bool = False
        self._input_logged_for_session: bool = False
        self._last_input_summary: str = "-"

        self._local_ip: str = detect_local_ip()
        self._server: Optional[WebSocketServer] = None
        self._bt_server: Optional[BluetoothServer] = None
        self._server_thread: Optional[threading.Thread] = None

        self._pc_input: PcInputController = PcInputController()
        self._controller: Optional[GamepadController] = None
        self._backend_error: Optional[str] = None
        try:
            self._controller = GamepadController(
                pc_action_callback=self._pc_input.apply_message
            )
        except GamepadBackendError as exc:
            self._backend_error = str(exc)

        # Referensi widget (diinisialisasi di build_ui)
        self._header_port_badge: Optional[ctk.CTkLabel] = None
        self._status_dot: Optional[ctk.CTkLabel] = None
        self._status_text: Optional[ctk.CTkLabel] = None
        self._ip_entry: Optional[ctk.CTkEntry] = None
        self._copy_button: Optional[ctk.CTkButton] = None
        self._port_entry: Optional[ctk.CTkEntry] = None
        self._start_button: Optional[ctk.CTkButton] = None
        self._stop_button: Optional[ctk.CTkButton] = None
        self._log_box: Optional[ctk.CTkTextbox] = None
        self._gamepad_dot: Optional[ctk.CTkLabel] = None
        self._gamepad_text: Optional[ctk.CTkLabel] = None
        self._last_input_label: Optional[ctk.CTkLabel] = None

        self.build_ui()
        self.app.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._backend_error is not None:
            status_err = (
                "Incompatible macOS" if IS_MACOS else "ViGEmBus not found"
            )
            dialog_title = (
                "macOS Version Error" if IS_MACOS else "ViGEmBus Not Found"
            )
            self._set_status(COLOR_STATUS_ERROR, status_err)
            self._set_gamepad_indicator(COLOR_STATUS_ERROR, "Unavailable")
            self._set_start_enabled(False)
            self._set_stop_enabled(False)
            self.log(f"ERROR: {self._backend_error}")
            error_message = self._backend_error
            self.app.after(
                150,
                lambda: messagebox.showerror(dialog_title, error_message),
            )
        else:
            backend_name = (
                self._controller.backend_name
                if self._controller is not None
                else "Active"
            )
            self._set_status(COLOR_STATUS_WAITING, "Ready to start")
            self._set_gamepad_indicator(COLOR_STATUS_CONNECTED, backend_name)
            self._set_start_enabled(True)
            self._set_stop_enabled(False)
            self.log(f"Ready ({backend_name}). Local IP: {self._local_ip}")
            if IS_MACOS:
                self.log(
                    "macOS 15+: Grant Accessibility permission if keystrokes are blocked."
                )

    def _run_on_ui_thread(self, callback: Callable[[], None]) -> None:
        """Menjalankan update GUI secara aman di main thread melalui app.after(0, ...)."""
        if self._is_closing:
            return
        if threading.get_ident() == self._main_thread_id:
            callback()
        else:
            try:
                self.app.after(0, callback)
            except Exception:
                pass

    def build_ui(self) -> None:
        """Membangun antarmuka desktop bergaya Light Retro OS dengan bevel 3D klasik."""
        # Frame luar bergaya jendela retro dengan bevel raised
        outer_frame = tk.Frame(self.app, bg=COLOR_BG, bd=2, relief="raised")
        outer_frame.pack(fill="both", expand=True)

        # Classic Retro OS Navy Title Banner
        header = tk.Frame(outer_frame, bg=COLOR_TITLEBAR_BG, height=36)
        header.pack(fill="x", side="top", padx=3, pady=(3, 0))
        header.pack_propagate(False)

        badge_box = tk.Frame(
            header,
            bg=COLOR_TITLEBAR_BADGE_BG,
            bd=1,
            relief="raised",
        )
        badge_box.pack(side="left", padx=(8, 8), pady=6)

        ctk.CTkLabel(
            badge_box,
            text="VC",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=COLOR_TITLEBAR_BG,
            fg_color=COLOR_TITLEBAR_BADGE_BG,
            bg_color=COLOR_TITLEBAR_BADGE_BG,
            width=24,
            height=20,
        ).pack(padx=2)

        ctk.CTkLabel(
            header,
            text="VControllers",
            font=ctk.CTkFont(family="Tahoma", size=14, weight="bold"),
            text_color=COLOR_TITLEBAR_TEXT,
            fg_color=COLOR_TITLEBAR_BG,
            bg_color=COLOR_TITLEBAR_BG,
        ).pack(side="left")

        self._header_port_badge = ctk.CTkLabel(
            header,
            text=f"{PROTOCOL_TAG} // {DEFAULT_PORT}",
            font=ctk.CTkFont(family=FONT_MONO, size=10, weight="bold"),
            text_color=COLOR_TITLEBAR_SUB,
            fg_color=COLOR_TITLEBAR_BG,
            bg_color=COLOR_TITLEBAR_BG,
        )
        self._header_port_badge.pack(side="right", padx=(0, 10))

        # Garis pemisah bevel klasik di bawah title banner
        tk.Frame(outer_frame, bg=COLOR_BORDER, height=1).pack(
            fill="x",
            padx=3,
            pady=(3, 0),
        )
        tk.Frame(outer_frame, bg=COLOR_HIGHLIGHT, height=1).pack(
            fill="x",
            padx=3,
        )

        # Status bar klasik di bagian bawah jendela
        footer_bar = tk.Frame(outer_frame, bg=COLOR_BG)
        footer_bar.pack(fill="x", side="bottom", padx=6, pady=(4, 6))

        tk.Frame(outer_frame, bg=COLOR_HIGHLIGHT, height=1).pack(
            fill="x",
            side="bottom",
            padx=3,
        )
        tk.Frame(outer_frame, bg=COLOR_BORDER, height=1).pack(
            fill="x",
            side="bottom",
            padx=3,
        )

        # Baris 1 status bar: Gamepad virtual
        gp_pane = tk.Frame(footer_bar, bg=COLOR_STATUS_BG, bd=2, relief="sunken")
        gp_pane.pack(fill="x", pady=(0, 3))

        ctk.CTkLabel(
            gp_pane,
            text="Virtual Gamepad:",
            font=ctk.CTkFont(family="Tahoma", size=11),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            height=20,
        ).pack(side="left", padx=(8, 0), pady=2)

        self._gamepad_dot = ctk.CTkLabel(
            gp_pane,
            text="●",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=COLOR_STATUS_CONNECTED,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            height=20,
        )
        self._gamepad_dot.pack(side="left", padx=(6, 4), pady=2)

        self._gamepad_text = ctk.CTkLabel(
            gp_pane,
            text="Active",
            font=ctk.CTkFont(family="Tahoma", size=11, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            height=20,
        )
        self._gamepad_text.pack(side="left", pady=2)

        # Status bar row 2: Last input
        input_pane = tk.Frame(
            footer_bar,
            bg=COLOR_STATUS_BG,
            bd=2,
            relief="sunken",
        )
        input_pane.pack(fill="x")

        ctk.CTkLabel(
            input_pane,
            text="Last Input:",
            font=ctk.CTkFont(family="Tahoma", size=11),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            height=20,
        ).pack(side="left", padx=(8, 0), pady=2)

        self._last_input_label = ctk.CTkLabel(
            input_pane,
            text="-",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            anchor="w",
            height=20,
        )
        self._last_input_label.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(6, 8),
            pady=2,
        )

        # Kontainer isi utama
        main_frame = tk.Frame(outer_frame, bg=COLOR_BG)
        main_frame.pack(fill="both", expand=True, padx=10, pady=(8, 4))

        # Panel status koneksi (sunken bevel)
        status_frame = tk.Frame(
            main_frame,
            bg=COLOR_STATUS_BG,
            bd=2,
            relief="sunken",
            height=34,
        )
        status_frame.pack(fill="x", pady=(0, 8))
        status_frame.pack_propagate(False)

        ctk.CTkLabel(
            status_frame,
            text="Status:",
            font=ctk.CTkFont(family="Tahoma", size=11),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
        ).pack(side="left", padx=(8, 0))

        self._status_dot = ctk.CTkLabel(
            status_frame,
            text="●",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=COLOR_STATUS_WAITING,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
        )
        self._status_dot.pack(side="left", padx=(6, 4))

        self._status_text = ctk.CTkLabel(
            status_frame,
            text="Waiting for connection",
            font=ctk.CTkFont(family="Tahoma", size=11, weight="bold"),
            text_color=COLOR_TEXT_PRIMARY,
            fg_color=COLOR_STATUS_BG,
            bg_color=COLOR_STATUS_BG,
            anchor="w",
        )
        self._status_text.pack(side="left", fill="x", expand=True, padx=(0, 8))

        # Classic "Network" group box (groove relief)
        net_group = tk.LabelFrame(
            main_frame,
            text=" Network ",
            font=("Tahoma", 9, "bold"),
            fg=COLOR_TEXT_PRIMARY,
            bg=COLOR_BG,
            bd=2,
            relief="groove",
            padx=8,
            pady=6,
        )
        net_group.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            net_group,
            text="Local IP Address:",
            font=ctk.CTkFont(family="Tahoma", size=11),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_BG,
            bg_color=COLOR_BG,
            anchor="w",
            height=18,
        ).pack(fill="x", pady=(0, 3))

        ip_row = tk.Frame(net_group, bg=COLOR_BG)
        ip_row.pack(fill="x", pady=(0, 7))

        ip_bevel = tk.Frame(ip_row, bg=COLOR_INSET_BG, bd=2, relief="sunken")
        ip_bevel.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._ip_entry = ctk.CTkEntry(
            ip_bevel,
            fg_color=COLOR_INSET_BG,
            bg_color=COLOR_INSET_BG,
            border_width=0,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            height=26,
            corner_radius=0,
        )
        self._ip_entry.insert(0, self._local_ip)
        self._ip_entry.configure(state="readonly")
        self._ip_entry.pack(fill="x")

        copy_bevel = tk.Frame(ip_row, bg=COLOR_BG, bd=2, relief="raised")
        copy_bevel.pack(side="right")

        self._copy_button = ctk.CTkButton(
            copy_bevel,
            text="Copy IP",
            width=86,
            height=26,
            corner_radius=0,
            fg_color=COLOR_BTN_BG,
            bg_color=COLOR_BG,
            border_width=0,
            hover_color=COLOR_BTN_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(family="Tahoma", size=11, weight="bold"),
            command=self.copy_ip,
        )
        self._copy_button.pack()

        port_row = tk.Frame(net_group, bg=COLOR_BG)
        port_row.pack(fill="x")

        ctk.CTkLabel(
            port_row,
            text="Port:",
            font=ctk.CTkFont(family="Tahoma", size=11),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_BG,
            bg_color=COLOR_BG,
            height=24,
        ).pack(side="left", padx=(0, 8))

        port_bevel = tk.Frame(
            port_row,
            bg=COLOR_INSET_BG,
            bd=2,
            relief="sunken",
        )
        port_bevel.pack(side="left")

        self._port_entry = ctk.CTkEntry(
            port_bevel,
            width=86,
            height=24,
            corner_radius=0,
            fg_color=COLOR_INSET_BG,
            bg_color=COLOR_INSET_BG,
            border_width=0,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._port_entry.insert(0, str(DEFAULT_PORT))
        self._port_entry.pack()

        ctk.CTkLabel(
            port_row,
            text="TCP / WebSocket",
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=COLOR_TEXT_SECONDARY,
            fg_color=COLOR_BG,
            bg_color=COLOR_BG,
            height=24,
        ).pack(side="left", padx=(10, 0))

        # Start & Stop buttons with classic 3D raised bevel
        start_bevel = tk.Frame(main_frame, bg=COLOR_BG, bd=2, relief="raised")
        start_bevel.pack(fill="x", pady=(0, 6))

        self._start_button = ctk.CTkButton(
            start_bevel,
            text="▶  Start Server",
            height=30,
            corner_radius=0,
            fg_color=COLOR_START_BG,
            bg_color=COLOR_BG,
            border_color=COLOR_START_BORDER,
            border_width=1,
            hover_color=COLOR_START_HOVER,
            text_color=COLOR_START_TEXT,
            text_color_disabled=COLOR_TEXT_DISABLED,
            font=ctk.CTkFont(family="Tahoma", size=12, weight="bold"),
            command=self.start_server,
        )
        self._start_button.pack(fill="x")

        stop_bevel = tk.Frame(main_frame, bg=COLOR_BG, bd=2, relief="raised")
        stop_bevel.pack(fill="x", pady=(0, 8))

        self._stop_button = ctk.CTkButton(
            stop_bevel,
            text="■  Stop Server",
            height=30,
            corner_radius=0,
            fg_color=COLOR_BTN_DISABLED_BG,
            bg_color=COLOR_BG,
            border_color=COLOR_BTN_DISABLED_BORDER,
            border_width=1,
            hover_color=COLOR_STOP_HOVER,
            text_color=COLOR_STOP_TEXT,
            text_color_disabled=COLOR_TEXT_DISABLED,
            font=ctk.CTkFont(family="Tahoma", size=12, weight="bold"),
            state="disabled",
            command=self.stop_server,
        )
        self._stop_button.pack(fill="x")

        # Classic "Activity Log" group box
        log_group = tk.LabelFrame(
            main_frame,
            text=" Activity Log ",
            font=("Tahoma", 9, "bold"),
            fg=COLOR_TEXT_PRIMARY,
            bg=COLOR_BG,
            bd=2,
            relief="groove",
            padx=6,
            pady=5,
        )
        log_group.pack(fill="both", expand=True)

        log_bevel = tk.Frame(
            log_group,
            bg=COLOR_INSET_BG,
            bd=2,
            relief="sunken",
        )
        log_bevel.pack(fill="both", expand=True, pady=(0, 6))

        self._log_box = ctk.CTkTextbox(
            log_bevel,
            height=80,
            corner_radius=0,
            fg_color=COLOR_INSET_BG,
            bg_color=COLOR_INSET_BG,
            border_width=0,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_MONO, size=10),
            scrollbar_button_color=COLOR_SCROLLBAR,
            scrollbar_button_hover_color=COLOR_SCROLLBAR_HOVER,
            wrap="word",
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True)

        clear_row = tk.Frame(log_group, bg=COLOR_BG)
        clear_row.pack(fill="x")

        clear_bevel = tk.Frame(clear_row, bg=COLOR_BG, bd=2, relief="raised")
        clear_bevel.pack(side="right")

        ctk.CTkButton(
            clear_bevel,
            text="Clear Log",
            width=108,
            height=22,
            corner_radius=0,
            fg_color=COLOR_BTN_BG,
            bg_color=COLOR_BG,
            border_width=0,
            hover_color=COLOR_BTN_HOVER,
            text_color=COLOR_TEXT_PRIMARY,
            font=ctk.CTkFont(family="Tahoma", size=10),
            command=self._clear_log,
        ).pack()

    def _set_status(self, color: str, text: str) -> None:
        if self._status_dot is not None:
            self._status_dot.configure(text_color=color)
        if self._status_text is not None:
            self._status_text.configure(text=text)

    def _set_gamepad_indicator(self, color: str, text: str) -> None:
        if self._gamepad_dot is not None:
            self._gamepad_dot.configure(text_color=color)
        if self._gamepad_text is not None:
            self._gamepad_text.configure(text=text)

    def _set_start_enabled(self, enabled: bool) -> None:
        if self._start_button is not None:
            self._start_button.configure(
                state="normal" if enabled else "disabled",
                fg_color=COLOR_START_BG if enabled else COLOR_BTN_DISABLED_BG,
                border_color=(
                    COLOR_START_BORDER if enabled else COLOR_BTN_DISABLED_BORDER
                ),
            )

    def _set_stop_enabled(self, enabled: bool) -> None:
        if self._stop_button is not None:
            self._stop_button.configure(
                state="normal" if enabled else "disabled",
                fg_color=COLOR_STOP_BG if enabled else COLOR_BTN_DISABLED_BG,
                border_color=(
                    COLOR_STOP_BORDER if enabled else COLOR_BTN_DISABLED_BORDER
                ),
            )

    def _set_port_enabled(self, enabled: bool) -> None:
        if self._port_entry is not None:
            self._port_entry.configure(
                state="normal" if enabled else "disabled",
                fg_color=COLOR_INSET_BG if enabled else COLOR_STATUS_BG,
                text_color=(
                    COLOR_TEXT_PRIMARY if enabled else COLOR_TEXT_SECONDARY
                ),
            )

    def _set_last_input_text(self, text: str) -> None:
        if self._last_input_label is not None:
            display_text = text if len(text) <= 32 else f"{text[:29]}..."
            self._last_input_label.configure(text=display_text)

    def _parse_port(self) -> Optional[int]:
        """Memvalidasi input port dari widget entry (1-65535)."""
        if self._port_entry is None:
            return DEFAULT_PORT
        raw = self._port_entry.get().strip()
        try:
            port = int(raw)
        except ValueError:
            return None
        if 1 <= port <= 65535:
            return port
        return None

    def start_server(self) -> None:
        """Starts the asyncio WebSocket server in a separate background thread."""
        if self._controller is None:
            self.log("Cannot start server: Controller backend is not available.")
            return

        if self._is_running:
            return

        port = self._parse_port()
        if port is None:
            self.log("Invalid port. Enter a number between 1 and 65535.")
            self._set_status(COLOR_STATUS_ERROR, "Invalid port")
            return

        self._is_running = True
        self._had_server_error = False
        self._client_connected = False
        self._input_logged_for_session = False
        self._last_input_summary = "-"

        if self._header_port_badge is not None:
            self._header_port_badge.configure(text=f"{PROTOCOL_TAG} // {port}")

        self._set_start_enabled(False)
        self._set_stop_enabled(True)
        self._set_port_enabled(False)
        self._set_status(COLOR_STATUS_WAITING, "Waiting for connection")
        self._set_last_input_text("-")

        server = WebSocketServer(
            port=port,
            on_connect=self.on_client_connect,
            on_disconnect=self.on_client_disconnect,
            on_input=self.on_input_received,
            on_log=self.log,
            on_error=self._on_server_error,
        )
        self._server = server

        self._server_thread = threading.Thread(
            target=self._run_server_loop,
            args=(server,),
            name="WebSocketServerThread",
            daemon=True,
        )
        self._server_thread.start()

        bt_server = BluetoothServer(
            on_connect=self.on_client_connect,
            on_disconnect=self.on_client_disconnect,
            on_input=self.on_input_received,
            on_log=self.log,
            on_error=self._on_bt_error,
            is_busy_fn=lambda: self._client_connected,
        )
        self._bt_server = bt_server
        bt_server.start_background()

    def _run_server_loop(self, server: WebSocketServer) -> None:
        """Background thread target running asyncio.run(server.start())."""
        try:
            asyncio.run(server.start())
        except Exception as exc:
            self._on_server_error(f"Server stopped due to error: {exc}")
        finally:
            if self._controller is not None:
                self._controller.reset()
            self._pc_input.reset()
            self._run_on_ui_thread(lambda: self._on_server_stopped(server))

    def _on_bt_error(self, message: str) -> None:
        """Logs non-fatal Bluetooth SPP errors without stopping the WiFi server."""
        self.log(f"Bluetooth: {message}")

    def _on_server_error(self, message: str) -> None:
        """Handles fatal server errors such as port already in use."""
        def _update() -> None:
            self._had_server_error = True
            self.log(f"ERROR: {message}")
            self._set_status(COLOR_STATUS_ERROR, "Failed to start server")

        self._run_on_ui_thread(_update)

    def _on_server_stopped(self, server: WebSocketServer) -> None:
        """Updates GUI state after the server thread finishes cleanly."""
        if self._server is not server:
            return

        was_error = self._had_server_error

        self._server = None
        if self._bt_server is not None:
            self._bt_server.stop()
            self._bt_server = None
        self._server_thread = None
        self._is_running = False
        self._client_connected = False
        self._last_input_summary = "-"

        self._set_start_enabled(self._controller is not None)
        self._set_stop_enabled(False)
        self._set_port_enabled(True)
        self._set_last_input_text("-")

        if not was_error and self._controller is not None:
            self._set_status(COLOR_STATUS_WAITING, "Server stopped")

    def stop_server(self) -> None:
        """Stops the WebSocket server and resets the virtual gamepad to neutral."""
        if not self._is_running:
            return

        self._set_stop_enabled(False)
        if self._server is not None:
            self._server.stop()
        if self._bt_server is not None:
            self._bt_server.stop()
            self._bt_server = None

        if self._controller is not None:
            self._controller.reset()
        self._pc_input.reset()

        self.log("Server stopped.")

    def on_client_connect(self, ip: str) -> None:
        """Callback when an Android device connects to the WebSocket or Bluetooth server."""
        self._client_connected = True

        def _update() -> None:
            self._input_logged_for_session = False
            self._set_status(COLOR_STATUS_CONNECTED, f"Connected ({ip})")
            self.log(f"Client connected ({ip})")

        self._run_on_ui_thread(_update)

    def on_client_disconnect(self) -> None:
        """Callback when the client disconnects; always resets gamepad and PC inputs."""
        self._client_connected = False
        if self._controller is not None:
            self._controller.reset()
        self._pc_input.reset()

        def _update() -> None:
            self._input_logged_for_session = False
            self._last_input_summary = "-"
            self._set_last_input_text("-")
            if self._is_running:
                self._set_status(COLOR_STATUS_WAITING, "Waiting for connection")
            self.log("Client disconnected. Inputs reset to neutral.")

        self._run_on_ui_thread(_update)

    def on_input_received(self, input_data: Dict[str, Any]) -> None:
        """Dispatches gamepad or PC mouse/keyboard inputs and updates Last Input."""
        msg_type = input_data.get("type")
        summary: Optional[str] = None

        if msg_type == "gamepad":
            if self._controller is not None:
                self._controller.apply_input(input_data)
                summary = self._controller.get_active_inputs(input_data)
        elif msg_type == "custom_button":
            btn_id = str(input_data.get("id", input_data.get("button", ""))).strip()
            pressed = bool(input_data.get("pressed", False))
            if btn_id and self._controller is not None:
                summary = self._controller.apply_custom_button(btn_id, pressed)
        else:
            summary = self._pc_input.apply_message(input_data)

        if summary is None:
            return

        should_log_first = not self._input_logged_for_session
        should_update_label = summary != self._last_input_summary

        if should_log_first:
            self._input_logged_for_session = True
        if should_update_label:
            self._last_input_summary = summary

        if should_log_first or should_update_label:
            def _update() -> None:
                if should_log_first:
                    self.log("Input received ✓")
                if should_update_label:
                    self._set_last_input_text(summary)

            self._run_on_ui_thread(_update)

    def log(self, message: str) -> None:
        """Menambahkan baris log bertimestamp [HH:MM:SS] dengan auto-scroll ke bawah."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"

        def _append() -> None:
            if self._log_box is None:
                return
            self._log_box.configure(state="normal")
            self._log_box.insert("end", line)

            # Batasi jumlah baris agar memori tetap ringan
            line_count = int(self._log_box.index("end-1c").split(".")[0])
            if line_count > MAX_LOG_LINES:
                excess = line_count - MAX_LOG_LINES
                self._log_box.delete("1.0", f"{excess + 1}.0")

            self._log_box.see("end")
            self._log_box.configure(state="disabled")

        self._run_on_ui_thread(_append)

    def _clear_log(self) -> None:
        """Mengosongkan seluruh isi area log aktivitas."""
        if self._log_box is None:
            return
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def copy_ip(self) -> None:
        """Copies the local IP address to the Windows clipboard."""
        self.app.clipboard_clear()
        self.app.clipboard_append(self._local_ip)
        self.app.update_idletasks()
        self.log(f"Copied IP {self._local_ip} to clipboard.")

        if self._copy_button is not None:
            self._copy_button.configure(text="Copied ✓")
            self.app.after(
                1500,
                lambda: (
                    self._copy_button.configure(text="Copy IP")
                    if self._copy_button is not None and not self._is_closing
                    else None
                ),
            )

    def _on_close(self) -> None:
        """Menghentikan server dan mereset virtual gamepad sebelum menutup jendela."""
        self._is_closing = True
        if self._server is not None:
            self._server.stop()
        if self._bt_server is not None:
            self._bt_server.stop()
        if self._controller is not None:
            self._controller.reset()
        self._pc_input.reset()
        self.app.destroy()

    def run(self) -> None:
        """Menjalankan main loop GUI CustomTkinter."""
        self.app.mainloop()


if __name__ == "__main__":
    server_app = GamepadServerApp()
    server_app.run()
