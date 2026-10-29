#!/usr/bin/env python3
"""Phase 5C: the consent prompt (`lumen run --ask`, SEC-005) and the audit log (`--audit-log`, SEC-010), through the
real command line — the prompt on a real pseudo-terminal, every program on BOTH backends — verifying actual
resource access (the disk, the printed output, a local server's hit counter), not merely that a message appeared.

Prompts are a user interface, not a security boundary (the Phase 5 spec says so): these tests check that the prompt
can only ADD what the program declared and no flag granted, that every default is a denial, that nothing runs without
a real terminal, that what it SHOWS is what is EFFECTIVE, and — separately — that the runtime still enforces whatever
Policy results.
"""
import os, sys, re, json, pty, select, signal, shutil, tempfile, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LUMEN = os.path.join(HERE, "..", "lumen.py")
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

PASS = 0
FAIL = 0
FAILURES = []


def record(ok, name, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


class World:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.inside = os.path.join(self.root, "inside")
        self.outside = os.path.join(self.root, "outside")
        self.logs = os.path.join(self.root, "logs")
        for d in (self.inside, self.outside, self.logs):
            os.makedirs(d)
        self.put(self.inside, "a.txt", "INSIDE-A")
        self.put(self.outside, "secret.txt", "TOP-SECRET-OUTSIDE")
        self.log = os.path.join(self.logs, "audit.jsonl")
        self.prog = os.path.join(self.root, "prog.lum")

    @staticmethod
    def put(d, name, text):
        with open(os.path.join(d, name), "w") as f:
            f.write(text)

    def program(self, src, name="prog.lum"):
        path = os.path.join(self.root, name)
        with open(path, "w") as f:
            f.write(src.replace("{inside}", self.inside).replace("{outside}", self.outside).replace("{log}", self.log)
                    .replace("{root}", self.root))
        return path

    def exists(self, *parts):
        return os.path.exists(os.path.join(*parts))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def cli(args, cwd=None, stdin_text=""):
    r = subprocess.run([sys.executable, LUMEN] + list(args), capture_output=True, text=True, input=stdin_text, cwd=cwd)
    return r.stdout, r.stderr, r.returncode


_PROMPT_END = re.compile(r"(deny: |\[y/N\] )$")


def pty_run(args, answers, cwd=None, eof_after_answers=False, timeout=30):
    """Runs `lumen ARGS` with a real terminal as stdin/stdout/stderr, typing each answer when a prompt appears.
    Returns (transcript, exit_code). After the answers are used up the next prompt gets end-of-input (Ctrl-D)
    if `eof_after_answers`, else the test fails by timeout (the run is killed)."""
    pid, fd = pty.fork()
    if pid == 0:
        if cwd:
            os.chdir(cwd)
        os.execv(sys.executable, [sys.executable, LUMEN] + list(args))
    transcript, pending, queue = b"", b"", list(answers)
    deadline = time.time() + timeout
    status = None
    try:
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                transcript += data
                pending += data
                if _PROMPT_END.search(pending.decode(errors="replace").replace("\r", "")):
                    if queue:
                        os.write(fd, (queue.pop(0) + "\n").encode())
                    elif eof_after_answers:
                        os.write(fd, b"\x04")
                    pending = b""
            else:
                done, st = os.waitpid(pid, os.WNOHANG)
                if done:
                    status = st
                    break
        # drain whatever is left
        try:
            while select.select([fd], [], [], 0.2)[0]:
                data = os.read(fd, 4096)
                if not data:
                    break
                transcript += data
        except OSError:
            pass
    finally:
        if status is None:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                _, status = os.waitpid(pid, 0)
            except ChildProcessError:
                status = 0
        os.close(fd)
    return transcript.decode(errors="replace").replace("\r\n", "\n"), os.waitstatus_to_exitcode(status)


def effective_block(transcript):
    """{capability: 'allowed' | 'denied'} from the 'Effective permissions' block of a transcript."""
    out = {}
    m = re.search(r"Effective permissions for this run:\n((?:  .*\n)+)", transcript)
    if not m:
        return None
    for line in m.group(1).splitlines():
        cap, _, rest = line.strip().partition(" ")
        out[cap] = "allowed" if rest.strip().startswith("allowed") else "denied"
    return out


class Server:
    """Loopback HTTP server that counts the requests it received."""
    def __init__(self):
        outer = self
        self.hits = []
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                body = b"NET-OK"
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *a): pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
    def close(self):
        self.srv.shutdown()


