"""Conformance tests for docs/security.md (chapter 7) — Language Specification 3.0.

Chapter 7 specifies the security model; docs/capabilities.md (tested in
test_capabilities.py) specifies each capability and the host-side policy.
The 3.0 model has one rule that every test below is a consequence of:

    DECLARING a capability (`use fs.read`) is not GRANTING it. The host decides
    what a run may do (a `Policy`; `--allow-*` on the command line). Nothing in
    a .lum file can create, widen or inspect a grant.

So the cases are organised around the four independent checks an operation must
pass — declared by this module, declared by every importer, granted by the
host, inside the granted scope — and each is tested by making exactly that one
check fail while the others pass.
"""
import os, tempfile, shutil
from harness import check, run_source, print_final_summary, ALL, FS_ALL, NET_ALL
import harness
from lumen import Policy

# (operation call, capability it needs). One real, representative call each.
OPS = [
    ('read_file("{p}")',            "read_file",   "fs.read"),
    ('file_exists("{p}")',          "file_exists", "fs.read"),
    ('is_dir("{p}")',               "is_dir",      "fs.read"),
    ('write_file("{p}", "y")',      "write_file",  "fs.write"),
    ('append_file("{p}", "y")',     "append_file", "fs.write"),
    ('delete_file("{p}")',          "delete_file", "fs.write"),
    ('list_dir("{d}")',             "list_dir",    "fs.list"),
    ('http_get("http://127.0.0.1:9/x")', "http_get", "net.http"),
]


def sandbox():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "f.txt")
    with open(p, "w") as f:
        f.write("content")
    return d, p


def fill(call, d, p):
    return call.format(d=d, p=p)


# ======================================================================
# §7.3  default-deny: nothing is permitted without BOTH a declaration and a grant
# ======================================================================
d, p = sandbox()
try:
    for call, op, cap in OPS:
        src_call = fill(call, d, p)
        check("security.md §7.3", f"{op}: no declaration and no grant is denied",
              f"print {src_call}", expect_ok=False,
              expect_error_substr=f"PermissionError: {op}() needs the '{cap}' capability, which this program has not declared")
        check("security.md §7.3", f"{op}: declaring '{cap}' alone does not grant it",
              f"use {cap}\nprint {src_call}", expect_ok=False,
              expect_error_substr=f"PermissionError: {op}() requires the '{cap}' capability, which was not granted for this run")
        check("security.md §7.3", f"{op}: a host grant alone, without a declaration, is denied",
              f"print {src_call}", expect_ok=False, policy=ALL,
              expect_error_substr=f"which this program has not declared")
finally:
    shutil.rmtree(d)

# ---- declared AND granted: the operation actually runs ----
d, p = sandbox()
try:
    check("security.md §7.3", "declared and granted: read_file reads", f'use fs.read\nprint read_file("{p}")',
          "content", policy=Policy({"fs.read": None}))
    check("security.md §7.3", "declared and granted: write_file then read_file",
          f'use fs.read\nuse fs.write\nwrite_file("{p}", "new")\nprint read_file("{p}")',
          "new", policy=Policy({"fs.read": None, "fs.write": None}))
    check("security.md §7.3", "declared and granted: list_dir", f'use fs.list\nprint list_dir("{d}")',
          "[f.txt]", policy=Policy({"fs.list": None}))
    check("security.md §7.3", "declared and granted: http_get reaches the network layer (connection refused, not a denial)",
          'use net.http\ntry:\n    http_get("http://127.0.0.1:9/x")\ncatch e:\n    print e.kind',
          "Error", policy=NET_ALL)
finally:
    shutil.rmtree(d)

# ======================================================================
# §7.3  capabilities are independent
# ======================================================================
d, p = sandbox()
try:
    for granted in ("fs.read", "fs.write", "fs.list", "net.http"):
        policy = Policy({granted: None})
        for call, op, cap in OPS:
            body = fill(call, d, p)
            src = (f"use fs\nuse net\n"        # the program declares EVERYTHING: only the host grant differs
                   f"try:\n    {body}\n    print \"ok\"\ncatch e:\n    print e.kind")
            out, err = run_source(src, policy=policy)
            got = out.strip()
            # an allowed op either succeeds or fails at the OS/network layer (kind "Error");
            # a disallowed one is a PermissionError
            expected_allowed = (cap == granted)
            ok = err is None and ((got in ("ok", "Error")) if expected_allowed else (got == "PermissionError"))
            if ok:
                harness.PASS += 1
            else:
                harness.FAIL += 1
                harness.FAILURES.append(
                    f"[security.md §7.3] only '{granted}' granted: {op}() should be "
                    f"{'allowed' if expected_allowed else 'denied'}, got out={out!r} err={err!r}")
            if op == "write_file":   # restore the file the matrix may have changed / deleted
                with open(p, "w") as f: f.write("content")
            if op == "delete_file" and not os.path.exists(p):
                with open(p, "w") as f: f.write("content")
