#!/usr/bin/env bash
# Remove a direct (systemd) Obscura Gateway install.
#   sudo ./deploy/uninstall.sh            keep config + tokens (/etc/obscura-gateway)
#   sudo ./deploy/uninstall.sh --purge    also delete config, tokens, crash logs, user, network, images
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1
systemctl disable --now obscura-gateway.service 2>/dev/null
systemctl disable --now obscura-egress.service 2>/dev/null
docker ps -aq --filter label=obscura-gw=1 | xargs -r docker rm -f >/dev/null
[ -f /opt/obscura-gateway/deploy/firewall.sh ] && sh /opt/obscura-gateway/deploy/firewall.sh --remove
rm -f /etc/systemd/system/obscura-gateway.service /etc/systemd/system/obscura-egress.service /usr/local/bin/obscura-gw
systemctl daemon-reload
rm -rf /opt/obscura-gateway
if [ $PURGE = 1 ]; then
  rm -rf /etc/obscura-gateway /var/lib/obscura-gateway
  docker network rm obscura-net >/dev/null 2>&1
  docker image rm obscura-gw-pdftext:1 >/dev/null 2>&1
  userdel obscura-gw 2>/dev/null
  echo "purged"
else
  echo "removed; config and tokens kept in /etc/obscura-gateway (use --purge to delete)"
fi
