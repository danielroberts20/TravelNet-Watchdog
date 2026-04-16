# Watchdog

Monitors the TravelNet Raspberry Pi and takes automated recovery actions on failure.

## What it does

- Checks internet connectivity, Tailscale reachability, and TravelNet API health every 60 seconds
- Sends Pushcut alerts on failure
- Escalates through SSH docker restart → SSH reboot → Shelly power cycle
- Can wake or shut down the compute PC via WoL and SSH
- Runs as a systemd service on a dedicated Raspberry Pi 4B

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with real values
```

## Running manually

```bash
source .venv/bin/activate
python watchdog.py
```

## Installing as a systemd service

```bash
sudo cp watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable watchdog
sudo systemctl start watchdog
sudo systemctl status watchdog
```
