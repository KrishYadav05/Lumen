#!/usr/bin/env python3
"""Phase 4/5: security hardening — adversarial capability-bypass attempts.

docs/security.md (ch. 7) and docs/capabilities.md specify the capability model
normatively, and tests/conformance/test_security.py + test_capabilities.py check
the documented claims in-process against the interpreter. This suite is
different on purpose: it tries to actually BREAK the model, through the real
command line, on BOTH backends, and it verifies actual resource access rather
than error messages:

  * a denied write is checked by looking at the disk afterwards;
  * a denied read is checked by confirming the secret never reaches stdout;
  * a denied network request is checked by counting the requests a real local
    HTTP server received.

The categories follow the Phase 5 test plan (docs/security-testing.md):
default-deny, read/write separation, path restrictions, network restrictions,
module security, runtime parity, error handling, CLI behaviour, and
regressions for every bypass found so far.

Every case is one of:
  (a) an attempt that MUST fail, identically, on both backends, or
  (b) an outcome the spec allows that both backends must produce exactly.
"""
import subprocess, sys, os, tempfile, shutil, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

LUMEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lumen.py")
# The loopback servers below are reached directly even where a proxy is configured.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

PASS = 0
FAIL = 0
FAILURES = []


def _norm(out, *names):
    for n in names:
        out = out.replace(n, "<program>")
    return out


def run_backend(src, vm, flags=(), stdin_text=None):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + list(flags) + [path]
    r = subprocess.run(args, capture_output=True, text=True, input=stdin_text if stdin_text is not None else "")
    os.unlink(path)
    return _norm(r.stdout, os.path.basename(path)), r.returncode


def run_backend_multifile(files, entry, vm, flags=()):
    d = tempfile.mkdtemp()
    try:
        for fname, content in files.items():
            fpath = os.path.join(d, fname)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            with open(fpath, "w") as f:
                f.write(content.replace("{d}", d))
        args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + list(flags) + [os.path.join(d, entry)]
        r = subprocess.run(args, capture_output=True, text=True, input="")
        return r.stdout.replace(d, "<dir>"), r.returncode
    finally:
        shutil.rmtree(d)


def _record(ok, failure_text):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(failure_text)


def blocked_on_both(name, src, must_contain, flags=()):
    """BOTH backends must reject `src` (non-zero exit) with `must_contain` in the output — a real
    PermissionError / NameError, not a crash, not a silent success, and not two backends
    disagreeing about why."""
    out_i, rc_i = run_backend(src, vm=False, flags=flags)
    out_v, rc_v = run_backend(src, vm=True, flags=flags)
    _record(rc_i != 0 and rc_v != 0 and must_contain in out_i and must_contain in out_v,
            f"NOT BLOCKED in {name!r}: interp(rc={rc_i})={out_i!r} vm(rc={rc_v})={out_v!r} (expected both to fail containing {must_contain!r})")


def blocked_on_both_multifile(name, files, entry, must_contain, flags=()):
    out_i, rc_i = run_backend_multifile(files, entry, vm=False, flags=flags)
    out_v, rc_v = run_backend_multifile(files, entry, vm=True, flags=flags)
    _record(rc_i != 0 and rc_v != 0 and must_contain in out_i and must_contain in out_v,
            f"NOT BLOCKED in {name!r}: interp(rc={rc_i})={out_i!r} vm(rc={rc_v})={out_v!r} (expected both to fail containing {must_contain!r})")


def equivalent_on_both(name, src, flags=()):
    out_i, rc_i = run_backend(src, vm=False, flags=flags)
    out_v, rc_v = run_backend(src, vm=True, flags=flags)
    _record(out_i == out_v and rc_i == rc_v, f"DIVERGENCE in {name!r}: interp(rc={rc_i})={out_i!r} vm(rc={rc_v})={out_v!r}")


def equivalent_on_both_multifile(name, files, entry, flags=()):
    out_i, rc_i = run_backend_multifile(files, entry, vm=False, flags=flags)
    out_v, rc_v = run_backend_multifile(files, entry, vm=True, flags=flags)
    _record(out_i == out_v and rc_i == rc_v, f"DIVERGENCE in {name!r}: interp(rc={rc_i})={out_i!r} vm(rc={rc_v})={out_v!r}")


def expect_on_both(name, src, expected_stdout, flags=(), rc=0):
    """Both backends must produce EXACTLY this stdout and exit code — a positive assertion, so two
    backends failing identically for the wrong reason cannot pass."""
    for vm in (False, True):
        out, got_rc = run_backend(src, vm=vm, flags=flags)
        _record(out.strip() == expected_stdout.strip() and got_rc == rc,
                f"WRONG OUTPUT in {name!r} ({'vm' if vm else 'interp'}): rc={got_rc} out={out!r} expected rc={rc} out={expected_stdout!r}")


def expect_report_on_both(name, src, first_lines, code, flags=(), rc=1):
    """An uncaught failure on both backends: stdout BEGINS with exactly `first_lines` (the program's own output and
    the failure's message — the part every release since 1.0 has printed), then names its stable `code`, then
    gives the calls that led to it (Lumen 3.1 added those two things after the message)."""
    for vm in (False, True):
        out, got_rc = run_backend(src, vm=vm, flags=flags)
        ok = (out.startswith(first_lines) and got_rc == rc
              and out[len(first_lines):].lstrip("\n").startswith(f"  code: {code}\n  trace (most recent call last):\n"))
        _record(ok, f"WRONG OUTPUT in {name!r} ({'vm' if vm else 'interp'}): rc={got_rc} out={out!r} expected to begin {first_lines!r} then code {code}")