finally:
    shutil.rmtree(d)

check("security.md §7.3", "`use fs.read` declares read only: write_file is not declared",
      'use fs.read\nwrite_file("/tmp/_lumen_never", "x")', expect_ok=False, policy=ALL,
      expect_error_substr="write_file() needs the 'fs.write' capability, which this program has not declared")
check("security.md §7.3", "`use fs` is shorthand declaring fs.read, fs.write and fs.list",
      'use fs\nprint file_exists("/")\nprint list_dir("/") != []', "true\ntrue", policy=ALL)
check("security.md §7.3", "`use net` is shorthand declaring net.http",
      'use net\ntry:\n    http_get("http://127.0.0.1:9/")\ncatch e:\n    print e.kind', "Error", policy=ALL)

# ======================================================================
# §7.3  what can be declared
# ======================================================================
check("security.md §7.3", "an unknown bare name is not a capability", 'use process',
      expect_ok=False, expect_error_substr="ModuleError: unknown module 'process'")
check("security.md §7.3", "an unknown dotted capability name is rejected at check time",
      'use fs.chmod', expect_ok=False, expect_error_substr="ModuleError: unknown capability 'fs.chmod'")
for reserved in ("net.connect", "env.read", "process.spawn"):
    check("security.md §7.3", f"'{reserved}' is reserved and cannot be declared (unsupported operations fail closed)",
          f'use {reserved}', expect_ok=False,
          expect_error_substr=f"capability '{reserved}' at line 1 is reserved but not implemented")
check("security.md §7.3", "a bad declaration is an error even in code that never runs",
      'fn never():\n    return 1\nuse fs.nope', expect_ok=False, expect_error_substr="unknown capability")

# declarations are static and top-level only
check("security.md §7.3", "a declaration inside a function body is rejected",
      'fn f():\n    use fs.read\n    return 1', expect_ok=False,
      expect_error_substr="'use fs.read' at line 2 must be at the top level")
check("security.md §7.3", "a declaration inside an untaken branch is rejected, not silently ignored",
      'if false:\n    use fs.read\nprint 1', expect_ok=False, expect_error_substr="must be at the top level")
check("security.md §7.3", "a declaration inside a loop body is rejected",
      'for i in [1]:\n    use fs.read', expect_ok=False, expect_error_substr="must be at the top level")
check("security.md §7.3", "a declaration inside a test block is rejected",
      'test "t":\n    use fs.read\n    assert(true)', expect_ok=False, expect_error_substr="must be at the top level")
check("security.md §7.3", "declaration order does not matter: a use line after the first call still declares",
      f'try:\n    read_file("/nonexistent-for-lumen-test")\ncatch e:\n    print e.kind\nuse fs.read',
      "Error", policy=Policy({"fs.read": None}))

# ======================================================================
# §7.4  structured denial errors
# ======================================================================
check("security.md §7.4", "a denial is catchable and carries kind/capability/operation/resource/reason",
      'use fs.read\ntry:\n    read_file("/some/path")\ncatch e:\n    print e.kind\n    print e.capability\n'
      '    print e.operation\n    print e.resource\n    print e.reason',
      "PermissionError\nfs.read\nread_file\n/some/path\nnot-granted")
check("security.md §7.4", "reason is 'not-declared' when the module never declared the capability",
      'try:\n    read_file("/p")\ncatch e:\n    print e.reason', "not-declared")
check("security.md §7.4", "reason is 'out-of-scope' for a path outside the granted directories",
      'use fs.read\ntry:\n    read_file("/etc/hostname")\ncatch e:\n    print e.reason',
      "out-of-scope", policy=Policy({"fs.read": [tempfile.gettempdir() + "/_lumen_no_such_scope"]}))
