# Security

Obscura Gateway gives remote agents a browser that runs on your server. This page covers what it protects against, what it doesn't, and what you need to do.

## Threat model

| Threat | Mitigation |
|---|---|
| Strangers using your browsers | Every `/mcp` request needs a bearer token: 192 random bits, compared in constant time. With no tokens configured, every request is rejected. |
| One agent reading another's browser | Every session gets its own container. A session id is accepted only with the token that created it; other tokens get `404`. `X-Agent-Id`-style headers are ignored. |
| A leaked token | `obscura-gw revoke-token <name>` takes effect on the next request, without a restart, and closes that token's browsers. Use a token per machine or per agent so a leak can be revoked on its own. |
| The browser used to reach internal services (SSRF) | Obscura refuses private addresses at the application level. On top of that, browsers run on a dedicated bridge, and `firewall.sh` drops their traffic to RFC 1918, CGNAT/VPN (100.64/10), loopback, link-local/metadata (169.254/16), multicast and the host itself. Only the public internet is reachable. |
| Malicious PDFs | PDFs are downloaded and parsed in a throwaway container: read-only root filesystem, 192 MB tmpfs, `--cap-drop ALL`, `no-new-privileges`, user `nobody`, 512 MB memory, 64 processes, same egress firewall, 100 MB download limit, 120 s timeout. Nothing is parsed on the host. |
| Resource exhaustion | Pool cap (25), per-token cap (5), idle reap (15 min), 8 MB request-body limit, a timeout on every backend call. |
| Other users on the gateway host talking to browsers directly | Browser ports are published on `127.0.0.1` only, and each browser requires its own random token. |
| Request smuggling or desync behind proxies | Request bodies are always read in full before auth decisions; error responses close the connection. |

## What you must do

1. **Treat the Docker socket as root.** The gateway starts containers, so whoever controls the gateway process or its config can control the host. Run it on a machine you trust, and don't give the `obscura-gw` user or the config file to anyone else.
2. **Protect the config file.** It holds the tokens. The installer creates it with mode `640`, owned by the service user. With Docker, `chmod 600 config/gateway.env`.
3. **Use TLS for remote access.** Cloudflare Tunnel, a reverse proxy with HTTPS, or a VPN. Don't send tokens over plain HTTP on untrusted networks.
4. **Keep the egress firewall on.** `firewall.sh` needs iptables and must be reapplied after a reboot. The systemd unit and the Compose service both do this.
5. **Rotate tokens.** Re-create a token whenever you aren't sure where it has been.
6. **Pin the Obscura image.** Upgrade on purpose and run the tests. See [operations.md](operations.md#upgrading-obscura).

## Things to know

- `/healthz` and `/setup.sh` need no authentication. `/healthz` reveals the number of live sessions and the image name. `/setup.sh` contains no secrets.
- `client-setup.sh` writes the token into client config files: `~/.claude.json`, your shell rc for Codex, and `~/.config/mcp/mcp.json` (mode 600). Passing the token as an argument leaves it in your shell history; run the installer without a token to type it in instead.
- The gateway logs each session's agent name and client IP (`CF-Connecting-IP` when behind Cloudflare), as well as rejected requests. Crash logs keep the last ten tool calls and URLs of a crashed session.
- Browsing goes out from your server's IP address. You are responsible for what agents do with it.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's private vulnerability reporting ("Security" tab → "Report a vulnerability"), or contact the maintainers privately.
