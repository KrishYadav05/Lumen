"""Conformance tests for docs/capabilities.md §5 — time-of-check/time-of-use races.

The scenario: a program is granted `--allow-*=INSIDE`. A *different process* (the attacker; Lumen has no
mkdir/rename/symlink builtin, so the Lumen program itself cannot do this) has write access inside INSIDE and,
between the moment the runtime decides a path is inside the scope and the moment it touches the file system,
renames a directory in the path and puts a symlink to OUTSIDE in its place.

The swap is made DETERMINISTIC by wrapping the runtime's path-authorisation step (`_fs_target`): the wrapper
calls the real one, then performs the swap — i.e. it lands the attacker's move in exactly the window between
check and use. Each test then looks at the real file system for what the operation did OUTSIDE the scope:
a secret read, a file created, a victim modified or deleted, an existence or listing revealed.

A second group runs a real racing thread and checks the same invariants statistically; a third pins the
granted directory itself.
"""
import os, sys, tempfile, shutil, threading, time
from harness import print_final_summary
import harness
import lumen
from lumen import Policy, CapabilityContext, capability_call, LumenError, LumenRaise

SPEC = "capabilities.md §5.4"

def record(ok, name, detail=""):
    if ok:
        harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[{SPEC}] {name}: {detail}")

def read(p):
    """File contents, or None if it is gone — a deleted victim must be a recorded failure, not a crash."""
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None

