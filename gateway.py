#!/usr/bin/env python3
"""
Obscura Gateway
===============
One authenticated MCP endpoint in front of a pool of isolated Obscura headless
browsers (https://hub.docker.com/r/h4ckf0r0day/obscura), one Docker container
per agent session.

  Agent A (laptop) ─┐                          ┌─▶ obscura container  (session A1)
  Agent B (laptop) ─┼──▶  Gateway  /mcp  ──────┼─▶ obscura container  (session B1)
  Agent C (server) ─┘   auth · routing ·       └─▶ obscura container  (session C1)
                        lifecycle · PDF text

  * Auth + identity : a bearer token per machine or per agent. Sessions belong
                      to the token that opened them. Tokens are hot-reloaded.
  * Isolation       : every MCP session gets its own browser (cookies, tabs,
                      storage). `obscura mcp --http` alone shares one browser
                      between all clients.
  * Lifecycle       : spawn on demand, health-check, self-heal crashed
                      browsers (with crash logs), reap idle ones, DELETE closes.
  * Extras          : sandboxed PDF -> text (browser_pdf_text), per-token caps,
                      a client installer at /setup.sh.

Single file, Python 3.8+ standard library only. MIT licensed.
"""
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import socket
import subprocess
import threading
import time
from collections import deque
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------- configuration --------------------------------
ENV_FILE    = os.environ.get("OBSCURA_GW_ENV_FILE", "/etc/obscura-gateway/gateway.env")

