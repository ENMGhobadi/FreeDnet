# FreeDnet

A Windows 11 desktop proxy router (**FreeDnet Pro**) with a cloud backend (**Render / SnappDeploy / Oracle / Docker**) and multi-account **Google Apps Script (GAS)** relay support for bypassing strict network filtering.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Key Features

- **Dual Chain Modes:** Switch live between **Via Google Apps Script (Anti-Filtering)** and **Direct to Backend (Max Speed)**.
- **Multi-GAS & Multi-Server Smart Pools:**
  - **Fair-Share (`1/N`)** or **Priority (`#1 -> #2` Failover)** distribution across multiple GAS accounts and backend servers, plus **Only: `<Server>`** lock.
  - **Free Gmail Quota Presets** (`Low`, `Medium`, `High`, `X-High`) with automatic rate-limit cooldowns.
  - **Keep Awake (10m)** to prevent free-tier containers from sleeping.
- **High-Speed Transport:** Persistent Keep-Alive connection pool, full-duplex `TX`/`RX` relay, Gzip payload compression (skipping already-encrypted TLS records), and low-latency TCP coalescing.
- **1-Click System Proxy & Xray TUN Mode:**
  - Toggle Windows System Proxy (`127.0.0.1:1080`) in one click.
  - Enable **TUN Mode** (`FreeDnetTUN` virtual adapter via Xray-core + Wintun) with automatic Iran split-routing (`geoip:ir`, `geosite:category-ir`, `.ir` direct).
- **Diagnostics:** Built-in **IP & Diagnostics** tab to ping all relays/servers, run end-to-end SOCKS5 tests, and verify your real Exit IP, Country, and ISP.
- **Customizable UI:** **5 themes** (*Midnight Dark*, *Slate Blue & Cool Gray*, *Obsidian Orange & Black*, *Emerald & Slate Gray*, *Warm Nescafe Light*) and **8 languages** (*English*, *Persian (RTL)*, *Chinese*, *Spanish*, *German*, *Russian*, *Japanese*, *Korean*) selectable live in **Settings & Logs**.

---

## Setup Guide (3 Steps)

### Step 1: Deploy the Backend Server (`server.js`)

1. Connect this repository to **Render**, **SnappDeploy**, **Oracle Cloud**, or any Node.js / Docker host.
2. Set the environment variable:
   - `AUTH_KEY`: a strong secret key of your choice (default fallback is `change-me-freednet-key`).
3. Deploy the service and copy your public HTTPS URL (e.g., `https://your-app.onrender.com`).

> You can deploy multiple backend servers (e.g., on both Render and SnappDeploy) and manage all of them from the **Backend Servers** tab in the app.

---

### Step 2: Deploy Google Apps Script Relays (`gas/Code.gs`)

Create 1 or more GAS Web Apps (using multiple free Google accounts increases daily quota and concurrency):

1. Open [script.google.com](https://script.google.com) and create a **New project**.
2. Replace the default code with the contents of [`gas/Code.gs`](gas/Code.gs).
3. Update `AUTH_KEY` at the top of `Code.gs` to match your backend `AUTH_KEY` from Step 1. Leave `DEFAULT_SERVER_URL` empty (`""`) if you manage backend servers in the FreeDnet app.
4. Click **Deploy -> New deployment -> Web app**:
   - **Execute as:** `Me`
   - **Who has access:** `Anyone`
5. Authorize the script and copy the generated Web App `/exec` URL.

> **Important:** Whenever you edit `Code.gs` later, go to **Deploy -> Manage deployments -> Edit (pencil) -> Version: New version -> Deploy**, otherwise `/exec` will keep running the old version.

---

### Step 3: Run or Build the Windows 11 Client

#### Option A: Run from Source
1. Install Python 3.10+ on Windows.
2. Install dependencies and launch:
   ```bat
   cd client
   pip install -r requirements.txt
   python freednet_client.py
   ```
   Or simply run `client\run.bat`.

#### Option B: Build Standalone `.exe` & Windows Installer
1. Run `client\build_exe.bat` to generate `client\dist\FreeDnet.exe`.
2. *(Optional)* Compile `client\installer.iss` with **Inno Setup 6** to build a Windows Setup wizard (grants Administrator privileges once during installation for zero-UAC TUN launches).

---

## Quick Usage in the App

1. Open **Settings & Logs**, paste your **Auth Key**, and choose your preferred **Theme** and **Language**.
2. Open **GAS Accounts**, click **+ Add GAS**, and add your `/exec` URL(s).
3. Open **Backend Servers**, click **+ Add Server**, and add your backend URL(s) from Step 1.
4. Go to **IP & Diagnostics** and click **Ping All Relays & Servers** to verify everything is `Ready`.
5. Click **▶ Start Proxy** (listens on `127.0.0.1:1080` for both SOCKS5 & HTTP), then turn on **Sys Proxy** or **TUN Mode** as needed.

---

## License

This project is licensed under the [MIT License](LICENSE).