check("security.md §7.4", "every error carries a kind: explicit fail() and runtime failures are plain 'Error'",
      'try:\n    fail("boom")\ncatch e:\n    print e.kind\ntry:\n    print [1][5]\ncatch e:\n    print e.kind',
      "Error\nError")
check("security.md §7.4", "a type error's kind is TypeError",
      'try:\n    print len(5)\ncatch e:\n    print e.kind', "TypeError")
check("security.md §7.4", "an uncaught denial exits with the PermissionError text",
      'use fs.read\nprint read_file("/x")', expect_ok=False, expect_error_substr="PermissionError:")

# A denial can be caught, but catching it cannot produce the data.
d, p = sandbox()
try:
    with open(p, "w") as f:
        f.write("TOP-SECRET-VALUE")
    out, err = run_source(
        f'use fs.read\nlet leaked = "nothing"\ntry:\n    leaked = read_file("{p}")\ncatch e:\n    print "denied: " + e.reason\n'
        f'print leaked')
    ok = (err is None and "TOP-SECRET-VALUE" not in out and out.strip() == "denied: not-granted\nnothing")
    if ok: harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[security.md §7.4] catching a denial must not yield the data: out={out!r} err={err!r}")
finally:
    shutil.rmtree(d)

# A denied write must not change the file system.
d, p = sandbox()
try:
    target = os.path.join(d, "should-not-exist.txt")
    run_source(f'use fs.write\ntry:\n    write_file("{target}", "x")\ncatch e:\n    print e.reason')
    run_source(f'try:\n    write_file("{target}", "x")\ncatch e:\n    print e.reason', policy=ALL)
    ok = not os.path.exists(target)
    if ok: harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append("[security.md §7.4] a denied write_file must not create the file")
finally:
    shutil.rmtree(d)

# ======================================================================
# §7.4  argument validation (a gated builtin never acts on an argument it does not understand)
# ======================================================================
check("security.md §7.4", "read_file(0) is a TypeError, not a read of file descriptor 0 (stdin)",
      'use fs.read\nread_file(0)', expect_ok=False, policy=ALL,
      expect_error_substr="TypeError: read_file() expects a Str path as argument 1, got Int")
check("security.md §7.4", "write_file with a non-Str body is a TypeError, not a host crash",
      'use fs.write\nwrite_file("/tmp/_lumen_never", 5)', expect_ok=False, policy=ALL,
      expect_error_substr="TypeError: write_file() expects a Str as argument 2, got Int")
check("security.md §7.4", "a path containing a NUL character is rejected",
      'use fs.read\nread_file("a" + chr(0) + "b")', expect_ok=False, policy=ALL,
      expect_error_substr="must not contain a NUL character")
check("security.md §7.4", "file contents may contain NUL (only the path is restricted)",
      f'use fs.write\nuse fs.read\nlet p = "{tempfile.gettempdir()}/_lumen_nul_ok"\nwrite_file(p, "a" + chr(0) + "b")\n'
      f'print len(read_file(p))', "3", policy=ALL)
check("security.md §7.4", "wrong arity is a TypeError",
      'use fs.read\nread_file()', expect_ok=False, policy=ALL, expect_error_substr="read_file() expects 1 argument, got 0")
try:
    os.unlink(os.path.join(tempfile.gettempdir(), "_lumen_nul_ok"))
except OSError:
    pass

# ======================================================================
# §7.4  net: http and https only
# ======================================================================
d, p = sandbox()
try:
    with open(p, "w") as f:
        f.write("LOCAL-FILE-CONTENT")
    for url in (f"file://{p}", f"FILE://{p}", "ftp://127.0.0.1/x", "data:text/plain,hello", "gopher://127.0.0.1/"):
        out, err = run_source(f'use net.http\ntry:\n    print http_get("{url}")\ncatch e:\n    print e.message', policy=NET_ALL)
        ok = err is None and "LOCAL-FILE-CONTENT" not in out and "hello" not in out and "only http:// and https://" in out
        if ok: harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[security.md §7.4] http_get({url.split(':')[0]}:...) must be refused: out={out!r} err={err!r}")
finally:
    shutil.rmtree(d)