def events(path):
    with open(path) as f:
        return [json.loads(l) for l in f.read().split("\n") if l]


def norm(evs):
    return [{k: v for k, v in e.items() if k not in ("ts", "backend")} for e in evs]


BACKENDS = (("interpreter", []), ("vm", ["--vm"]))

# A program that declares only fs.read, and reports what it could do.
READ_PROBE = '''use fs.read
print "RAN"
try:
    print "inside=" + read_file("{inside}/a.txt")
catch e:
    print "inside-denied=" + e.reason
try:
    print "outside=" + read_file("{outside}/secret.txt")
catch e:
    print "outside-denied=" + e.reason
'''

# ======================================================================
# 1. --ask without a terminal never prompts, never grants, never runs
# ======================================================================
for label, vm in BACKENDS:
    w = World(); prog = w.program(READ_PROBE)
    out, err, rc = cli(["run"] + vm + ["--ask", prog], stdin_text="everywhere\ny\n")
    record(rc == 2 and "RAN" not in out and "TOP-SECRET" not in out + err and "interactive terminal" in out,
           f"[{label}] --ask with stdin piped (an answer supplied) refuses to run: exit 2, program not run, nothing granted", repr((out, err, rc)))
    out, err, rc = cli(["run"] + vm + ["--ask", prog], stdin_text="")
    record(rc == 2 and "RAN" not in out, f"[{label}] --ask with stdin closed refuses to run", repr((out, rc)))
    w.close()

