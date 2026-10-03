# Operations

## The `obscura-gw` CLI

Run it on the gateway host: `sudo obscura-gw …` for a direct install, `docker compose exec gateway obscura-gw …` for Docker.

| Command | Description |
|---|---|
| `status` | Service state, health, token count, live browsers |
| `sessions` | Live browser containers (`docker ps`) |
| `logs [-f]` | Gateway log (journald; with Docker, use `docker compose logs -f gateway`) |
| `add-token <name> [-q]` | Create a token and print copy-paste client setup (`-q`: the token only) |
| `setup <name>` | Print the client setup again for an existing token |
| `tokens` | List tokens (masked) and their names |
| `revoke-token <name>` | Delete a token now; its browsers are closed |
| `rename-token <old> <new>` | Rename a token (its live sessions are closed) |
| `crashes [N]` | List the latest N crash logs: exit code, OOM flag, last URL |
| `crashes show [FILE]` | Print one crash log (default: the newest) |
| `restart`, `start`, `stop` | Control the service |

Token changes take effect immediately: the gateway notices when the config file changes. Revoking the last token makes the gateway reject everything until you add one.

## Logs

One line per event:

```
[open]   sid=laptop.3f2a… agent=laptop from=203.0.113.7     new MCP session
[spawn]  sid=laptop.3f2a… container=obscura-gw-sess-… port=8101
[pdf]    sid=… url=https://arxiv.org/pdf/… pages=12 chars=91576
[reap]   idle sid=…                                         closed after OBSCURA_GW_IDLE
[revoke] sid=…                                              token removed
[crash]  sid=… exit=139 (signal 11) oom=False last_call=browser_navigate https://…
[auth]   rejected POST /mcp from 198.51.100.4
```

## Crashes

When a browser dies (segfault, OOM, killed), the gateway:

1. Saves `docker logs --tail 300`, the exit code, the OOM flag, start and finish times, and the session's last 10 tool calls with their URLs to `OBSCURA_GW_CRASH_DIR/<time>-<session>.log`.
2. Tells the agent: `gateway: browser backend crashed (exit=…); browser state was lost, the next request respawns a fresh browser`.
3. Starts a fresh browser for the same session id on the next request.

`obscura-gw crashes` shows which URLs keep crashing the browser. Report reproducible crashes to Obscura with that log attached.

## Upgrading Obscura

New Obscura versions can change behaviour. For example, 0.2.3 began requiring `OBSCURA_MCP_TOKEN` for non-loopback HTTP; the gateway handles that. To upgrade safely:

1. Check the available tags: `https://hub.docker.com/r/h4ckf0r0day/obscura/tags`.
2. Run a second gateway with the new image on another port and label, using a scratch config:

   ```bash
   mkdir -p /tmp/gwtest && cat > /tmp/gwtest/env <<'EOF'
   OBSCURA_GW_PORT=8181
   OBSCURA_GW_LABEL=obscura-gwtest
   OBSCURA_GW_PORT_BASE=8400
   OBSCURA_GW_NETWORK=obscura-net
   OBSCURA_IMAGE=h4ckf0r0day/obscura:<new-tag>
   OBSCURA_GW_CRASH_DIR=/tmp/gwtest/crashes
   EOF
   OBSCURA_GW_ENV_FILE=/tmp/gwtest/env python3 gateway.py &
   OBSCURA_GW_LABEL=obscura-gwtest OBSCURA_GW_CRASH_DIR=/tmp/gwtest/crashes \
     python3 tests/test_gateway.py http://127.0.0.1:8181/mcp /tmp/gwtest/env ./obscura-gw
   ```

3. If everything passes, set `OBSCURA_IMAGE=…:<new-tag>` in the real config and restart.

## End-to-end tests

`tests/test_gateway.py` runs against a live gateway and covers:

- auth;
- keep-alive behaviour behind proxies;
- token ownership and session isolation (cookies);
- named sessions, DELETE, the per-token cap and the body limit;
- PDF extraction and paging;
- crash logging and self-healing;
- hot rename and revoke, and `/setup.sh`.

It creates and revokes its own temporary tokens (`machine-a`, `machine-b`, `laptop-b`) and needs internet access. Run it after every change, against both the local URL and the public URL.

## Common problems

| Problem | Fix |
|---|---|
| `browser_pdf_text` fails with "image not found" | Build the helper image: `docker build -t obscura-gw-pdftext:1 pdftext/` |
| First session after install is slow or times out | The Obscura image was still being pulled. The gateway pulls it at startup; check the logs. |
| Cloudflare returns 502 | The tunnel's service type must be **HTTP**, pointing at the gateway's address and port. |
| Every request returns 401 after an edit | Check the `OBSCURA_GW_TOKENS=` line. An empty value rejects everything by design. |
| Browsers can reach internal hosts | The firewall isn't applied. Run `sudo sh deploy/firewall.sh`, and check that `OBSCURA_GW_NETWORK` is the network the firewall's bridge belongs to. |
