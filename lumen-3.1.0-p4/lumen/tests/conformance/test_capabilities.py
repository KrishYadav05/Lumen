"""Conformance tests for docs/capabilities.md — what each capability means, how
the host scopes it, and how the command-line grant flags map onto a Policy.

Everything here verifies ACTUAL resource access, not just that an error message
appeared: a denied write is checked by looking at the disk afterwards, a denied
read by checking the secret never reaches the program's output, and a denied
network request by counting the requests a real local HTTP server received.
"""
import os, sys, tempfile, shutil, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from harness import run_source, print_final_summary
import harness
from lumen import Policy, parse_grant_flags, DENY_ALL, CAPABILITIES

# The loopback servers below must be reached directly, even where the environment
# configures an HTTP proxy.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"


def record(ok, spec, name, detail=""):
    if ok:
        harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[{spec}] {name}: {detail}")


def read(path):
    with open(path) as f:
        return f.read()


def run(src, policy, cwd=None):
    old = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        return run_source(src, policy=policy)
    finally:
        os.chdir(old)


# ======================================================================
# §2  Policy: construction and description
# ======================================================================
SPEC = "capabilities.md §2"
record(not DENY_ALL.granted("fs.read") and all(not DENY_ALL.granted(c) for c in CAPABILITIES),
       SPEC, "the empty policy grants no capability")
record(Policy.deny_all().describe() == [f"{c}: denied" for c in CAPABILITIES], SPEC, "describe() of the empty policy",
       str(Policy.deny_all().describe()))
record(Policy({"fs.read": None}).describe()[0] == "fs.read: allowed everywhere", SPEC, "describe() of an unrestricted grant")
for bad, why in [({"fs.chmod": None}, "unknown capability"), ({"fs.read": []}, "empty scope list"),
                 ({"net.http": ["bad host"]}, "malformed host"), ({"net.http": ["h:0"]}, "port 0"),
                 ({"net.http": ["h:70000"]}, "port out of range"), ({"net.http": ["h/path"]}, "scope with a path"),
                 ({"net.http": ["u@h"]}, "scope with userinfo")]:
    try:
        Policy(bad)
        record(False, SPEC, f"Policy({bad}) must be rejected ({why})", "no error")
    except ValueError:
        record(True, SPEC, f"Policy rejects {why}")
record(Policy({"net.http": ["Example.COM:443"]}).net_allows("example.com", 443), SPEC, "host scope is case-insensitive")
record(not Policy({"net.http": ["example.com:443"]}).net_allows("example.com", 80), SPEC, "host:port scope pins the port")
record(Policy({"net.http": ["example.com"]}).net_allows("example.com", 8443), SPEC, "a host-only scope allows any port")
record(not Policy({"net.http": ["example.com"]}).net_allows("www.example.com", 80), SPEC, "no implicit subdomain wildcard")
record(Policy({"net.http": ["[::1]:8080"]}).net_allows("::1", 8080), SPEC, "IPv6 scope in bracket form")

# ======================================================================
# §3  command-line flags -> Policy
# ======================================================================
SPEC = "capabilities.md §3"
def flags(*args):
    p, rest = parse_grant_flags(list(args))
    return p, rest
p, rest = flags("run", "x.lum")
record(p.describe() == Policy().describe() and rest == ["run", "x.lum"], SPEC, "no flags: empty policy, arguments untouched")
p, rest = flags("run", "--allow-read", "x.lum")
record(p.scope("fs.read") is None and p.granted("fs.read") and not p.granted("fs.write") and rest == ["run", "x.lum"],
       SPEC, "bare --allow-read grants fs.read everywhere and only fs.read")
p, _ = flags("--allow-write=/tmp")
record(p.granted("fs.write") and not p.granted("fs.read") and p.scope("fs.write") == (os.path.realpath("/tmp"),),
       SPEC, "--allow-write=PATH scopes fs.write only")