# stdin IS a terminal but stderr is not: still refused (the prompt could not be shown to a person)
w = World(); prog = w.program(READ_PROBE)
master, slave = pty.openpty()
p = subprocess.Popen([sys.executable, LUMEN, "run", "--ask", prog], stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
os.write(master, b"everywhere\ny\n")
try:
    out, err = p.communicate(timeout=20)
except subprocess.TimeoutExpired:
    p.kill(); out, err = p.communicate()
os.close(master); os.close(slave)
record(p.returncode == 2 and b"RAN" not in out and b"TOP-SECRET" not in out + err,
       "--ask with a terminal on stdin but stderr redirected is refused too (answers typed ahead are not honoured)", repr((out, err, p.returncode)))
w.close()

# --ask belongs to `run` only
w = World(); prog = w.program(READ_PROBE)
for cmd in (["check", "--ask", prog], ["test", "--ask", prog], ["repl", "--ask"], [prog, "--ask"], ["new", "--ask"]):
    out, err, rc = cli(cmd, cwd=w.root)
    record(rc == 2 and "only supported by 'run'" in out and "RAN" not in out, f"--ask with `{cmd[0] if cmd[0] != prog else 'file.lum'}` is an error (exit 2), nothing runs", repr((out, rc)))
w.close()

# nothing declared -> nothing to ask: the program just runs, exactly as without --ask (which needs a terminal, so run on a pty)
w = World(); prog = w.program('print "RAN"\nprint 1 + 1\n')
out, rc = pty_run(["run", "--ask", prog], [])
record(rc == 0 and "RAN" in out and "2" in out and "requests" not in out, "a program that declares nothing is run without any prompt", repr((out, rc)))
w.close()

# ======================================================================
# 2. Granting through the prompt: scoped, and enforced by the runtime
# ======================================================================
for label, vm in BACKENDS:
    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [w.inside, "y"])
    record(rc == 0 and "inside=INSIDE-A" in out and "outside-denied=out-of-scope" in out and "TOP-SECRET" not in out,
           f"[{label}] a path typed at the prompt grants exactly that directory (inside readable, outside denied, secret never printed)", out[-400:])
    out, rc = pty_run(["run"] + vm + ["--ask", prog], ["", "y"])
    record(rc == 0 and "inside-denied=not-granted" in out and "outside-denied=not-granted" in out and "INSIDE-A" not in out and "TOP-SECRET" not in out,
           f"[{label}] Enter at the prompt denies: nothing readable", out[-400:])
    out, rc = pty_run(["run"] + vm + ["--ask", prog], ["n", "y"])
    record("inside-denied=not-granted" in out and "TOP-SECRET" not in out, f"[{label}] 'n' denies")
    w.close()

    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [w.inside, "n"])
    record(rc == 1 and "RAN" not in out and "cancelled" in out, f"[{label}] 'n' at the final question: the program is not run (exit 1)", out[-300:])
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [w.inside, ""])
    record(rc == 1 and "RAN" not in out, f"[{label}] Enter at the final question: the program is not run")
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [], eof_after_answers=True)
    record(rc == 1 and "RAN" not in out and "cancelled" in out, f"[{label}] Ctrl-D at the first question: cancelled, not run", out[-300:])
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [w.inside], eof_after_answers=True)
    record(rc == 1 and "RAN" not in out, f"[{label}] Ctrl-D at the final question: cancelled, not run")
    w.close()

    # 'y' is not an answer to "where?"; three bad answers leave the capability denied
    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], ["y", "yes", "y", "y"])
    record("inside-denied=not-granted" in out and "too many invalid answers" in out and "INSIDE-A" not in out,
           f"[{label}] answering 'y' to \"which paths?\" grants nothing", out[-400:])
    w.close()

    # writes: disk is the witness
    WRITE_PROBE = '''use fs.write
print "RAN"
try:
    write_file("{inside}/new.txt", "WRITTEN")
    print "write-inside=ok"
catch e:
    print "write-inside-denied=" + e.reason
try:
    write_file("{outside}/new.txt", "WRITTEN")
    print "write-outside=ok"
catch e:
    print "write-outside-denied=" + e.reason
'''
    w = World(); prog = w.program(WRITE_PROBE)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], [w.inside, "y"])
    record(w.exists(w.inside, "new.txt") and not w.exists(w.outside, "new.txt") and "write-outside-denied=out-of-scope" in out,
           f"[{label}] a write scope typed at the prompt: file created inside, none outside (checked on disk)", out[-300:])
    w.close()
    w = World(); prog = w.program(WRITE_PROBE)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], ["", "y"])
    record(not w.exists(w.inside, "new.txt") and not w.exists(w.outside, "new.txt"), f"[{label}] Enter at a write prompt: nothing written anywhere (checked on disk)", out[-300:])
    w.close()

# 'everywhere' must be typed in full; look-alikes are paths
for label, vm in BACKENDS:
    WRITE_OUT = '''use fs.write
try:
    write_file("{outside}/probe.txt", "P")
    print "outside=ok"
catch e:
    print "outside-denied=" + e.reason
'''
    w = World(); prog = w.program(WRITE_OUT)
    out, rc = pty_run(["run"] + vm + ["--ask", prog], ["everywhere", "y"], cwd=w.root)
    record(w.exists(w.outside, "probe.txt") and "outside=ok" in out, f"[{label}] positive control: the literal word 'everywhere' grants unrestricted writes", out[-300:])
    w.close()
    for word in ("Everywhere", "*", "all"):
        w = World(); prog = w.program(WRITE_OUT)
        out, rc = pty_run(["run"] + vm + ["--ask", prog], [word, "y"], cwd=w.root)
        record(not w.exists(w.outside, "probe.txt") and "outside-denied=out-of-scope" in out,
               f"[{label}] {word!r} is a path, not 'everywhere': writes outside it are denied (checked on disk)", out[-300:])
        w.close()

