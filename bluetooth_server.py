"""Classic Bluetooth RFCOMM (SPP) server for VControllers on Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import socket
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, Optional

ConnectCallback = Callable[[str], None]
DisconnectCallback = Callable[[], None]
InputCallback = Callable[[Dict[str, Any]], None]
MessageCallback = Callable[[str], None]

# Standard Bluetooth Serial Port Profile (SPP) UUID:
# 00001101-0000-1000-8000-00805F9B34FB
SPP_UUID_STR = "00001101-0000-1000-8000-00805F9B34FB"
DEFAULT_BT_CHANNEL = 4


def is_bluetooth_supported() -> bool:
    """Returns True if Bluetooth RFCOMM SPP is supported on the current OS."""
    if sys.platform == "win32":
        return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")
    if sys.platform == "darwin":
        try:
            import IOBluetooth  # noqa: F401
            import Foundation  # noqa: F401
            import objc  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def get_local_bluetooth_address() -> str:
    """Attempts to read the local Windows Bluetooth adapter MAC address."""
    if sys.platform != "win32":
        return "00:00:00:00:00:00"

    try:
        # PowerShell / PnP query is lightweight and works without admin rights.
        ps_cmd = (
            "Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | "
            "ForEach-Object { (Get-PnpDeviceProperty -InstanceId $_.InstanceId "
            "-KeyName '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2' -ErrorAction SilentlyContinue).Data } | "
            "Select-Object -First 1"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        raw = res.stdout.strip().replace("-", ":").upper()
        if len(raw) == 12 and all(c in "0123456789ABCDEF" for c in raw):
            return ":".join(raw[i : i + 2] for i in range(0, 12, 2))
        if len(raw) == 17 and raw.count(":") == 5:
            return raw
    except Exception:
        pass

    return "00:00:00:00:00:00"


# ---------------------------------------------------------------------------
# Windows WSASetServiceW SDP Registration (ctypes)
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _NS_BTH = 16
    _RNRSERVICE_REGISTER = 0
    _RNRSERVICE_DELETE = 2
    _LUP_CONTAINERS = 0x0002
    _AF_BTH = 32
    _BTHPROTO_RFCOMM = 3

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _SOCKADDR_BTH(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("addressFamily", wintypes.USHORT),
            ("btAddr", ctypes.c_uint64),
            ("serviceClassId", _GUID),
            ("port", wintypes.ULONG),
        ]

    class _SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [
            ("lpSockaddr", ctypes.POINTER(_SOCKADDR_BTH)),
            ("iSockaddrLength", ctypes.c_int),
        ]

    class _CSADDR_INFO(ctypes.Structure):
        _fields_ = [
            ("LocalAddr", _SOCKET_ADDRESS),
            ("RemoteAddr", _SOCKET_ADDRESS),
            ("iSocketType", ctypes.c_int),
            ("iProtocol", ctypes.c_int),
        ]

    class _WSAQUERYSETW(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("lpszServiceInstanceName", wintypes.LPWSTR),
            ("lpServiceClassId", ctypes.POINTER(_GUID)),
            ("lpVersion", ctypes.c_void_p),
            ("lpszComment", wintypes.LPWSTR),
            ("dwNameSpace", wintypes.DWORD),
            ("lpNSProviderId", ctypes.POINTER(_GUID)),
            ("lpszContext", wintypes.LPWSTR),
            ("dwNumberOfProtocols", wintypes.DWORD),
            ("lpafpProtocols", ctypes.c_void_p),
            ("lpszQueryString", wintypes.LPWSTR),
            ("dwNumberOfCsAddrs", wintypes.DWORD),
            ("lpcsaBuffer", ctypes.POINTER(_CSADDR_INFO)),
            ("dwOutputFlags", wintypes.DWORD),
            ("lpBlob", ctypes.c_void_p),
        ]

    def _spp_guid() -> _GUID:
        return _GUID(
            0x00001101,
            0x0000,
            0x1000,
            (ctypes.c_ubyte * 8)(0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB),
        )

    _WS2_32 = ctypes.WinDLL("ws2_32", use_last_error=True)
    _WS2_32.WSASetServiceW.argtypes = [
        ctypes.POINTER(_WSAQUERYSETW),
        ctypes.c_int,
        wintypes.DWORD,
    ]
    _WS2_32.WSASetServiceW.restype = ctypes.c_int


class BluetoothServer:
    """Blocking-socket RFCOMM SPP server running in its own daemon thread."""

    def __init__(
        self,
        on_connect: Optional[ConnectCallback],
        on_disconnect: Optional[DisconnectCallback],
        on_input: Optional[InputCallback],
        on_log: Optional[MessageCallback] = None,
        on_error: Optional[MessageCallback] = None,
        channel: int = DEFAULT_BT_CHANNEL,
        is_busy_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_input = on_input
        self.on_log = on_log
        self.on_error = on_error
        self.channel = channel
        self._is_busy_fn = is_busy_fn

        self._server_sock: Optional[socket.socket] = None
        self._active_client: Optional[socket.socket] = None
        self._stop_requested = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._bound_port: int = channel
        self._sdp_queryset: Any = None
        self._sdp_refs: Any = None

    def _emit_log(self, message: str) -> None:
        if self.on_log is not None:
            self.on_log(message)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)

    def start_background(self) -> bool:
        """Starts the RFCOMM SPP listener in a background thread. Returns False if unsupported."""
        if not is_bluetooth_supported():
            if sys.platform == "darwin":
                self._emit_log(
                    "Bluetooth SPP disabled: install pyobjc-framework-IOBluetooth for macOS Bluetooth."
                )
            else:
                self._emit_log(
                    "Bluetooth SPP is not supported on this OS; WiFi WebSocket active."
                )
            return False

        self._stop_requested = False
        target = self._run_macos_loop if sys.platform == "darwin" else self._run_loop
        self._thread = threading.Thread(
            target=target,
            name="BluetoothSPPThread",
            daemon=True,
        )
        self._thread.start()
        return True

    def _run_macos_loop(self) -> None:
        """Runs macOS IOBluetooth RFCOMM SPP server on a dedicated NSRunLoop thread."""
        import IOBluetooth
        import objc
        from Foundation import NSDefaultRunLoopMode, NSObject, NSRunLoop

        parent = self

        class _MacSPPDelegate(NSObject):
            def init(self) -> Any:
                self = objc.super(_MacSPPDelegate, self).init()
                if self is not None:
                    self._service_record = None
                    self._notification = None
                    self._channel = None
                    self._buffer = ""
                return self

            def start(self) -> bool:
                spp_dict = IOBluetooth.IOBluetoothSDPServiceRecord.localizedSPPServiceDictionary()
                status, self._service_record = (
                    IOBluetooth.IOBluetoothSDPServiceRecord.publishServiceRecordWithDictionary_reference_(
                        spp_dict, None
                    )
                )
                if status != IOBluetooth.kIOReturnSuccess or self._service_record is None:
                    parent._emit_log(
                        f"Bluetooth SPP failed to publish SDP record (code {status})."
                    )
                    return False

                ok, ch_id = self._service_record.getRFCOMMChannelID_(None)
                if not ok:
                    parent._emit_log("Bluetooth SPP could not retrieve RFCOMM channel ID.")
                    return False

                self._notification = (
                    IOBluetooth.IOBluetoothRFCOMMChannel.registerForChannelOpenNotification_selector_withChannelID_direction_(
                        self,
                        b"newRFCOMMChannelOpened:channel:",
                        ch_id,
                        IOBluetooth.kIOBluetoothUserNotificationChannelDirectionIncoming,
                    )
                )
                if self._notification is None:
                    parent._emit_log("Bluetooth SPP failed to register channel notification.")
                    return False

                parent._emit_log(f"Bluetooth SPP ready on macOS (channel {ch_id}).")
                return True

            def newRFCOMMChannelOpened_channel_(self, notification: Any, channel: Any) -> None:
                device = channel.getDevice()
                addr = device.getAddressString() if device else "unknown"
                label = f"BT:{addr}"

                if (parent._is_busy_fn is not None and parent._is_busy_fn()) or self._channel is not None:
                    parent._emit_log(
                        f"Bluetooth rejected ({label}): another client is already connected."
                    )
                    try:
                        err_bytes = (
                            json.dumps(
                                {"type": "error", "message": "another client is already connected"}
                            )
                            + "\n"
                        ).encode("utf-8")
                        channel.writeSync_length_(err_bytes, len(err_bytes))
                    except Exception:
                        pass
                    try:
                        channel.closeChannel()
                    except Exception:
                        pass
                    return

                self._channel = channel
                self._buffer = ""
                channel.setDelegate_(self)
                if parent.on_connect is not None:
                    parent.on_connect(label)

            def rfcommChannelData_data_length_(self, channel: Any, data_ptr: Any, length: int) -> None:
                if channel != self._channel or length <= 0:
                    return
                try:
                    raw = bytes(data_ptr[:length])
                except Exception:
                    return
                self._buffer += raw.decode("utf-8", errors="ignore")
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and isinstance(payload.get("type"), str):
                        if parent.on_input is not None:
                            parent.on_input(payload)

            def rfcommChannelClosed_(self, channel: Any) -> None:
                if channel == self._channel:
                    self._channel = None
                    self._buffer = ""
                    if parent.on_disconnect is not None:
                        parent.on_disconnect()

            def cleanup(self) -> None:
                if self._notification is not None:
                    try:
                        self._notification.unregister()
                    except Exception:
                        pass
                    self._notification = None
                if self._channel is not None:
                    try:
                        self._channel.closeChannel()
                    except Exception:
                        pass
                    self._channel = None
                if self._service_record is not None:
                    try:
                        self._service_record.removeServiceRecord()
                    except Exception:
                        pass
                    self._service_record = None

        delegate = _MacSPPDelegate.alloc().init()
        try:
            if not delegate.start():
                return
            run_loop = NSRunLoop.currentRunLoop()
            while not self._stop_requested:
                run_loop.runMode_beforeDate_(
                    NSDefaultRunLoopMode,
                    objc.CFAbsoluteTimeGetCurrent() + 0.25,
                )
        except Exception as exc:
            if not self._stop_requested:
                self._emit_error(f"macOS Bluetooth SPP error: {exc}")
        finally:
            delegate.cleanup()

    def _register_sdp(self, port: int) -> None:
        if sys.platform != "win32":
            return
        try:
            guid = _spp_guid()
            local_bth = _SOCKADDR_BTH(
                addressFamily=_AF_BTH,
                btAddr=0,
                serviceClassId=guid,
                port=port,
            )
            remote_bth = _SOCKADDR_BTH(
                addressFamily=_AF_BTH,
                btAddr=0,
                serviceClassId=guid,
                port=0,
            )
            csaddr = _CSADDR_INFO(
                LocalAddr=_SOCKET_ADDRESS(
                    lpSockaddr=ctypes.pointer(local_bth),
                    iSockaddrLength=ctypes.sizeof(_SOCKADDR_BTH),
                ),
                RemoteAddr=_SOCKET_ADDRESS(
                    lpSockaddr=ctypes.pointer(remote_bth),
                    iSockaddrLength=ctypes.sizeof(_SOCKADDR_BTH),
                ),
                iSocketType=socket.SOCK_STREAM,
                iProtocol=_BTHPROTO_RFCOMM,
            )
            queryset = _WSAQUERYSETW(
                dwSize=ctypes.sizeof(_WSAQUERYSETW),
                lpszServiceInstanceName="VControllers SPP",
                lpServiceClassId=ctypes.pointer(guid),
                lpVersion=None,
                lpszComment="VControllers Bluetooth Gamepad & PC Remote",
                dwNameSpace=_NS_BTH,
                lpNSProviderId=None,
                lpszContext=None,
                dwNumberOfProtocols=0,
                lpafpProtocols=None,
                lpszQueryString=None,
                dwNumberOfCsAddrs=1,
                lpcsaBuffer=ctypes.pointer(csaddr),
                dwOutputFlags=0,
                lpBlob=None,
            )
            rc = _WS2_32.WSASetServiceW(
                ctypes.byref(queryset), _RNRSERVICE_REGISTER, 0
            )
            if rc == 0:
                self._sdp_queryset = queryset
                self._sdp_refs = (guid, local_bth, remote_bth, csaddr)
                self._emit_log(f"Bluetooth SPP ready (channel {port}).")
            else:
                err = ctypes.get_last_error()
                self._emit_log(
                    f"Bluetooth listening on channel {port} (SDP warning {err})."
                )
        except Exception as exc:
            self._emit_log(f"Bluetooth SDP registration skipped: {exc}")

    def _unregister_sdp(self) -> None:
        if sys.platform != "win32" or self._sdp_queryset is None:
            return
        try:
            _WS2_32.WSASetServiceW(
                ctypes.byref(self._sdp_queryset), _RNRSERVICE_DELETE, 0
            )
        except Exception:
            pass
        finally:
            self._sdp_queryset = None
            self._sdp_refs = None

    def _run_loop(self) -> None:
        try:
            srv = socket.socket(
                socket.AF_BLUETOOTH,
                socket.SOCK_STREAM,
                socket.BTPROTO_RFCOMM,
            )
            self._server_sock = srv

            bound = False
            candidate_channels = [self.channel] + [
                ch for ch in range(5, 31) if ch != self.channel
            ] + [ch for ch in range(1, 5) if ch != self.channel]
            last_err: Optional[OSError] = None
            for ch in candidate_channels:
                try:
                    srv.bind(("00:00:00:00:00:00", ch))
                    self._bound_port = ch
                    bound = True
                    break
                except OSError as err:
                    last_err = err
                    srv.close()
                    srv = socket.socket(
                        socket.AF_BLUETOOTH,
                        socket.SOCK_STREAM,
                        socket.BTPROTO_RFCOMM,
                    )
                    self._server_sock = srv

            if not bound:
                raise last_err or OSError("No free Bluetooth RFCOMM channel found")

            srv.listen(1)
            srv.settimeout(1.0)
            sock_name = srv.getsockname()
            if len(sock_name) >= 2 and int(sock_name[1]) > 0:
                self._bound_port = int(sock_name[1])
            self._register_sdp(self._bound_port)

            while not self._stop_requested:
                try:
                    client_sock, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break

                client_ip = f"BT:{addr[0]}" if addr else "BT:unknown"
                if self._is_busy_fn is not None and self._is_busy_fn():
                    self._reject_client(client_sock, client_ip)
                    continue

                self._handle_client(client_sock, client_ip)
        except OSError as exc:
            if not self._stop_requested:
                self._emit_log(f"Bluetooth unavailable: {exc}")
        except Exception as exc:
            if not self._stop_requested:
                self._emit_error(f"Bluetooth server error: {exc}")
        finally:
            self._unregister_sdp()
            self._close_sockets()

    def _reject_client(self, sock: socket.socket, client_label: str) -> None:
        self._emit_log(
            f"Bluetooth rejected ({client_label}): another client is already connected."
        )
        try:
            msg = json.dumps(
                {"type": "error", "message": "another client is already connected"}
            ) + "\n"
            sock.sendall(msg.encode("utf-8"))
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def _handle_client(self, client_sock: socket.socket, client_label: str) -> None:
        with self._lock:
            self._active_client = client_sock
        if self.on_connect is not None:
            self.on_connect(client_label)

        buffer = ""
        try:
            client_sock.settimeout(1.0)
            while not self._stop_requested:
                try:
                    chunk = client_sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict) and isinstance(data.get("type"), str):
                        if self.on_input is not None:
                            self.on_input(data)
        except OSError:
            pass
        finally:
            with self._lock:
                if self._active_client is client_sock:
                    self._active_client = None
            try:
                client_sock.close()
            except Exception:
                pass
            if self.on_disconnect is not None:
                self.on_disconnect()

    def _close_sockets(self) -> None:
        with self._lock:
            if self._active_client is not None:
                try:
                    self._active_client.close()
                except Exception:
                    pass
                self._active_client = None
            if self._server_sock is not None:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
                self._server_sock = None

    def stop(self) -> None:
        """Stops the Bluetooth SPP server and closes active sockets."""
        self._stop_requested = True
        self._unregister_sdp()
        self._close_sockets()
