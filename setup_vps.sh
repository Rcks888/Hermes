#!/bin/bash
# ============================================================
# Hermes VPS Setup — Step 0
# Installs Wine, Xvfb, MT5, and Python dependencies
# Run on: Ubuntu 24.04 VPS (see HERMES_BRIEF.md for host)
# ============================================================

set -e

echo "🔱 HERMES — VPS Setup Starting"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- 1. Enable 32-bit architecture (required by Wine) ---
echo "[1/6] Enabling 32-bit architecture..."
sudo dpkg --add-architecture i386
sudo apt-get update -y

# --- 2. Install Wine (WineHQ stable, not the Ubuntu package) ---
# Ubuntu noble ships Wine 9.0. MT5 build 6271 (17 Oct 2026) stopped
# initialising on it: a "debugger found" dialog blocked the IPC pipe, and
# MetaQuotes asks for Wine 10.0 or later. WineHQ stable (11.0) fixed it.
echo "[2/6] Installing Wine from WineHQ..."
sudo mkdir -pm755 /etc/apt/keyrings
sudo wget -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
sudo wget -NP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources
sudo apt-get update -y
sudo apt-get install -y --install-recommends winehq-stable winbind
# --install-recommends pulls in avahi-daemon, which opens UDP 5353. Not needed.
sudo systemctl disable --now avahi-daemon.service avahi-daemon.socket || true

echo "Wine version:"
wine --version
# After any Wine upgrade, update an existing prefix without the Mono/Gecko
# installer dialogs, which otherwise block headless on Xvfb:
#   WINEDLLOVERRIDES="mscoree,mshtml=" DISPLAY=:99 wineboot -u; wineserver -w

# --- 3. Install Xvfb (virtual display for headless MT5) ---
echo "[3/6] Installing Xvfb..."
sudo apt-get install -y xvfb

# --- 4. Install Python dependencies ---
echo "[4/6] Installing Python venv + dependencies..."
sudo apt-get install -y python3-venv python3-pip

cd ~/Olympus/Hermes
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.lock

# --- 5. Download and install MT5 via Wine ---
echo "[5/6] Downloading MetaTrader 5..."
MT5_INSTALLER="mt5setup.exe"
if [ ! -f "$MT5_INSTALLER" ]; then
    wget -O "$MT5_INSTALLER" "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "MT5 installer downloaded."
echo ""
echo "Next steps (manual):"
echo ""
echo "  1. Start virtual display:"
echo "     export DISPLAY=:99"
echo "     Xvfb :99 -screen 0 1024x768x16 &"
echo ""
echo "  2. Install MT5 via Wine:"
echo "     wine mt5setup.exe"
echo "     (Follow the installer — choose OANDA as broker)"
echo ""
echo "  3. After install, find MT5 path:"
echo "     find ~/.wine -name 'terminal64.exe' 2>/dev/null"
echo "     Update config/settings.json with that path"
echo ""
echo "  4. Test MT5 launch:"
echo "     wine ~/.wine/drive_c/Program\\ Files/MetaTrader\\ 5/terminal64.exe &"
echo ""
echo "  5. Fill in config/settings.json:"
echo "     - mt5.login: your login ID"
echo "     - mt5.password: your trading password"
echo "     - mt5.server: your server name (e.g. OandaGlobal-Demo)"
echo "     - mt5.path: terminal64.exe path from step 3"
echo ""
echo "  6. Test the soak script:"
echo "     source venv/bin/activate"
echo "     python run_soak.py"
echo ""
echo "  7. If soak works, add cron:"
echo "     crontab -e"
echo "     */15 * * * 1-5 sleep 10 && cd ~/Olympus/Hermes && source venv/bin/activate && python run_soak.py >> logs/cron.log 2>&1"
echo ""
echo "[6/6] Setup complete! Follow manual steps above."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