# ======================================================================
# 3. The prompt can only ADD what the program declared and no flag granted
# ======================================================================
for label, vm in BACKENDS:
    # undeclared capabilities are never offered; a flag grant for an undeclared one has no effect, and is shown as such
    UNDECLARED = '''use fs.read
print "RAN"
try:
    write_file("{inside}/sneak.txt", "X")
    print "write=ok"
catch e:
    print "write-denied=" + e.reason
try:
    print http_get("http://127.0.0.1:9/")
catch e:
    print "net-denied=" + e.reason
'''
    w = World(); prog = w.program(UNDECLARED)
    out, rc = pty_run(["run"] + vm + [f"--allow-write={w.inside}", "--allow-net", "--ask", prog], [w.inside, "y"])
    eff = effective_block(out)
    record("Allow fs.write?" not in out and "Allow net.http?" not in out, f"[{label}] capabilities the program does not declare are never asked about", out[:600])
    record(eff == {"fs.read": "allowed", "fs.write": "denied", "fs.list": "denied", "net.http": "denied"} and "does not declare" in out,
           f"[{label}] flag grants for undeclared capabilities are shown as denied, with the reason", repr(eff))
    record(not w.exists(w.inside, "sneak.txt") and "write-denied=not-declared" in out and "net-denied=not-declared" in out,
           f"[{label}] …and they really have no effect (disk checked)", out[-300:])
    w.close()

    # a flag-granted capability is not re-asked and keeps exactly its scope
    BOTH = '''use fs.read
use fs.write
print "RAN"
try:
    print "read-inside=" + read_file("{inside}/a.txt")
catch e:
    print "read-inside-denied=" + e.reason
try:
    print "read-outside=" + read_file("{outside}/secret.txt")
catch e:
    print "read-outside-denied=" + e.reason
try:
    write_file("{outside}/w.txt", "W")
    print "write-outside=ok"
catch e:
    print "write-outside-denied=" + e.reason
'''
    w = World(); prog = w.program(BOTH)
    out, rc = pty_run(["run"] + vm + [f"--allow-read={w.inside}", "--ask", prog], [w.outside, "y"])
    record("Allow fs.read?" not in out and "Allow fs.write?" in out and "granted by flag" in out,
           f"[{label}] a capability a flag already granted is shown, not re-asked", out[:700])
    record("read-inside=INSIDE-A" in out and "read-outside-denied=out-of-scope" in out and "TOP-SECRET" not in out and "write-outside=ok" in out and w.exists(w.outside, "w.txt"),
           f"[{label}] the prompt did not widen the flag's read scope, and the write it did grant works (checked on disk)", out[-400:])
    w.close()

    # everything granted by flags: no prompt at all (the flags are the grant)
    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + [f"--allow-read={w.inside}", "--ask", prog], [])
    record(rc == 0 and "requests these permissions" not in out and "inside=INSIDE-A" in out and "outside-denied=out-of-scope" in out,
           f"[{label}] when flags already grant everything the program declares there is no prompt", out[-300:])
    w.close()

# ======================================================================
# 4. What is shown is what is effective: every capability, checked against the program's actual behaviour
# ======================================================================
for label, vm in BACKENDS:
    srv = Server()
    PROBE_ALL = '''use fs.read
use fs.write
use fs.list
use net.http
print "RAN"
try:
    print "read:" + read_file("{inside}/a.txt")
catch e:
    print "read:denied"
try:
    write_file("{inside}/probe.txt", "P")
    print "write:ok"
catch e:
    print "write:denied"
try:
    print "list:" + list_dir("{inside}")[0]
catch e:
    print "list:denied"
try:
    print "net:" + http_get("http://127.0.0.1:PORT/probe")
catch e:
    print "net:denied"
'''.replace("PORT", str(srv.port))
    for scenario, answers in (("read+list+net granted, write denied", [None, "", None, None]),
                              ("only write granted", ["", None, "", ""]),
                              ("nothing granted", ["", "", "", ""])):
        w = World(); srv.hits.clear(); prog = w.program(PROBE_ALL)
        typed = []
        for cap, a in zip(("fs.read", "fs.write", "fs.list", "net.http"), answers):
            typed.append(w.inside if a is None and cap != "net.http" else (f"127.0.0.1:{srv.port}" if a is None else a))
        out, rc = pty_run(["run"] + vm + ["--ask", prog], typed + ["y"])
        eff = effective_block(out)
        behaved = {
            "fs.read": "allowed" if "read:INSIDE-A" in out else "denied",
            "fs.write": "allowed" if (w.exists(w.inside, "probe.txt") and "write:ok" in out) else "denied",
            "fs.list": "allowed" if "list:" in out and "list:denied" not in out else "denied",
            "net.http": "allowed" if (srv.hits == ["/probe"] and "net:NET-OK" in out) else "denied"}
        record(eff == behaved and rc == 0,
               f"[{label}] {scenario}: the 'Effective permissions' shown equal what the program could really do (disk and server checked)",
               f"shown={eff} behaved={behaved}\n{out[-600:]}")
        if scenario == "nothing granted":
            record(srv.hits == [] and not w.exists(w.inside, "probe.txt") and "INSIDE-A" not in out,
                   f"[{label}] nothing granted: no request reached the server, nothing written, nothing read", repr(srv.hits))
        w.close()
    srv.close()

