#!/bin/sh
# Obscura remote browser: configure this machine's agent CLIs.
#
#   curl -fsSL __BASE__/setup.sh | sh -s -- <TOKEN> [claude] [codex] [pi]
#
# With no CLI names, configures every one it finds (Claude Code, Codex, Pi).
# With no token, asks for it (input hidden). Safe to re-run: it replaces any
# previous "obscura-remote" entry. Contains no secrets; served by the gateway.
set -eu
URL="__BASE__/mcp"
NAME=obscura-remote
ENVVAR=OBSCURA_REMOTE_TOKEN

TOKEN="${1:-${OBSCURA_TOKEN:-}}"
[ $# -gt 0 ] && shift
if [ -z "$TOKEN" ]; then
  if [ -r /dev/tty ]; then
    printf 'Obscura token: ' >/dev/tty; stty -echo </dev/tty 2>/dev/null || true
    read -r TOKEN </dev/tty; stty echo </dev/tty 2>/dev/null || true; echo >/dev/tty
  fi
fi
[ -n "$TOKEN" ] || { echo "usage: sh setup.sh <TOKEN> [claude] [codex] [pi]" >&2; exit 1; }
case "$TOKEN" in *[!0-9a-fA-F]*) echo "token should be hex (from obscura-gw add-token)" >&2; exit 1;; esac

say(){ printf '%s\n' "$*"; }
ok(){ printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m!\033[0m %s\n' "$*"; }
want(){ [ -z "$TARGETS" ] && return 0; case " $TARGETS " in *" $1 "*) return 0;; esac; return 1; }
TARGETS="$*"

# 1) check the token before touching any config
say "Checking token against $URL ..."
INIT='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"obscura-setup","version":"1"}}}'
HDRS=$(curl -sS -m 30 -o /dev/null -D - -A "obscura-setup/1" -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
        -d "$INIT" "$URL" 2>&1) || { echo "cannot reach $URL: $HDRS" >&2; exit 1; }
CODE=$(printf '%s\n' "$HDRS" | awk 'toupper($1) ~ /^HTTP/ {c=$2} END{print c}')
SID=$(printf '%s\n' "$HDRS" | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
if [ "$CODE" != "200" ]; then
  echo "token rejected (HTTP $CODE). Create one on the server: obscura-gw add-token <name>" >&2; exit 1
fi
ok "token valid; this machine will appear as agent '${SID%%.*}'"
[ -n "$SID" ] && curl -s -m 10 -o /dev/null -X DELETE -A "obscura-setup/1" \
  -H "Authorization: Bearer $TOKEN" -H "Mcp-Session-Id: $SID" "$URL" || true

DONE=""
# 2) Claude Code: user scope = every project on this machine
if want claude && command -v claude >/dev/null 2>&1; then
  claude mcp remove "$NAME" -s user >/dev/null 2>&1 || true
  if claude mcp add --scope user --transport http "$NAME" "$URL" -H "Authorization: Bearer $TOKEN" >/dev/null; then
    ok "Claude Code: added '$NAME' (user scope)"; DONE="$DONE claude"
  else warn "Claude Code: 'claude mcp add' failed"; fi
elif want claude && [ -n "$TARGETS" ]; then warn "Claude Code: 'claude' not found"; fi

# 3) Codex: bearer token comes from an env var exported by your shell rc
if want codex && command -v codex >/dev/null 2>&1; then
  codex mcp remove "$NAME" >/dev/null 2>&1 || true
  if codex mcp add "$NAME" --url "$URL" --bearer-token-env-var "$ENVVAR" >/dev/null; then
    rcs=""
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do [ -f "$rc" ] && rcs="$rcs $rc"; done
    if [ -z "$rcs" ]; then case "${SHELL:-}" in *zsh) rcs="$HOME/.zshrc";; *) rcs="$HOME/.bashrc";; esac; fi
    for rc in $rcs; do
      tmp="$rc.obscura.$$"; grep -v "^export $ENVVAR=" "$rc" 2>/dev/null > "$tmp" || true
      printf 'export %s=%s\n' "$ENVVAR" "$TOKEN" >> "$tmp"; cat "$tmp" > "$rc"; rm -f "$tmp"
    done
    ok "Codex: added '$NAME'; $ENVVAR exported in$rcs (open a new shell, or: export $ENVVAR=...)"
    DONE="$DONE codex"
  else warn "Codex: 'codex mcp add' failed"; fi
elif want codex && [ -n "$TARGETS" ]; then warn "Codex: 'codex' not found"; fi

# 4) Pi (pi-mcp-adapter reads ~/.config/mcp/mcp.json, NOT ~/.pi/mcp.json)
if want pi && { command -v pi >/dev/null 2>&1 || [ -d "$HOME/.pi" ]; }; then
  cfg="$HOME/.config/mcp/mcp.json"; mkdir -p "$(dirname "$cfg")"
  merge='const fs=require("fs"),[f,n,u,t]=process.argv.slice(1);let c={};try{c=JSON.parse(fs.readFileSync(f,"utf8"))}catch(e){}
c.mcpServers=c.mcpServers||{};c.mcpServers[n]={url:u,auth:"bearer",bearerToken:t,directTools:true};
fs.writeFileSync(f,JSON.stringify(c,null,2)+"\n",{mode:0o600});fs.chmodSync(f,0o600)'
  pymerge='import json,os,sys
f,n,u,t=sys.argv[1:]
try: c=json.load(open(f))
except Exception: c={}
c.setdefault("mcpServers",{})[n]={"url":u,"auth":"bearer","bearerToken":t,"directTools":True}
fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600); os.write(fd,(json.dumps(c,indent=2)+"\n").encode()); os.close(fd); os.chmod(f,0o600)'
  if command -v node >/dev/null 2>&1; then node -e "$merge" "$cfg" "$NAME" "$URL" "$TOKEN"
  elif command -v python3 >/dev/null 2>&1; then python3 -c "$pymerge" "$cfg" "$NAME" "$URL" "$TOKEN"
  else warn "Pi: need node or python3 to edit $cfg"; cfg=""; fi
  [ -n "$cfg" ] && { ok "Pi: added '$NAME' to $cfg"; DONE="$DONE pi"; }
elif want pi && [ -n "$TARGETS" ]; then warn "Pi: 'pi' not found"; fi

if [ -z "$DONE" ]; then
  say "No supported CLI found. For any other MCP client use:"
else
  say "Done:$DONE. Restart running agent sessions to pick it up. Other MCP clients:"
fi
say "  Streamable HTTP  $URL"
say "  Header           Authorization: Bearer <your token>"
say "  stdio bridge     npx -y mcp-remote $URL --header \"Authorization: Bearer <your token>\""
