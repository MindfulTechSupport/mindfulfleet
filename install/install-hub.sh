#!/usr/bin/env bash
# Installs the Mindful Fleet hub and dashboard as a service. Run with sudo.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo." >&2; exit 1; }
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8742}"
case "$PORT" in ''|*[!0-9]*) echo "Port must be a number." >&2; exit 1 ;; esac

id mindful-fleet >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mindful-fleet

install -d -m 755 /opt/mindful-fleet/hub/dashboard
install -m 644 "$SOURCE"/hub/health.py /opt/mindful-fleet/hub/
install -m 755 "$SOURCE"/hub/mindful_fleet_hub.py /opt/mindful-fleet/hub/
install -m 644 "$SOURCE"/hub/dashboard/* /opt/mindful-fleet/hub/dashboard/

install -d -m 750 -o root -g mindful-fleet /etc/mindful-fleet
if [ -f /etc/mindful-fleet/hub.env ]; then
  TOKEN="$(sed -n 's/^MINDFUL_FLEET_TOKEN=//p' /etc/mindful-fleet/hub.env)"
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi
umask 027
cat > /etc/mindful-fleet/hub.env <<EOF
MINDFUL_FLEET_TOKEN=$TOKEN
MINDFUL_FLEET_PORT=$PORT
MINDFUL_FLEET_STATE=/var/lib/mindful-fleet/fleet.json
EOF
chown root:mindful-fleet /etc/mindful-fleet/hub.env
chmod 640 /etc/mindful-fleet/hub.env

cat > /etc/systemd/system/mindful-fleet-hub.service <<'EOF'
[Unit]
Description=Mindful Fleet hub and dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=mindful-fleet
EnvironmentFile=/etc/mindful-fleet/hub.env
ExecStart=/usr/bin/python3 /opt/mindful-fleet/hub/mindful_fleet_hub.py
StateDirectory=mindful-fleet
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mindful-fleet-hub >/dev/null
systemctl restart mindful-fleet-hub

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Hub installed."
echo "Dashboard:  http://$IP:$PORT/"
echo "Hub address for each reporter:  http://$IP:$PORT"
echo "Fleet token (keep this private):  $TOKEN"
