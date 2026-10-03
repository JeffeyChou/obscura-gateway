#!/usr/bin/env python3
"""End-to-end tests for a running Obscura Gateway.

    python3 tests/test_gateway.py URL ENV_FILE CLI

  URL       e.g. http://127.0.0.1:8080/mcp (or your public https URL)
  ENV_FILE  the gateway's config file (tests add/revoke temporary tokens in it)
  CLI       path to obscura-gw

Run on the gateway host (it uses `docker` to kill a browser for the crash test)
with internet access (example.com, arxiv.org, vldb.org, usenix.org).
Set OBSCURA_GW_LABEL / OBSCURA_GW_CRASH_DIR if your gateway uses non-defaults.
"""
import http.client, json, os, subprocess, urllib.parse, sys, time, urllib.request, urllib.error
URL, ENVF, CLI = sys.argv[1], sys.argv[2], sys.argv[3]
env = dict(os.environ, OBSCURA_GW_ENV_FILE=ENVF)
ok_all = True
def check(name, cond, extra=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""), flush=True)

def cli(*a):
    return subprocess.run([CLI, *a], env=env, capture_output=True, text=True)

def rpc(tok, method, params=None, sid=None, rid=1, headers=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None: body["id"] = rid
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "User-Agent": "obscura-gw-test/1"}
    if tok: h["Authorization"] = "Bearer " + tok
    if sid: h["Mcp-Session-Id"] = sid
    h.update(headers or {})
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        return e.code, None, None
    raw = r.read().decode()
    msg = None
    for line in raw.splitlines():
        if line.startswith("data:"): msg = json.loads(line[5:])
    if msg is None and raw.strip(): msg = json.loads(raw)
    return r.status, r.headers.get("Mcp-Session-Id"), msg

def delete(tok, sid):
    req = urllib.request.Request(URL, method="DELETE",
          headers={"Authorization": "Bearer " + tok, "Mcp-Session-Id": sid, "User-Agent": "obscura-gw-test/1"})
    return urllib.request.urlopen(req, timeout=30).status
def session(tok, headers=None):
    st, sid, _ = rpc(tok, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "gw-test", "version": "1"}}, headers=headers)
    if sid: rpc(tok, "notifications/initialized", sid=sid, rid=None)
    return st, sid

def call(tok, sid, tool, args):
    st, _, m = rpc(tok, "tools/call", {"name": tool, "arguments": args}, sid=sid, rid=7)
    text = ""
    if m and "result" in m:
        text = " ".join(c.get("text", "") for c in m["result"].get("content", []))
    return st, text

# --- client installer is served without auth and contains no token ---
try:
    _r = urllib.request.urlopen(urllib.request.Request(URL.rsplit("/mcp", 1)[0] + "/setup.sh",
                                headers={"User-Agent": "obscura-gw-test/1"}), timeout=30)
    _sh = _r.read().decode()
    check("GET /setup.sh serves the installer", _r.status == 200 and "claude mcp add" in _sh
          and "__BASE__" not in _sh)
except Exception as e:
    check("GET /setup.sh serves the installer", False, e)

# --- tokens via CLI (hot reload, no restart) ---
for leftover in ("machine-a", "machine-b", "laptop-b"):   # from an aborted earlier run
    cli("revoke-token", leftover)
ta = cli("add-token", "machine-a", "-q").stdout.strip()
tb = cli("add-token", "machine-b", "-q").stdout.strip()
check("add-token refuses duplicate name", cli("add-token", "machine-a").returncode != 0)
time.sleep(0.2)

st, sa = session(ta)
check("token A initialize", st == 200 and sa and sa.startswith("machine-a."), sa)
st, _, m = rpc(ta, "tools/list", sid=sa, rid=2)
names = [t["name"] for t in (m or {}).get("result", {}).get("tools", [])]
check("token A tools/list", len(names) > 5, f"{len(names)} tools")

