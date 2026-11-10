#!/usr/bin/env bash
# Installs the update command and a daily update check. Called by the other installers.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"

install -d -m 755 /opt/mindful-fleet /etc/mindful-fleet
install -m 644 "$SOURCE/VERSION" /opt/mindful-fleet/VERSION
install -m 755 "$SOURCE/install/mindful-fleet" /usr/local/bin/mindful-fleet
[ -f /etc/mindful-fleet/update.conf ] || printf 'AUTO_UPDATE=on\nSKIP_VERSION=\nINTERVAL=daily\n' > /etc/mindful-fleet/update.conf

cat > /etc/systemd/system/mindful-fleet-update.service <<'UNIT'
[Unit]
Description=Mindful Fleet update check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mindful-fleet update --auto
UNIT

# A random delay keeps a whole fleet from restarting at the same moment.
# "mindful-fleet every" changes the schedule with a separate file that updates don't touch.
cat > /etc/systemd/system/mindful-fleet-update.timer <<'UNIT'
[Unit]
Description=Daily Mindful Fleet update check

[Timer]
OnCalendar=daily
RandomizedDelaySec=6h
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now mindful-fleet-update.timer >/dev/null 2>&1