p, _ = flags("--allow-fs=/tmp")
record(all(p.granted(c) and p.scope(c) == (os.path.realpath("/tmp"),) for c in ("fs.read", "fs.write", "fs.list"))
       and not p.granted("net.http"), SPEC, "--allow-fs=PATH is read+write+list for that path, not net")
p, _ = flags("--allow-net=a.example:443,b.example")
record(p.granted("net.http") and p.net_allows("a.example", 443) and p.net_allows("b.example", 9)
       and not p.net_allows("a.example", 80) and not p.granted("fs.read"), SPEC, "--allow-net=HOST[:PORT],... lists hosts")
p, _ = flags("--allow-read=/tmp", "--allow-read=/usr")
record(set(p.scope("fs.read")) == {os.path.realpath("/tmp"), os.path.realpath("/usr")}, SPEC, "repeated flags accumulate")
p, _ = flags("--allow-read=/tmp", "--allow-read")
record(p.scope("fs.read") is None, SPEC, "an unrestricted grant absorbs scoped ones (order: scoped first)")
p, _ = flags("--allow-read", "--allow-read=/tmp")
record(p.scope("fs.read") is None, SPEC, "an unrestricted grant absorbs scoped ones (order: bare first)")
for bad in (["--allow-all"], ["--allow-reed"], ["--allow-read="], ["--allow-read=a,,b"], ["--allow-net=bad host"],
            ["--allow-net=h:99999"]):
    try:
        flags(*bad)
        record(False, SPEC, f"{bad} must be an error", "accepted")
    except ValueError:
        record(True, SPEC, f"{bad} is an error, never silently ignored")

