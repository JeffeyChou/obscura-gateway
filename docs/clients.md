# Connecting clients

Every client needs the same two things:

```
URL:     https://browser.example.com/mcp        (Streamable HTTP)
Header:  Authorization: Bearer <TOKEN>
```

Create a token on the gateway host with `obscura-gw add-token <name>`. It prints all the commands below with your URL and token filled in; `obscura-gw setup <name>` prints them again.

## One command (Claude Code, Codex, Pi)

On the client machine:

```bash
curl -fsSL https://browser.example.com/setup.sh | sh -s -- <TOKEN>              # every CLI found
curl -fsSL https://browser.example.com/setup.sh | sh -s -- <TOKEN> claude codex # only these
```

The installer:

- checks the token first, and changes nothing if it's rejected;
- configures each CLI it finds;
- can be re-run safely: it replaces any earlier `obscura-remote` entry.

Run it without a token and it asks for one with hidden input, which keeps the token out of your shell history. The script contains no secrets and is served without authentication.

## Claude Code

```bash
claude mcp add --scope user --transport http obscura-remote \
  https://browser.example.com/mcp -H "Authorization: Bearer <TOKEN>"
claude mcp get obscura-remote      # Status: ✔ Connected
```

`--scope user` makes it available in every project. Use `--scope project` to commit it to one repo's `.mcp.json`, but don't commit tokens.

## Codex

Codex reads HTTP bearer tokens from an environment variable:

```bash
codex mcp add obscura-remote --url https://browser.example.com/mcp \
  --bearer-token-env-var OBSCURA_REMOTE_TOKEN
echo 'export OBSCURA_REMOTE_TOKEN=<TOKEN>' >> ~/.zshrc     # or ~/.bashrc
```

GUI-launched Codex, such as an editor extension, may not see variables from your shell rc. Set the variable where that app can read it.

## Pi (pi-mcp-adapter)

pi-mcp-adapter reads `~/.config/mcp/mcp.json`, or `.mcp.json` per project. It does **not** read `~/.pi/mcp.json`.

```json
{
  "mcpServers": {
    "obscura-remote": {
      "url": "https://browser.example.com/mcp",
      "auth": "bearer",
      "bearerToken": "<TOKEN>",
      "directTools": true
    }
  }
}
```

## Cursor, Gemini CLI, Claude Desktop and other JSON-configured clients

Most clients accept this shape in their MCP config file:

```json
{
  "mcpServers": {
    "obscura-remote": {
      "type": "http",
      "url": "https://browser.example.com/mcp",
      "headers": { "Authorization": "Bearer <TOKEN>" }
    }
  }
}
```

Some clients call the field `httpUrl` or `serverUrl`, or use `"transport": "streamable-http"`; check their docs.

## stdio-only clients

Bridge with [mcp-remote](https://www.npmjs.com/package/mcp-remote):

```bash
npx -y mcp-remote https://browser.example.com/mcp --header "Authorization: Bearer <TOKEN>"
# plain-http URLs (e.g. a VPN address) also need: --allow-http --transport http-only
```

## Your own code

Send JSON-RPC over HTTP POST to `/mcp`:

- Headers: `Authorization`, `Content-Type: application/json`, `Accept: application/json, text/event-stream`.
- Keep the `Mcp-Session-Id` response header and send it back on later requests.
- Send `DELETE /mcp` with that header when you are done; this frees the browser immediately.
- Behind Cloudflare, set your own `User-Agent`. The default `Python-urllib` one is blocked.

Optional request headers:

| Header | Effect |
|---|---|
| `X-Session-Id: <name>` | On `initialize`: a named, reusable session (`<token-name>.<name>`). Reconnecting with the same name gets the same browser while it is alive. |

`X-Agent-Id` is ignored. Identity comes only from the token.

## Per machine or per agent?

Both work. Each MCP connection gets its own browser either way. See [Tokens](../README.md#tokens-per-machine-or-per-agent) for the trade-offs. In short:

- Use one token per machine you own.
- Use separate tokens for anything you may need to revoke on its own.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401` | Wrong or revoked token. Check with `obscura-gw tokens` on the server. |
| `403`, Cloudflare error 1010 | Your client's User-Agent is blocked by Cloudflare. Set a custom one. |
| `too many sessions for this token (5)` | That token already has 5 live browsers. Close sessions (DELETE), wait for the idle reap (15 min), or raise `OBSCURA_GW_MAX_PER_AGENT`. |
| `session expired; re-initialize` | The gateway restarted or reaped the session. Clients reconnect automatically; scripts should send `initialize` again. |
| `gateway: browser backend crashed …` | The page crashed the browser. The next request gets a fresh browser; the server keeps a crash log (`obscura-gw crashes`). |
| A page shows "Just a moment…", a CAPTCHA, or is empty | The site's bot protection is blocking Obscura. This is not a gateway problem. |