class World:
    """A scratch directory with an authorised `inside/`, an unauthorised `outside/` holding a secret, and
    a lookalike sibling `inside-evil/`. Rebuilt for every case so one case's writes cannot hide another's leak."""
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.inside = os.path.join(self.root, "inside")
        self.outside = os.path.join(self.root, "outside")
        self.evil = os.path.join(self.root, "inside-evil")
        for d in (self.inside, self.outside, self.evil):
            os.makedirs(d)
        self.write(self.inside, "a.txt", "INSIDE-A")
        self.write(self.outside, "secret.txt", "TOP-SECRET-OUTSIDE")
        self.write(self.evil, "secret.txt", "TOP-SECRET-EVIL")

    @staticmethod
    def write(d, name, text):
        with open(os.path.join(d, name), "w") as f:
            f.write(text)

    def read(self, *parts):
        with open(os.path.join(*parts)) as f:
            return f.read()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def with_world(fn):
    for vm in (False, True):
        w = World()
        try:
            fn(w, vm)
        finally:
            w.close()


class Server:
    """A real local HTTP server that records every request it receives."""
    def __init__(self):
        self.hits = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                q = urlsplit(self.path)
                if q.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", parse_qs(q.query)["to"][0])
                    self.end_headers()
                    return
                body = b"served:" + q.path.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# =====================================================================
# DEFAULT-DENY (SEC-001/002): nothing without BOTH a declaration and a host grant
# =====================================================================
blocked_on_both("no declaration, no grant: read_file is denied", 'print read_file("/tmp/x")', "PermissionError")
blocked_on_both("no declaration, no grant: http_get is denied", 'print http_get("http://x")', "PermissionError")
for call in ('read_file("/tmp/x")', 'file_exists("/tmp/x")', 'is_dir("/tmp")', 'write_file("/tmp/x", "y")',
             'append_file("/tmp/x", "y")', 'delete_file("/tmp/x")', 'list_dir("/tmp")', 'http_get("http://x")'):
    op = call.split("(")[0]
    blocked_on_both(f"{op}: declaring fs+net WITHOUT a host grant is denied", f'use fs\nuse net\nprint {call}',
                    "was not granted for this run")
    blocked_on_both(f"{op}: a host grant of everything WITHOUT a declaration is denied", f'print {call}',
                    "has not declared", flags=("--allow-fs", "--allow-net"))


def _default_deny_disk(w, vm):
    target = os.path.join(w.outside, "created.txt")
    src = (f'use fs\ntry:\n    write_file("{target}", "x")\ncatch e:\n    print e.reason\n'
           f'try:\n    append_file("{w.outside}/secret.txt", "TAMPER")\ncatch e:\n    print e.reason\n'
           f'try:\n    delete_file("{w.outside}/secret.txt")\ncatch e:\n    print e.reason\n'
           f'try:\n    print read_file("{w.outside}/secret.txt")\ncatch e:\n    print e.reason')
    out, rc = run_backend(src, vm)
    _record(out.split() == ["not-granted"] * 4 and rc == 0
            and not os.path.exists(target) and w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE"
            and "TOP-SECRET" not in out,
            f"DEFAULT-DENY changed the world ({'vm' if vm else 'interp'}): out={out!r}")
with_world(_default_deny_disk)

_s = Server()
try:
    for vm in (False, True):
        out, rc = run_backend(f'use net\ntry:\n    http_get("http://127.0.0.1:{_s.port}/default-deny")\ncatch e:\n    print e.reason', vm)
        _record(out.strip() == "not-granted" and _s.hits == [],
                f"DEFAULT-DENY reached the network ({'vm' if vm else 'interp'}): out={out!r} server_hits={_s.hits}")
finally:
    _s.close()

# =====================================================================
# READ / WRITE SEPARATION (SEC-003)
# =====================================================================
def _read_only(w, vm):
    src = (f'use fs\nprint read_file("{w.inside}/a.txt")\n'
           f'try:\n    write_file("{w.inside}/a.txt", "CHANGED")\ncatch e:\n    print "write:" + e.reason\n'
           f'try:\n    append_file("{w.inside}/a.txt", "CHANGED")\ncatch e:\n    print "append:" + e.reason\n'
           f'try:\n    delete_file("{w.inside}/a.txt")\ncatch e:\n    print "delete:" + e.reason\n'
           f'try:\n    print list_dir("{w.inside}")\ncatch e:\n    print "list:" + e.reason')
    out, rc = run_backend(src, vm, flags=("--allow-read",))
    _record(out.split("\n")[:-1] == ["INSIDE-A", "write:not-granted", "append:not-granted", "delete:not-granted", "list:not-granted"]
            and w.read(w.inside, "a.txt") == "INSIDE-A",
            f"read-only grant leaked write/list ({'vm' if vm else 'interp'}): out={out!r} file={w.read(w.inside, 'a.txt')!r}")
