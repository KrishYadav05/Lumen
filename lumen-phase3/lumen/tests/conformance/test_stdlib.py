"""Conformance tests for docs/standard-library.md."""
import os, tempfile, shutil, subprocess, sys
from harness import check, run_source, print_final_summary
import harness

LUMEN = os.path.join(os.path.dirname(__file__), "..", "..", "lumen.py")

# ---- §8.3 display conversion table (one case per row) ----
check("standard-library.md §8.3", "Int display", 'print 42', "42")
check("standard-library.md §8.3", "Float display", 'print 3.5', "3.5")
check("standard-library.md §8.3", "Str display (unquoted)", 'print "hello"', "hello")
check("standard-library.md §8.3", "Bool true display", 'print true', "true")
check("standard-library.md §8.3", "Bool false display", 'print false', "false")
check("standard-library.md §8.3", "List display with recursive element conversion",
      'print [1, "a", true]', '[1, a, true]')
check("standard-library.md §8.3", "Dict display, insertion order",
      'print {"b": 1, "a": 2}', '{b: 1, a: 2}')
check("standard-library.md §8.3", "struct display shows declared field order",
      'struct P:\n    x\n    y\nprint P { x: 1, y: 2 }', "P { x: 1, y: 2 }")
check("standard-library.md §8.3", "enum payload variant display",
      'enum E:\n    Pair(a, b)\nprint Pair(1, 2)', "Pair(1, 2)")
check("standard-library.md §8.3", "enum unit variant display (bare name)",
      'enum E:\n    A\nprint A', "A")
check("standard-library.md §8.3", "task handle display", 'fn f():\n    return 1\nlet t = spawn f()\nwait(t)\nprint t', "<task>")
check("standard-library.md §8.3", "channel display", 'print chan()', "<channel>")

# ---- §8.5 collection functions: correct usage ----
check("standard-library.md §8.5", "len() on Str counts characters", 'print len("hello")', "5")
check("standard-library.md §8.5", "len() on List counts elements", 'print len([1,2,3])', "3")
check("standard-library.md §8.5", "len() on Dict counts pairs", 'print len({"a":1,"b":2})', "2")
check("standard-library.md §8.5", "len() on empty collections is 0", 'print len([])', "0")
check("standard-library.md §8.5", "push() mutates in place and returns nothing-visible",
      'let a = [1]\npush(a, 2)\nprint a', "[1, 2]")
check("standard-library.md §8.5", "push() mutation is visible through another reference to the same List",
      'let a = [1]\nlet b = a\npush(b, 2)\nprint a', "[1, 2]")
check("standard-library.md §8.5", "keys() returns insertion-ordered List of keys",
      'print keys({"z": 1, "a": 2})', '[z, a]')
check("standard-library.md §8.5", "values() returns insertion-ordered List of values, parallel to keys()",
      'print values({"z": 1, "a": 2})', '[1, 2]')

# ---- §8.5 collection functions: wrong-type usage is a clean error, never a raw crash ----
check("standard-library.md §8.5", "len() on Int is a clean TypeError", 'print len(5)', expect_ok=False, expect_error_substr="len() expects")
check("standard-library.md §8.5", "len() on Bool is a clean TypeError", 'print len(true)', expect_ok=False, expect_error_substr="len() expects")
check("standard-library.md §8.5", "push() on a Str is a clean TypeError", 'push("hi", 1)', expect_ok=False, expect_error_substr="push() expects a List")
check("standard-library.md §8.5", "push() on a Dict is a clean TypeError", 'push({"a":1}, 2)', expect_ok=False, expect_error_substr="push() expects a List")
check("standard-library.md §8.5", "keys() on a List is a clean TypeError", 'print keys([1,2])', expect_ok=False, expect_error_substr="keys() expects a Dict")
check("standard-library.md §8.5", "values() on an Int is a clean TypeError", 'print values(5)', expect_ok=False, expect_error_substr="values() expects a Dict")
check("standard-library.md §8.5", "wrong-type builtin errors are catchable, not process-fatal",
      'try:\n    push("hi", 1)\ncatch e:\n    print "caught: " + e.message', "caught: TypeError: push() expects a List as its first argument, got str")

