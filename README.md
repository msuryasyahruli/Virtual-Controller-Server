# VControllers

Windows desktop application (CustomTkinter) that receives gamepad input from the Android app over WebSocket and forwards it to a virtual Xbox 360 controller using the **ViGEmBus** driver.

## Download Application (`.exe`)

No Python installation required — download the standalone Windows executable directly from the releases page:

- **[Download VControllers.exe (Latest Release)](https://github.com/<username>/<repo-name>/releases/latest/download/VControllers.exe)**
- **[View All Releases](https://github.com/<username>/<repo-name>/releases/latest)**

> **Note:** The **ViGEmBus** driver must still be installed on Windows before running `VControllers.exe`.

## Requirements

- Windows 10 / 11
- **ViGEmBus** driver — https://github.com/nefarius/ViGEmBus/releases
- Python 3.8 or newer *(only required when running from source)*
- Windows PC and Android device connected to the same WiFi network

## Project Structure

- `server_app.py` — Main desktop GUI application (entry point)
- `gamepad_controller.py` — Virtual Xbox 360 controller (`vgamepad` / ViGEmBus)
- `websocket_server.py` — Async WebSocket server (`asyncio`, single active client)
- `requirements.txt` — Python dependencies

## Installation & Running (from Source)

1. **Download & install ViGEmBus**  
   Download the latest installer from:  
   https://github.com/nefarius/ViGEmBus/releases  
   Run the `.exe` installer until finished (restart your PC if prompted).

2. **Install Python dependencies**  
   Open Command Prompt or PowerShell in the `python-server` folder and run:
   ```bash
   python -m pip install -r requirements.txt
   ```

3. **Launch the desktop app**
   ```bash
   python server_app.py
   ```

4. **Start the server & connect from Android**  
   Click **Start Server**, then enter the displayed Windows local IP address into the **VControllers** Android app (default port `8765`).

## Windows Firewall Note

Windows Firewall may block incoming connections from your Android device. Make sure TCP port **8765** is allowed:

- The first time you click **Start Server**, select **Allow access** on the Windows Defender Firewall prompt for **Private** networks.
- Or open PowerShell as **Administrator** and run:
  ```powershell
  New-NetFirewallRule -DisplayName "VControllers (TCP 8765)" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
  ```

## Building `.exe`

1. Install PyInstaller:
   ```bash
   python -m pip install pyinstaller
   ```

2. Build `VControllers.exe` without a console window:
   ```bash
   python -m PyInstaller --onefile --windowed --name "VControllers" --collect-all customtkinter --collect-all vgamepad server_app.py
   ```

The compiled executable will be generated at `dist/VControllers.exe`.