with_world(_read_only)

def _write_only(w, vm):
    src = (f'use fs\nwrite_file("{w.inside}/new.txt", "written")\n'
           f'try:\n    print read_file("{w.inside}/a.txt")\ncatch e:\n    print "read:" + e.reason\n'
           f'try:\n    print file_exists("{w.inside}/a.txt")\ncatch e:\n    print "exists:" + e.reason')
    out, rc = run_backend(src, vm, flags=("--allow-write",))
    _record(out.split() == ["read:not-granted", "exists:not-granted"] and w.read(w.inside, "new.txt") == "written"
            and "INSIDE-A" not in out,
            f"write-only grant leaked read ({'vm' if vm else 'interp'}): out={out!r}")
with_world(_write_only)

def _list_only(w, vm):
    src = (f'use fs\nprint list_dir("{w.inside}")\n'
           f'try:\n    print read_file("{w.inside}/a.txt")\ncatch e:\n    print "read:" + e.reason')
    out, rc = run_backend(src, vm, flags=("--allow-list",))
    _record(out.split("\n")[:-1] == ["[a.txt]", "read:not-granted"], f"list-only grant leaked read ({'vm' if vm else 'interp'}): out={out!r}")
with_world(_list_only)

# a network grant never implies filesystem access, and the reverse (SEC-003)
blocked_on_both("a net grant does not grant fs", 'use fs\nuse net\nprint read_file("/etc/hostname")', "not granted for this run",
                flags=("--allow-net",))
blocked_on_both("an fs grant does not grant net", 'use fs\nuse net\nprint http_get("http://127.0.0.1:9/")', "not granted for this run",
                flags=("--allow-fs",))

# =====================================================================
# PATH RESTRICTIONS (SEC-007)
# =====================================================================
def _scoped(w, vm, prog, expect, flag_kind="read", also=None):
    out, rc = run_backend(prog, vm, flags=(f"--allow-{flag_kind}={w.inside}",))
    ok = out.strip() == expect and "TOP-SECRET" not in out
    if also:
        ok = ok and also(w)
    _record(ok, f"PATH RESTRICTION failed ({'vm' if vm else 'interp'}): out={out!r} expected {expect!r}")