# ---- §8.6 fail() ----
check("standard-library.md §8.6", "fail() message passed through unmodified",
      'try:\n    fail("exact text")\ncatch e:\n    print e.message', "exact text")

# ---- §8.7 I/O signatures (capability-gated behavior re-verified from the stdlib-contract angle) ----
def io_roundtrip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "f.txt")
        out, err = run_source(f'use fs\nprint write_file("{p}", "content")')
        ok1 = err is None and out.strip() == "nothing"
        out2, err2 = run_source(f'use fs\nprint read_file("{p}")')
        ok2 = err2 is None and out2.strip() == "content"
        if ok1 and ok2:
            harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[standard-library.md §8.4/§8.7] write_file returns nothing, read_file round-trips: "
                                     f"write=({out!r},{err!r}) read=({out2!r},{err2!r})")
    finally:
        shutil.rmtree(d)
io_roundtrip()

# ---- §8.8 CLI (subprocess — these exercise the actual entry point, not just in-process pipeline) ----
def cli_check(spec_ref, name, args, expect_substr=None, expect_rc=0):
    r = subprocess.run([sys.executable, LUMEN] + args, capture_output=True, text=True)
    ok = r.returncode == expect_rc and (expect_substr is None or expect_substr in r.stdout)
    if ok: harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[{spec_ref}] {name}: rc={r.returncode} stdout={r.stdout!r}")

d = tempfile.mkdtemp()
with open(os.path.join(d, "ok.lum"), "w") as f: f.write('print "hi"')
with open(os.path.join(d, "bad.lum"), "w") as f: f.write('let x: Int = "s"')
cli_check("standard-library.md §8.8", "lumen run executes a valid file", ["run", os.path.join(d, "ok.lum")], "hi")
cli_check("standard-library.md §8.8", "lumen check reports success without executing",
          ["check", os.path.join(d, "ok.lum")], "No errors found")
cli_check("standard-library.md §8.8", "lumen check reports a static error", ["check", os.path.join(d, "bad.lum")],
          "TypeError", expect_rc=1)
cli_check("standard-library.md §8.8", "lumen run --vm executes the supported subset",
          ["run", "--vm", os.path.join(d, "ok.lum")], "hi")
shutil.rmtree(d)

d2 = tempfile.mkdtemp()
r = subprocess.run([sys.executable, LUMEN, "new", os.path.join(d2, "proj")], capture_output=True, text=True)
ok = (r.returncode == 0
      and os.path.isfile(os.path.join(d2, "proj", "src", "main.lum"))
      and os.path.isfile(os.path.join(d2, "proj", "lumen.toml"))
      and os.path.isfile(os.path.join(d2, "proj", "tests", "main_test.lum"))
      and os.path.isfile(os.path.join(d2, "proj", "README.md")))
if ok: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] lumen new scaffolds src/main.lum + tests/ + README.md + lumen.toml: rc={r.returncode}")

r_run = subprocess.run([sys.executable, LUMEN, "run"], capture_output=True, text=True, cwd=os.path.join(d2, "proj"))
ok_run = r_run.returncode == 0 and "Hello from" in r_run.stdout
if ok_run: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] `lumen run` with zero args works via lumen.toml's entry: rc={r_run.returncode} stdout={r_run.stdout!r}")

r2 = subprocess.run([sys.executable, LUMEN, "install"], capture_output=True, text=True, cwd=os.path.join(d2, "proj"))
ok2 = r2.returncode == 0 and "No dependencies" in r2.stdout
if ok2: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] lumen install with no deps reports cleanly: stdout={r2.stdout!r}")
shutil.rmtree(d2)

print_final_summary()