# a closed stdin is not a terminal either (this used to be an AttributeError)
w = World(); prog = w.program(READ_PROBE)
r = subprocess.run([sys.executable, LUMEN, "run", "--ask", prog], capture_output=True, text=True, preexec_fn=lambda: os.close(0))
record(r.returncode == 2 and "interactive terminal" in r.stdout and "Traceback" not in r.stderr + r.stdout and "RAN" not in r.stdout,
       "--ask with stdin closed is refused cleanly (exit 2, no traceback)", repr((r.stdout, r.stderr[-200:], r.returncode)))
w.close()

# --ask with an audit log: typing 'everywhere' for fs.write is refused with an explanation, never a traceback
for label, vm in BACKENDS:
    w = World(); prog = w.program(WRITE_PROBE)
    out, rc = pty_run(["run"] + vm + [f"--audit-log={w.log}", "--ask", prog], ["everywhere", "", "y"], cwd=w.root)
    record("Traceback" not in out and "unrestricted fs.write" in out and rc == 0 and not w.exists(w.inside, "new.txt") and not w.exists(w.outside, "new.txt")
           and "write-inside-denied=not-granted" in out,
           f"[{label}] 'everywhere' for fs.write next to an audit log is refused and re-asked; Enter leaves it denied (disk checked)", out[-600:])
    w.close()
    w = World(); prog = w.program(WRITE_PROBE)
    out, rc = pty_run(["run"] + vm + [f"--audit-log={w.log}", "--ask", prog], [w.inside, "y"], cwd=w.root)
    record(w.exists(w.inside, "new.txt") and not w.exists(w.outside, "new.txt") and "FORGED" not in open(w.log).read(),
           f"[{label}] a write scope that does not reach the log is granted next to an audit log (positive control)", out[-300:])
    w.close()

# ======================================================================
# 5. Terminal safety
# ======================================================================
w = World()
weird = w.program('use fs.read\nprint "RAN"\n', name="a\x1b[31mred\x07.lum")
out, rc = pty_run(["run", "--ask", weird], ["", "y"])
record("\x1b" not in out and "\x07" not in out and "\\x1b" in out,
       "a program file name containing escape sequences is shown escaped; no control character reaches the terminal", repr(out[:200]))
w.close()

