"""Conformance tests for docs/security-model.md §6 (audit log, SEC-010) and docs/capabilities.md §7 (consent prompt,
SEC-005).

The audit log is the host's record of what a program REQUESTED (its `use` declarations), what the host GRANTED, and what
each gated operation was DECIDED — without exposing sensitive data. These tests read the log back from disk and check:

  * every decision is recorded (allow before the operation, deny however late it arises), with the right fields;
  * nothing sensitive is in it: no file contents, no URL userinfo / path / query, and a hostile file name cannot forge
    a record (each record is exactly one line);
  * the interpreter and the VM write the same records;
  * the program cannot reach its own log: a write grant that could is refused when the policy is built;
  * if the log cannot be written the operation is NOT performed (checked on the real disk);

and, for the prompt (`request_consent`, driven here with in-memory streams; the real terminal path is tested through a
pseudo-terminal in tests/test_consent_cli.py):

  * only declared capabilities are asked about, and only ones no flag already granted — a prompt never widens a grant;
  * every default (Enter, EOF, 'y' without a scope, a wrong answer) is a denial; `everywhere` must be typed in full;
  * the permissions shown are the effective permissions.
"""
import os, io, re, sys, json, shutil, tempfile, threading, contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from harness import print_final_summary
import harness
import lumen
from lumen import (Policy, AuditLog, AuditError, CapabilityContext, capability_call, LumenError, LumenRaise,
                   PermissionDenied, parse_grant_flags, request_consent, permission_lines)

SPEC = "security-model.md §6 / capabilities.md §7"


def record(ok, name, detail=""):
    if ok:
        harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[{SPEC}] {name}: {detail}")


def read(p):
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def events(path):
    """The log as a list of dicts (every line must be valid JSON on its own)."""
    out = []
    with open(path) as f:
        for line in f.read().split("\n"):
            if line:
                out.append(json.loads(line))
    return out


def norm(evs):
    """Records without their timestamps (and without `backend`, which differs by design)."""
    return [{k: v for k, v in e.items() if k not in ("ts", "backend")} for e in evs]


class Tree:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.inside = os.path.join(self.root, "inside")
        self.outside = os.path.join(self.root, "outside")
        self.logs = os.path.join(self.root, "logs")
        for d in (self.inside, self.outside, self.logs):
            os.makedirs(d)
        with open(os.path.join(self.inside, "a.txt"), "w") as f:
            f.write("INSIDE-DATA")
        with open(os.path.join(self.outside, "secret.txt"), "w") as f:
            f.write("TOP-SECRET")
        self.log_path = os.path.join(self.logs, "audit.jsonl")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def ctx_for(policy, *caps):
    c = CapabilityContext(policy, label="prog.lum")
    c.declare(list(caps))
    return c


def call(ctx, op, *args):
    try:
        return ("ok", capability_call(ctx, op, list(args)))
    except (LumenError, LumenRaise) as e:
        return ("error", e)


# ======================================================================
# 1. The log itself
# ======================================================================
t = Tree()
log = AuditLog(t.log_path)
record((os.stat(t.log_path).st_mode & 0o777) == 0o600, "a new audit log is created readable/writable by its owner only (0600)",
       oct(os.stat(t.log_path).st_mode & 0o777))