# ======================================================================
# §7.5  imported modules cannot hold authority their importers lack
# ======================================================================
def module_case(name, files, entry, policy, expect_substr=None, expect_ok=None, expect_stdout=None):
    d = tempfile.mkdtemp()
    try:
        for fname, content in files.items():
            with open(os.path.join(d, fname), "w") as f:
                f.write(content.replace("{d}", d))
        out, err = run_source(open(os.path.join(d, entry)).read().replace("{d}", d), base_dir=d, policy=policy)
        ok = True
        if expect_ok is True and err is not None: ok = False
        if expect_ok is False and err is None: ok = False
        if expect_substr is not None and (err is None or expect_substr not in err): ok = False
        if expect_stdout is not None and out.strip() != expect_stdout: ok = False
        if ok: harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[security.md §7.5] {name}: out={out!r} err={err!r}")
    finally:
        shutil.rmtree(d)

module_case("a library cannot use a capability its importer did not declare",
            {"main.lum": 'use "lib.lum" as l\nprint l.go()',
             "lib.lum": 'use fs.read\nfn go():\n    return read_file("/etc/hostname")'},
            "main.lum", ALL, expect_ok=False,
            expect_substr="declares it, but this program, which imports it (directly or indirectly), does not")
module_case("importing a module that declares fs does not give the importer fs",
            {"main.lum": 'use "lib.lum" as l\nprint read_file("/etc/hostname")', "lib.lum": 'use fs\n'},
            "main.lum", ALL, expect_ok=False, expect_substr="which this program has not declared")
module_case("importer and library both declare and the host grants: the library works",
            {"main.lum": 'use fs.read\nuse "lib.lum" as l\nprint l.go()',
             "lib.lum": 'use fs.read\nfn go():\n    return read_file("{d}/data.txt")',
             "data.txt": "payload"},
            "main.lum", Policy({"fs.read": None}), expect_ok=True, expect_stdout="payload")
module_case("the library gets only the INTERSECTION: importer declares read, library declares read+write",
            {"main.lum": 'use fs.read\nuse "lib.lum" as l\nprint l.go()',
             "lib.lum": 'use fs\nfn go():\n    write_file("{d}/out.txt", "x")\n    return 1'},
            "main.lum", ALL, expect_ok=False, expect_substr="write_file() needs the 'fs.write' capability: 'lib.lum' declares it, but")
module_case("a denial inside a library's top level keeps its structure for the importer's catch",
            {"main.lum": 'use "lib.lum" as l\n',
             "lib.lum": 'use fs.read\nprint read_file("/etc/hostname")'},
            "main.lum", ALL, expect_ok=False, expect_substr="ModuleError: error loading 'lib.lum': PermissionError:")
module_case("the host's grant is shared by every module: one policy, not one per module",
            {"main.lum": 'use fs.read\nuse "lib.lum" as l\nprint l.go()',
             "lib.lum": 'use fs.read\nfn go():\n    return read_file("{d}/data.txt")',
             "data.txt": "x"},
            "main.lum", Policy(), expect_ok=False, expect_substr="which was not granted for this run")
module_case("a transitive chain is limited by every link: A declares, B does not, C does",
            {"main.lum": 'use fs.read\nuse "b.lum" as b\nprint b.go()',
             "b.lum": 'use "c.lum" as c\nfn go():\n    return c.go()',
             "c.lum": 'use fs.read\nfn go():\n    return read_file("/etc/hostname")'},
            "main.lum", ALL, expect_ok=False, expect_substr="'b.lum', which imports it (directly or indirectly), does not")

# ======================================================================
# §7.2  memory safety (unchanged by 3.0)
# ======================================================================
check("security.md §7.2", "no address-of or pointer syntax exists (parse-level check)",
      'let p = &x', expect_ok=False)
check("security.md §7.2", "list index bounds are always checked, never silently OOB",
      'try:\n    print [1,2][999]\ncatch e:\n    print "bounds-checked"', "bounds-checked")

# ---- every builtin has a capability decision (the check security.md §7.6 relies on) ----
# A builtin is either gated (in lumen.OPERATIONS, with a capability) or on this REVIEWED list of
# builtins that cannot reach outside the process. Adding a builtin without deciding which fails here,
# so an I/O primitive cannot slip in unmediated. `read_line` (stdin) and `now` (the clock) are the two
# ambient inputs, documented in security-model.md §5.4.
from lumen import BUILTIN_FUNCTION_NAMES, CAPABILITY_BUILTIN_NAMES, OPERATIONS, CAPABILITIES
REVIEWED_NON_GATED = {"assert", "assert_eq", "assert_ne", "chan", "chr", "fail", "keys", "len", "now", "ord",
                      "push", "range", "read_line", "receive", "send", "type_of", "values", "wait",
                      # 3.1 error handling: pure functions on Result/Option values, and `fail_with`, which
                      # only raises — none of them can reach outside the process
                      "ok_or", "unwrap", "unwrap_or", "is_ok", "is_err", "is_some", "is_none", "fail_with"}
