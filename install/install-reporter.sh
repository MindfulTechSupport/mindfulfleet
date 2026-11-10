#!/usr/bin/env bash
# Installs the Mindful Fleet reporter as a service. Run with sudo.
# Usage: sudo ./install-reporter.sh http://HUB-ADDRESS:8742
# It reads the fleet token from MINDFUL_FLEET_TOKEN, reuses an installed one, or asks.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo." >&2; exit 1; }
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
HUB="${1:-}"
case "$HUB" in
  http://*|https://*) ;;
  *) echo "Usage: sudo $0 http://HUB-ADDRESS:8742" >&2; exit 1 ;;
esac

TOKEN="${MINDFUL_FLEET_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f /etc/mindful-fleet/reporter.env ]; then
  TOKEN="$(sed -n 's/^MINDFUL_FLEET_TOKEN=//p' /etc/mindful-fleet/reporter.env)"
fi
if [ -z "$TOKEN" ]; then
  read -rp "Fleet token: " TOKEN </dev/tty
fi
[ -n "$TOKEN" ] || { echo "A fleet token is required." >&2; exit 1; }

id mindful-fleet >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mindful-fleet
# vcgencmd, which reports throttling, needs the video group.
getent group video >/dev/null && usermod -aG video mindful-fleet

install -d -m 755 /opt/mindful-fleet/reporter
install -m 755 "$SOURCE"/reporter/mindful_fleet_reporter.py /opt/mindful-fleet/reporter/

install -d -m 750 -o root -g mindful-fleet /etc/mindful-fleet
umask 027
# Keep any other settings someone added, such as sensor names or the XMRig address.
EXTRA=""
if [ -f /etc/mindful-fleet/reporter.env ]; then
  EXTRA="$(grep -vE '^MINDFUL_FLEET_(HUB|TOKEN)=' /etc/mindful-fleet/reporter.env || true)"
fi
{
  echo "MINDFUL_FLEET_HUB=$HUB"
  echo "MINDFUL_FLEET_TOKEN=$TOKEN"
  if [ -n "$EXTRA" ]; then printf '%s\n' "$EXTRA"; fi
} > /etc/mindful-fleet/reporter.env
chown root:mindful-fleet /etc/mindful-fleet/reporter.env
chmod 640 /etc/mindful-fleet/reporter.env

# Device access stays on, because vcgencmd talks to the firmware through /dev/vcio.
cat > /etc/systemd/system/mindful-fleet-reporter.service <<'EOF'
[Unit]
Description=Mindful Fleet reporter
After=network-online.target
Wants=network-online.target

[Service]
User=mindful-fleet
EnvironmentFile=/etc/mindful-fleet/reporter.env
ExecStart=/usr/bin/python3 /opt/mindful-fleet/reporter/mindful_fleet_reporter.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

bash "$SOURCE/install/install-updater.sh"
systemctl daemon-reload
systemctl enable mindful-fleet-reporter >/dev/null
systemctl restart mindful-fleet-reporter

set -a; . /etc/mindful-fleet/reporter.env; set +a
if runuser -u mindful-fleet --preserve-environment -- /usr/bin/python3 /opt/mindful-fleet/reporter/mindful_fleet_reporter.py --once; then
  echo "Reporter installed. $(hostname) is checking in with $HUB"
else
  echo "Reporter installed, but this first report didn't get through. Check the hub address and token." >&2
  exit 1
fi