# ======================================================================
# §4  filesystem scopes
# ======================================================================
SPEC = "capabilities.md §4"
root = tempfile.mkdtemp()
try:
    inside = os.path.join(root, "inside")
    outside = os.path.join(root, "outside")
    evil = os.path.join(root, "inside-evil")          # shares the textual prefix of `inside`
    for d_ in (inside, outside, evil, os.path.join(inside, "sub")):
        os.makedirs(d_)
    for path, text in [(os.path.join(inside, "a.txt"), "A"), (os.path.join(inside, "sub", "s.txt"), "S"),
                       (os.path.join(outside, "b.txt"), "SECRET-B"), (os.path.join(evil, "c.txt"), "SECRET-C")]:
        with open(path, "w") as f:
            f.write(text)

    def reads(path, policy, kind_only=False):
        out, err = run(f'use fs.read\ntry:\n    print read_file("{path}")\ncatch e:\n'
                       f'    if e.kind == "PermissionError":\n        print e.kind + ":" + e.reason\n'
                       f'    else:\n        print e.kind + ":" + e.message', policy)
        assert err is None, err
        return out.strip()

    pol = Policy({"fs.read": [inside]})
    record(reads(os.path.join(inside, "a.txt"), pol) == "A", SPEC, "a file inside the granted directory is readable")
    record(reads(os.path.join(inside, "sub", "s.txt"), pol) == "S", SPEC, "a file in a subdirectory of the grant is readable")
    out = reads(os.path.join(outside, "b.txt"), pol)
    record(out == "PermissionError:out-of-scope" and "SECRET" not in out, SPEC, "a file outside the granted directory is denied", out)
    out = reads(os.path.join(evil, "c.txt"), pol)
    record(out == "PermissionError:out-of-scope", SPEC, "a sibling directory sharing the text prefix is NOT inside the scope", out)
    out = reads(os.path.join(inside, "..", "outside", "b.txt"), pol)
    record(out == "PermissionError:out-of-scope", SPEC, "`..` traversal out of the scope is denied", out)
    out = reads(os.path.join(inside, "sub", "..", "..", "outside", "b.txt"), pol)
    record(out == "PermissionError:out-of-scope", SPEC, "deeper `..` traversal is denied", out)
    out = reads(os.path.join(inside, "no-such-dir", "..", "..", "outside", "b.txt"), pol)
    record(out == "PermissionError:out-of-scope", SPEC, "`..` after a directory that does not exist is still denied", out)
    record(reads(os.path.join(inside, "sub", "..", "a.txt"), pol) == "A", SPEC, "`..` that stays inside the scope is fine")

    # a single-file scope
    one = Policy({"fs.read": [os.path.join(inside, "a.txt")]})
    record(reads(os.path.join(inside, "a.txt"), one) == "A", SPEC, "a scope naming one file allows that file")
    record(reads(os.path.join(inside, "sub", "s.txt"), one) == "PermissionError:out-of-scope", SPEC,
           "a scope naming one file allows nothing else")

    # several scopes
    two = Policy({"fs.read": [inside, evil]})
    record(reads(os.path.join(evil, "c.txt"), two) == "SECRET-C", SPEC, "a second scope entry grants its own directory")
    record(reads(os.path.join(outside, "b.txt"), two) == "PermissionError:out-of-scope", SPEC, "…and still nothing else")

    # existence probes are reads too: an out-of-scope probe is denied, never answered false
    out, err = run(f'use fs.read\ntry:\n    print file_exists("{os.path.join(outside, "b.txt")}")\ncatch e:\n    print e.reason', pol)
    record(out.strip() == "out-of-scope", SPEC, "file_exists outside the scope is denied, not answered (no existence oracle)", out)
    out, err = run(f'use fs.read\ntry:\n    print is_dir("{outside}")\ncatch e:\n    print e.reason', pol)
    record(out.strip() == "out-of-scope", SPEC, "is_dir outside the scope is denied", out)
    out, err = run(f'use fs.read\nprint file_exists("{os.path.join(inside, "a.txt")}")\nprint is_dir("{inside}")', pol)
    record(out.split() == ["true", "true"], SPEC, "file_exists/is_dir inside the scope answer normally", out)

    # listing
    lp = Policy({"fs.list": [inside]})
    out, err = run(f'use fs.list\nprint list_dir("{inside}")', lp)
    record(out.strip() == "[a.txt, sub]", SPEC, "fs.list lists the granted directory", out)
    out, err = run(f'use fs.list\ntry:\n    print list_dir("{root}")\ncatch e:\n    print e.reason', lp)
    record(out.strip() == "out-of-scope", SPEC, "fs.list refuses the PARENT of the granted directory", out)

    # read / write / list scopes are independent
    mixed = Policy({"fs.read": [inside], "fs.write": [outside]})
    out, err = run(
        f'use fs\n'
        f'print read_file("{os.path.join(inside, "a.txt")}")\n'
        f'try:\n    write_file("{os.path.join(inside, "w.txt")}", "x")\ncatch e:\n    print "write-inside:" + e.reason\n'
        f'write_file("{os.path.join(outside, "w.txt")}", "ok")\n'
        f'try:\n    read_file("{os.path.join(outside, "w.txt")}")\ncatch e:\n    print "read-outside:" + e.reason', mixed)
    record(out.split("\n")[:-1] == ["A", "write-inside:out-of-scope", "read-outside:out-of-scope"],
           SPEC, "fs.read and fs.write carry independent scopes", out)
    record(read(os.path.join(outside, "w.txt")) == "ok" and not os.path.exists(os.path.join(inside, "w.txt")),
           SPEC, "…and the disk shows exactly the permitted write, nothing else")

    # writes outside the scope change nothing on disk
    wp = Policy({"fs.write": [inside]})
    target = os.path.join(outside, "new.txt")
    run(f'use fs.write\ntry:\n    write_file("{target}", "x")\ncatch e:\n    print e.reason', wp)
    record(not os.path.exists(target), SPEC, "a denied write_file creates no file")
    before = read(os.path.join(outside, "b.txt"))
    run(f'use fs.write\ntry:\n    append_file("{os.path.join(outside, "b.txt")}", "MORE")\ncatch e:\n    print e.reason', wp)
    record(read(os.path.join(outside, "b.txt")) == before, SPEC, "a denied append_file leaves the file untouched")
    run(f'use fs.write\ntry:\n    delete_file("{os.path.join(outside, "b.txt")}")\ncatch e:\n    print e.reason', wp)
    record(os.path.exists(os.path.join(outside, "b.txt")), SPEC, "a denied delete_file leaves the file in place")
    run(f'use fs.write\nwrite_file("{os.path.join(inside, "new.txt")}", "made")', wp)
    record(read(os.path.join(inside, "new.txt")) == "made", SPEC, "a permitted write creates the file inside the scope")
    run(f'use fs.write\nappend_file("{os.path.join(inside, "new.txt")}", "+more")\ndelete_file("{os.path.join(inside, "a.txt")}")', wp)
    record(read(os.path.join(inside, "new.txt")) == "made+more" and not os.path.exists(os.path.join(inside, "a.txt")),
           SPEC, "permitted append and delete take effect")
    with open(os.path.join(inside, "a.txt"), "w") as f:
        f.write("A")

    # relative paths resolve against the process directory, like the builtins do
    old = os.getcwd()
    os.chdir(root)
    try:
        rel = Policy({"fs.read": ["inside"]})          # resolved NOW, relative to the current directory
        o1 = reads("inside/a.txt", rel)
        o2 = reads("./inside/../outside/b.txt", rel)
        o3 = reads("outside/b.txt", rel)
    finally:
        os.chdir(old)
    record(o1 == "A" and o2 == "PermissionError:out-of-scope" and o3 == "PermissionError:out-of-scope", SPEC,
           "relative scopes and relative paths resolve against the same directory", f"{o1!r} {o2!r} {o3!r}")

    # ---------------- symbolic links ----------------
    if hasattr(os, "symlink"):
        SPEC = "capabilities.md §4.2"
        link_out = os.path.join(inside, "to-outside.txt")
        link_in = os.path.join(inside, "to-inside.txt")
        link_dir = os.path.join(inside, "to-outside-dir")
        dangling = os.path.join(inside, "dangling.txt")
        os.symlink(os.path.join(outside, "b.txt"), link_out)
        os.symlink(os.path.join(inside, "sub", "s.txt"), link_in)
        os.symlink(outside, link_dir)
        os.symlink(os.path.join(outside, "will-not-exist.txt"), dangling)

        out = reads(link_out, pol)
        record(out == "PermissionError:out-of-scope" and "SECRET" not in out, SPEC,
               "a symlink inside the scope that points outside cannot be read through", out)
        record(reads(link_in, pol) == "S", SPEC, "a symlink inside the scope that points inside is readable")
        out, err = run(f'use fs.list\ntry:\n    print list_dir("{link_dir}")\ncatch e:\n    print e.reason', lp)
        record(out.strip() == "out-of-scope", SPEC, "a directory symlink pointing outside cannot be listed through")
        out = reads(os.path.join(link_dir, "b.txt"), pol)
        record(out == "PermissionError:out-of-scope", SPEC, "a path THROUGH a directory symlink to outside is denied", out)
        out, err = run(f'use fs.read\ntry:\n    print file_exists("{link_out}")\ncatch e:\n    print e.reason', pol)
        record(out.strip() == "out-of-scope", SPEC, "file_exists through an escaping symlink is denied")

        secret_before = read(os.path.join(outside, "b.txt"))
        run(f'use fs.write\ntry:\n    write_file("{link_out}", "OVERWRITTEN")\ncatch e:\n    print e.reason', wp)
        record(read(os.path.join(outside, "b.txt")) == secret_before, SPEC, "write_file through an escaping symlink does not modify the target")
        run(f'use fs.write\ntry:\n    append_file("{link_out}", "APPENDED")\ncatch e:\n    print e.reason', wp)
        record(read(os.path.join(outside, "b.txt")) == secret_before, SPEC, "append_file through an escaping symlink does not modify the target")
        run(f'use fs.write\ntry:\n    write_file("{dangling}", "x")\ncatch e:\n    print e.reason', wp)
        record(not os.path.exists(os.path.join(outside, "will-not-exist.txt")), SPEC,
               "write_file through a DANGLING escaping symlink does not create the target")
        run(f'use fs.write\ntry:\n    write_file("{os.path.join(link_dir, "made.txt")}", "x")\ncatch e:\n    print e.reason', wp)
        record(not os.path.exists(os.path.join(outside, "made.txt")), SPEC,
               "creating a file through a directory symlink to outside is refused")

        # deleting a symlink removes the LINK (a name inside the scope), never its target
        run(f'use fs.write\ndelete_file("{link_out}")', wp)
        record(not os.path.lexists(link_out) and os.path.exists(os.path.join(outside, "b.txt")), SPEC,
               "delete_file on an escaping symlink removes the link and leaves the target")

        # a scope given through a symlink works, because scopes are resolved to real paths
        alias = os.path.join(root, "alias-of-inside")
        os.symlink(inside, alias)
        viap = Policy({"fs.read": [alias]})
        record(reads(os.path.join(inside, "a.txt"), viap) == "A", SPEC, "a scope given as a symlink covers the real directory")
        record(reads(os.path.join(alias, "a.txt"), viap) == "A", SPEC, "…and paths spelled through that symlink")
        record(reads(os.path.join(outside, "b.txt"), viap) == "PermissionError:out-of-scope", SPEC, "…and nothing beyond it")

        # a chain that leaves and comes back resolves to where it ends
        hop = os.path.join(outside, "hop")
        os.symlink(os.path.join(inside, "sub", "s.txt"), hop)
        chain = os.path.join(inside, "chain")
        os.symlink(hop, chain)
        record(reads(chain, pol) == "S", SPEC, "only the final real location matters: a chain ending inside is allowed")
        # --- defence in depth: the open itself must refuse a symlink in the final component -------
        # `_fs_target` resolves symlinks before the check, so the only way a symlink can reach
        # `_open_checked` is a race (the path was swapped after the check). Simulate that swap by
        # handing `_open_checked` an escaping link directly, in each layer's isolation:
        #   * as on Linux:     O_NOFOLLOW and the /proc/self/fd re-check both active;
        #   * as on macOS/BSD: the /proc re-check is unavailable (sys.platform patched), so
        #                      O_NOFOLLOW alone must stop it.
        import lumen as _lumen
        from lumen import CapabilityContext, PermissionDenied, _open_checked
        raced = os.path.join(inside, "raced-link")
        os.symlink(os.path.join(outside, "b.txt"), raced)
        ctx = CapabilityContext(Policy({"fs.read": [inside], "fs.write": [inside]}))
        ctx.declare(["fs.read", "fs.write"])
        before = read(os.path.join(outside, "b.txt"))
        for platform_name in (sys.platform, "darwin"):
            saved = _lumen.sys.platform
            _lumen.sys.platform = platform_name
            try:
                for mode in ("r", "w", "a"):
                    cap = "fs.read" if mode == "r" else "fs.write"
                    try:
                        f = _open_checked(ctx, cap, "op", raced, raced, mode)
                        f.close()
                        stopped = False
                    except (PermissionDenied, OSError):
                        stopped = True
                    record(stopped and read(os.path.join(outside, "b.txt")) == before, SPEC,
                           f"a swapped-in symlink is never opened (mode {mode!r}, platform {platform_name!r})")
            finally:
                _lumen.sys.platform = saved
    else:
        record(True, "capabilities.md §4.2", "symlinks unavailable on this platform (not tested)")