# ======================================================================
# 6. The audit log through the CLI
# ======================================================================
LOGGED = '''use fs
use net
print "RAN"
try:
    print read_file("{inside}/a.txt")
catch e:
    print "x"
try:
    print read_file("{outside}/secret.txt")
catch e:
    print "outside:" + e.reason
try:
    print http_get("http://user:hunter2@127.0.0.1:PORT/private/path?token=SEKRET")
catch e:
    print "net:" + e.reason
'''
logs = {}
for label, vm in BACKENDS:
    srv = Server()
    w = World(); prog = w.program(LOGGED.replace("PORT", str(srv.port)))
    out, err, rc = cli(["run"] + vm + [f"--allow-read={w.inside}", f"--audit-log={w.log}", prog], cwd=w.root)
    text = open(w.log).read()
    evs = events(w.log)
    record(rc == 0 and "INSIDE-A" in out and "TOP-SECRET" not in out + text and srv.hits == [],
           f"[{label}] positive control: audited run behaves as an unaudited one (inside readable, outside denied, no request sent)", repr((out, srv.hits)))
    record(evs[0]["event"] == "run" and evs[0]["command"] == "run" and evs[0]["backend"] == label and evs[0]["file"] == prog
           and evs[0]["grants"] == {"fs.read": [w.inside]},
           f"[{label}] the first record says which program ran, on which backend, with which grants", repr(evs[0]))
    acc = [(e["decision"], e["capability"], e["operation"], e.get("reason")) for e in evs if e["event"] == "access"]
    record(acc == [("allow", "fs.read", "read_file", None), ("deny", "fs.read", "read_file", "out-of-scope"),
                   ("deny", "net.http", "http_get", "not-granted")],
           f"[{label}] allows and denials are recorded in order with their reasons", repr(acc))
    record(not any(s in text for s in ("hunter2", "SEKRET", "private", "token", "TOP-SECRET", "INSIDE-A")),
           f"[{label}] the log holds no credentials, URL path or query, and no file contents", text)
    record(oct(os.stat(w.log).st_mode & 0o777) == "0o600", f"[{label}] the log file is private (0600)")
    logs[label] = json.loads(json.dumps(norm(evs)).replace(w.root, "<root>").replace(str(srv.port), "<port>"))
    srv.close(); w.close()
record(logs["interpreter"] == logs["vm"], "the CLI writes the same audit records on the interpreter and the VM", repr(logs))

# --audit-level=denied
w = World(); prog = w.program(READ_PROBE)
out, err, rc = cli(["run", f"--allow-read={w.inside}", f"--audit-log={w.log}", "--audit-level=denied", prog])
evs = events(w.log)
record([(e["event"], e.get("decision")) for e in evs] == [("run", None), ("declare", None), ("access", "deny")],
       "--audit-level=denied keeps the run, the declaration and the denials but not the allows", repr(evs))
w.close()

# the program cannot erase or forge its own log
for label, vm in BACKENDS:
    TAMPER = '''use fs
print "RAN"
try:
    write_file("{log}", "FORGED")
    print "write=ok"
catch e:
    print "write:" + e.reason
try:
    append_file("{log}", "FORGED")
    print "append=ok"
catch e:
    print "append:" + e.reason
try:
    delete_file("{log}")
    print "delete=ok"
catch e:
    print "delete:" + e.reason
try:
    write_file("{inside}/fine.txt", "F")
    print "inside=ok"
catch e:
    print "inside:" + e.reason
'''
    w = World(); prog = w.program(TAMPER)
    out, err, rc = cli(["run"] + vm + [f"--allow-write={w.inside}", f"--audit-log={w.log}", prog])
    text = open(w.log).read() if os.path.exists(w.log) else None
    record(text is not None and "FORGED" not in text and out.count("out-of-scope") == 3 and "inside=ok" in out and w.exists(w.inside, "fine.txt"),
           f"[{label}] write / append / delete aimed at the log are refused; the log is intact on disk; ordinary writes still work", repr((out, text)))
    record(text is not None and text.count('"decision":"deny"') == 3, f"[{label}] …and the three attempts are recorded", text)
    w.close()

    # a configuration that would let the program reach its log is refused before anything runs
    for flags, why in ((["--allow-write"], "an unrestricted --allow-write"), ([f"--allow-write={'{root}'}"], "a write scope containing the log"),
                       (["--allow-fs"], "an unrestricted --allow-fs")):
        w = World(); prog = w.program('use fs\nwrite_file("{root}/ran.txt", "x")\nprint "RAN"\n')
        fl = [f.replace("{root}", w.root) for f in flags]
        out, err, rc = cli(["run"] + vm + fl + [f"--audit-log={w.log}", prog])
        record(rc == 2 and "RAN" not in out and not w.exists(w.root, "ran.txt") and "Error:" in out,
               f"[{label}] {why} together with --audit-log is refused (exit 2) and the program does not run (disk checked)", repr((out, rc)))
        w.close()