class World:
    def __init__(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.inside = os.path.join(self.root, "inside")
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(os.path.join(self.inside, "d"))
        os.makedirs(os.path.join(self.outside, "pdir"))
        for d, n, t in ((self.inside, "d/secret.txt", "INSIDE-FILE"), (self.inside, "d/victim.txt", "INSIDE-VICTIM"),
                        (self.outside, "secret.txt", "TOP-SECRET"), (self.outside, "victim.txt", "OUTSIDE-VICTIM"),
                        (self.outside, "probe.txt", "x")):
            with open(os.path.join(d, n), "w") as f:
                f.write(t)

    def swap(self):
        """The attacker's move: inside/d  ->  inside/d.moved ; inside/d becomes a symlink to outside/."""
        os.rename(os.path.join(self.inside, "d"), os.path.join(self.inside, "d.moved"))
        os.symlink(self.outside, os.path.join(self.inside, "d"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

def make_ctx(w, caps):
    ctx = CapabilityContext(Policy({c: [w.inside] for c in caps}))
    ctx.declare(list(caps))
    return ctx

class RaceAfterCheck:
    """Context manager: lumen._fs_target runs normally, then the attacker swaps the directory."""
    def __init__(self, w): self.w = w
    def __enter__(self):
        self.orig = lumen._fs_target
        w, orig = self.w, self.orig
        def wrapped(*a, **k):
            result = orig(*a, **k)
            w.swap()
            return result
        lumen._fs_target = wrapped
        return self
    def __exit__(self, *exc):
        lumen._fs_target = self.orig

def attempt(w, caps, op, *args):
    ctx = make_ctx(w, caps)
    with RaceAfterCheck(w):
        try:
            return ("ok", capability_call(ctx, op, list(args)))
        except (LumenError, LumenRaise, OSError) as e:
            return ("stopped", e)

def untouched_outside(w):
    return (read(os.path.join(w.outside, "secret.txt")) == "TOP-SECRET"
            and read(os.path.join(w.outside, "victim.txt")) == "OUTSIDE-VICTIM"
            and sorted(os.listdir(w.outside)) == ["pdir", "probe.txt", "secret.txt", "victim.txt"])

if not hasattr(os, "symlink"):
    record(True, "symlinks unavailable on this platform (race tests not run)")
else:
    # ---- read: the secret outside must not come back ----
    w = World(); kind, val = attempt(w, ["fs.read"], "read_file", os.path.join(w.inside, "d", "secret.txt"))
    record(val != "TOP-SECRET" and untouched_outside(w), "a read of a path swapped for a link to outside never returns the outside file", repr((kind, val)))
    w.close()

    # ---- write: nothing may be created outside ----
    w = World(); attempt(w, ["fs.write"], "write_file", os.path.join(w.inside, "d", "created.txt"), "PAYLOAD")
    record(not os.path.exists(os.path.join(w.outside, "created.txt")),
           "write_file through a swapped directory creates no file outside the scope (not even an empty one)",
           str(sorted(os.listdir(w.outside))))
    w.close()

    w = World(); attempt(w, ["fs.write"], "write_file", os.path.join(w.inside, "d", "victim.txt"), "PWNED")
    record(read(os.path.join(w.outside, "victim.txt")) == "OUTSIDE-VICTIM", "write_file through a swapped directory does not modify an outside file")
    w.close()

    w = World(); attempt(w, ["fs.write"], "append_file", os.path.join(w.inside, "d", "victim.txt"), "PWNED")
    record(read(os.path.join(w.outside, "victim.txt")) == "OUTSIDE-VICTIM", "append_file through a swapped directory does not modify an outside file")
    w.close()

    w = World(); attempt(w, ["fs.write"], "append_file", os.path.join(w.inside, "d", "created.txt"), "PAYLOAD")
    record(not os.path.exists(os.path.join(w.outside, "created.txt")), "append_file through a swapped directory creates no file outside the scope")
    w.close()

    # ---- delete: the outside victim must survive ----
    w = World(); attempt(w, ["fs.write"], "delete_file", os.path.join(w.inside, "d", "victim.txt"))
    record(os.path.exists(os.path.join(w.outside, "victim.txt")), "delete_file through a swapped directory does not delete an outside file")
    w.close()

    # ---- existence / type / listing: no information about outside ----
    w = World(); kind, val = attempt(w, ["fs.read"], "file_exists", os.path.join(w.inside, "d", "probe.txt"))
    record(val is not True, "file_exists through a swapped directory does not reveal an outside file", repr((kind, val)))
    w.close()

    w = World(); kind, val = attempt(w, ["fs.read"], "is_dir", os.path.join(w.inside, "d", "pdir"))
    record(val is not True, "is_dir through a swapped directory does not reveal an outside directory", repr((kind, val)))
    w.close()

    w = World(); kind, val = attempt(w, ["fs.list"], "list_dir", os.path.join(w.inside, "d"))
    record(not (kind == "ok" and "probe.txt" in val), "list_dir through a swapped directory does not list the outside directory", repr((kind, val)))
    w.close()

    # the refusal is a structured PermissionError (reason out-of-scope), not a generic OS error
    from lumen import PermissionDenied
    for op, caps, mk in (("read_file", ["fs.read"], lambda w: [os.path.join(w.inside, "d", "secret.txt")]),
                         ("write_file", ["fs.write"], lambda w: [os.path.join(w.inside, "d", "n.txt"), "x"]),
                         ("append_file", ["fs.write"], lambda w: [os.path.join(w.inside, "d", "n.txt"), "x"]),
                         ("delete_file", ["fs.write"], lambda w: [os.path.join(w.inside, "d", "victim.txt")]),
                         ("file_exists", ["fs.read"], lambda w: [os.path.join(w.inside, "d", "probe.txt")]),
                         ("is_dir", ["fs.read"], lambda w: [os.path.join(w.inside, "d", "pdir")]),
                         ("list_dir", ["fs.list"], lambda w: [os.path.join(w.inside, "d")])):
        w = World(); kind, val = attempt(w, caps, op, *mk(w))
        record(kind == "stopped" and isinstance(val, PermissionDenied) and val.reason == "out-of-scope"
               and val.capability == caps[0] and val.operation == op,
               f"{op}: a directory swapped for a link mid-operation is refused with a structured out-of-scope PermissionError", repr((kind, val)))
        w.close()

    # ---- positive control: with no attacker, the same operations succeed ----
    w = World(); ctx = make_ctx(w, ["fs.read", "fs.write", "fs.list"])
    ok = (capability_call(ctx, "read_file", [os.path.join(w.inside, "d", "secret.txt")]) == "INSIDE-FILE"
          and capability_call(ctx, "file_exists", [os.path.join(w.inside, "d", "victim.txt")]) is True
          and capability_call(ctx, "is_dir", [os.path.join(w.inside, "d")]) is True
          and sorted(capability_call(ctx, "list_dir", [os.path.join(w.inside, "d")])) == ["secret.txt", "victim.txt"])
    capability_call(ctx, "write_file", [os.path.join(w.inside, "d", "new.txt"), "N"])
    capability_call(ctx, "append_file", [os.path.join(w.inside, "d", "new.txt"), "M"])
    ok = ok and read(os.path.join(w.inside, "d", "new.txt")) == "NM"
    capability_call(ctx, "delete_file", [os.path.join(w.inside, "d", "new.txt")])
    ok = ok and not os.path.exists(os.path.join(w.inside, "d", "new.txt"))
    record(ok, "positive control: every operation works inside the scope when nothing races")
    w.close()

    # ---- ordinary (non-racing) behaviour is identical with and without a scope ----
    # The descriptor-relative implementation must not change what any operation returns or which error text it gives:
    # run the same awkward cases under an unrestricted grant and a scoped one and require identical outcomes.
    w = World()
    unres = CapabilityContext(Policy({c: None for c in ("fs.read", "fs.write", "fs.list")})); unres.declare(["fs.read", "fs.write", "fs.list"])
    scoped = make_ctx(w, ["fs.read", "fs.write", "fs.list"])
    os.makedirs(os.path.join(w.inside, "emptydir"))
    I = lambda *p: os.path.join(w.inside, *p)
    cases = [("read_file", [I("d", "secret.txt")]), ("read_file", [I("nope.txt")]), ("read_file", [I("d")]),
             ("read_file", [I("d", "secret.txt", "x")]), ("read_file", [I("missing", "x.txt")]),
             ("file_exists", [I("d", "secret.txt")]), ("file_exists", [I("d", "secret.txt", "x")]), ("file_exists", [I("missing", "x")]),
             ("file_exists", [I("d")]), ("file_exists", [w.inside]), ("is_dir", [I("d")]), ("is_dir", [I("d", "secret.txt")]),
             ("is_dir", [I("d", "secret.txt", "x")]), ("is_dir", [w.inside]), ("is_dir", [I("nope")]),
             ("list_dir", [I("d")]), ("list_dir", [I("emptydir")]), ("list_dir", [I("d", "secret.txt")]), ("list_dir", [I("nope")]),
             ("list_dir", [w.inside]),
             ("write_file", [I("d")] + ["x"]), ("write_file", [I("missing", "n.txt"), "x"]), ("append_file", [I("d"), "x"]),
             ("delete_file", [I("d")]), ("delete_file", [I("nope.txt")]), ("delete_file", [I("emptydir")]), ("delete_file", [w.inside]),
             ("read_file", [w.inside])]
    def outcome(ctx, op, args):
        try:
            v = capability_call(ctx, op, args)
            return ("ok", v)
        except (LumenError, LumenRaise) as e:
            return ("err", str(getattr(e, "message", e)))
        except Exception as e:                      # a raw Python exception is a bug in itself
            return ("CRASH", repr(e))
    mismatches = []
    for op, args in cases:
        a, b = outcome(unres, op, args), outcome(scoped, op, args)
        if a != b or a[0] == "CRASH":
            mismatches.append((op, args[0].replace(w.inside, "<in>"), a, b))
    record(not mismatches, "scoped and unrestricted file access return identical results and error messages for ordinary cases (no raw exceptions)",
           str(mismatches[:3]))
    w.close()

    # ---- a real racing thread (statistical: complements the deterministic swap above) ----
    # An attacker thread keeps turning inside/d into a link to outside/ and back, while worker threads hammer every
    # operation through inside/d. Invariants that must hold on EVERY iteration: no outside secret is ever returned,
    # nothing is ever created in or deleted from outside/, no outside file or directory is ever reported to exist.
    w = World()
    stop = threading.Event()
    d_path, moved = os.path.join(w.inside, "d"), os.path.join(w.inside, "d.moved")
    def attacker():
        while not stop.is_set():
            try:
                os.rename(d_path, moved)
                os.symlink(w.outside, d_path)
                time.sleep(0.0005)
                os.unlink(d_path)
                os.rename(moved, d_path)
                time.sleep(0.0005)
            except OSError:
                pass
    violations = []
    rctx = make_ctx(w, ["fs.read", "fs.write", "fs.list"])
    def worker(kind):
        i = 0
        while not stop.is_set():
            i += 1
            try:
                if kind == "read":
                    v = capability_call(rctx, "read_file", [os.path.join(d_path, "secret.txt")])
                    if v == "TOP-SECRET": violations.append("read returned the outside secret")
                elif kind == "exists":
                    if capability_call(rctx, "file_exists", [os.path.join(d_path, "probe.txt")]) is True:
                        violations.append("file_exists reported an outside file")
                    if capability_call(rctx, "is_dir", [os.path.join(d_path, "pdir")]) is True:
                        violations.append("is_dir reported an outside directory")
                elif kind == "list":
                    v = capability_call(rctx, "list_dir", [d_path])
                    if "probe.txt" in v or "pdir" in v: violations.append("list_dir listed the outside directory")
                elif kind == "write":
                    capability_call(rctx, "write_file", [os.path.join(d_path, "created.txt"), "x"])
                    capability_call(rctx, "append_file", [os.path.join(d_path, "created2.txt"), "x"])
                elif kind == "delete":
                    capability_call(rctx, "delete_file", [os.path.join(d_path, "victim.txt")])
            except (LumenError, LumenRaise, OSError):
                pass
    threads = [threading.Thread(target=attacker)] + [threading.Thread(target=worker, args=(k,)) for k in ("read", "exists", "list", "write", "delete")]
    for t in threads: t.start()
    time.sleep(3.0)
    stop.set()
    for t in threads: t.join()
    leaked_files = sorted(set(os.listdir(w.outside)) - {"pdir", "probe.txt", "secret.txt", "victim.txt"})
    record(not violations and not leaked_files and read(os.path.join(w.outside, "secret.txt")) == "TOP-SECRET"
           and read(os.path.join(w.outside, "victim.txt")) == "OUTSIDE-VICTIM" and os.path.exists(os.path.join(w.outside, "probe.txt")),
           "a racing attacker thread never gets an operation through to outside the scope (3 s, 5 workers)",
           f"violations={sorted(set(violations))} created_outside={leaked_files}")
    w.close()

    # ---- the granted directory itself is pinned ----
    # The directory named by the grant is opened when the policy is built. An attacker who, in the window after the
    # check, renames it and puts a link to outside/ at its old path does not redirect a program that was granted the
    # original directory: every operation still lands in (or is refused for) the directory that was granted.
    def root_swap(w):
        os.rename(w.inside, os.path.join(w.root, "inside.held"))
        os.symlink(w.outside, w.inside)
    for op, caps, mk in (
            ("read_file", ["fs.read"], lambda w: [os.path.join(w.inside, "a.txt")]),
            ("file_exists", ["fs.read"], lambda w: [os.path.join(w.inside, "probe.txt")]),
            ("is_dir", ["fs.read"], lambda w: [os.path.join(w.inside, "pdir")]),
            ("list_dir", ["fs.list"], lambda w: [w.inside]),
            ("write_file", ["fs.write"], lambda w: [os.path.join(w.inside, "created.txt"), "x"]),
            ("append_file", ["fs.write"], lambda w: [os.path.join(w.inside, "victim.txt"), "x"]),
            ("delete_file", ["fs.write"], lambda w: [os.path.join(w.inside, "victim.txt")])):
        w = World()
        with open(os.path.join(w.inside, "a.txt"), "w") as f: f.write("INSIDE-A")
        with open(os.path.join(w.outside, "a.txt"), "w") as f: f.write("TOP-SECRET")
        ctx = make_ctx(w, caps)
        orig = lumen._fs_target
        def wrapped(*a, _w=w, _orig=orig, **k):
            r = _orig(*a, **k); root_swap(_w); return r
        lumen._fs_target = wrapped
        try:
            try: got = capability_call(ctx, op, mk(w))
            except (LumenError, LumenRaise, OSError): got = "stopped"
        finally:
            lumen._fs_target = orig
        clean = (got != "TOP-SECRET" and not (op in ("file_exists", "is_dir") and got is True and False)
                 and not (op == "list_dir" and got != "stopped" and ("probe.txt" in got or "pdir" in got))
                 and not os.path.exists(os.path.join(w.outside, "created.txt"))
                 and read(os.path.join(w.outside, "victim.txt")) == "OUTSIDE-VICTIM"
                 and os.path.exists(os.path.join(w.outside, "probe.txt")))
        record(clean, f"{op}: after the granted directory is renamed and a link to outside put in its place, nothing outside is touched", repr(got))
        w.close()

    # ---- an ANCESTOR of the granted directory is swapped: only pinning protects against this ----
    # scope = root/a/inside. After the check the attacker renames root/a away and puts a link to a lookalike tree
    # (root/evil, which has its own inside/ with a probe file and a victim) where it was. A policy that re-resolved
    # the path would follow the link; one that pinned inside/ when it was built does not.
    # Run it twice: as on Linux, and with the platform reported as macOS, where there is no /proc re-check to fall
    # back on — there the pinned descriptor is the ONLY thing standing between the program and the lookalike tree.
    for plat, op, caps, mk in [(plat, *case) for plat in (sys.platform, "darwin") for case in (
            ("file_exists", ["fs.read"], lambda base: [os.path.join(base, "inside", "probe.txt")]),
            ("list_dir", ["fs.list"], lambda base: [os.path.join(base, "inside")]),
            ("write_file", ["fs.write"], lambda base: [os.path.join(base, "inside", "created.txt"), "x"]),
            ("delete_file", ["fs.write"], lambda base: [os.path.join(base, "inside", "victim.txt")]))]:
        _saved_platform = sys.platform
        sys.platform = plat
        root = os.path.realpath(tempfile.mkdtemp())
        base, evil = os.path.join(root, "a"), os.path.join(root, "evil")
        for d in (os.path.join(base, "inside"), os.path.join(evil, "inside")):
            os.makedirs(d)
        with open(os.path.join(base, "inside", "victim.txt"), "w") as f: f.write("INSIDE-VICTIM")
        with open(os.path.join(evil, "inside", "victim.txt"), "w") as f: f.write("EVIL-VICTIM")
        with open(os.path.join(evil, "inside", "probe.txt"), "w") as f: f.write("x")
        ctx = CapabilityContext(Policy({c: [os.path.join(base, "inside")] for c in caps})); ctx.declare(list(caps))
        orig = lumen._fs_target
        def wrapped(*a, _root=root, _base=base, _evil=evil, _orig=orig, **k):
            r = _orig(*a, **k)
            os.rename(_base, os.path.join(_root, "a.held")); os.symlink(_evil, _base)
            return r
        lumen._fs_target = wrapped
        try:
            try: got = capability_call(ctx, op, mk(base))
            except (LumenError, LumenRaise, OSError): got = "stopped"
        finally:
            lumen._fs_target = orig
        clean = (got is not True if op == "file_exists" else True) \
            and not (op == "list_dir" and got != "stopped" and "probe.txt" in got) \
            and not os.path.exists(os.path.join(evil, "inside", "created.txt")) \
            and read(os.path.join(evil, "inside", "victim.txt")) == "EVIL-VICTIM"
        sys.platform = _saved_platform
        record(clean, f"{op}: swapping an ANCESTOR of the granted directory after the check reaches nothing outside (pinned; platform {plat!r})", repr(got))
        shutil.rmtree(root, ignore_errors=True)

    # ---- single-file and not-yet-existing scopes are anchored through their parent ----
    w = World()
    one = os.path.join(w.inside, "d", "secret.txt")
    ctx = CapabilityContext(Policy({"fs.read": [one], "fs.write": [one]})); ctx.declare(["fs.read", "fs.write"])
    ok = capability_call(ctx, "read_file", [one]) == "INSIDE-FILE"
    capability_call(ctx, "write_file", [one, "CHANGED"])
    ok = ok and read(one) == "CHANGED"
    try:
        capability_call(ctx, "read_file", [os.path.join(w.inside, "d", "victim.txt")]); ok = False
    except LumenError:
        pass
    record(ok, "a single-file scope grants exactly that file, through its parent directory")
    later = os.path.join(w.inside, "later")                      # a scope that does not exist when the policy is built...
    ctx = CapabilityContext(Policy({"fs.write": [later], "fs.read": [later]})); ctx.declare(["fs.read", "fs.write"])
    os.mkdir(later)                                              # ...and is created afterwards (by the host, not the program)
    capability_call(ctx, "write_file", [os.path.join(later, "x.txt"), "LATE"])
    record(read(os.path.join(later, "x.txt")) == "LATE" and capability_call(ctx, "read_file", [os.path.join(later, "x.txt")]) == "LATE",
           "a scope directory created after the policy was built is pinned on first use and works")
    w.close()

    # ---- where descriptor-relative access is unavailable (Windows), the path-based fallback still denies escapes ----
    saved = lumen._HAVE_DIRFD
    lumen._HAVE_DIRFD = False
    try:
        w = World()
        ctx = make_ctx(w, ["fs.read", "fs.write", "fs.list"])
        os.symlink(os.path.join(w.outside, "secret.txt"), os.path.join(w.inside, "esc.txt"))
        try:
            v = capability_call(ctx, "read_file", [os.path.join(w.inside, "esc.txt")])
        except LumenError as e:
            v = "denied"
        ok = (v == "denied" and capability_call(ctx, "read_file", [os.path.join(w.inside, "d", "secret.txt")]) == "INSIDE-FILE"
              and capability_call(ctx, "is_dir", [os.path.join(w.inside, "d")]) is True
              and sorted(capability_call(ctx, "list_dir", [os.path.join(w.inside, "d")])) == ["secret.txt", "victim.txt"])
        capability_call(ctx, "write_file", [os.path.join(w.inside, "d", "n.txt"), "N"])
        capability_call(ctx, "delete_file", [os.path.join(w.inside, "d", "n.txt")])
        ok = ok and not os.path.exists(os.path.join(w.inside, "d", "n.txt"))
        record(ok, "without descriptor-relative access the path-based fallback still works and refuses an escaping symlink")
        w.close()
    finally:
        lumen._HAVE_DIRFD = saved

print_final_summary()
