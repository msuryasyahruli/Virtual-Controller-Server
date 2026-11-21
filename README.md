# WiFi Gamepad Server

Server WebSocket Python untuk Windows 10/11 yang menerima input gamepad dari aplikasi Android melalui jaringan WiFi lokal dan meneruskannya ke virtual Xbox 360 controller menggunakan driver **ViGEmBus**.

## Prasyarat

- Windows 10 / 11
- Python 3.8 atau lebih baru
- Driver **ViGEmBus**
- PC Windows dan perangkat Android berada di jaringan WiFi yang sama

## Cara Instalasi & Menjalankan

1. **Download & install ViGEmBus**  
   Unduh installer terbaru dari:  
   https://github.com/nefarius/ViGEmBus/releases  
   Jalankan installer (`.exe`) hingga selesai (restart PC jika diminta).

2. **Install dependensi Python**  
   Buka Command Prompt / PowerShell di folder `python-server`, lalu jalankan:
   ```bash
   pip install -r requirements.txt
   ```

3. **Jalankan server**
   ```bash
   python server.py
   ```
   Terminal akan menampilkan IP lokal Windows dan port server (`8765`).

4. **Hubungkan dari aplikasi Android**  
   Buka aplikasi **WiFi Gamepad** di Android, lalu masukkan alamat IP Windows yang ditampilkan di terminal (port default `8765`).

## Catatan Windows Firewall

Windows Firewall mungkin memblokir koneksi masuk dari perangkat Android. Pastikan port **8765 (TCP)** diizinkan:

- Saat pertama kali menjalankan `python server.py`, klik **Allow access** (Izinkan akses) pada popup Windows Defender Firewall untuk jaringan **Private**.
- Atau buka PowerShell sebagai **Administrator** dan jalankan perintah berikut untuk membuka port 8765 secara manual:
  ```powershell
  New-NetFirewallRule -DisplayName "WiFi Gamepad Server (TCP 8765)" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
  ```