finally:
    shutil.rmtree(root)

# ======================================================================
# §5  network scopes — against real servers, counting what they receive
# ======================================================================
SPEC = "capabilities.md §5"


class Server:
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
                body = b"hello-from-server"
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


A, B = Server(), Server()
try:
    only_a = Policy({"net.http": [f"127.0.0.1:{A.port}"]})

    def get(url, policy, extra=""):
        out, err = run(f'use net.http\ntry:\n    print http_get("{url}")\ncatch e:\n'
                       f'    if e.kind == "PermissionError":\n        print e.kind + ":" + e.reason\n'
                       f'    else:\n        print e.kind + ":" + e.message\n{extra}', policy)
        assert err is None, err
        return out.strip()

    record(get(f"http://127.0.0.1:{A.port}/x", only_a) == "hello-from-server", SPEC, "a request to the granted host:port succeeds")
    record(len(A.hits) == 1, SPEC, "…and the granted server received exactly that request")
    out = get(f"http://127.0.0.1:{B.port}/secret", only_a)
    record(out == "PermissionError:out-of-scope" and B.hits == [], SPEC,
           "a request to another port is denied and that server receives NOTHING", f"{out!r} hits={B.hits}")
    record(get(f"http://127.0.0.1:{B.port}/x", Policy({"net.http": ["127.0.0.1"]})) == "hello-from-server" and len(B.hits) == 1,
           SPEC, "a host-only scope allows any port on that host")
    B.hits.clear()

    # matching is by NAME, not by resolved address: documented, and tested so it stays documented
    out = get(f"http://localhost:{A.port}/x", only_a)
    record(out == "PermissionError:out-of-scope", SPEC,
           "a different NAME for the same address is a different host (scope matching is by name, not by resolved IP)", out)

    # redirects must not widen reach
    A.hits.clear(); B.hits.clear()
    out = get(f"http://127.0.0.1:{A.port}/redirect?to=http://127.0.0.1:{B.port}/secret", only_a)
    record(out == "PermissionError:redirect-out-of-scope" and B.hits == [] and len(A.hits) == 1, SPEC,
           "a redirect to an ungranted host is denied and the target receives NOTHING", f"{out!r} A={A.hits} B={B.hits}")
    A.hits.clear()
    out = get(f"http://127.0.0.1:{A.port}/redirect?to=http://127.0.0.1:{A.port}/landed", only_a)
    record(out == "hello-from-server" and A.hits == ["/redirect?to=http://127.0.0.1:%d/landed" % A.port, "/landed"], SPEC,
           "a redirect that stays inside the grant is followed", f"{out!r} {A.hits}")
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        f.write("LOCAL-SECRET")
        local = f.name
    try:
        out = get(f"http://127.0.0.1:{A.port}/redirect?to=file://{local}", only_a)
        record("LOCAL-SECRET" not in out, SPEC, "a redirect to a file:// URL never reads the file", out)
        out2, err2 = run(f'use net.http\ntry:\n    print http_get("http://127.0.0.1:{A.port}/redirect?to=file://{local}")\n'
                         f'catch e:\n    print e.message', Policy({"net.http": None}))
        record("LOCAL-SECRET" not in out2, SPEC, "…even when net.http is granted everywhere", out2)
    finally:
        os.unlink(local)

    # credentials and query strings never appear in a denial
    out, err = run(f'use net.http\ntry:\n    http_get("http://user:s3cr3t-pw@127.0.0.1:{B.port}/p?token=abc123")\n'
                   f'catch e:\n    print e.message\n    print e.resource', only_a)
    record("s3cr3t-pw" not in out and "abc123" not in out and f"http://127.0.0.1:{B.port}" in out, SPEC,
           "a denial echoes scheme://host:port only: no userinfo, no query", out)

    # default ports
    out = get("http://127.0.0.1/x", only_a)
    record(out == "PermissionError:out-of-scope", SPEC, "an http URL with no port means port 80 (denied: only the other port is granted)", out)
    out = get("https://127.0.0.1/x", only_a)
    record(out == "PermissionError:out-of-scope", SPEC, "an https URL with no port means port 443")
finally:
    A.close(); B.close()

print_final_summary()
