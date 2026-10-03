# Deployment

Obscura Gateway runs on one Linux host with Docker. Pick one of two ways to run it, then decide how clients reach it.

- [Requirements](#requirements)
- [Option A: Docker Compose](#option-a-docker-compose)
- [Option B: direct install (systemd)](#option-b-direct-install-systemd)
- [Remote access](#remote-access)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)

## Requirements

| | |
|---|---|
| OS | Linux (tested on Debian 13, amd64) |
| Docker | Engine 24+ (Compose v2 for option A) |
| Python | 3.8+ on the host for option B (standard library only); bundled in the image for option A |
| iptables | For the egress firewall (optional, recommended) |
| Hardware | ~250 MB RAM per busy browser; CPU is usually the limit (4 cores ≈ 25 browsers) |

## Option A: Docker Compose

```bash
git clone https://github.com/<you>/obscura-gateway && cd obscura-gateway
mkdir -p config && cp deploy/gateway.env.example config/gateway.env
$EDITOR config/gateway.env          # optional: OBSCURA_GW_PUBLIC_BASE, OBSCURA_GW_HOST, ...
docker compose up -d --build
docker compose exec gateway obscura-gw add-token my-laptop
```

What runs:

| Service | Purpose |
|---|---|
| `gateway` | The gateway. Uses host networking and the host's Docker socket to start one browser container per session. |
| `pdftext` | Builds the `obscura-gw-pdftext:1` helper image, creates the `obscura-net` browser network, then exits. |
| `egress-firewall` | Applies `deploy/firewall.sh` on the host (needs `NET_ADMIN`) and stays idle, so the rules are reapplied whenever Docker starts it. Delete this service if you manage the firewall yourself. |

Everyday commands:

```bash
docker compose exec gateway obscura-gw status
docker compose exec gateway obscura-gw tokens
docker compose logs -f gateway
docker compose restart gateway
docker compose down            # stops the gateway; its browsers are closed on SIGTERM
```

Notes:

- `config/gateway.env` holds settings **and tokens**. Keep it private (`chmod 600`). Tokens are hot-reloaded, so `add-token` and `revoke-token` need no restart. Other settings need `docker compose restart gateway`.
- Crash logs go to `./data/crashes`. The gateway runs as root inside its container, so those files are owned by root on the host.
- If you change the network name, bridge name or subnet, set `OBSCURA_NETWORK`, `OBSCURA_BRIDGE` and `OBSCURA_SUBNET` for Compose **and** `OBSCURA_GW_NETWORK` in `config/gateway.env`.
- Host networking is required because the gateway talks to browsers on `127.0.0.1:<port>`. This works on Linux only.

## Option B: direct install (systemd)

```bash
git clone https://github.com/<you>/obscura-gateway && cd obscura-gateway
sudo ./deploy/install.sh --first-token my-laptop
```

`install.sh` is idempotent. Re-run it to upgrade; existing config and tokens are kept. It:

1. Creates the system user `obscura-gw` and adds it to the `docker` group.
2. Installs the code to `/opt/obscura-gateway` (root-owned, read-only) and links `/usr/local/bin/obscura-gw`.
3. Creates `/etc/obscura-gateway/gateway.env` from the template (mode 640, owned by `obscura-gw`) and `/var/lib/obscura-gateway/crashes`.
4. Creates the Docker network `obscura-net` (bridge `br-obscura`, `172.31.250.0/24`).
5. Pulls the Obscura image and builds `obscura-gw-pdftext:1`.
6. Installs and starts `obscura-gateway.service` (hardened: `ProtectSystem=strict`, `NoNewPrivileges`, …) and `obscura-egress.service` (the firewall).

Options:

| Option | Effect |
|---|---|
| `--public-base URL` | Sets `OBSCURA_GW_PUBLIC_BASE` (the URL clients use) |
| `--host ADDR` / `--port N` | Listening address / port (default `127.0.0.1:8080`) |
| `--first-token NAME` | Creates a token and prints the client setup at the end |
| `--no-firewall` | Skips the egress firewall |
| `--no-systemd` | Installs files only; you start `gateway.py` yourself |

Everyday commands:

```bash
sudo obscura-gw status
sudo obscura-gw add-token lab-server
sudo obscura-gw logs -f
sudo systemctl restart obscura-gateway
```

## Remote access

The gateway listens on `127.0.0.1` by default. Choose one way to make it reachable, then set `OBSCURA_GW_PUBLIC_BASE` to the URL clients will use. `add-token` prints that URL, and `/setup.sh` bakes it in.

### Cloudflare Tunnel (public HTTPS, no open ports)

1. Install `cloudflared` on the host and create a tunnel (Zero Trust → Networks → Tunnels).
2. Add a **Public Hostname**, e.g. `browser.example.com`:
   - **Service type `HTTP`** (not HTTPS: the gateway speaks plain HTTP to the local tunnel)
   - URL `127.0.0.1:8080`
3. Set `OBSCURA_GW_PUBLIC_BASE=https://browser.example.com`.

Caveats:

- Cloudflare's Browser Integrity Check rejects the default `Python-urllib` User-Agent (error 1010). MCP clients are fine; scripts should send their own User-Agent.
- Don't put Cloudflare Access in front unless your clients can send its extra headers. The bearer token is the authentication.

### Tailscale / WireGuard (private)

Set `OBSCURA_GW_HOST` to the host's VPN address, e.g. `100.x.y.z`, and `OBSCURA_GW_PUBLIC_BASE=http://100.x.y.z:8080`. Traffic is encrypted by the VPN. Clients must be on the VPN.

### Reverse proxy (Caddy, nginx)

Proxy `https://browser.example.com` to `http://127.0.0.1:8080`. Don't buffer responses, and allow at least 180 s per request (page loads can be slow). Caddy example:

```caddyfile
browser.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
        transport http {
            read_timeout 200s
        }
    }
}
```

### LAN only

`OBSCURA_GW_HOST=0.0.0.0` accepts direct connections on every interface. Tokens still apply, but traffic is plain HTTP. Use this only on a trusted network.

## Upgrading

```bash
git pull
docker compose up -d --build            # option A
sudo ./deploy/install.sh                # option B
```

To move to a newer Obscura image, change `OBSCURA_IMAGE` (pin a version tag, not `latest`), restart, and run the end-to-end tests. See [operations.md](operations.md#upgrading-obscura).

## Uninstalling

```bash
docker compose down                     # option A (add -v and delete ./config ./data to remove everything)
sudo ./deploy/uninstall.sh              # option B: keeps /etc/obscura-gateway (tokens)
sudo ./deploy/uninstall.sh --purge      # option B: also config, crash logs, user, network, helper image
```
