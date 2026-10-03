# Obscura Gateway

**One authenticated MCP endpoint that gives every AI agent its own isolated headless browser, from any machine.**

[中文文档](README.zh-CN.md) · [Deployment](docs/deployment.md) · [Clients](docs/clients.md) · [Configuration](docs/configuration.md) · [Security](docs/security.md) · [Operations](docs/operations.md)

Obscura Gateway sits in front of [Obscura](https://hub.docker.com/r/h4ckf0r0day/obscura), a fast headless browser with an MCP server. It runs **one Obscura container per agent session**, puts authentication, routing, lifecycle management and crash recovery in front of them, and exposes everything as a single Streamable-HTTP MCP endpoint. Claude Code, Codex, Pi, Cursor and other MCP clients connect with a URL and a token.

```
 laptop: Claude Code ─┐                                   ┌─▶ browser container  (session 1)
 laptop: Codex ───────┤   HTTPS    ┌──────────────────┐   ├─▶ browser container  (session 2)
 server: Pi ──────────┼──────────▶ │  Obscura Gateway │ ──┼─▶ browser container  (session 3)
 CI job: any MCP ─────┘  + token   │  auth · routing  │   └─▶ ...  (pool, idle-reaped)
                                   │  lifecycle · PDF │
                                   └──────────────────┘   egress firewall: public internet only
```

## Why

Obscura is a good browser for agents. Running it for several agents on several machines raises problems it does not solve on its own:

| Pain point with plain Obscura | What the gateway does |
|---|---|
| `obscura mcp --http` shares **one browser** between all clients: agent B sees agent A's cookies and current page | A dedicated browser container per MCP session; sessions are owned by the token that opened them |
| One static token (`OBSCURA_MCP_TOKEN`) for everyone, so access can't be revoked per agent | One token per machine or per agent, hot-reloaded, revocable in one command |
| Every machine needs its own Obscura binary and setup | One server; clients only need a URL and a token. `add-token` prints copy-paste setup for Claude Code, Codex and Pi |
| A browser crash takes the server down for all clients and leaves no trace | The crashed session is respawned on the next request; exit code, OOM flag, last URLs and logs are saved |
| PDFs come back as raw, truncated `%PDF-` bytes | `browser_pdf_text` returns the text, page by page, using a sandboxed extractor; raw PDF output is replaced automatically |
| Exposing a browser to the network is risky (SSRF) | Browsers run on a dedicated Docker network whose egress firewall allows the public internet only (no LAN, VPN, metadata or host) |
| Manual setup | `docker compose up -d` or `sudo ./deploy/install.sh` |

## Comparison

| | Obscura `mcp` (stdio) | Obscura `mcp --http` | **Obscura Gateway** |
|---|:---:|:---:|:---:|
| One-command server deployment | — | `docker run` | ✅ `docker compose up` / `install.sh` |
| Use from other machines | ❌ local only | ✅ | ✅ (tunnel / VPN / reverse proxy recipes) |
| Browser isolation between clients | per local process | ❌ one shared browser | ✅ one container per session |
| Many agents at once | each runs its own browser locally | ❌ they overwrite each other's page | ✅ pooled (default 25, 5 per token) |
| Authentication | n/a | one shared token | ✅ per-machine / per-agent tokens |
| Revoke one agent | n/a | ❌ rotate the shared token | ✅ `obscura-gw revoke-token <name>` |
| Add / remove tokens without restart | n/a | ❌ | ✅ hot reload |
| Session ownership (others can't use your session id) | n/a | ❌ | ✅ |
| Browser crash recovery | restart the client | container down for everyone | ✅ per-session respawn |
| Crash diagnostics | — | container logs (if kept) | ✅ exit code, OOM, last 10 URLs, logs |
| Idle cleanup / resource caps | — | — | ✅ idle reap, pool cap, per-token cap, body limit |
| PDF → text | ❌ raw bytes | ❌ raw bytes | ✅ `browser_pdf_text` + automatic replacement |
| Private-network (SSRF) protection | app-level | app-level | app-level **+ network egress firewall** |
| Client onboarding | manual | manual | ✅ `curl …/setup.sh \| sh -s -- <token>` |
| Dependencies on the server | Obscura binary | Docker | Docker + Python 3.8 (stdlib only) |

The isolation row was measured: two clients of one `obscura mcp --http` container (v0.2.3) received no session id, and a cookie set by the first was visible to the second, as was its current page.

## Quick start

You need a Linux host with Docker.

### Option A: Docker Compose

```bash
git clone https://github.com/JeffeyChou/obscura-gateway && cd obscura-gateway
mkdir -p config && cp deploy/gateway.env.example config/gateway.env
docker compose up -d --build
docker compose exec gateway obscura-gw add-token my-laptop
```

### Option B: direct install (systemd)

```bash
git clone https://github.com/JeffeyChou/obscura-gateway && cd obscura-gateway
sudo ./deploy/install.sh --first-token my-laptop
```

Both listen on `127.0.0.1:8080` by default. To use the gateway from other machines, set `OBSCURA_GW_PUBLIC_BASE` and put a tunnel, VPN or reverse proxy in front of it (see [docs/deployment.md](docs/deployment.md#remote-access)).

### Connect your agents

`add-token` prints everything needed on the client machine. The shortest path:

```bash
curl -fsSL https://browser.example.com/setup.sh | sh -s -- <TOKEN>
```

This checks the token, then configures every supported CLI it finds:

- **Claude Code**: `claude mcp add --scope user --transport http obscura-remote …`
- **Codex**: `codex mcp add obscura-remote --url … --bearer-token-env-var OBSCURA_REMOTE_TOKEN`
- **Pi**: `~/.config/mcp/mcp.json`

Any other MCP client needs:

```
Transport: Streamable HTTP
URL:       https://browser.example.com/mcp
Header:    Authorization: Bearer <TOKEN>
```

Clients that only support stdio can use the bridge `npx -y mcp-remote <URL> --header "Authorization: Bearer <TOKEN>"`. See [docs/clients.md](docs/clients.md).

## Tokens: per machine or per agent

A token is an identity, not a browser. Every MCP session still gets its own browser, so several agents on one machine can share a token and stay isolated from each other. Agents that share a token also share:

- its name in the logs,
- its session cap (`OBSCURA_GW_MAX_PER_AGENT`, default 5),
- its revocation.

| | One token per machine | One token per agent |
|---|---|---|
| Setup | once per machine | once per agent |
| Revoke | whole machine | a single agent |
| Logs show | machine | agent |
| Session cap | shared by the machine | per agent |

```bash
obscura-gw add-token lab-server        # prints copy-paste client setup
obscura-gw setup lab-server            # print it again later
obscura-gw tokens                      # list (masked)
obscura-gw revoke-token lab-server     # immediate; its browsers are closed
```

## Tools

All Obscura browser tools are available (`browser_navigate`, `browser_markdown`, `browser_click`, `browser_fill_form`, `browser_screenshot`, `browser_evaluate`, cookies, tabs and more; 37 in v0.2.3). The gateway adds:

| Tool | Description |
|---|---|
| `browser_pdf_text` | Text of a PDF (`url`, `first_page`, `last_page`, `offset`, `max_chars`). Defaults to the current page. Long documents are paged with `offset`. |

If any tool returns raw PDF bytes, for example `browser_markdown` on a PDF URL, the gateway replaces them with the extracted text.

## Capacity

One browser uses about 2 MB idle and about 250 MB on heavy pages, measured with Obscura 0.2.3. On a 4-core / 16 GB host, CPU is the limit before memory; the default pool of 25 concurrent browsers is a reasonable starting point. Idle browsers are reaped after 15 minutes.

## Security in one paragraph

Tokens are 192-bit random values, compared in constant time. Auth fails closed when no tokens are configured. Each session is reachable only with the token that opened it. Browser containers can reach the public internet but not your LAN, VPN, cloud metadata or the host. PDFs are downloaded and parsed in a throwaway container that is read-only, has no capabilities and runs as `nobody`. The gateway itself needs the Docker socket, which is root-equivalent, so run it on a host you control. Read [docs/security.md](docs/security.md) before exposing it publicly.

## Limitations

- Linux hosts only (the Docker deployment uses host networking; macOS and Windows have not been tested).
- Sites with strong bot protection (Cloudflare challenges, AWS WAF, Anubis, Google CAPTCHAs) still block Obscura. `OBSCURA_GW_STEALTH=1` made no difference on the sites we tested.
- `browser_pdf_text` fetches without the browser's cookies, so PDFs behind a login fail with a clear error.
- Sessions live in memory: restarting the gateway closes every browser.
- One host, no horizontal scaling.

## Project layout

```
gateway.py            the gateway (single file, Python stdlib)
obscura-gw            admin CLI (tokens, status, sessions, crashes, setup)
client-setup.sh       client installer, served at /setup.sh
pdftext/              sandboxed PDF -> text helper image
deploy/               install.sh, uninstall.sh, systemd units, firewall.sh, config template
docker-compose.yml    Docker deployment
tests/test_gateway.py end-to-end tests against a running gateway
docs/                 deployment, clients, configuration, security, operations, architecture
```

## Contributing

Issues and pull requests are welcome. Run the end-to-end tests against a gateway before sending a change:

```bash
python3 tests/test_gateway.py http://127.0.0.1:8080/mcp /etc/obscura-gateway/gateway.env "$(command -v obscura-gw)"
```

## License and credits

MIT, see [LICENSE](LICENSE).

[Obscura](https://hub.docker.com/r/h4ckf0r0day/obscura) is a separate project by its own authors, licensed under the Apache License 2.0. Obscura Gateway does not include or modify Obscura code; it pulls and runs Obscura's published Docker image at runtime, and is not affiliated with the Obscura project.