def _load_env_file(path):
    """Read KEY=VALUE lines into os.environ (variables already set win)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    except OSError:
        pass

_load_env_file(ENV_FILE)

HOST        = os.environ.get("OBSCURA_GW_HOST", "127.0.0.1")
PORT        = int(os.environ.get("OBSCURA_GW_PORT", "8080"))
IMAGE       = os.environ.get("OBSCURA_IMAGE", "h4ckf0r0day/obscura:0.2.3")
IDLE_SECS   = int(os.environ.get("OBSCURA_GW_IDLE", "900"))       # reap idle sessions
MAX_POOL    = int(os.environ.get("OBSCURA_GW_MAXPOOL", "25"))
MAX_PER_AGENT = int(os.environ.get("OBSCURA_GW_MAX_PER_AGENT", "5"))  # sessions per token
NETWORK     = os.environ.get("OBSCURA_GW_NETWORK", "")   # e.g. obscura-net (egress-firewalled)
MAX_BODY    = int(os.environ.get("OBSCURA_GW_MAX_BODY", str(8 * 1024 * 1024)))
PORT_BASE   = int(os.environ.get("OBSCURA_GW_PORT_BASE", "8100"))
PORT_SPAN   = int(os.environ.get("OBSCURA_GW_PORT_SPAN", "200"))
STEALTH     = os.environ.get("OBSCURA_GW_STEALTH", "0") == "1"
BACKEND_BIND= os.environ.get("OBSCURA_GW_BACKEND_BIND", "127.0.0.1")
SPAWN_WAIT  = int(os.environ.get("OBSCURA_GW_SPAWN_WAIT", "25"))  # seconds to become healthy
LABEL       = os.environ.get("OBSCURA_GW_LABEL", "obscura-gw")
# crash diagnostics: logs + exit state of backends that died unexpectedly
CRASH_DIR   = os.environ.get("OBSCURA_GW_CRASH_DIR",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)), "crashes"))
CRASH_KEEP  = int(os.environ.get("OBSCURA_GW_CRASH_KEEP", "50"))
# extra backend knobs (shell-split), e.g.
#   OBSCURA_GW_DOCKER_ARGS="--memory 2g --pids-limit 512 --shm-size 256m --init"
#   OBSCURA_GW_OBSCURA_ARGS="--v8-flags=--max-old-space-size=1024"   (global obscura flags)
# PDF text extraction: runs curl + pdftotext in a throwaway, egress-firewalled
# container (never on the host), because obscura returns PDFs as raw bytes.
PDF_IMAGE   = os.environ.get("OBSCURA_GW_PDF_IMAGE", "obscura-gw-pdftext:1")
PDF_TIMEOUT = int(os.environ.get("OBSCURA_GW_PDF_TIMEOUT", "120"))
PDF_DEFAULT_CHARS = int(os.environ.get("OBSCURA_GW_PDF_CHARS", "60000"))
# Public base URL clients use; baked into the /setup.sh client installer.
# Public base URL clients use (e.g. https://browser.example.com), baked into the
# /setup.sh installer. Empty: derived from the request (Host / X-Forwarded-*).
PUBLIC_BASE = os.environ.get("OBSCURA_GW_PUBLIC_BASE", "").rstrip("/")
SETUP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client-setup.sh")
DOCKER_ARGS = shlex.split(os.environ.get("OBSCURA_GW_DOCKER_ARGS", ""))
OBSCURA_ARGS= shlex.split(os.environ.get("OBSCURA_GW_OBSCURA_ARGS", ""))

SAFE = re.compile(r"[^A-Za-z0-9_.-]")

# Tokens: OBSCURA_GW_TOKENS, comma-separated, each "token" or "token:agent".
# The token is the only identity: unlabeled tokens get a stable "tok-<hash>".
def _parse_tokens(raw):
    out = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        tok, _, ag = part.partition(":")
        tok = tok.strip()
        ag = SAFE.sub("-", ag.strip()) or "tok-" + hashlib.sha256(tok.encode()).hexdigest()[:8]
        out[tok] = ag
    return out

def _read_env_tokens():
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("OBSCURA_GW_TOKENS="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        return None     # no tokens line at all: treat as unreadable, keep current set
    except OSError:
        return None

TOKENS = _parse_tokens(os.environ.get("OBSCURA_GW_TOKENS", ""))
_tok_mtime = None

def log(*a):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)

# ----------------------------- session pool ---------------------------------
class Session:
    __slots__ = ("sid", "agent", "container", "port", "url", "last", "creating",
                 "started", "calls", "crash", "btok", "page_url", "pdf_cache")
    def __init__(self, sid, agent, container, port):
        self.sid = sid
        self.agent = agent
        self.container = container
        self.port = port
        self.url = f"http://{BACKEND_BIND}:{port}/mcp"
        self.last = time.time()
        self.creating = True
        self.started = time.time()
        self.calls = deque(maxlen=10)   # recent (ts, tool, url/args) for crash reports
        self.crash = None               # summary once a crash has been recorded
        self.btok = secrets.token_hex(24)  # obscura >=0.2.3 requires OBSCURA_MCP_TOKEN
        self.page_url = None            # last URL the browser navigated to
        self.pdf_cache = None           # (url, text, pages) of the last extracted PDF

    def note_call(self, tool, args):
        url = args.get("url") if isinstance(args, dict) else None
        if url is None:
            url = json.dumps(args, ensure_ascii=False)[:200] if args else ""
        self.calls.append((time.time(), str(tool)[:80], str(url)[:500]))

SESSIONS = {}          # sid -> Session
LOCK = threading.RLock()
CRASH_LOCK = threading.Lock()

def _docker(*args, timeout=60, env=None):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, **env} if env else None)

def _free_port():
    used = {s.port for s in SESSIONS.values()}
    for p in range(PORT_BASE, PORT_BASE + PORT_SPAN):
        if p in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((BACKEND_BIND, p)) != 0:   # nothing listening -> free
                return p
    raise RuntimeError("no free backend port")

def _container_running(name):
    r = _docker("inspect", "-f", "{{.State.Running}}", name)
    return r.returncode == 0 and r.stdout.strip() == "true"

def _ts(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))

def record_crash(s, reason):
    """Save logs + exit state of a backend that died unexpectedly (once per container).
    Returns a short summary string. Must be called before the container is removed."""
    with CRASH_LOCK:
        return _record_crash(s, reason)

def _record_crash(s, reason):
    if s.crash:
        return s.crash
    st = {}
    r = _docker("inspect", "-f", "{{json .State}}", s.container, timeout=15)
    if r.returncode == 0:
        try:
            st = json.loads(r.stdout)
        except ValueError:
            pass
    if st.get("Running"):
        return None
    code, oom = st.get("ExitCode"), st.get("OOMKilled")
    sig = f" (signal {code - 128})" if isinstance(code, int) and code > 128 else ""
    last = s.calls[-1] if s.calls else None
    summary = (f"exit={code}{sig} oom={oom} error={st.get('Error') or '-'} "
               f"last_call={last[1] + ' ' + last[2] if last else '-'}")
    s.crash = summary
    log(f"[crash] sid={s.sid} container={s.container} {summary} ({reason})")
    try:
        lg = _docker("logs", "--tail", "300", s.container, timeout=20)
        logs = (lg.stdout or "") + (("\n--- stderr ---\n" + lg.stderr) if lg.stderr else "")
        logs = re.sub(r"\x1b\[[0-9;]*m", "", logs)
        os.makedirs(CRASH_DIR, exist_ok=True)
        fn = os.path.join(CRASH_DIR, time.strftime("%Y%m%d-%H%M%S") + "-" + SAFE.sub("-", s.sid) + ".log")
        with open(fn, "w") as f:
            f.write(f"sid:        {s.sid}\nagent:      {s.agent}\ncontainer:  {s.container}\n"
                    f"reason:     {reason}\nspawned:    {_ts(s.started)}\n"
                    f"exit_code:  {code}{sig}\noom_killed: {oom}\nerror:      {st.get('Error') or ''}\n"
                    f"started:    {st.get('StartedAt')}\nfinished:   {st.get('FinishedAt')}\n"
                    f"status:     {st.get('Status')}\n\nrecent tool calls (oldest first):\n")
            for t, tool, url in s.calls:
                f.write(f"  {_ts(t)}  {tool}  {url}\n")
            f.write("\n--- docker logs --tail 300 ---\n" + logs)
        files = sorted(x for x in os.listdir(CRASH_DIR) if x.endswith(".log"))
        for old in files[:max(0, len(files) - CRASH_KEEP)]:
            os.unlink(os.path.join(CRASH_DIR, old))
    except Exception as e:
        log(f"[crash] could not write crash log for sid={s.sid}: {e}")
    return summary

def _health(url, tok=None):
    body = json.dumps({"jsonrpc":"2.0","id":"hc","method":"initialize",
                       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                                 "clientInfo":{"name":"gw-health","version":"1"}}}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
            headers={"Content-Type":"application/json",
                     "Accept":"application/json, text/event-stream",
                     **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False

def get_or_create(sid, agent):
    """Return a ready Session for sid, spawning a backend container if needed."""
    prev_calls = None
    with LOCK:
        s = SESSIONS.get(sid)
        if s is not None and not s.creating and not _container_running(s.container):
            # backend died / was removed out-of-band -> drop and respawn
            record_crash(s, "heal: backend not running on next request")
            log(f"[heal] dead backend for sid={sid}; respawning")
            SESSIONS.pop(sid, None)
            _docker("rm", "-f", s.container)
            prev_calls = s.calls   # keep call history across respawns (repeat crashes)
            s = None
        if s is None:
            if len(SESSIONS) >= MAX_POOL:
                raise RuntimeError(f"pool exhausted ({MAX_POOL})")
            if sum(1 for x in SESSIONS.values() if x.agent == agent) >= MAX_PER_AGENT:
                raise RuntimeError(f"too many sessions for this token ({MAX_PER_AGENT}); "
                                   "close one (DELETE) or wait for idle reap")
            port = _free_port()
            cname = f"{LABEL}-sess-" + SAFE.sub("-", sid)[:48]
            s = Session(sid, agent, cname, port)
            if prev_calls:
                s.calls.extend(prev_calls)
            SESSIONS[sid] = s
            spawn = True
        else:
            spawn = False
            s.last = time.time()
    if not spawn:
        # wait if another thread is still bringing it up
        for _ in range(SPAWN_WAIT * 4):
            if not s.creating:
                return s
            time.sleep(0.25)
        return s
    # spawn backend container (outside lock; other sessions proceed in parallel)
    try:
        _docker("rm", "-f", s.container)  # clear any stale name
        run = ["run", "-d", "--name", s.container,
               "--label", f"{LABEL}=1", "--label", f"{LABEL}-sid={sid}",
               "--restart", "no",
               *(["--network", NETWORK] if NETWORK else []),
               "-p", f"{BACKEND_BIND}:{s.port}:8080",
               "-e", "OBSCURA_MCP_TOKEN",
               *DOCKER_ARGS,
               IMAGE, *OBSCURA_ARGS, "mcp", "--http", "--host", "0.0.0.0", "--port", "8080"]
        if STEALTH:
            run.append("--stealth")
        r = _docker(*run, env={"OBSCURA_MCP_TOKEN": s.btok})
        if r.returncode != 0:
            raise RuntimeError("docker run failed: " + r.stderr.strip())
        deadline = time.time() + SPAWN_WAIT
        while time.time() < deadline:
            if _health(s.url, s.btok):
                break
            time.sleep(0.4)
        else:
            raise RuntimeError("backend did not become healthy")
        log(f"[spawn] sid={sid} agent={agent or '-'} container={s.container} port={s.port}")
        s.creating = False
        return s
    except Exception:
        with LOCK:
            SESSIONS.pop(sid, None)
        _docker("rm", "-f", s.container)
        raise

def destroy(sid):
    with LOCK:
        s = SESSIONS.pop(sid, None)
    if s:
        _docker("rm", "-f", s.container)
        log(f"[destroy] sid={sid} container={s.container}")
        return True
    return False

def reaper():
    while True:
        time.sleep(30)
        reload_tokens()   # revocations take effect even without traffic
        scan_dead()
        now = time.time()
        with LOCK:
            stale = [sid for sid, s in SESSIONS.items()
                     if not s.creating and now - s.last > IDLE_SECS]
        for sid in stale:
            log(f"[reap] idle sid={sid}")
            destroy(sid)

def scan_dead():
    """Record crashes of backends that exited between requests (logs survive until rm)."""
    r = _docker("ps", "-a", "--filter", f"label={LABEL}=1", "--filter", "status=exited",
                "--filter", "status=dead", "--format", "{{.Names}}", timeout=15)
    dead = set(r.stdout.split())
    if not dead:
        return
    with LOCK:
        hit = [s for s in SESSIONS.values() if s.container in dead and not s.creating and not s.crash]
    for s in hit:
        record_crash(s, "reaper: backend exited between requests")

def cleanup_orphans():
    r = _docker("ps", "-aq", "--filter", f"label={LABEL}=1")
    ids = [x for x in r.stdout.split() if x]
    # keep diagnostics of backends that had already crashed before this restart
    ex = _docker("ps", "-a", "--filter", f"label={LABEL}=1", "--filter", "status=exited",
                 "--format", "{{.Names}}\t{{.Label \"" + LABEL + "-sid\"}}")
    for line in ex.stdout.splitlines():
        name, _, sid = line.partition("\t")
        if name:
            record_crash(Session(sid or name, "?", name, 0), "startup: exited orphan")
    if ids:
        _docker("rm", "-f", *ids)
        log(f"[startup] removed {len(ids)} orphan backend container(s)")

TOK_LOCK = threading.Lock()

def reload_tokens():
    """Re-read tokens when the env file changes; drop sessions of revoked agents.

    Serialized: the reaper thread and request threads both call this, and a
    caller must never see the new mtime recorded before TOKENS is swapped."""
    global TOKENS, _tok_mtime
    with TOK_LOCK:
        try:
            m = os.stat(ENV_FILE).st_mtime_ns
        except OSError:
            return
        if m == _tok_mtime:
            return
        raw = _read_env_tokens()
        if raw is None:
            return
        # an explicit empty list (last token revoked) is honored: with no tokens
        # _auth fails closed, so nobody gets in. Writes are atomic (mv), so an
        # empty value is never a half-written file.
        new = _parse_tokens(raw)
        gone = set(TOKENS.values()) - set(new.values())
        changed = new != TOKENS
        TOKENS = new
        _tok_mtime = m          # only after TOKENS is current
    if changed:
        log(f"[tokens] reloaded: {len(new)} token(s), agents={sorted(set(new.values()))}"
            + ("  (no tokens: all requests are rejected)" if not new else ""))
        with LOCK:
            dead = [sid for sid, s in SESSIONS.items() if s.agent in gone]
        for sid in dead:
            log(f"[revoke] sid={sid}")
            destroy(sid)

# ----------------------------- PDF text extraction ----------------------------
PDF_TOOL = {
    "name": "browser_pdf_text",
    "description": ("Extract the text of a PDF (paper, report). Use this instead of browser_markdown "
                    "for PDF URLs: the browser only sees raw PDF bytes. Defaults to the current page "
                    "URL. Long documents are paged: pass offset to continue reading. Provided by the "
                    "obscura gateway (curl + pdftotext in a sandbox; no browser cookies are sent)."),
    "inputSchema": {"type": "object", "properties": {
        "url": {"type": "string", "description": "PDF URL (default: the current page)"},
        "first_page": {"type": "integer", "minimum": 1},
        "last_page": {"type": "integer", "minimum": 1},
        "offset": {"type": "integer", "minimum": 0, "description": "character offset to start at"},
        "max_chars": {"type": "integer", "minimum": 1000, "maximum": 400000,
                      "description": f"default {PDF_DEFAULT_CHARS}"}}}}

def _looks_pdf(text):
    return isinstance(text, str) and text.lstrip()[:5] == "%PDF-"

def pdf_extract(url, first=None, last=None):
    """Return (text, pages) or raise RuntimeError with a readable reason."""
    if not re.match(r"^https?://", url or "", re.I):
        raise RuntimeError("url must be http(s)")
    run = ["run", "--rm", "--network", NETWORK or "bridge", "--read-only",
           "--tmpfs", "/tmp:rw,size=192m", "--memory", "512m", "--pids-limit", "64",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534",
           "--label", f"{LABEL}-pdf=1", PDF_IMAGE, url, str(first or 1), str(last or 0)]
    try:
        r = _docker(*run, timeout=PDF_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PDF extraction timed out after {PDF_TIMEOUT}s")
    err = (r.stderr or "").strip()
    m = re.search(r"^PAGES (\d+)", err, re.M)
    if r.returncode != 0:
        msg = [l for l in err.splitlines() if l.startswith("ERROR")] or err.splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(msg[0][:300])
    text = r.stdout.replace("\r\n", "\n")
    pages = int(m.group(1)) if m else None
    # mark page boundaries (pdftotext emits form feeds)
    n = first or 1
    parts = text.split("\f")
    text = "".join(f"\n[page {n + i}]\n" + part for i, part in enumerate(parts) if part.strip())
    return text, pages

def pdf_tool_result(s, args, note=""):
    """Build MCP tool result content for browser_pdf_text."""
    url = args.get("url") or s.page_url
    if not url:
        return True, "No URL given and the browser has no current page."
    first, last = args.get("first_page"), args.get("last_page")
    offset = max(0, int(args.get("offset") or 0))
    limit = min(400000, max(1000, int(args.get("max_chars") or PDF_DEFAULT_CHARS)))
    key = (url, first, last)
    if s.pdf_cache and s.pdf_cache[0] == key:
        text, pages = s.pdf_cache[1], s.pdf_cache[2]
    else:
        try:
            text, pages = pdf_extract(url, first, last)
        except RuntimeError as e:
            return True, f"PDF text extraction failed for {url}: {e}"
        s.pdf_cache = (key, text, pages)
        log(f"[pdf] sid={s.sid} url={url[:200]} pages={pages} chars={len(text)}")
    chunk = text[offset:offset + limit]
    end = offset + len(chunk)
    head = (f"{note}PDF text of {url} ({pages if pages is not None else '?'} pages, "
            f"{len(text)} chars; showing {offset}-{end})")
    if end < len(text):
        head += f". More text remains: call browser_pdf_text with offset={end}"
    if not text.strip():
        return False, head + "\n\n(no extractable text: the PDF may be scanned images)"
    return False, head + "\n\n" + chunk

def _sse_or_json(data, ctype):
    """Parse a backend response body into (list_of_messages, is_sse)."""
    txt = data.decode("utf-8", "replace")
    if "text/event-stream" in (ctype or ""):
        msgs = []
        for line in txt.splitlines():
            if line.startswith("data:"):
                try:
                    msgs.append(json.loads(line[5:]))
                except Exception:
                    pass
        return msgs, True
    try:
        return [json.loads(txt)], False
    except Exception:
        return [], False

def _encode(msg, sse):
    body = json.dumps(msg, ensure_ascii=False)
    if sse:
        return f"event: message\ndata: {body}\n\n".encode(), "text/event-stream"
    return body.encode(), "application/json"

# ----------------------------- HTTP handler ---------------------------------
HOP = {"connection","keep-alive","proxy-authenticate","proxy-authorization",
       "te","trailers","transfer-encoding","upgrade","host","content-length"}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "obscura-gateway/1.0"

    def _json_err(self, code, msg, rpc_id=None):
        payload = json.dumps({"jsonrpc":"2.0","id":rpc_id,
                              "error":{"code":code if code<0 else -32000,"message":msg}}).encode()
        self.send_response(200 if code == 0 else code if code>=400 else 400)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth(self):
        reload_tokens()
        if not TOKENS:
            return False, ""    # fail closed: no tokens -> nobody gets in
        h = self.headers.get("Authorization","").strip()
        if h.lower().startswith("bearer"):
            h = h[6:].strip()
        tok = h
        for t, ag in TOKENS.items():
            if secrets.compare_digest(t, tok):
                return True, ag
        return False, ""

    def _own(self, sid, agent):
        """Session only if it exists AND belongs to this token's agent."""
        with LOCK:
            s = SESSIONS.get(sid) if sid else None
        return s if s is not None and s.agent == agent else None

    def _public_base(self):
        if PUBLIC_BASE:
            return PUBLIC_BASE
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or f"{HOST}:{PORT}"
        proto = self.headers.get("X-Forwarded-Proto") or ("https" if self.headers.get("CF-Visitor") else "http")
        if not re.match(r"^[A-Za-z0-9.:\[\]-]+$", host):     # never echo a weird Host into a script
            host = f"{HOST}:{PORT}"
        return f"{proto.split(',')[0].strip()}://{host}"

    def _client(self):
        # behind cloudflared the TCP peer is local; Cloudflare supplies the real IP
        return self.headers.get("CF-Connecting-IP") or self.client_address[0]

    def log_message(self, fmt, *args):
        pass  # quiet; we log meaningful events ourselves

    # ---- health endpoint (no auth) ----
    def do_GET(self):
        if self.path.split("?")[0] in ("/healthz","/health"):
            with LOCK:
                body = json.dumps({"status":"ok","sessions":len(SESSIONS),
                                   "max_pool":MAX_POOL,"image":IMAGE}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if self.path.split("?")[0] == "/setup.sh":
            # client installer: no secrets inside, so no auth (the token is an argument)
            try:
                body = open(SETUP_SCRIPT).read().replace("__BASE__", self._public_base()).encode()
            except OSError:
                self.send_error(404); return
            self.send_response(200); self.send_header("Content-Type", "text/x-shellscript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body); return
        if not self.path.startswith("/mcp"):
            self.send_error(404); return
        ok, agent = self._auth()
        if not ok:
            self._unauth(); return
        s = self._own(self.headers.get("Mcp-Session-Id"), agent)
        if not s:
            self.send_error(404, "unknown or missing session"); return
        s.last = time.time()
        self._proxy("GET", s, None)

    def do_DELETE(self):
        if not self.path.startswith("/mcp"):
            self.send_error(404); return
        ok, agent = self._auth()
        if not ok:
            self._unauth(); return
        s = self._own(self.headers.get("Mcp-Session-Id"), agent)
        if s:
            destroy(s.sid)
        self.send_response(204); self.send_header("Content-Length","0"); self.end_headers()

    def do_POST(self):
        if not self.path.startswith("/mcp"):
            self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length","0") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.send_error(413, "request body too large"); self.close_connection = True; return
        # drain the body before any early return: proxies (cloudflared) reuse
        # connections, and unread bytes would be parsed as the next request
        raw = self.rfile.read(length) if length else b""
        ok, agent = self._auth()
        if not ok:
            self._unauth(); return
        method, rpc_id, tcall = None, None, None
        try:
            obj = json.loads(raw) if raw else {}
            if isinstance(obj, dict):
                method = obj.get("method"); rpc_id = obj.get("id")
                if method == "tools/call" and isinstance(obj.get("params"), dict):
                    p = obj["params"]
                    tcall = (p.get("name"), p.get("arguments") or {})
        except Exception:
            pass

        sid = self.headers.get("Mcp-Session-Id")
        xsess = self.headers.get("X-Session-Id")
        prefix = f"{agent}."

        with LOCK:
            other = SESSIONS.get(sid) if sid else None
        known = other is not None and other.agent == agent
        if other is not None and not known:
            # someone else's session id: never route into it
            self.send_error(404, "unknown or missing session"); return

        if not known:
            if method == "initialize" or sid is None:
                if sid is None or not sid.startswith(prefix):
                    if xsess:
                        sid = prefix + SAFE.sub("-", xsess)[:64]
                    else:
                        sid = prefix + secrets.token_hex(8)
                # else: our own (expired) sid -> honor it (persistent)
            else:
                # non-initialize call with unknown session -> ask client to re-init
                self.send_error(404, "session expired; re-initialize"); return
        if not known:
            log(f"[open] sid={sid} agent={agent} from={self._client()}")
        try:
            s = get_or_create(sid, agent)
        except Exception as e:
            log(f"[error] spawn sid={sid}: {e}")
            self._json_err(-32000, f"gateway: {e}", rpc_id); return
        if tcall:
            s.note_call(*tcall)
        if tcall and tcall[0] == PDF_TOOL["name"]:
            s.last = time.time()
            is_err, text = pdf_tool_result(s, tcall[1] if isinstance(tcall[1], dict) else {})
            self._send_rpc({"jsonrpc": "2.0", "id": rpc_id, "result": {
                "content": [{"type": "text", "text": text}], "isError": is_err}},
                "text/event-stream" in self.headers.get("Accept", ""), s)
            return
        self._proxy("POST", s, raw, rpc_id, method=method, tcall=tcall)

    # ---- reverse proxy to the backend obscura ----
    def _backend_failed(self, s, e, rpc_id):
        """Backend connection failed: if the container died, record it and say so."""
        summary = None
        if not _container_running(s.container):
            summary = record_crash(s, f"proxy: {type(e).__name__}: {e}")
        if summary:
            self._json_err(-32000, "gateway: browser backend crashed "
                           f"({summary.split(' last_call=')[0]}); browser state was lost, "
                           "the next request respawns a fresh browser for this session", rpc_id)
        else:
            self._json_err(-32000, f"gateway backend error: {e}", rpc_id)

    def _send_rpc(self, msg, sse, s):
        data, ctype = _encode(msg, sse)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", s.sid)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _rewrite(self, s, data, ctype, method, tcall):
        """Add browser_pdf_text to tools/list; turn raw-PDF tool output into text."""
        if method not in ("tools/list", "tools/call"):
            return data, ctype
        msgs, sse = _sse_or_json(data, ctype)
        if len(msgs) != 1 or not isinstance(msgs[0].get("result"), dict):
            return data, ctype
        msg, res = msgs[0], msgs[0]["result"]
        if method == "tools/list":
            tools = res.get("tools")
            if isinstance(tools, list) and not any(t.get("name") == PDF_TOOL["name"] for t in tools):
                tools.append(PDF_TOOL)
            return _encode(msg, sse)
        name, args = tcall if tcall else (None, {})
        content = res.get("content") or []
        text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if name == "browser_navigate":
            m = re.match(r"Navigated to (\S+)", text)
            if m:
                s.page_url = m.group(1)
            elif isinstance(args, dict) and args.get("url"):
                s.page_url = args["url"]
            return data, ctype
        if _looks_pdf(text) and s.page_url:
            is_err, new = pdf_tool_result(s, {}, note=f"[gateway] {name} returned raw PDF bytes; "
                                                      "replaced with extracted text. ")
            msg["result"] = {"content": [{"type": "text", "text": new}], "isError": is_err}
            return _encode(msg, sse)
        return data, ctype

    def _proxy(self, verb, s, body, rpc_id=None, method=None, tcall=None):
        fwd_headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in HOP or lk in ("authorization","mcp-session-id") or lk.startswith("x-agent") or lk.startswith("x-session"):
                continue
            fwd_headers[k] = v
        fwd_headers.setdefault("Accept","application/json, text/event-stream")
        fwd_headers["Authorization"] = f"Bearer {s.btok}"
        req = urllib.request.Request(s.url, data=body if verb=="POST" else None,
                                     method=verb, headers=fwd_headers)
        try:
            resp = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            self._backend_failed(s, e, rpc_id); return
        try:
            data = resp.read()
        except Exception as e:
            self._backend_failed(s, e, rpc_id); return
        status = getattr(resp, "status", 200) or 200
        ctype = resp.headers.get("Content-Type","application/json")
        if verb == "POST" and status == 200:
            try:
                data, ctype = self._rewrite(s, data, ctype, method, tcall)
            except Exception as e:
                log(f"[warn] rewrite failed sid={s.sid}: {type(e).__name__}: {e}")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", s.sid)     # tell client its session id
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        s.last = time.time()

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        super().send_error(code, message, explain)

    def _unauth(self):
        log(f"[auth] rejected {self.command} {self.path} from {self._client()}")
        body = b'{"error":"unauthorized"}'
        self.send_response(401)
        self.send_header("WWW-Authenticate",'Bearer realm="obscura-gateway"')
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Connection","close")
        self.end_headers(); self.wfile.write(body)
        self.close_connection = True

def ensure_images():
    """Pull the browser image and check the PDF helper image once at startup."""
    if _docker("image", "inspect", IMAGE).returncode != 0:
        log(f"[startup] pulling {IMAGE} (first run) ...")
        r = _docker("pull", IMAGE, timeout=900)
        log(f"[startup] pull {IMAGE}: " + ("ok" if r.returncode == 0 else r.stderr.strip()[-300:]))
    if _docker("image", "inspect", PDF_IMAGE).returncode != 0:
        log(f"[startup] PDF helper image {PDF_IMAGE} not found: browser_pdf_text will fail until "
            "you build it (docker build -t obscura-gw-pdftext:1 pdftext/)")

def main():
    if not TOKENS and _read_env_tokens() in (None, ""):
        log(f"[startup] no tokens configured in {ENV_FILE}: every request is rejected until you "
            "run `obscura-gw add-token <name>`")
    ensure_images()
    cleanup_orphans()
    threading.Thread(target=reaper, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)

    def _stop(signum, _frame):
        # SIGTERM/SIGINT (systemctl stop, docker stop): close every browser, then exit.
        # Needed in Docker, where the gateway is PID 1 and SIGTERM is otherwise ignored.
        log(f"[stop] signal {signum}: closing {len(SESSIONS)} browser(s)")
        with LOCK:
            sids = list(SESSIONS)
        for sid in sids:
            destroy(sid)
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    reload_tokens()
    log(f"[ready] obscura-gateway on {HOST}:{PORT}  image={IMAGE} "
        f"tokens={len(TOKENS)} agents={sorted(set(TOKENS.values()))} "
        f"max_pool={MAX_POOL} idle={IDLE_SECS}s")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

def healthcheck():
    """`gateway.py --healthcheck`: exit 0 if the local gateway answers /healthz."""
    host = "127.0.0.1" if HOST in ("0.0.0.0", "", "::") else HOST
    try:
        with urllib.request.urlopen(f"http://{host}:{PORT}/healthz", timeout=4) as r:
            return 0 if r.status == 200 else 1
    except Exception:
        return 1

if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--healthcheck"]:
        sys.exit(healthcheck())
    main()
