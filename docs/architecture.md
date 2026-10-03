# Architecture

```
client ──HTTPS──▶ tunnel / proxy ──HTTP──▶ gateway.py (127.0.0.1:8080)
                                              │  docker run / rm   (Docker socket)
                                              │  HTTP + per-browser token
                                              ▼
                    ┌──────────── obscura-net (egress firewall) ────────────┐
                    │  obscura container ×N   (one per MCP session)          │
                    │  pdftext container      (one per PDF, --rm)            │
                    └────────────────────────────────────────────────────────┘
                                              ▼
                                       public internet only
```

`gateway.py` is a single-file, standard-library Python HTTP server (`ThreadingHTTPServer`). It holds every piece of state in memory.

## Request flow (`POST /mcp`)

1. Read the whole body (at most `OBSCURA_GW_MAX_BODY`), then authenticate the bearer token. The token maps to an agent name.
2. Resolve the session:
   - A known `Mcp-Session-Id` owned by this token: use it.
   - A session id owned by another token: `404`.
   - An `initialize` request (or no id): new id `<agent>.<random>`, or `<agent>.<X-Session-Id>` for a named session.
3. `get_or_create`: if the session has no live container, enforce the pool and per-token caps, then:
   - pick a free loopback port;
   - `docker run h4ckf0r0day/obscura mcp --http` with a random `OBSCURA_MCP_TOKEN`, on `OBSCURA_GW_NETWORK`;
   - wait for its `initialize` to answer.
4. Handle gateway tools locally (`browser_pdf_text`). Otherwise proxy the JSON-RPC to the container, adding the container's own token.
5. Rewrite the response:
   - `tools/list`: add `browser_pdf_text`.
   - `browser_navigate`: remember the current URL.
   - Any tool output starting with `%PDF-`: replace it with the extracted text.
6. Return the response with `Mcp-Session-Id: <sid>`.

`GET /mcp` (SSE) and `DELETE /mcp` follow the same ownership rules. `DELETE` removes the container.

## Lifecycle

| Event | Action |
|---|---|
| No request for `OBSCURA_GW_IDLE` seconds | Container removed (`[reap]`) |
| Container found dead (on request, or the 30 s scan) | Crash log written, session marked; the next request respawns it under the same id |
| Token revoked or renamed | That token's sessions are closed |
| SIGTERM / SIGINT | All containers removed, then the gateway exits |
| Gateway start | Leftover containers with this gateway's label are removed |

## PDF extraction

`pdf_extract(url)` runs `docker run --rm obscura-gw-pdftext:1 <url> <first> <last>` on the browser network: read-only, no capabilities, user `nobody`, with memory and process limits. Inside, `curl` (http/https only, 100 MB, 90 s) and `pdftotext -layout` produce the text. Page breaks become `[page N]` markers. The result is cached per session, so paging with `offset` doesn't download again.

## Tokens

`OBSCURA_GW_TOKENS` in the config file is re-read when the file's mtime changes, under a lock. The CLI writes the file atomically: a temp file in the same directory, then `mv`, keeping the original owner. A missing line keeps the current tokens; an explicitly empty line rejects every request.