check("no token -> 401", rpc(None, "initialize")[0] == 401)
check("bad token -> 401", rpc("deadbeef", "initialize")[0] == 401)

# keep-alive regression: a rejected request must not desync the next one on the same connection
u = urllib.parse.urlsplit(URL)
conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=60)
init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "ka", "version": "1"}}})
codes = []
for tok in ("deadbeef", ta, "deadbeef", ta):
    try:
        conn.request("POST", u.path, body=init, headers={"Authorization": "Bearer " + tok,
                     "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                     "User-Agent": "obscura-gw-test/1"})
        r = conn.getresponse(); r.read(); codes.append(r.status)
        if r.status == 200:
            sid = r.getheader("Mcp-Session-Id")
            if sid: delete(ta, sid)
    except Exception as e:
        conn.close(); codes.append(type(e).__name__)
        conn = (http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection)(u.netloc, timeout=60)
check("rejected request does not break the next one (keep-alive)", codes == [401, 200, 401, 200], codes)

st, sb = session(tb, headers={"X-Agent-Id": "machine-a"})
check("X-Agent-Id ignored (identity from token only)", sb and sb.startswith("machine-b."), sb)

st, _, _ = rpc(tb, "tools/list", sid=sa, rid=3)
check("token B cannot use A's session", st == 404, st)
st, _, _ = rpc(tb, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "x", "version": "1"}}, sid=sa)
check("token B cannot re-init into A's session id", st == 404, st)

# --- browser isolation: A sets state, B must not see it ---
nav = "browser_navigate" if "browser_navigate" in names else names[0]
call(ta, sa, nav, {"url": "https://example.com"})
call(tb, sb, nav, {"url": "https://example.com"})
call(ta, sa, "browser_set_cookie", {"name": "who", "value": "machineA", "domain": "example.com"})
_, a_sees = call(ta, sa, "browser_get_cookies", {"domain": "example.com"})
_, b_sees = call(tb, sb, "browser_get_cookies", {"domain": "example.com"})
check("A sees its own cookie", "machineA" in a_sees, a_sees.strip()[:80])
check("B does not see A's cookie", "machineA" not in b_sees, b_sees.strip()[:80])