def _path_cases():
    def run_case(label, build_prog, expect, flag_kind="read", also=None, pre=None):
        def body(w, vm):
            if pre:
                pre(w)
            _scoped(w, vm, build_prog(w), expect, flag_kind, also)
        with_world(body)
        _ = label

    attempt = lambda path: 'use fs\ntry:\n    print read_file("%s")\ncatch e:\n    print e.reason' % path
    run_case("inside", lambda w: attempt(f"{w.inside}/a.txt"), "INSIDE-A")
    run_case("outside", lambda w: attempt(f"{w.outside}/secret.txt"), "out-of-scope")
    run_case("dot-dot", lambda w: attempt(f"{w.inside}/../outside/secret.txt"), "out-of-scope")
    run_case("deep dot-dot", lambda w: attempt(f"{w.inside}/a.txt/../../outside/secret.txt"), "out-of-scope")
    run_case("dot-dot after a missing dir", lambda w: attempt(f"{w.inside}/missing/../../outside/secret.txt"), "out-of-scope")
    run_case("prefix-sibling", lambda w: attempt(f"{w.evil}/secret.txt"), "out-of-scope")
    run_case("absolute /etc", lambda w: attempt("/etc/hostname"), "out-of-scope")
    run_case("double slash", lambda w: attempt(f"{w.root}//outside//secret.txt"), "out-of-scope")
    run_case("proc self root", lambda w: attempt(f"/proc/self/root{w.outside}/secret.txt"), "out-of-scope")
    run_case("exists oracle", lambda w: 'use fs\ntry:\n    print file_exists("%s/secret.txt")\ncatch e:\n    print e.reason' % w.outside, "out-of-scope")
    run_case("list parent", lambda w: 'use fs\ntry:\n    print list_dir("%s")\ncatch e:\n    print e.reason' % w.root, "out-of-scope", "list")
    # writes
    run_case("write outside creates nothing",
             lambda w: 'use fs\ntry:\n    write_file("%s/new.txt", "x")\ncatch e:\n    print e.reason' % w.outside, "out-of-scope", "write",
             also=lambda w: not os.path.exists(os.path.join(w.outside, "new.txt")))
    run_case("write via dot-dot creates nothing",
             lambda w: 'use fs\ntry:\n    write_file("%s/../outside/new.txt", "x")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "write",
             also=lambda w: not os.path.exists(os.path.join(w.outside, "new.txt")))
    run_case("delete outside leaves it",
             lambda w: 'use fs\ntry:\n    delete_file("%s/secret.txt")\ncatch e:\n    print e.reason' % w.outside, "out-of-scope", "write",
             also=lambda w: w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE")
    run_case("append outside leaves it",
             lambda w: 'use fs\ntry:\n    append_file("%s/secret.txt", "X")\ncatch e:\n    print e.reason' % w.outside, "out-of-scope", "write",
             also=lambda w: w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE")

    if hasattr(os, "symlink"):
        link = lambda w: os.symlink(os.path.join(w.outside, "secret.txt"), os.path.join(w.inside, "link.txt"))
        run_case("symlink read", lambda w: attempt(f"{w.inside}/link.txt"), "out-of-scope", pre=link)
        run_case("symlink write leaves target",
                 lambda w: 'use fs\ntry:\n    write_file("%s/link.txt", "X")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "write",
                 pre=link, also=lambda w: w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE")
        run_case("symlink append leaves target",
                 lambda w: 'use fs\ntry:\n    append_file("%s/link.txt", "X")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "write",
                 pre=link, also=lambda w: w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE")
        run_case("dir symlink traversal",
                 lambda w: attempt(f"{w.inside}/dl/secret.txt"), "out-of-scope",
                 pre=lambda w: os.symlink(w.outside, os.path.join(w.inside, "dl")))
        run_case("dir symlink listing",
                 lambda w: 'use fs\ntry:\n    print list_dir("%s/dl")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "list",
                 pre=lambda w: os.symlink(w.outside, os.path.join(w.inside, "dl")))
        run_case("dir symlink create-through",
                 lambda w: 'use fs\ntry:\n    write_file("%s/dl/made.txt", "x")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "write",
                 pre=lambda w: os.symlink(w.outside, os.path.join(w.inside, "dl")),
                 also=lambda w: not os.path.exists(os.path.join(w.outside, "made.txt")))
        run_case("dangling symlink create-through",
                 lambda w: 'use fs\ntry:\n    write_file("%s/dang.txt", "x")\ncatch e:\n    print e.reason' % w.inside, "out-of-scope", "write",
                 pre=lambda w: os.symlink(os.path.join(w.outside, "ghost.txt"), os.path.join(w.inside, "dang.txt")),
                 also=lambda w: not os.path.exists(os.path.join(w.outside, "ghost.txt")))
        run_case("delete removes only the link",
                 lambda w: 'use fs\ndelete_file("%s/link.txt")\nprint "deleted"' % w.inside, "deleted", "write",
                 pre=link, also=lambda w: (not os.path.lexists(os.path.join(w.inside, "link.txt"))
                                           and w.read(w.outside, "secret.txt") == "TOP-SECRET-OUTSIDE"))
        run_case("link that stays inside is fine",
                 lambda w: attempt(f"{w.inside}/inner-link.txt"), "INSIDE-A",
                 pre=lambda w: os.symlink(os.path.join(w.inside, "a.txt"), os.path.join(w.inside, "inner-link.txt")))
_path_cases()

# =====================================================================
# NETWORK RESTRICTIONS (SEC-008)
# =====================================================================
A, B = Server(), Server()
try:
    def net_case(name, url_a_hits_expected, prog, flags, expect_out, a_hits, b_hits, vm):
        A.hits.clear(); B.hits.clear()
        out, rc = run_backend(prog, vm, flags=flags)
        _record(out.strip() == expect_out and A.hits == a_hits and B.hits == b_hits,
                f"NETWORK {name} ({'vm' if vm else 'interp'}): out={out!r} (want {expect_out!r}) A={A.hits} (want {a_hits}) B={B.hits} (want {b_hits})")

    get = lambda url: ('use net\ntry:\n    print http_get("%s")\ncatch e:\n    if e.kind == "PermissionError":\n'
                       '        print e.reason\n    else:\n        print e.kind + ":" + e.message' % url)
    only_a = (f"--allow-net=127.0.0.1:{A.port}",)
    for vm in (False, True):
        net_case("allowed host:port is reached", None, get(f"http://127.0.0.1:{A.port}/ok"), only_a, "served:/ok", ["/ok"], [], vm)
        net_case("other port is denied and receives nothing", None, get(f"http://127.0.0.1:{B.port}/nope"), only_a, "out-of-scope", [], [], vm)
        net_case("host-only scope allows any port", None, get(f"http://127.0.0.1:{B.port}/any"), ("--allow-net=127.0.0.1",), "served:/any", [], ["/any"], vm)
        net_case("no net grant: nothing is sent", None, get(f"http://127.0.0.1:{A.port}/none"), (), "not-granted", [], [], vm)
        net_case("redirect to an ungranted host is stopped before the request",
                 None, get(f"http://127.0.0.1:{A.port}/redirect?to=http://127.0.0.1:{B.port}/stolen"), only_a,
                 "redirect-out-of-scope", [f"/redirect?to=http://127.0.0.1:{B.port}/stolen"], [], vm)
        net_case("redirect inside the grant is followed",
                 None, get(f"http://127.0.0.1:{A.port}/redirect?to=http://127.0.0.1:{A.port}/landed"), only_a,
                 "served:/landed", [f"/redirect?to=http://127.0.0.1:{A.port}/landed", "/landed"], [], vm)
        net_case("a different name for the same address is a different host",
                 None, get(f"http://localhost:{A.port}/alias"), only_a, "out-of-scope", [], [], vm)

    # http_get must not read local files, even with net granted everywhere (REGRESSION: found in Phase 5)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        f.write("LOCAL-FILE-SECRET")
        secret_path = f.name
    try:
        for vm in (False, True):
            for url in (f"file://{secret_path}", f"FILE://{secret_path}", f"file:{secret_path}", f"file://localhost{secret_path}"):
                out, rc = run_backend(f'use net\ntry:\n    print http_get("{url}")\ncatch e:\n    print e.message', vm, flags=("--allow-net",))
                _record("LOCAL-FILE-SECRET" not in out and "only http:// and https://" in out,
                        f"REGRESSION file:// via http_get ({'vm' if vm else 'interp'}) url={url!r}: out={out!r}")
            A.hits.clear()
            out, rc = run_backend(f'use net\ntry:\n    print http_get("http://127.0.0.1:{A.port}/redirect?to=file://{secret_path}")\ncatch e:\n    print e.message',
                                  vm, flags=("--allow-net",))
            _record("LOCAL-FILE-SECRET" not in out, f"REGRESSION redirect to file:// ({'vm' if vm else 'interp'}): out={out!r}")
    finally:
        os.unlink(secret_path)

    # a denial echoes neither credentials nor query strings
    for vm in (False, True):
        out, rc = run_backend(f'use net\ntry:\n    http_get("http://admin:hunter2@127.0.0.1:{B.port}/p?apikey=SEKRIT")\ncatch e:\n    print e.message\n    print e.resource',
                              vm, flags=only_a)
        _record("hunter2" not in out and "SEKRIT" not in out, f"credentials leaked into a denial ({'vm' if vm else 'interp'}): {out!r}")
finally:
    A.close(); B.close()

# =====================================================================
# MODULE SECURITY (SEC-009)
# =====================================================================
ALLF = ("--allow-fs", "--allow-net")
blocked_on_both_multifile("a library cannot use a capability its importer did not declare",
    {"main.lum": 'use "lib.lum" as l\nprint l.go()', "lib.lum": 'use fs.read\nfn go():\n    return read_file("/etc/hostname")'},
    "main.lum", "does not — a library cannot hold authority its importer lacks", flags=ALLF)
blocked_on_both_multifile("importing a module that declares fs does not give the importer fs (std/fs.lum no longer acts as a grant)",
    {"main.lum": 'use "helper.lum" as h\nprint read_file("/etc/hostname")', "helper.lum": 'use fs'},
    "main.lum", "has not declared", flags=ALLF)
blocked_on_both_multifile("a declaration two import-hops away does not extend to the top-level program",
    {"main.lum": 'use "b.lum" as b\nprint read_file("/etc/hostname")', "b.lum": 'use "c.lum" as c\n', "c.lum": 'use fs'},
    "main.lum", "has not declared", flags=ALLF)
blocked_on_both_multifile("an importer's declaration does not extend to an imported module lacking its own",
    {"main.lum": 'use fs\nuse "helper.lum" as h\nprint h.try_read()', "helper.lum": 'fn try_read():\n    return read_file("/etc/hostname")'},
    "main.lum", "has not declared", flags=ALLF)
blocked_on_both_multifile("the most destructive builtin, delete_file, gets the same module-boundary rule",
    {"main.lum": 'use fs\nuse "helper.lum" as h\nprint h.try_delete()', "helper.lum": 'fn try_delete():\n    return delete_file("/tmp/x")'},
    "main.lum", "has not declared", flags=ALLF)
blocked_on_both_multifile("two independent `use` of one module path each have their own declarations (no caching)",
    {"main.lum": 'use "helper.lum" as h1\nuse "helper.lum" as h2\nprint read_file("/etc/hostname")', "helper.lum": 'use fs'},
    "main.lum", "has not declared", flags=ALLF)
blocked_on_both_multifile("a library declaring write cannot write when the importer declared only read",
    {"main.lum": 'use fs.read\nuse "lib.lum" as l\nprint l.go()', "lib.lum": 'use fs\nfn go():\n    write_file("/tmp/_lumen_never_written", "x")\n    return 1'},
    "main.lum", "does not — a library cannot hold authority", flags=ALLF)
def _lib_scope(vm):
    d = os.path.realpath(tempfile.mkdtemp())
    try:
        os.makedirs(os.path.join(d, "data"))
        with open(os.path.join(d, "data", "ok.txt"), "w") as f: f.write("OK-DATA")
        with open(os.path.join(d, "secret.txt"), "w") as f: f.write("LIB-SECRET")
        main = ('use fs.read\nuse "lib.lum" as l\nprint l.get("%s/data/ok.txt")\n'
                'try:\n    print l.get("%s/secret.txt")\ncatch e:\n    print e.reason' % (d, d))
        lib = 'use fs.read\nfn get(p):\n    return read_file(p)'
        for name, text in (("main.lum", main), ("lib.lum", lib)):
            with open(os.path.join(d, name), "w") as f: f.write(text)
        args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + [f"--allow-read={d}/data", os.path.join(d, "main.lum")]
        r = subprocess.run(args, capture_output=True, text=True, input="")
        _record(r.stdout.split() == ["OK-DATA", "out-of-scope"] and "LIB-SECRET" not in r.stdout,
                f"a library must be bound by the host's path scope ({'vm' if vm else 'interp'}): {r.stdout!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
_lib_scope(False); _lib_scope(True)

# importing is loading CODE and is not gated by fs.read — so it must not become a way to read DATA
# (REGRESSION: found in Phase 5; `use "secret.txt" as s` used to leak the first token of the file
# through the parse error, and "cannot find" vs "error loading" revealed which paths exist).
for vm in (False, True):
    for label, files in (("a .txt data file", {"main.lum": 'use "secret.txt" as s', "secret.txt": "TOP-SECRET-PASSWORD 12345\n"}),
                         ("a JSON data file", {"main.lum": 'use "secret.json" as s', "secret.json": '{"password": "TOP-SECRET-PASSWORD"}'}),
                         ("a dotfile with no extension", {"main.lum": 'use ".env" as s', ".env": "TOP_SECRET_PASSWORD=hunter2"}),
                         ("a path outside the project", {"main.lum": 'use "/etc/passwd" as s'})):
        out, rc = run_backend_multifile(files, "main.lum", vm)
        _record(rc != 0 and "cannot find module" in out and "TOP" not in out and "hunter2" not in out and "root:" not in out
                and "error loading" not in out,
                f"REGRESSION import-as-read-primitive: {label} ({'vm' if vm else 'interp'}): rc={rc} out={out!r}")
    # the answer for an existing non-.lum file is IDENTICAL to the answer for a missing one (no existence oracle)
    exist, _ = run_backend_multifile({"main.lum": 'use "a.txt" as s', "a.txt": "x"}, "main.lum", vm)
    missing, _ = run_backend_multifile({"main.lum": 'use "a.txt" as s'}, "main.lum", vm)
    _record(exist == missing, f"REGRESSION import existence oracle ({'vm' if vm else 'interp'}): {exist!r} vs {missing!r}")

# a module a program WRITES at run time and then imports is still a library: it cannot exceed its importer
def _generated_module(vm):
    d = os.path.realpath(tempfile.mkdtemp())
    try:
        os.makedirs(os.path.join(d, "w"))
        main = ('use fs.write\nwrite_file("%s/w/gen.lum", "use fs.read" + chr(10) + "fn grab():" + chr(10) + "    return read_file(" '
                '+ chr(34) + "/etc/hostname" + chr(34) + ")" + chr(10))\nuse "w/gen.lum" as g\nprint g.grab()' % d)
        with open(os.path.join(d, "main.lum"), "w") as f:
            f.write(main)
        args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + ["--allow-fs", os.path.join(d, "main.lum")]
        r = subprocess.run(args, capture_output=True, text=True, input="")
        host = open("/etc/hostname").read().strip() if os.path.exists("/etc/hostname") else "\0"
        _record(r.returncode != 0 and "a library cannot hold authority its importer lacks" in r.stdout and host not in r.stdout.replace("/etc/hostname", ""),
                f"a module written at run time must not exceed its importer ({'vm' if vm else 'interp'}): rc={r.returncode} out={r.stdout!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)
_generated_module(False); _generated_module(True)

# =====================================================================
# ERROR HANDLING (SEC-006): a denial is structured, and catching it gains nothing
# =====================================================================
expect_on_both("a denial carries kind, capability, operation, resource and reason",
               'use fs.read\ntry:\n    read_file("/some/where")\ncatch e:\n    print e.kind\n    print e.capability\n    print e.operation\n    print e.resource\n    print e.reason',
               "PermissionError\nfs.read\nread_file\n/some/where\nnot-granted")
expect_on_both("catching a denial does not yield the data, and execution continues with the fallback",
               'use fs.read\nlet data = "fallback"\ntry:\n    data = read_file("/etc/hostname")\ncatch e:\n    print "denied"\nprint data',
               "denied\nfallback")
blocked_on_both("a try/catch around one denied call does not weaken the NEXT call's check",
                'use fs.read\ntry:\n    read_file("/tmp/a")\ncatch e:\n    print "first denied"\nprint read_file("/tmp/b")', "PermissionError")
expect_on_both("denying in a loop never starts succeeding",
               'use fs.read\nlet denied = 0\nfor i in range(50):\n    try:\n        read_file("/etc/hostname")\n    catch e:\n        denied = denied + 1\nprint denied',
               "50")
expect_on_both("a denial inside a spawned task surfaces at wait() and is still a denial",
               'use fs.read\nfn job():\n    return read_file("/etc/hostname")\nlet t = spawn job()\ntry:\n    wait(t)\ncatch e:\n    print e.kind + ":" + e.reason',
               "PermissionError:not-granted")
expect_report_on_both("an uncaught denial exits non-zero with the PermissionError text and no partial effect",
                      'use fs.read\nprint "before"\nprint read_file("/etc/hostname")\nprint "after"',
                      "before\nPermissionError: read_file() requires the 'fs.read' capability, which was not granted for this run — grant it with --allow-read[=PATH,...] (declaring it with 'use' does not grant it)",
                      "R0008")

# =====================================================================
# ARGUMENT VALIDATION: gated builtins never act on an argument they do not understand
# =====================================================================
blocked_on_both("read_file(0) is a TypeError — it is NOT a read of file descriptor 0",
                'use fs.read\nprint read_file(0)', "TypeError: read_file() expects a Str path as argument 1, got Int", flags=("--allow-read",))
blocked_on_both("read_file(true) is a TypeError", 'use fs.read\nprint read_file(true)', "TypeError", flags=("--allow-read",))
blocked_on_both("write_file with a non-Str body is a TypeError, not a host crash",
                'use fs.write\nwrite_file("/tmp/_lumen_never_written", 5)', "TypeError: write_file() expects a Str as argument 2, got Int", flags=("--allow-write",))
blocked_on_both("a path with a NUL character is rejected", 'use fs.read\nprint read_file("a" + chr(0) + "b")', "NUL character", flags=("--allow-read",))
blocked_on_both("http_get(5) is a TypeError", 'use net.http\nprint http_get(5)', "TypeError", flags=("--allow-net",))

# =====================================================================
# CLI BEHAVIOUR: grant, deny, non-interactive, no silent bypass
# =====================================================================
def cli(args, stdin_text=""):
    r = subprocess.run([sys.executable, LUMEN] + args, capture_output=True, text=True, input=stdin_text, timeout=60)
    return r.stdout + r.stderr, r.returncode

_tmp = tempfile.mkdtemp()
try:
    prog = os.path.join(_tmp, "p.lum")
    with open(prog, "w") as f:
        f.write('use fs.read\nprint read_file("%s")' % prog)
    out, rc = cli(["run", prog])
    _record(rc == 1 and "not granted for this run" in out, f"CLI: no flags must deny: rc={rc} out={out!r}")
    out, rc = cli(["run", prog, "--allow-read"])
    _record(rc == 0 and "use fs.read" in out, f"CLI: --allow-read must grant: rc={rc} out={out!r}")
    out, rc = cli(["run", "--allow-read", prog])
    _record(rc == 0, f"CLI: flags may precede the file: rc={rc}")
    out, rc = cli([prog, "--allow-read"])
    _record(rc == 0, f"CLI: the legacy `lumen file.lum --allow-read` form honours flags: rc={rc}")
    out, rc = cli(["run", "--vm", prog, f"--allow-read={_tmp}"])
    _record(rc == 0 and "use fs.read" in out, f"CLI: scoped flag on the VM: rc={rc} out={out!r}")
    out, rc = cli(["run", prog, "--allow-read=/nonexistent-lumen-scope"])
    _record(rc == 1 and "outside the paths granted" in out, f"CLI: a scope that excludes the file denies: rc={rc} out={out!r}")
    # non-interactive: never prompts, never hangs on stdin, denial is the default
    out, rc = cli(["run", prog], stdin_text="y\ny\ny\n")
    _record(rc == 1 and "not granted for this run" in out and "Allow" not in out,
            f"CLI: stdin answers must not grant anything and there is no prompt: rc={rc} out={out!r}")
    # no flag can bypass the model
    for bad in ("--allow-all", "--allow-everything", "--allow", "--allow-reed", "--allow-read="):
        out, rc = cli(["run", prog, bad])
        _record(rc == 2 and "Error:" in out, f"CLI: {bad!r} must be a usage error (exit 2), not silently accepted: rc={rc} out={out!r}")
    out, rc = cli(["run", prog, "--no-sandbox"])
    _record(rc != 0 and "use fs.read" not in out,
            f"CLI: an unknown flag must not be treated as 'disable security': rc={rc} out={out!r}")
    # discoverability: the permission flags are documented by --help, and a bad path is a clean error
    out, rc = cli(["--help"])
    _record(rc == 0 and "--allow-read" in out and "--allow-net" in out and "There is deliberately no --allow-all" in out
            and "Traceback" not in out, f"CLI: --help must document the permission flags: rc={rc} out={out[:200]!r}")
    for argv in (["run", "/nonexistent/x.lum"], ["/nonexistent/x.lum"], ["check", "/nonexistent/x.lum"]):
        out, rc = cli(argv)
        _record(rc == 1 and "Traceback" not in out and "cannot read" in out, f"CLI: {argv} must be a clean error: rc={rc} out={out!r}")
    # `lumen check` never executes the program, so it needs no grants and performs no I/O
    out, rc = cli(["check", prog])
    _record(rc == 0 and "No errors found" in out, f"CLI: check needs no grants: rc={rc} out={out!r}")
    # lumen test honours the flags and denies by default
    tdir = os.path.join(_tmp, "t"); os.makedirs(tdir)
    with open(os.path.join(tdir, "x_test.lum"), "w") as f:
        f.write('use fs.read\ntest "reads":\n    assert_eq(read_file("%s"), "hello")\n' % os.path.join(tdir, "data.txt"))
    with open(os.path.join(tdir, "data.txt"), "w") as f:
        f.write("hello")
    out, rc = cli(["test", tdir])
    _record(rc == 1 and "FAILED" in out and "not granted" in out, f"CLI: lumen test denies by default: rc={rc} out={out!r}")
    out, rc = cli(["test", tdir, f"--allow-read={tdir}"])
    _record(rc == 0 and "1 passed" in out, f"CLI: lumen test honours --allow-read=DIR: rc={rc} out={out!r}")
    out, rc = cli(["test", tdir, "--vm", f"--allow-read={tdir}"])
    _record(rc == 0 and "1 passed" in out, f"CLI: lumen test --vm honours --allow-read=DIR: rc={rc} out={out!r}")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# the shipped example behaves as its header comment says, identically on both backends (cwd = repo root)
_ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_HELLO_LEN = len(open(os.path.join(_ROOT, "examples", "hello.lum")).read())
for _flags, _want in (
        ((), "inside  : denied (not-granted)\noutside : denied (not-granted)\nlisting : denied (not-granted)"),
        (("--allow-read=examples",), f"inside  : read {_HELLO_LEN} characters\noutside : denied (out-of-scope)\nlisting : denied (not-granted)"),
        (("--allow-read=examples", "--allow-list=examples"), None)):
    _outs = []
    for _vm in (False, True):
        _r = subprocess.run([sys.executable, LUMEN, "run"] + (["--vm"] if _vm else []) + list(_flags) + ["examples/capabilities_demo.lum"],
                            capture_output=True, text=True, cwd=_ROOT, input="")
        _outs.append(_r.stdout.strip())
    _ok = _outs[0] == _outs[1] and (_want is None or _outs[0] == _want) and (_want is not None or "listing : " in _outs[0] and "denied" not in _outs[0].split("\n")[-1])
    _record(_ok, f"examples/capabilities_demo.lum with {_flags}: interp={_outs[0]!r} vm={_outs[1]!r}")

# =====================================================================
# CAPABILITY SURFACE: there is no escape hatch hiding behind a different name
# =====================================================================
blocked_on_both("unknown capability name is a clean ModuleError, not silently granted", 'use os\nprint read_file("/tmp/x")', "ModuleError")
blocked_on_both("'process' is not a recognized capability (no process-spawn escape hatch)", 'use process', "ModuleError")
blocked_on_both("'process.spawn' is reserved, not implemented, and cannot be declared", 'use process.spawn', "reserved but not implemented")
blocked_on_both("'env.read' is reserved, not implemented", 'use env.read', "reserved but not implemented")
blocked_on_both("'net.connect' is reserved, not implemented", 'use net.connect', "reserved but not implemented")
blocked_on_both("'system' is not a recognized capability", 'use system', "ModuleError")
blocked_on_both("there is no exec() builtin to shell out with", 'exec("ls")', "NameError")
blocked_on_both("there is no system() builtin to shell out with", 'system("ls")', "NameError")
blocked_on_both("there is no subprocess()/popen() builtin", 'popen("ls")', "NameError")
blocked_on_both("there is no eval()/reflection builtin to reach outside the object model", 'eval("1+1")', "NameError")
blocked_on_both("there is no environment-variable builtin", 'print getenv("HOME")', "NameError")
blocked_on_both("spawning a builtin (not a user function) is rejected, not a capability bypass route",
                'spawn read_file("/tmp/x")', "NameError")
blocked_on_both("a declaration inside a function is rejected, not honoured late",
                'fn never_called():\n    use fs\nprint read_file("/tmp/x")', "must be at the top level of the file")
blocked_on_both("a declaration inside an if-branch is rejected, not silently ignored",
                'if false:\n    use fs\nprint read_file("/tmp/x")', "must be at the top level of the file")
blocked_on_both_multifile("a declaration hidden in a library function is rejected when the library loads",
    {"main.lum": 'use "helper.lum" as h\nh.grant()\nprint read_file("/etc/hostname")', "helper.lum": 'fn grant():\n    use fs'},
    "main.lum", "must be at the top level of the file", flags=ALLF)

# =====================================================================
# RUNTIME PARITY: boundary behaviour both backends must produce identically
# =====================================================================
equivalent_on_both("a spawned task has exactly its spawner's declarations and the host's grants",
    'use fs.read\nfn job():\n    return file_exists("/")\nlet t = spawn job()\nprint wait(t)', flags=("--allow-read",))
equivalent_on_both_multifile("a library that declares what its importer declared works, via its own exported function",
    {"main.lum": 'use fs.read\nuse "helper.lum" as h\ntry:\n    print h.read_it()\ncatch e:\n    print "io: " + e.kind',
     "helper.lum": 'use fs.read\nfn read_it():\n    return read_file("/tmp/does_not_exist_sec_check2")'},
    "main.lum", flags=("--allow-read",))
equivalent_on_both("declarations are static: a later statement cannot un-declare them, and order does not matter",
    'try:\n    read_file("/tmp/does_not_exist_sec_check3")\ncatch e:\n    print "reached io layer"\nlet x = 1\nuse fs', flags=("--allow-read",))
equivalent_on_both("one 'fs' declaration plus --allow-fs covers all seven fs-gated builtins uniformly",
    'use fs\nprint file_exists("/tmp/does_not_exist_sec_check4")\n'
    'print is_dir("/tmp/does_not_exist_sec_check4")\n'
    'try:\n    append_file("/nonexistent_dir_sec_check/x", "y")\ncatch e:\n    print "reached io layer"\n'
    'try:\n    list_dir("/tmp/does_not_exist_sec_check4")\ncatch e:\n    print "reached io layer"\n'
    'try:\n    delete_file("/tmp/does_not_exist_sec_check4")\ncatch e:\n    print "reached io layer"\n'
    'try:\n    read_file("/tmp/does_not_exist_sec_check4")\ncatch e:\n    print "reached io layer"\n'
    'try:\n    write_file("/nonexistent_dir_sec_check/x", "y")\ncatch e:\n    print "reached io layer"', flags=("--allow-fs",))


print(f"\n{'='*60}\nSECURITY HARDENING: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
if FAILURES:
    for f in FAILURES:
        print("\n" + f)
sys.exit(1 if FAIL else 0)