unreviewed = BUILTIN_FUNCTION_NAMES - CAPABILITY_BUILTIN_NAMES - REVIEWED_NON_GATED
stale = REVIEWED_NON_GATED - BUILTIN_FUNCTION_NAMES
check_ok = not unreviewed and not stale
if check_ok:
    harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[security.md §7.6] every builtin must be capability-gated or on the reviewed non-gated list: "
                            f"unreviewed={sorted(unreviewed)} stale={sorted(stale)}")
bad_caps = {op: cap for op, (cap, _) in OPERATIONS.items() if cap not in CAPABILITIES}
if not bad_caps:
    harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[security.md §7.6] OPERATIONS names a capability that does not exist: {bad_caps}")

# ---- single choke point: a static tripwire over the interpreter's own source ----
# "Every protected operation is mediated" (security.md §7.4) is a claim about ALL of lumen.py, so it is
# checked structurally, not just by exercising the eight builtins: parse lumen.py and find every use of a
# primitive that can touch the file system, the network, the environment, other processes or dynamic code,
# along with the function it is in. Each must be on this reviewed list. A new `open()`, `os.rename`,
# `socket`, `eval`, ... anywhere else fails the test until someone reviews it and adds it here — with the
# reason. (This catches additions; it does not prove the reviewed list itself is right.)
import ast, lumen as _lumen_mod
_tree = ast.parse(open(_lumen_mod.__file__).read())
_hits = set()

def _dotted(n):
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr); n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id); return ".".join(reversed(parts))
    return None

class _Scan(ast.NodeVisitor):
    def __init__(self): self.stack = []
    def visit_ClassDef(self, n): self.stack.append(("c", n.name)); self.generic_visit(n); self.stack.pop()
    def visit_FunctionDef(self, n): self.stack.append(("f", n.name)); self.generic_visit(n); self.stack.pop()
    def where(self):
        cs = [x[1] for x in self.stack if x[0] == "c"]; fs = [x[1] for x in self.stack if x[0] == "f"]
        return (cs[-1] if cs else None, fs[0] if fs else None)
    def visit_Call(self, n):
        if isinstance(n.func, ast.Name) and n.func.id in ("open", "input", "eval", "exec", "__import__", "compile"):
            _hits.add((n.func.id,) + self.where())
        self.generic_visit(n)
    def visit_Attribute(self, n):
        d = _dotted(n)
        if d and ((d.startswith("os.") and not d.startswith("os.path")) or d.split(".")[0] in
                  ("subprocess", "socket", "ctypes", "importlib", "shutil", "pathlib", "tempfile", "urllib", "ssl", "http", "pty", "signal")):
            _hits.add((d,) + self.where())
        self.generic_visit(n)
    def visit_Import(self, n):
        for a in n.names: _hits.add(("import " + a.name,) + self.where())
    def visit_ImportFrom(self, n):
        _hits.add(("from " + (n.module or "") + " import",) + self.where())
_Scan().visit(_tree)