# log path problems
w = World(); prog = w.program(READ_PROBE)
out, err, rc = cli(["run", f"--audit-log={os.path.join(w.root, 'nodir', 'x.jsonl')}", prog])
record(rc == 2 and "cannot open the audit log" in out and "RAN" not in out, "an unwritable audit log path stops the run before it starts", repr((out, rc)))
out, err, rc = cli(["run", f"--audit-log={w.logs}", prog])
record(rc == 2 and "RAN" not in out, "a directory as the audit log is refused")
os.symlink(os.path.join(w.outside, "secret.txt"), os.path.join(w.logs, "ln.jsonl"))
out, err, rc = cli(["run", f"--audit-log={os.path.join(w.logs, 'ln.jsonl')}", prog])
record(rc == 2 and "symbolic link" in out and open(os.path.join(w.outside, "secret.txt")).read() == "TOP-SECRET-OUTSIDE",
       "a symlink as the audit log is refused and its target is untouched", repr((out, rc)))
for bad in (["--audit-log"], ["--audit-level=all"], [f"--audit-log={w.log}", "--audit-level=bogus"], ["--audit-bogus=1"]):
    out, err, rc = cli(["run"] + bad + [prog])
    record(rc == 2 and "Error:" in out and "RAN" not in out, f"audit flag misuse {bad} is an error (exit 2) and nothing runs", repr((out, rc)))
w.close()

# lumen test and repl record a run, too
w = World()
t = w.program('use fs.read\ntest "reads inside":\n    assert(read_file("{inside}/a.txt") == "INSIDE-A")\ntest "outside is denied":\n    try:\n        read_file("{outside}/secret.txt")\n        assert(false)\n    catch e:\n        assert(e.reason == "out-of-scope")\n', name="t.lum")
out, err, rc = cli(["test", t, f"--allow-read={w.inside}", f"--audit-log={w.log}"])
evs = events(w.log)
record(rc == 0 and evs[0]["event"] == "run" and evs[0]["command"] == "test" and sum(1 for e in evs if e.get("decision") == "deny") >= 1,
       "`lumen test` is audited too: a run record, then the decisions", repr((out, evs[:2])))
w.close()

# ======================================================================
# 7. The prompt and the audit log together
# ======================================================================
for label, vm in BACKENDS:
    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + [f"--audit-log={w.log}", "--ask", prog], [w.inside, "y"])
    evs = events(w.log)
    kinds = [(e["event"], e.get("decision"), e.get("capability")) for e in evs]
    record(kinds[:4] == [("request", None, None), ("consent", "granted", "fs.read"), ("confirm", "run", None), ("run", None, None)]
           and evs[1]["scope"] == w.inside and evs[3]["grants"] == {"fs.read": [w.inside]},
           f"[{label}] the request, the consent answer, the confirmation and the resulting grants are in the log, in that order", repr(kinds))
    record(("access", "allow", "fs.read") in kinds and ("access", "deny", "fs.read") in kinds, f"[{label}] …followed by the run's own decisions")
    w.close()
    w = World(); prog = w.program(READ_PROBE)
    out, rc = pty_run(["run"] + vm + [f"--audit-log={w.log}", "--ask", prog], [w.inside, "n"])
    evs = events(w.log)
    record(rc == 1 and [e["event"] for e in evs] == ["request", "consent", "confirm"] and evs[-1]["decision"] == "cancelled",
           f"[{label}] a cancelled run is logged as cancelled, and no `run` record exists because nothing ran", repr([e["event"] for e in evs]))
    w.close()

# ======================================================================
# 8. Help
# ======================================================================
out, err, rc = cli(["--help"])
record(rc == 0 and "--ask" in out and "--audit-log=FILE" in out and "--audit-level" in out and "Without --ask nothing is ever prompted for" in out,
       "`lumen --help` documents --ask and the audit flags", out[-500:])

print()
print("=" * 60)
print(f"CONSENT + AUDIT CLI SUITE: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
if FAILURES:
    print(f"\n{len(FAILURES)} failures:")
    for f in FAILURES:
        print("  FAIL:", f)
    sys.exit(1)
