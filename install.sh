#!/usr/bin/env bash
# Mindful Fleet one-line installer. Downloads this project from GitHub and runs its installers.
#   hub                 install the dashboard on this computer
#   join HUB-URL TOKEN  install the reporter on this computer
#   update              update whatever is installed here and keep its settings
set -euo pipefail

REPO="MindfulTechSupport/mindful-fleet"
BRANCH="${MINDFUL_FLEET_CHANNEL:-release}"  # The release line only holds versions that are ready.

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo." >&2; exit 1; }
ACTION="${1:-}"
case "$ACTION" in
  hub|update) ;;
  join) [ $# -eq 3 ] || { echo "Usage: join HUB-URL TOKEN" >&2; exit 1; } ;;
  *) echo "Usage: install.sh hub | join HUB-URL TOKEN | update" >&2; exit 1 ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$WORK" --strip-components=1

case "$ACTION" in
  hub)
    bash "$WORK/install/install-hub.sh"
    ;;
  join)
    MINDFUL_FLEET_TOKEN="$3" bash "$WORK/install/install-reporter.sh" "$2"
    ;;
  update)
    FOUND=no
    if [ -f /etc/mindful-fleet/hub.env ]; then
      PORT="$(sed -n 's/^MINDFUL_FLEET_PORT=//p' /etc/mindful-fleet/hub.env)"
      bash "$WORK/install/install-hub.sh" "${PORT:-8742}" >/dev/null
      echo "Dashboard updated."
      FOUND=yes
    fi
    if [ -f /etc/mindful-fleet/reporter.env ]; then
      HUB="$(sed -n 's/^MINDFUL_FLEET_HUB=//p' /etc/mindful-fleet/reporter.env)"
      bash "$WORK/install/install-reporter.sh" "$HUB"
      FOUND=yes
    fi
    [ "$FOUND" = yes ] || { echo "Nothing is installed here yet. Use hub or join." >&2; exit 1; }
    ;;
esac