_OPEN_CHECKED = (None, "_open_checked")
PERMITTED = {
    # the resource-access layer
    ("os.open",) + _OPEN_CHECKED, ("os.fdopen",) + _OPEN_CHECKED, ("os.ftruncate",) + _OPEN_CHECKED,
    ("os.close",) + _OPEN_CHECKED, ("os.O_RDONLY",) + _OPEN_CHECKED, ("os.O_WRONLY",) + _OPEN_CHECKED,
    ("os.O_CREAT",) + _OPEN_CHECKED, ("os.O_APPEND",) + _OPEN_CHECKED, ("open",) + _OPEN_CHECKED,
    # descriptor-relative file access (5B): the pinned-scope walk and the operations built on it
    ("os.open", "Policy", "_pinned_fd"), ("os.O_NOFOLLOW", "Policy", "_pinned_fd"),
    ("os.open", "Policy", "_anchor"), ("os.close", "Policy", "_anchor"), ("os.sep", "Policy", "_anchor"),
    ("os.curdir", "Policy", "_anchor"), ("os.close", "Policy", "__del__"),
    ("os.open", None, "_beneath"), ("os.O_NOFOLLOW", None, "_beneath"), ("os.close", None, "_beneath"),
    ("os.fstat", None, "_fs_mode"), ("os.stat", None, "_fs_mode"), ("os.stat", None, "_is_symlink_at"),
    ("os.open", None, "_fs_listdir"), ("os.listdir", None, "_fs_listdir"), ("os.close", None, "_fs_listdir"),
    ("os.remove", None, "_fs_unlink"), ("os.unlink", None, "_fs_unlink"), ("os.strerror", None, "_fs_unlink"),
    ("os.strerror", None, "_open_checked"),
    # the AUDIT LOG sink (5C): a file the HOST opens, at start-up, and appends to; the program never receives a handle
    # and Policy refuses a write grant that could reach it (tests/conformance/test_audit.py)
    ("open", None, "declared_capabilities"),
    ("os.open", "AuditLog", "__init__"), ("os.O_WRONLY", "AuditLog", "__init__"), ("os.O_APPEND", "AuditLog", "__init__"),
    ("os.O_CREAT", "AuditLog", "__init__"), ("os.fstat", "AuditLog", "__init__"), ("os.close", "AuditLog", "__init__"),
    ("os.write", "AuditLog", "record"), ("os.close", "AuditLog", "close"),
    # module-level feature detection only (`os.open in os.supports_dir_fd`, flag constants) — no call is made
    ("os.open", None, None), ("os.stat", None, None), ("os.unlink", None, None), ("os.listdir", None, None),
    ("os.supports_dir_fd", None, None), ("os.supports_fd", None, None), ("os.O_RDONLY", None, None),
    ("import urllib.request", None, "_http_get"), ("urllib.request", None, "_http_get"),
    ("urllib.request", "_Scoped", "_http_get"), ("urllib.request.build_opener", None, "_http_get"),
    ("urllib.request.HTTPRedirectHandler", "_Scoped", "_http_get"),
    ("os.sep", None, "_is_within"),                        # a string constant
    # pure parsing / harmless modules
    ("from urllib.parse import", None, "_authorize_url"), ("from urllib.parse import", None, "_parse_host_port"),
    ("from urllib.parse import", None, "_redact_url"),
    # loading .lum SOURCE (only *.lum, relative; see _locate_module) — not gated by fs.read, see security-model.md 5.4
    ("open", "Interpreter", "_exec_UseModule"), ("open", "VM", "_use_module"),
    # the type checker reads an imported module's function SIGNATURES (same file set as the import above;
    # a failure is swallowed, so nothing about the file's contents is ever reported)
    ("open", "TypeChecker", "_load_module"),
    # the standard streams: print goes to stdout, read_line reads stdin — ambient by design
    ("input", "Interpreter", "_eval_Call"), ("input", "VM", "_exec_one"), ("input", None, "repl"),
    # host-side command-line code: runs with the HOST's authority, outside any Lumen program
    ("open", None, "_prepare_tests"), ("open", None, "check_file"), ("open", None, "new_project"),
    ("open", None, "parse_manifest"), ("open", None, "run_file"), ("open", None, "run_file_vm"),
    ("os.makedirs", None, "cmd_install"), ("os.makedirs", None, "new_project"), ("os.walk", None, "cmd_test"),
    ("import subprocess", None, "_cmd_self_test"), ("subprocess.run", None, "_cmd_self_test"),
}
HARMLESS_IMPORTS = {"import os", "import re", "import sys", "import queue", "import threading", "import time",
                    "import errno", "import stat", "import contextlib", "import json",
                    "from datetime import"}
unreviewed = sorted((h for h in _hits if h not in PERMITTED and h[0] not in HARMLESS_IMPORTS), key=str)
if not unreviewed:
    harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append("[security.md §7.4] new file/network/process/dynamic-code primitive in lumen.py outside the "
                            f"reviewed resource-access layer (review it, then add it to PERMITTED): {unreviewed}")
# …and the reviewed list is not stale (an entry nothing uses any more is a sign the code moved)
stale_permits = sorted((h for h in PERMITTED if h not in _hits), key=str)
if not stale_permits:
    harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[security.md §7.4] PERMITTED lists primitives that no longer appear (update the list): {stale_permits}")

print_final_summary()
