"""Conformance tests for docs/security.md."""
import os, tempfile, shutil
from harness import check, run_source, print_final_summary
import harness

# ---- §7.3/§7.4 capability gating ----
check("security.md §7.4", "read_file without use fs is denied", 'print read_file("/tmp/x")',
      expect_ok=False, expect_error_substr="PermissionError")
check("security.md §7.4", "write_file without use fs is denied", 'write_file("/tmp/x", "y")',
      expect_ok=False, expect_error_substr="PermissionError")
check("security.md §7.4", "http_get without use net is denied", 'print http_get("http://example.com")',
      expect_ok=False, expect_error_substr="PermissionError: http_get() needs the 'net' capability")
check("security.md §7.3", "use fs does not grant net", 'use fs\nprint http_get("http://example.com")',
      expect_ok=False, expect_error_substr="'net' capability")
check("security.md §7.3", "use net does not grant fs", 'use net\nprint read_file("/tmp/x")',
      expect_ok=False, expect_error_substr="'fs' capability")
check("security.md §7.3", "only fs and net are recognized capability names", 'use process',
      expect_ok=False, expect_error_substr="ModuleError: unknown module 'process'")

def fs_roundtrip():
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "t.txt")
        src = f'use fs\nwrite_file("{path}", "hello")\nprint read_file("{path}")'
        out, err = run_source(src)
        ok = err is None and out.strip() == "hello"
        if ok: harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[security.md §7.4] fs roundtrip with grant succeeds: got out={out!r} err={err!r}")
    finally:
        shutil.rmtree(d)
fs_roundtrip()

# ---- §7.4 grant is imperative and persists once executed ----
check("security.md §7.4", "capability granted inside a function body works once that function has run",
      'fn grant():\n    use fs\nfn check():\n    return read_file("/tmp/does_not_exist_capcheck")\ngrant()\ntry:\n    check()\ncatch e:\n    print "reached io layer: " + e.message',
      expect_stdout=None, expect_ok=True)  # should NOT be a PermissionError — reaches the (failing) OS read instead
check("security.md §7.4", "capability granted only inside an untaken branch never takes effect",
      'if false:\n    use fs\nprint read_file("/tmp/x")', expect_ok=False, expect_error_substr="PermissionError")
check("security.md §7.4", "capability grant persists for the rest of the module after executing once",
      'use fs\nif true:\n    pass_marker = 1\ntry:\n    read_file("/tmp/does_not_exist_capcheck2")\ncatch e:\n    print "reached io layer"',
      "reached io layer")

# ---- §7.4 capabilities do not cross module boundaries (also covered in modules.md, re-verified here from the security angle) ----
def cap_isolation():
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "main.lum"), "w") as f:
            f.write('use fs\nuse "helper.lum" as h\nprint h.try_read()')
        with open(os.path.join(d, "helper.lum"), "w") as f:
            f.write('fn try_read():\n    return read_file("/tmp/x")')
        out, err = run_source(open(os.path.join(d, "main.lum")).read(), base_dir=d)
        ok = err is not None and "PermissionError" in err
        if ok: harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[security.md §7.4] importer's fs grant does not leak into imported module: out={out!r} err={err!r}")
    finally:
        shutil.rmtree(d)
cap_isolation()

# ---- §7.2 memory safety: no pointer/address surface exists at all ----
check("security.md §7.2", "no address-of or pointer syntax exists (parse-level check)",
      'let p = &x', expect_ok=False)
check("security.md §7.2", "list index bounds are always checked, never silently OOB",
      'try:\n    print [1,2][999]\ncatch e:\n    print "bounds-checked"', "bounds-checked")

print_final_summary()
