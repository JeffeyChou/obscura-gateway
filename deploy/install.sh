#!/usr/bin/env bash
# Obscura Gateway: direct (systemd) install on a Linux host with Docker.
#
#   sudo ./deploy/install.sh [--public-base URL] [--host ADDR] [--port N]
#                            [--no-firewall] [--no-systemd] [--first-token NAME]
#
# Installs to /opt/obscura-gateway (code, read-only), /etc/obscura-gateway
# (config + tokens), /var/lib/obscura-gateway (crash logs); runs the gateway as
# the system user `obscura-gw` (member of the docker group). Idempotent: re-run
# to upgrade; existing config and tokens are kept.
set -euo pipefail

PREFIX=/opt/obscura-gateway
ETC=/etc/obscura-gateway
STATE=/var/lib/obscura-gateway
SVC_USER=obscura-gw
NETWORK=obscura-net
BRIDGE=br-obscura
SUBNET=172.31.250.0/24
PUBLIC_BASE="" HOST="" PORT="" FIREWALL=1 SYSTEMD=1 FIRST_TOKEN=""

while [ $# -gt 0 ]; do
  case "$1" in
    --public-base) PUBLIC_BASE=$2; shift 2;;
    --host) HOST=$2; shift 2;;
    --port) PORT=$2; shift 2;;
    --no-firewall) FIREWALL=0; shift;;
    --no-systemd) SYSTEMD=0; shift;;
    --first-token) FIRST_TOKEN=$2; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown option: $1" >&2; exit 1;;
  esac
done

SRC=$(cd "$(dirname "$0")/.." && pwd)
step(){ printf '\033[1m==> %s\033[0m\n' "$*"; }
die(){ echo "error: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "run as root (sudo $0 ...)"
command -v docker >/dev/null || die "Docker is required: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "Docker daemon is not running"
command -v python3 >/dev/null || die "python3 (3.8+) is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || die "python3 >= 3.8 is required"
[ $SYSTEMD = 0 ] || command -v systemctl >/dev/null || die "systemd not found (use --no-systemd, or the Docker deployment)"

step "service user $SVC_USER"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir "$STATE" --shell /usr/sbin/nologin "$SVC_USER"
fi
usermod -aG docker "$SVC_USER"

step "files -> $PREFIX"
install -d -m 755 "$PREFIX" "$PREFIX/deploy" "$PREFIX/pdftext"
install -m 755 "$SRC/gateway.py" "$SRC/obscura-gw" "$PREFIX/"
install -m 644 "$SRC/client-setup.sh" "$PREFIX/"
install -m 644 "$SRC/pdftext/Dockerfile" "$SRC/pdftext/pdftext.sh" "$PREFIX/pdftext/"
install -m 755 "$SRC/deploy/firewall.sh" "$PREFIX/deploy/"
ln -sf "$PREFIX/obscura-gw" /usr/local/bin/obscura-gw

step "config -> $ETC/gateway.env"
install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$ETC"
install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$STATE" "$STATE/crashes"
if [ ! -f "$ETC/gateway.env" ]; then
  install -m 640 -o "$SVC_USER" -g "$SVC_USER" "$SRC/deploy/gateway.env.example" "$ETC/gateway.env"
  echo "    created from template"
else
  echo "    kept existing config and tokens"
fi
setkey(){  # setkey KEY VALUE  (only when a value was given on the command line)
  [ -n "$2" ] || return 0
  if grep -q "^$1=" "$ETC/gateway.env"; then sed -i "s#^$1=.*#$1=$2#" "$ETC/gateway.env"
  else echo "$1=$2" >> "$ETC/gateway.env"; fi
}
setkey OBSCURA_GW_PUBLIC_BASE "$PUBLIC_BASE"
setkey OBSCURA_GW_HOST "$HOST"
setkey OBSCURA_GW_PORT "$PORT"
chown "$SVC_USER:$SVC_USER" "$ETC/gateway.env"; chmod 640 "$ETC/gateway.env"

step "docker network $NETWORK ($BRIDGE, $SUBNET)"
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  docker network create --driver bridge --subnet "$SUBNET" \
    -o com.docker.network.bridge.name="$BRIDGE" "$NETWORK" >/dev/null
fi

step "images"
img=$(grep -E '^OBSCURA_IMAGE=' "$ETC/gateway.env" | tail -1 | cut -d= -f2-)
docker pull -q "${img:-h4ckf0r0day/obscura:0.2.3}"
docker build -q -t obscura-gw-pdftext:1 "$PREFIX/pdftext" >/dev/null && echo "    built obscura-gw-pdftext:1"

if [ $SYSTEMD = 1 ]; then
  step "systemd units"
  install -m 644 "$SRC/deploy/obscura-gateway.service" /etc/systemd/system/
  if [ $FIREWALL = 1 ]; then
    command -v iptables >/dev/null || die "iptables not found (install it, or pass --no-firewall)"
    install -m 644 "$SRC/deploy/obscura-egress.service" /etc/systemd/system/
  fi
  systemctl daemon-reload
  [ $FIREWALL = 1 ] && systemctl enable --now obscura-egress.service >/dev/null
  systemctl enable obscura-gateway.service >/dev/null
  systemctl restart obscura-gateway.service
  sleep 2
  systemctl is-active --quiet obscura-gateway || { journalctl -u obscura-gateway -n 30 --no-pager; die "gateway failed to start"; }
elif [ $FIREWALL = 1 ]; then
  step "egress firewall (no systemd: re-run $PREFIX/deploy/firewall.sh after each reboot)"
  sh "$PREFIX/deploy/firewall.sh"
fi

step "done"
if [ -n "$FIRST_TOKEN" ]; then
  obscura-gw add-token "$FIRST_TOKEN"
else
  cat <<EOF
Next:
  sudo obscura-gw add-token my-laptop      # prints copy-paste client setup
  sudo obscura-gw status
Remote access: set OBSCURA_GW_PUBLIC_BASE in $ETC/gateway.env and put a tunnel or
reverse proxy (Cloudflare Tunnel, Tailscale, Caddy...) in front of
$(grep -E '^OBSCURA_GW_HOST=' "$ETC/gateway.env" | cut -d= -f2):$(grep -E '^OBSCURA_GW_PORT=' "$ETC/gateway.env" | cut -d= -f2). See docs/deployment.md.
EOF
fi
