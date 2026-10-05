"""Conformance tests for docs/modules.md. Uses temp dirs for multi-file scenarios."""
import os, tempfile, shutil
from harness import check, run_source, print_final_summary
import harness

def write(d, name, content):
    path = os.path.join(d, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)

def module_check(spec_ref, name, files, entry, expect_stdout=None, expect_error_substr=None, expect_ok=None):
    d = tempfile.mkdtemp()
    try:
        for fname, content in files.items():
            write(d, fname, content)
        src = files[entry]
        out, err = run_source(src, base_dir=d)
        ok = True
        detail = ""
        if expect_ok is True and err is not None:
            ok, detail = False, f"expected success, got error: {err}"
        elif expect_ok is False and err is None:
            ok, detail = False, "expected failure, got success"
        if ok and expect_stdout is not None and out.strip() != expect_stdout.strip():
            ok, detail = False, f"stdout mismatch: got {out!r}"
        if ok and expect_error_substr is not None:
            if err is None or expect_error_substr not in err:
                ok, detail = False, f"expected error containing {expect_error_substr!r}, got {err!r}"
        if ok:
            harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[{spec_ref}] {name}: {detail}")
    finally:
        shutil.rmtree(d)

# ---- §5.4 resolution order ----
module_check("modules.md §5.4", "import resolves relative to importing file",
    files={"main.lum": 'use "lib.lum" as l\nprint l.greet()', "lib.lum": 'fn greet():\n    return "hi"'},
    entry="main.lum", expect_stdout="hi")

module_check("modules.md §5.4", "missing module produces clean ModuleError",
    files={"main.lum": 'use "missing.lum" as m\n'},
    entry="main.lum", expect_ok=False, expect_error_substr="cannot find module")

module_check("modules.md §5.4", "lumen_packages fallback resolution works",
    files={"main.lum": 'use "pkg/mod.lum" as p\nprint p.f()', "lumen_packages/pkg/mod.lum": 'fn f():\n    return "from package"'},
    entry="main.lum", expect_stdout="from package")

# ---- §5.3 no visibility control: everything reachable ----
module_check("modules.md §5.3", "top-level let is reachable via module alias",
    files={"main.lum": 'use "consts.lum" as c\nprint c.PI', "consts.lum": 'let PI = 3\n'},
    entry="main.lum", expect_stdout="3")

module_check("modules.md §5.3", "fn-local let is NOT part of the module namespace",
    files={"main.lum": 'use "lib.lum" as l\nprint l.hidden',
           "lib.lum": 'fn f():\n    let hidden = 1\n    return hidden'},
    entry="main.lum", expect_ok=False, expect_error_substr="NameError")

# ---- §5.5 re-execution semantics: no caching, no capability propagation ----
module_check("modules.md §5.5", "two `use` of the same path are independent instances",
    files={"main.lum": ('use "counter.lum" as a\nuse "counter.lum" as b\n'
                          'a.bump()\nprint a.get()\nprint b.get()'),
           "counter.lum": 'let n = 0\nfn bump():\n    n = n + 1\nfn get():\n    return n'},
    entry="main.lum",
    # note: bump() reassigns a fn-local shadow of n (language.md §1.11 Rule C/B),
    # so this also cross-checks that scoping rule inside an imported module.
    expect_stdout="0\n0")

module_check("modules.md §5.5", "capability granted in importer does NOT propagate to imported module",
    files={"main.lum": 'use fs\nuse "reader.lum" as r\nprint r.read_it()',
           "reader.lum": 'fn read_it():\n    return read_file("/tmp/whatever")'},
    entry="main.lum", expect_ok=False, expect_error_substr="PermissionError")

module_check("modules.md §5.5", "capability granted INSIDE imported module does not leak to importer",
    files={"main.lum": 'use "granter.lum" as g\nprint read_file("/tmp/whatever")',
           "granter.lum": 'use fs\n'},
    entry="main.lum", expect_ok=False, expect_error_substr="PermissionError")

# ---- §5.6 cyclic dependency handling ----
module_check("modules.md §5.6", "direct two-module cycle detected cleanly",
    files={"main.lum": 'use "a.lum" as a\nprint "unreachable"',
           "a.lum": 'use "b.lum" as b\n',
           "b.lum": 'use "a.lum" as a\n'},
    entry="main.lum", expect_ok=False, expect_error_substr="circular import")

module_check("modules.md §5.6", "three-module cycle (a->b->c->a) detected cleanly",
    files={"main.lum": 'use "a.lum" as a\nprint "unreachable"',
           "a.lum": 'use "b.lum" as b\n',
           "b.lum": 'use "c.lum" as c\n',
           "c.lum": 'use "a.lum" as a\n'},
    entry="main.lum", expect_ok=False, expect_error_substr="circular import")

module_check("modules.md §5.6", "self-import is a one-module cycle, detected cleanly",
    files={"main.lum": 'use "self.lum" as s\nprint "unreachable"',
           "self.lum": 'use "self.lum" as s\n'},
    entry="main.lum", expect_ok=False, expect_error_substr="circular import")

module_check("modules.md §5.6", "shared dependency imported by two siblings is NOT a false-positive cycle",
    files={"main.lum": 'use "a.lum" as a\nuse "b.lum" as b\nprint a.f() + b.g()',
           "a.lum": 'use "shared.lum" as s\nfn f():\n    return s.val()',
           "b.lum": 'use "shared.lum" as s\nfn g():\n    return s.val()',
           "shared.lum": 'fn val():\n    return 1'},
    entry="main.lum", expect_stdout="2")

module_check("modules.md §5.6", "diamond import (both branches independently OK) succeeds",
    files={"main.lum": 'use "left.lum" as l\nprint l.result()',
           "left.lum": 'use "right.lum" as r\nfn result():\n    return r.value() + 1',
           "right.lum": 'fn value():\n    return 10'},
    entry="main.lum", expect_stdout="11")

# ---- nested module chains ----
module_check("modules.md §5.5", "three-deep import chain works",
    files={"main.lum": 'use "mid.lum" as m\nprint m.f()',
           "mid.lum": 'use "deep.lum" as d\nfn f():\n    return d.g() + 1',
           "deep.lum": 'fn g():\n    return 100'},
    entry="main.lum", expect_stdout="101")

print_final_summary()
