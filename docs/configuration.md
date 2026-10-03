# Configuration

All settings are `KEY=VALUE` lines in the config file. Its location depends on how you deployed:

- Direct install: `/etc/obscura-gateway/gateway.env`.
- Docker: `./config/gateway.env`.
- Elsewhere: point `OBSCURA_GW_ENV_FILE` at your own path.

Real environment variables override the file. Tokens (`OBSCURA_GW_TOKENS`) are re-read whenever the file changes. Every other setting is read at startup, so restart the gateway after changing it.

The template with comments is [`deploy/gateway.env.example`](../deploy/gateway.env.example).

## Network

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_GW_HOST` | `127.0.0.1` | Listening address. `0.0.0.0` for every interface, or a VPN address. |
| `OBSCURA_GW_PORT` | `8080` | Listening port. |
| `OBSCURA_GW_PUBLIC_BASE` | *(derived)* | URL clients use, e.g. `https://browser.example.com`. Printed by `add-token` and baked into `/setup.sh`. If empty, the gateway derives it from the request's `Host` / `X-Forwarded-*` headers. |
| `OBSCURA_GW_MAX_BODY` | `8388608` | Maximum request body in bytes (larger requests get `413`). |

## Browsers

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_IMAGE` | `h4ckf0r0day/obscura:0.2.3` | Browser image. Pin a version tag. |
| `OBSCURA_GW_NETWORK` | *(empty)* | Docker network for browsers (`obscura-net` in the template). Should be the network covered by the egress firewall. |
| `OBSCURA_GW_MAXPOOL` | `25` | Maximum concurrent browsers in total. |
| `OBSCURA_GW_MAX_PER_AGENT` | `5` | Maximum concurrent browsers per token. |
| `OBSCURA_GW_IDLE` | `900` | Seconds without requests before a browser is closed. |
| `OBSCURA_GW_SPAWN_WAIT` | `25` | Seconds to wait for a new browser to become healthy. |
| `OBSCURA_GW_STEALTH` | `0` | `1` passes `--stealth` to Obscura. |
| `OBSCURA_GW_DOCKER_ARGS` | *(empty)* | Extra `docker run` options per browser, e.g. `--memory 1g --pids-limit 512`. |
| `OBSCURA_GW_OBSCURA_ARGS` | *(empty)* | Extra Obscura flags placed before `mcp`, e.g. `-v`. |
| `OBSCURA_GW_PORT_BASE` | `8100` | First host port for browsers (bound to `OBSCURA_GW_BACKEND_BIND` only). |
| `OBSCURA_GW_PORT_SPAN` | `200` | Size of that port range. |
| `OBSCURA_GW_BACKEND_BIND` | `127.0.0.1` | Host address browser ports are published on. Keep it on loopback. |
| `OBSCURA_GW_LABEL` | `obscura-gw` | Docker label for this gateway's containers. Change it to run two gateways on one host. |

Each browser container receives a random `OBSCURA_MCP_TOKEN`, which Obscura 0.2.3+ requires. The gateway uses that token to talk to the browser, so nothing else on the host can.

## PDF text extraction

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_GW_PDF_IMAGE` | `obscura-gw-pdftext:1` | Helper image (built from `pdftext/`). |
| `OBSCURA_GW_PDF_TIMEOUT` | `120` | Seconds per extraction (download + parse). |
| `OBSCURA_GW_PDF_CHARS` | `60000` | Default characters returned per `browser_pdf_text` call. |

## Diagnostics

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_GW_CRASH_DIR` | `<gateway dir>/crashes` | Where crash logs go (the template sets `/var/lib/obscura-gateway/crashes`). |
| `OBSCURA_GW_CRASH_KEEP` | `50` | Number of crash logs kept. |

## Tokens

| Variable | Description |
|---|---|
| `OBSCURA_GW_TOKENS` | `token:name,token:name,…`. Managed by `obscura-gw`; edit by hand only if you must. An unnamed token gets the label `tok-<sha256[:8]>`. An empty value rejects every request. |

## CLI-only variables

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_GW_ENV_FILE` | `/etc/obscura-gateway/gateway.env` | Config file used by both the gateway and `obscura-gw`. |
| `OBSCURA_GW_SERVICE` | `obscura-gateway` | systemd unit name used by `obscura-gw status/logs/restart`. |

## Docker Compose variables

These are set in the shell or a `.env` file next to `docker-compose.yml`:

| Variable | Default | Description |
|---|---|---|
| `OBSCURA_NETWORK` | `obscura-net` | Browser network name (must equal `OBSCURA_GW_NETWORK`). |
| `OBSCURA_BRIDGE` | `br-obscura` | Linux bridge name (used by the firewall). |
| `OBSCURA_SUBNET` | `172.31.250.0/24` | Browser network subnet. |
