#!/bin/sh
# Egress firewall for Obscura Gateway browser containers (root; idempotent).
#
# Browsers (and the PDF helper) run on a dedicated Docker bridge. This allows
# them to reach the public internet only: no LAN, no VPN/tailnet (100.64/10),
# no link-local / cloud metadata, no other containers, no services on the host.
# Obscura already refuses private IPs at the application level; this is the
# network-level backstop (DNS rebinding, redirects, future bugs).
#
#   sh firewall.sh            apply (bridge: $OBSCURA_BRIDGE, default br-obscura)
#   sh firewall.sh --remove   remove the rules for that bridge
set -eu
BR=${OBSCURA_BRIDGE:-br-obscura}
CH=OBSCURA-EGRESS

if [ "${1:-}" = "--remove" ]; then
  while iptables -D DOCKER-USER -i "$BR" -j $CH 2>/dev/null; do :; done
  while iptables -D INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP 2>/dev/null; do :; done
  # drop the shared chain only when no bridge uses it any more
  if ! iptables -S DOCKER-USER 2>/dev/null | grep -q -- "-j $CH"; then
    iptables -F $CH 2>/dev/null || true; iptables -X $CH 2>/dev/null || true
  fi
  echo "obscura egress firewall removed for $BR"; exit 0
fi

iptables -L DOCKER-USER -n >/dev/null 2>&1 || { echo "DOCKER-USER chain missing: is Docker running?" >&2; exit 1; }
iptables -N $CH 2>/dev/null || iptables -F $CH
iptables -A $CH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 \
           172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/3; do
  iptables -A $CH -d $net -j DROP
done
iptables -A $CH -j RETURN
# forwarded traffic (other hosts, VPN, other containers)
iptables -C DOCKER-USER -i "$BR" -j $CH 2>/dev/null || iptables -I DOCKER-USER -i "$BR" -j $CH
# traffic to the host itself (the gateway, sshd, local services)
iptables -C INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP 2>/dev/null || \
  iptables -I INPUT -i "$BR" -m conntrack --ctstate NEW -j DROP
echo "obscura egress firewall active on $BR"