# --- crash diagnostics: kill A's backend mid-session -> crash log + transparent heal ---
LABEL = os.environ.get("OBSCURA_GW_LABEL", "obscura-gw")
CRASH_DIR = os.environ.get("OBSCURA_GW_CRASH_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "crashes"))
def crash_files():
    try: return {f for f in os.listdir(CRASH_DIR) if f.endswith("-" + sa + ".log")}
    except OSError: return set()
before = crash_files()
cid = subprocess.run(["docker", "ps", "-q", "--filter", f"label={LABEL}-sid={sa}"], capture_output=True, text=True).stdout.split()
check("found A's backend container", len(cid) == 1, cid)
if cid:
    subprocess.run(["docker", "kill", cid[0]], capture_output=True)
    time.sleep(0.5)
    st, txt = call(ta, sa, nav, {"url": "https://example.com/?after-kill"})
    new = crash_files() - before
    body = open(os.path.join(CRASH_DIR, sorted(new)[-1])).read() if new else ""
    check("crash log written for killed backend", len(new) == 1, sorted(new))
    check("crash log has exit code + last URL", "exit_code:  137" in body and "example.com" in body)
    for f in new: os.unlink(os.path.join(CRASH_DIR, f))   # do not leave test noise in the real crash dir
    st, txt = call(ta, sa, nav, {"url": "https://example.com"})
    check("session healed after crash (same sid, fresh browser)", st == 200 and "Example Domain" in txt, txt.strip()[:80])
    alls = lambda: set(os.listdir(CRASH_DIR)) if os.path.isdir(CRASH_DIR) else set()
    n_before = alls()
    delete_sid = session(ta)[1]
    if delete_sid:
        call(ta, delete_sid, nav, {"url": "https://example.com"}); delete(ta, delete_sid)
    time.sleep(0.5)
    check("normal DELETE writes no crash log", delete_sid and alls() == n_before)

# --- PDF handling (gateway extracts text; obscura alone returns raw bytes) ---
check("tools/list advertises browser_pdf_text", "browser_pdf_text" in names)
call(ta, sa, nav, {"url": "https://arxiv.org/pdf/2408.11971"})
_, md = call(ta, sa, "browser_markdown", {})
check("browser_markdown on a PDF returns text, not %PDF bytes",
      not md.lstrip().startswith("%PDF") and "HoSZp" in md, md[:90].replace("\n", " "))
_, pt = call(ta, sa, "browser_pdf_text", {"url": "https://www.vldb.org/conf/1998/p476.pdf", "max_chars": 2000})
check("browser_pdf_text extracts a PDF by URL", "Materialized" in pt and "offset=" in pt, pt[:90].replace("\n", " "))
_, pt2 = call(ta, sa, "browser_pdf_text", {"url": "https://www.vldb.org/conf/1998/p476.pdf", "max_chars": 2000, "offset": 2000})
check("browser_pdf_text pages with offset", "showing 2000-" in pt2, pt2[:80].replace("\n", " "))
_, bad = call(ta, sa, "browser_pdf_text", {"url": "https://example.com"})
check("browser_pdf_text on non-PDF fails cleanly", "did not return a PDF" in bad, bad[:90])
_, us = call(ta, sa, nav, {"url": "https://www.usenix.org/conference/nsdi15/technical-sessions/presentation/agarwal"})
check("usenix page loads without killing the browser (obscura >=0.2.3)", "Succinct" in us, us[:90])

# --- persistent named session via X-Session-Id ---
st, sp = session(ta, headers={"X-Session-Id": "work"})
check("X-Session-Id named session scoped to token", sp == "machine-a.work", sp)

# --- DELETE by other token is a no-op; by owner closes ---
delete(tb, sa)
check("B's DELETE leaves A's session alive", rpc(ta, "tools/list", sid=sa, rid=4)[0] == 200)
delete(ta, sp)
check("owner DELETE closes session", rpc(ta, "tools/list", sid=sp, rid=5)[0] == 404)

# --- public-exposure limits ---
req = urllib.request.Request(URL, data=b"x" * (9 * 1024 * 1024), method="POST", headers={
      "Authorization": "Bearer " + ta, "Content-Type": "application/json", "User-Agent": "obscura-gw-test/1"})
try:
    code = urllib.request.urlopen(req, timeout=30).status
except urllib.error.HTTPError as e:
    code = e.code
except Exception as e:
    code = 413 if "413" in str(e) else str(e)
# direct connections may see the early close (EPIPE/reset) before reading the 413
check("oversized body rejected (413 or early close)", code == 413 or any(k in str(code) for k in ("Broken pipe", "reset")), code)
cap = int(os.environ.get("OBSCURA_GW_MAX_PER_AGENT", "5"))
extra = [session(ta)[1] for _ in range(cap + 1)]
check(f"per-token session cap ({cap})", None in extra and sum(1 for x in extra if x) <= cap,
      f"{sum(1 for x in extra if x)} opened")
for x in extra:
    if x: delete(ta, x)
check("cap does not block other tokens", session(tb)[1] is not None)

# --- rename + revoke (hot reload) ---
r = cli("rename-token", "machine-b", "laptop-b"); time.sleep(0.2)
st, sb2 = session(tb)
check("rename-token relabels same token", r.returncode == 0 and sb2 and sb2.startswith("laptop-b."), sb2)
cli("revoke-token", "machine-a"); time.sleep(0.2)
check("revoked token -> 401", rpc(ta, "tools/list", sid=sa, rid=6)[0] == 401)
check("other token unaffected by revoke", rpc(tb, "tools/list", sid=sb2, rid=8)[0] == 200)
cli("revoke-token", "laptop-b")
print("ALL PASS" if ok_all else "SOME FAILED")
sys.exit(0 if ok_all else 1)
