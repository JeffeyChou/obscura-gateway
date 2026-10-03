# Changelog

## 0.1.0 — first public release

- One isolated Obscura browser container per MCP session, behind one authenticated Streamable-HTTP endpoint.
- Per-machine / per-agent bearer tokens with hot reload, revoke and rename; sessions owned by their token.
- Pool cap, per-token cap, idle reaping, request body limit.
- Self-healing browsers with crash logs (exit code, OOM, last URLs, container logs); `obscura-gw crashes`.
- `browser_pdf_text` and automatic replacement of raw PDF output, via a sandboxed curl + pdftotext container.
- Egress firewall for the browser network (public internet only).
- Client onboarding: `obscura-gw add-token` prints copy-paste setup; `/setup.sh` configures Claude Code, Codex and Pi.
- Deployment with Docker Compose or `deploy/install.sh` (systemd, hardened unit); graceful SIGTERM shutdown.
- Supports Obscura 0.2.3+ (per-browser `OBSCURA_MCP_TOKEN`).