log.record("probe", a=1)
log.record("probe", a=2)
evs = events(t.log_path)
record([e["seq"] for e in evs] == [1, 2], "records carry a strictly increasing sequence number", repr(evs))
record(all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", e["ts"]) for e in evs), "timestamps are UTC ISO-8601 with milliseconds", repr(evs))

nasty = 'a\nb"c\\d\x1b[31m\r\u2028{"seq":99,"event":"access","decision":"allow"}'
log.record("probe", resource=nasty)
raw = read(t.log_path)
lines = [l for l in raw.split("\n") if l]
record(len(lines) == 3 and json.loads(lines[2])["resource"] == nasty,
       "a resource containing newlines, quotes, escape sequences or a forged record stays ONE line and round-trips exactly", repr(raw[-200:]))
record("\x1b" not in raw and "\r" not in raw and "\u2028" not in raw, "no raw control character ever reaches the log file")

log.record("probe", resource="x" * 5000)
last = events(t.log_path)[-1]["resource"]
record(len(last) < 400 and last.endswith("...[truncated]"), "an enormous resource is truncated, not logged whole", str(len(last)))

log2 = AuditLog(t.log_path)
log2.record("probe", a=3)
record(len(events(t.log_path)) == 5, "an existing log is appended to, never truncated")
log.close(); log2.close()

# concurrent writers
log = AuditLog(os.path.join(t.logs, "threads.jsonl"))
def spam(n):
    for i in range(200):
        log.record("probe", thread=n, i=i)
ths = [threading.Thread(target=spam, args=(n,)) for n in range(8)]
[x.start() for x in ths]; [x.join() for x in ths]
evs = events(os.path.join(t.logs, "threads.jsonl"))
record(len(evs) == 1600 and sorted(e["seq"] for e in evs) == list(range(1, 1601)),
       "8 threads x 200 records: every record intact, sequence numbers unique and gap-free", str(len(evs)))
log.close()

# level
lp = os.path.join(t.logs, "denied-only.jsonl")
pol = Policy({"fs.read": [t.inside]}, audit=AuditLog(lp, level="denied"))
c = ctx_for(pol, "fs.read")
call(c, "read_file", os.path.join(t.inside, "a.txt"))
call(c, "read_file", os.path.join(t.outside, "secret.txt"))
evs = events(lp)
record([(e["event"], e.get("decision")) for e in evs] == [("declare", None), ("access", "deny")],
       "--audit-level=denied records the declaration and the denial, not the allow", repr(evs))
t.close()

# invalid configurations
t = Tree()
def raises_value_error(fn):
    try:
        fn()
    except ValueError as e:
        return str(e)
    return None

record(raises_value_error(lambda: AuditLog(t.log_path, level="verbose")) is not None, "an unknown audit level is refused")
record(raises_value_error(lambda: AuditLog("")) is not None, "an empty audit log path is refused")
record(raises_value_error(lambda: AuditLog(t.logs + os.sep)) is not None, "an audit log path naming a directory is refused")
record(raises_value_error(lambda: AuditLog(t.logs)) is not None, "an existing directory cannot be the audit log")
link = os.path.join(t.logs, "link.jsonl")
os.symlink(os.path.join(t.outside, "secret.txt"), link)
msg = raises_value_error(lambda: AuditLog(link))
record(msg is not None and "symbolic link" in msg and read(os.path.join(t.outside, "secret.txt")) == "TOP-SECRET",
       "an audit log path that is a symlink is refused and its target untouched", str(msg))
hard = os.path.join(t.logs, "hard.jsonl")
with open(hard, "w") as f:
    f.write("")
os.link(hard, os.path.join(t.inside, "alias.jsonl"))
msg = raises_value_error(lambda: AuditLog(hard))
record(msg is not None and "hard link" in msg, "an audit log with another hard link (reachable from elsewhere) is refused", str(msg))
t.close()

# ======================================================================
# 2. The program cannot reach its own log
# ======================================================================
t = Tree()
def policy_with(grants):
    return Policy(grants, audit=AuditLog(t.log_path))
msg = raises_value_error(lambda: policy_with({"fs.write": None}))
record(msg is not None and "unrestricted" in msg, "an unrestricted fs.write grant cannot be combined with an audit log", str(msg))
msg = raises_value_error(lambda: policy_with({"fs.write": [t.root]}))
record(msg is not None and "inside the fs.write scope" in msg, "an fs.write scope that contains the log is refused", str(msg))
msg = raises_value_error(lambda: policy_with({"fs.write": [t.logs]}))
record(msg is not None, "an fs.write scope that IS the log's directory is refused")
msg = raises_value_error(lambda: policy_with({"fs.write": [t.log_path]}))
record(msg is not None, "an fs.write scope that is the log file itself is refused")
os.symlink(t.logs, os.path.join(t.inside, "sneaky"))
msg = raises_value_error(lambda: policy_with({"fs.write": [os.path.join(t.inside, "sneaky")]}))
record(msg is not None, "an fs.write scope given as a symlink to the log's directory is refused (scopes are resolved first)", str(msg))
record(raises_value_error(lambda: policy_with({"fs.write": [t.inside]})) is None, "an fs.write scope elsewhere is fine")
record(raises_value_error(lambda: policy_with({"fs.read": None, "fs.list": None, "net.http": None})) is None,
       "read, list and network grants may be unrestricted (they cannot alter the log)")
base = Policy({"fs.read": [t.inside]}, audit=AuditLog(t.log_path))
msg = raises_value_error(lambda: base.extended({"fs.write": [t.logs]}))
record(msg is not None, "extending a policy re-checks the log: a prompt cannot add an fs.write scope that reaches it", str(msg))
record(raises_value_error(lambda: Policy({"fs.write": None})) is None, "without an audit log nothing changes: unrestricted fs.write is fine")

# Policy.extended only ADDS: an existing grant is never widened or replaced
t2 = Tree()
narrow = Policy({"fs.read": [t2.inside], "net.http": ["a.example:81"]})
grown = narrow.extended({"fs.read": None, "net.http": None, "fs.write": [t2.outside]})
record(grown.scope("fs.read") == (t2.inside,) and grown.scope("net.http") == (("a.example", 81),) and grown.scope("fs.write") == (t2.outside,),
       "Policy.extended never widens or replaces an existing grant, and adds the new ones", repr((grown.scope("fs.read"), grown.scope("net.http"))))
v6 = Policy({"net.http": ["[::1]:8080", "x.example"]}).extended({})
record(v6.scope("net.http") == (("::1", 8080), ("x.example", None)), "extending a policy round-trips IPv6 and host-only network scopes", repr(v6.scope("net.http")))
record(narrow.scope("fs.read") == (t2.inside,) and not narrow.granted("fs.write"), "…and the original policy is unchanged (policies are immutable)")
t2.close()

# a program holding fs.write for another directory cannot touch the log, and the log survives its attempts
pol = Policy({"fs.write": [t.inside], "fs.read": [t.inside]}, audit=AuditLog(t.log_path))
c = ctx_for(pol, "fs.write", "fs.read")
before = read(t.log_path)
r1 = call(c, "write_file", t.log_path, "FORGED")
r2 = call(c, "delete_file", t.log_path)
r3 = call(c, "append_file", os.path.join(t.inside, "..", "logs", "audit.jsonl"), "FORGED")
after = read(t.log_path)
record(all(isinstance(r[1], PermissionDenied) and r[1].reason == "out-of-scope" for r in (r1, r2, r3))
       and os.path.exists(t.log_path) and "FORGED" not in after,
       "write / delete / append aimed at the audit log are refused and the log is intact on disk", repr((r1, r2, r3)))
record(after.startswith(before) and after.count('"decision":"deny"') == 3, "…and the three attempts are in the log as denials", after)
t.close()

# ======================================================================
# 3. What is recorded for each decision
# ======================================================================
t = Tree()
pol = Policy({"fs.read": [t.inside], "fs.write": [t.inside], "fs.list": [t.inside]}, audit=AuditLog(t.log_path))
c = ctx_for(pol, "fs.read", "fs.write", "fs.list")
a_txt = os.path.join(t.inside, "a.txt")
new = os.path.join(t.inside, "new.txt")
calls = [("read_file", [a_txt]), ("file_exists", [a_txt]), ("is_dir", [t.inside]), ("list_dir", [t.inside]),
         ("write_file", [new, "TOPSECRETDATA-written"]), ("append_file", [new, "TOPSECRETDATA-appended"]),
         ("delete_file", [new])]
for op, args in calls:
    capability_call(c, op, args)
evs = [e for e in events(t.log_path) if e["event"] == "access"]
want = [(op, lumen.OPERATIONS[op][0], args[0]) for op, args in calls]
got = [(e["operation"], e["capability"], e["resource"]) for e in evs]
record(got == want and all(e["event"] == "access" and e["decision"] == "allow" and e["module"] == "prog.lum" for e in evs),
       "each of the 7 filesystem operations is recorded once as an allow with its capability, operation and resource", repr(got))
text = read(t.log_path)
record("INSIDE-DATA" not in text and "TOPSECRETDATA" not in text, "file contents — read or written — never appear in the log")
record(not any(k in e for e in evs for k in ("content", "data", "value", "args")), "records carry no content/argument fields")

n_before = len(events(t.log_path))
for bad in (lambda: capability_call(c, "read_file", [5]), lambda: capability_call(c, "write_file", [new]),
            lambda: capability_call(c, "read_file", ["a\x00b"])):
    try:
        bad()
    except LumenError:
        pass
record(len(events(t.log_path)) == n_before, "malformed calls (TypeError) are not permission events and are not logged")

# denial reasons
def last_decision(ctx, op, *args):
    n = len(events(t.log_path))
    r = call(ctx, op, *args)
    new_evs = events(t.log_path)[n:]
    return r, new_evs

r, ev = last_decision(c, "read_file", os.path.join(t.outside, "secret.txt"))
record(len(ev) == 1 and ev[0]["decision"] == "deny" and ev[0]["reason"] == "out-of-scope" and ev[0]["resource"] == os.path.join(t.outside, "secret.txt")
       and "TOP-SECRET" not in read(t.log_path), "out-of-scope: recorded as one denial with its reason, and the secret is not in the log", repr(ev))
nodecl = CapabilityContext(pol, label="prog.lum")
r, ev = last_decision(nodecl, "read_file", a_txt)
record(len(ev) == 1 and ev[0]["reason"] == "not-declared", "not-declared is recorded", repr(ev))
child = CapabilityContext(pol, label="lib.lum", parent=nodecl)
child.declare(["fs.read"])
r, ev = last_decision(child, "read_file", a_txt)
record(len(ev) == 1 and ev[0]["reason"] == "not-declared-by-importer" and ev[0]["module"] == "lib.lum",
       "not-declared-by-importer is recorded against the library", repr(ev))
ng = Policy({}, audit=pol.audit)
c2 = ctx_for(ng, "fs.read")
r, ev = last_decision(c2, "read_file", a_txt)
record(len(ev) == 1 and ev[0]["reason"] == "not-granted", "not-granted is recorded", repr(ev))

# declarations are the program's REQUESTS
fresh = CapabilityContext(pol, label="x.lum")
n = len(events(t.log_path))
fresh.declare(["fs.read", "net.http"])
fresh.declare(["fs.read"])                       # nothing new: nothing recorded
ev = events(t.log_path)[n:]
record(len(ev) == 1 and ev[0]["event"] == "declare" and ev[0]["capabilities"] == ["fs.read", "net.http"] and ev[0]["module"] == "x.lum",
       "a module's `use` declarations are recorded once, as its request", repr(ev))
t.close()

# ---- a denial that arises LATE (a directory swapped for a link between check and use) is recorded too ----
if hasattr(os, "symlink") and lumen._HAVE_DIRFD:
    t = Tree()
    os.makedirs(os.path.join(t.inside, "d"))
    pol = Policy({"fs.write": [t.inside]}, audit=AuditLog(t.log_path))
    c = ctx_for(pol, "fs.write")
    orig = lumen._fs_target
    def swapping(*a, **k):
        result = orig(*a, **k)
        os.rename(os.path.join(t.inside, "d"), os.path.join(t.inside, "d.moved"))
        os.symlink(t.outside, os.path.join(t.inside, "d"))
        return result
    lumen._fs_target = swapping
    try:
        r = call(c, "write_file", os.path.join(t.inside, "d", "created.txt"), "PAYLOAD")
    finally:
        lumen._fs_target = orig
    evs = [e for e in events(t.log_path) if e["event"] == "access"]
    record(isinstance(r[1], PermissionDenied) and not os.path.exists(os.path.join(t.outside, "created.txt"))
           and [(e["decision"], e.get("reason")) for e in evs] == [("allow", None), ("deny", "out-of-scope")],
           "a race caught after the check: nothing is created outside, and the log shows the allow and then the denial",
           repr((r, [(e["decision"], e.get("reason")) for e in evs])))
    t.close()

# ---- network: only scheme://host:port is ever logged; redirect hops are recorded ----
class _Handler(BaseHTTPRequestHandler):
    hits = []
    def do_GET(self):
        type(self).hits.append(self.path)
        if self.path.startswith("/hop"):
            self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{self.server.other}/landed?k=v"); self.end_headers(); return
        if self.path.startswith("/inner"):
            self.send_response(302); self.send_header("Location", "/p?token=abc"); self.end_headers(); return
        body = b"hello"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

class _Other(_Handler):
    hits = []

os.environ["NO_PROXY"] = "127.0.0.1,localhost"
sa, sb = ThreadingHTTPServer(("127.0.0.1", 0), _Handler), ThreadingHTTPServer(("127.0.0.1", 0), _Other)
sa.other = sb.server_address[1]
for s in (sa, sb):
    threading.Thread(target=s.serve_forever, daemon=True).start()
pa, pb = sa.server_address[1], sb.server_address[1]

t = Tree()
pol = Policy({"net.http": [f"127.0.0.1:{pa}"]}, audit=AuditLog(t.log_path))
c = ctx_for(pol, "net.http")
r = call(c, "http_get", f"http://127.0.0.1:{pa}/p?token=SEKRET#frag")
record(r == ("ok", "hello"), "positive control: the permitted request works", repr(r))
call(c, "http_get", f"http://user:hunter2@127.0.0.1:{pa}/p")     # (urllib cannot send userinfo, so this one fails at the OS; the decision is still logged)
r = call(c, "http_get", f"http://127.0.0.1:{pa}/inner/x")
record(r == ("ok", "hello"), "positive control: a redirect inside the granted host works", repr(r))
r = call(c, "http_get", f"http://127.0.0.1:{pa}/hop?secret=SEKRET2")
record(isinstance(r[1], PermissionDenied) and r[1].reason == "redirect-out-of-scope" and _Other.hits == [],
       "a redirect to an ungranted host is refused before it is requested (the other server saw nothing)", repr((r, _Other.hits)))
r = call(c, "http_get", f"http://evil:pw@example.invalid:81/x?token=SEKRET3")
text = read(t.log_path)
evs = [e for e in events(t.log_path) if e["event"] == "access"]
record(not any(s in text for s in ("hunter2", "SEKRET", "token", "frag", "/inner", "/hop", "/landed", "user:", "evil", "pw@")),
       "no URL userinfo, path, query or fragment ever reaches the log", text)
record([(e["decision"], e["resource"], e.get("redirect"), e.get("reason")) for e in evs] == [
    ("allow", f"http://127.0.0.1:{pa}", None, None),
    ("allow", f"http://127.0.0.1:{pa}", None, None),
    ("allow", f"http://127.0.0.1:{pa}", None, None),
    ("allow", f"http://127.0.0.1:{pa}", True, None),
    ("allow", f"http://127.0.0.1:{pa}", None, None),
    ("deny", f"http://127.0.0.1:{pb}", None, "redirect-out-of-scope"),
    ("deny", "http://example.invalid:81", None, "out-of-scope")],
    "requests and redirect hops are recorded as scheme://host:port only, hops marked `redirect`", repr([(e["decision"], e["resource"], e.get("redirect"), e.get("reason")) for e in evs]))
t.close(); sa.shutdown(); sb.shutdown()

# ======================================================================
# 4. A log that cannot be written stops the operation (fail closed)
# ======================================================================
t = Tree()
pol = Policy({"fs.write": [t.inside]}, audit=AuditLog(t.log_path))
c = ctx_for(pol, "fs.write")
os.close(pol.audit._fd)                       # the log is now unwritable (EBADF), as a full disk would be
target = os.path.join(t.inside, "must-not-exist.txt")
r = call(c, "write_file", target, "X")
record(isinstance(r[1], AuditError) and not os.path.exists(target) and "operation was not performed" in str(r[1]),
       "an allowed operation whose record cannot be written is NOT performed (checked on disk)", repr(r))
r = call(c, "write_file", os.path.join(t.outside, "x.txt"), "X")
record(isinstance(r[1], AuditError) and not os.path.exists(os.path.join(t.outside, "x.txt")),
       "…and a denial whose record cannot be written still grants nothing", repr(r))
pol.audit._fd = None
t.close()

# ======================================================================
# 5. Interpreter and VM write the same log
# ======================================================================
def run_on(backend, path, policy):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            (lumen.run_file_vm if backend == "vm" else lumen.run_file)(path, policy=policy)
    except (LumenError, LumenRaise) as e:
        return buf.getvalue(), str(e)
    return buf.getvalue(), None

t = Tree()
with open(os.path.join(t.root, "lib.lum"), "w") as f:
    f.write('use fs.read\nfn peek(p):\n    return read_file(p)\n')
with open(os.path.join(t.root, "lib2.lum"), "w") as f:
    f.write('fn peek(p):\n    return read_file(p)\n')          # declares nothing: an importer-ceiling denial
prog = (f'use fs\nuse net\nuse "lib.lum" as lib\nuse "lib2.lum" as lib2\n'
        f'let a = "{t.inside}/a.txt"\nlet s = "{t.outside}/secret.txt"\n'
        'print read_file(a)\nprint lib.peek(a)\n'
        'try:\n    print read_file(s)\ncatch e:\n    print e.reason\n'
        'try:\n    print lib2.peek(a)\ncatch e:\n    print e.reason\n'
        f'write_file("{t.inside}/w.txt", "x")\nprint file_exists(a)\nprint list_dir("{t.inside}")\n'
        'try:\n    print http_get("http://127.0.0.1:9/x?q=1")\ncatch e:\n    print e.reason\n')
pp = os.path.join(t.root, "prog.lum")
with open(pp, "w") as f:
    f.write(prog)
logs, outs = {}, {}
for backend in ("interpreter", "vm"):
    lp = os.path.join(t.logs, f"{backend}.jsonl")
    pol = Policy({"fs.read": [t.inside], "fs.write": [t.inside], "fs.list": [t.inside]}, audit=AuditLog(lp))
    outs[backend] = run_on(backend, pp, pol)
    logs[backend] = norm(events(lp))
    os.remove(os.path.join(t.inside, "w.txt"))
record(outs["interpreter"] == outs["vm"] and outs["interpreter"][1] is None, "the parity program runs identically on both backends", repr(outs))
decisions = [(e["event"], e.get("decision"), e.get("reason"), e.get("module")) for e in logs["interpreter"]]
record(logs["interpreter"] == logs["vm"],
       "the interpreter and the VM write identical audit records (declarations, allows and denials, library modules included)",
       f"\ninterpreter={decisions}\nvm={[(e['event'], e.get('decision'), e.get('reason'), e.get('module')) for e in logs['vm']]}")
record(("access", "deny", "not-declared-by-importer", "lib2.lum") not in decisions and ("access", "deny", "not-declared", "lib2.lum") in decisions
       and ("access", "deny", "out-of-scope", "prog.lum") in decisions and ("access", "allow", None, "lib.lum") in decisions
       and ("declare", None, None, "lib.lum") in decisions,
       "…and that log is not vacuous: it contains the library's declaration and allow, the library's missing-declaration denial and the out-of-scope denial", repr(decisions))
t.close()

# ======================================================================
# 6. Flags
# ======================================================================
t = Tree()
def flags(*a):
    try:
        pol, rest = parse_grant_flags(list(a))
        return pol, rest, None
    except ValueError as e:
        return None, None, str(e)

pol, rest, err = flags(f"--audit-log={t.log_path}", "--allow-read", "f.lum")
record(err is None and rest == ["f.lum"] and pol.audit is not None and pol.audit.level == "all", "--audit-log=FILE is accepted and removed from the arguments", str(err))
pol, rest, err = flags(f"--audit-log={t.log_path}", "--audit-level=denied")
record(err is None and pol.audit.level == "denied", "--audit-level=denied is accepted")
for bad, why in ((("--audit-log",), "--audit-log without a file"), (("--audit-log=",), "--audit-log with an empty value"),
                 (("--audit-level=denied",), "--audit-level without a log"), ((f"--audit-log={t.log_path}", "--audit-level=bogus"), "an unknown level"),
                 ((f"--audit-log={t.log_path}", f"--audit-log={t.log_path}"), "a repeated --audit-log"),
                 (("--audit-verbose",), "an unknown --audit flag"),
                 ((f"--audit-log={t.log_path}", f"--allow-write"), "--audit-log with an unrestricted --allow-write"),
                 ((f"--audit-log={t.log_path}", f"--allow-write={t.root}"), "--audit-log with a write scope that contains it")):
    pol, rest, err = flags(*bad)
    record(err is not None, f"{why} is an error", str(err))
t.close()

# ======================================================================
# 7. The consent prompt (request_consent), in-memory streams
# ======================================================================
def consent(policy, declared, answers, program="app.lum"):
    ins, outs = io.StringIO("".join(a + "\n" for a in answers)), io.StringIO()
    result = request_consent(policy, set(declared), program, ins, outs)
    return result, outs.getvalue()

t = Tree()
base = Policy()
r, shown = consent(base, [], [])
record(r is base and shown == "", "a program that declares nothing is not asked anything (and nothing is printed)")
r, shown = consent(Policy({"fs.read": None}), ["fs.read"], [])
record(r is not None and shown == "", "everything declared is already granted by flags: no prompt")

r, shown = consent(base, ["fs.read"], [t.inside, "y"])
record(r is not None and r.granted("fs.read") and r.scope("fs.read") == (t.inside,), "a path answer grants exactly that scope", shown)
record(not any(r.granted(c) for c in ("fs.write", "fs.list", "net.http")), "…and nothing else")
record("Allow net.http?" not in shown and "Allow fs.write?" not in shown, "a capability the program did not declare is never offered")

r, shown = consent(base, ["fs.read"], ["", "y"])
record(r is not None and not r.granted("fs.read"), "Enter at the scope prompt denies that capability", shown)
r, shown = consent(base, ["fs.read"], ["n", "y"])
record(r is not None and not r.granted("fs.read"), "'n' denies")
r, shown = consent(base, ["fs.read"], [t.inside, ""])
record(r is None, "Enter at the final question cancels: nothing runs")
r, shown = consent(base, ["fs.read"], [t.inside, "N"])
record(r is None, "'N' at the final question cancels")
r, shown = consent(base, ["fs.read"], [t.inside, "maybe"])
record(r is None, "anything but yes at the final question cancels")
r, shown = consent(base, ["fs.read"], [t.inside])                     # then end of input
record(r is None, "end of input at the final question cancels (a closed terminal cannot grant)")
r, shown = consent(base, ["fs.read"], [])
record(r is None, "end of input at the first question cancels")

r, shown = consent(base, ["fs.read"], ["everywhere", "y"])
record(r is not None and r.scope("fs.read") is None and r.granted("fs.read"), "the literal word 'everywhere' grants unrestricted access", shown)
for word in ("Everywhere", "EVERYWHERE", "*", "/", "all"):
    r, shown = consent(base, ["fs.read"], [word, "y"])
    record(r is not None and r.granted("fs.read") and r.scope("fs.read") is not None,
           f"{word!r} is NOT 'everywhere': it is read as a path and gives a scoped grant", repr(r and r.scope("fs.read")))

r, shown = consent(base, ["fs.read"], ["y", "yes", "Y", "y"])
record(r is not None and not r.granted("fs.read") and "too many invalid answers" in shown,
       "'y' without saying what to allow is not a grant: three of them leave the capability denied", shown)
r, shown = consent(base, ["fs.read"], ["yes", t.inside, "y"])
record(r is not None and r.scope("fs.read") == (t.inside,), "…a wrong answer is re-asked, and a valid one then works")

r, shown = consent(base, ["net.http"], ["http://x", "127.0.0.1:80", "y"])
record(r is not None and r.scope("net.http") == (("127.0.0.1", 80),), "an invalid host scope is rejected and re-asked", shown)
r, shown = consent(base, ["fs.read"], ["a,,b", t.inside, "y"])
record(r is not None and r.scope("fs.read") == (t.inside,), "an empty entry in a list is rejected and re-asked")

# a prompt never widens a flag grant and never re-asks about it
flagged = Policy({"fs.read": [t.inside]})
r, shown = consent(flagged, ["fs.read", "fs.write"], [t.outside, "y"])
record(r is not None and r.scope("fs.read") == (t.inside,) and r.scope("fs.write") == (t.outside,)
       and "Allow fs.read?" not in shown and "Allow fs.write?" in shown,
       "capabilities already granted by a flag are shown, not re-asked, and keep exactly their scope", shown)
record("granted by flag" in shown and t.inside in shown, "…and the display says which are granted by a flag")

# the display is honest about what is effective
lines = permission_lines(Policy({"fs.read": [t.inside], "fs.write": None, "net.http": ["h.example"]}), {"fs.read", "net.http"})
joined = "\n".join(lines)
record("fs.read" in lines[0] and "allowed" in lines[0] and "fs.write" in lines[1] and "does not declare" in lines[1]
       and lines[2].strip().endswith("denied") and "allowed: h.example" in lines[3],
       "effective permissions: granted+declared = allowed; granted but undeclared = denied and says why; the rest denied", joined)

# terminal safety
r, shown = consent(base, ["fs.read"], ["", "n"], program="a\x1b[31mred\x07.lum")
record("\x1b" not in shown and "\x07" not in shown and "\\x1b" in shown, "control characters in the program name are shown escaped, never sent to the terminal", repr(shown[:80]))
weird = os.path.join(t.inside, "d\x1b[2Jir")
r, shown = consent(base, ["fs.read"], [weird, "y"])
record("\x1b" not in shown, "…and so are control characters in a path typed as a scope")

# a combination the audit log refuses is re-asked, never a crash (this was a traceback when first written)
lp = os.path.join(t.logs, "refuse.jsonl")
pol = Policy({}, audit=AuditLog(lp))
r, shown = consent(pol, ["fs.write"], ["everywhere", t.root, t.inside, "y"])
record(r is not None and r.scope("fs.write") == (t.inside,) and "unrestricted" in shown and "lies inside" in shown,
       "typing 'everywhere' (or a scope containing the log) for fs.write next to an audit log is refused with an explanation and re-asked; a safe scope then works", shown)
r, shown = consent(pol, ["fs.write"], ["everywhere", "", "y"])
record(r is not None and not r.granted("fs.write"), "…and Enter after a refusal leaves the capability denied")
weird_logs = os.path.join(t.root, "l\x1b[2Jogs")
os.makedirs(weird_logs)
pol_w = Policy({}, audit=AuditLog(os.path.join(weird_logs, "a.jsonl")))
r, shown = consent(pol_w, ["fs.write"], [t.root, "", "y"])
record("lies inside" in shown and "\x1b" not in shown, "text from a refusal (which can contain a hostile directory name) is escaped before it reaches the terminal", repr(shown[-300:]))

# a garbled terminal cannot grant
import codecs
raw = io.TextIOWrapper(io.BytesIO(b"\xff\xfe\n"), encoding="utf-8", errors="strict")
outs = io.StringIO()
record(request_consent(Policy(), {"fs.read"}, "app.lum", raw, outs) is None, "input that cannot be decoded cancels the prompt (nothing runs)")

# consent decisions are audited
lp = os.path.join(t.logs, "consent.jsonl")
pol = Policy({"fs.list": None}, audit=AuditLog(lp))
r, shown = consent(pol, ["fs.read", "fs.write", "fs.list"], [t.inside, "", "y"])
evs = events(lp)
record([(e["event"], e.get("decision"), e.get("capability")) for e in evs] ==
       [("request", None, None), ("consent", "granted", "fs.read"), ("consent", "denied", "fs.write"), ("confirm", "run", None)]
       and evs[0]["undecided"] == ["fs.read", "fs.write"] and evs[0]["granted_by_flags"] == ["fs.list"] and evs[1]["scope"] == t.inside,
       "the request, each consent answer and the final decision are written to the audit log", repr(evs))
lp = os.path.join(t.logs, "consent2.jsonl")
pol = Policy({}, audit=AuditLog(lp))
consent(pol, ["fs.read"], [t.inside, "n"])
record(events(lp)[-1] == {k: v for k, v in events(lp)[-1].items()} and events(lp)[-1]["decision"] == "cancelled", "a cancelled run is logged as cancelled")
t.close()

print_final_summary()
