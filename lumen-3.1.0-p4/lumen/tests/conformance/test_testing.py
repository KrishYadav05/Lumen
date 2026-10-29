"""Conformance tests for docs/testing.md (Language Specification 1.1)."""
import sys, os, subprocess, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from harness import check, print_final_summary
import harness

LUMEN = os.path.join(os.path.dirname(__file__), "..", "..", "lumen.py")

# ---- T.1 test bodies don't execute inline ----
check("testing.md §T.1", "test body does not run under ordinary `run` (registers, doesn't execute)",
      'print "before"\ntest "should not run":\n    fail("must never happen")\nprint "after"', "before\nafter")
check("testing.md §T.1", "test body isolated like a function (Rule C): sees globals declared AFTER it too",
      'test "sees later global":\n    assert_eq(g, 1)\nlet g = 1\nprint "ok"', "ok")
check("testing.md §T.1", "two tests may share the same name (no uniqueness requirement)",
      'test "dup":\n    assert(true)\ntest "dup":\n    assert(true)\nprint "ok"', "ok")

# ---- T.2 assert / assert_eq / assert_ne ----
check("testing.md §T.2", "assert() truthy passes", 'assert(1)\nprint "ok"', "ok")
check("testing.md §T.2", "assert() falsy fails with exact message",
      'assert(0)', expect_ok=False, expect_error_substr="assertion failed")
check("testing.md §T.2", "assert_eq exact message format",
      'assert_eq(4, 5)', expect_ok=False, expect_error_substr="assertion failed: 4 != 5")
check("testing.md §T.2", "assert_ne exact message format",
      'assert_ne(5, 5)', expect_ok=False, expect_error_substr="assertion failed: 5 == 5")
check("testing.md §T.2", "assert_eq passing returns nothing / does not raise", 'assert_eq(5, 5)\nprint "ok"', "ok")
check("testing.md §T.2", "assertions are ordinary catchable runtime failures (errors.md §4.2.5)",
      'try:\n    assert(false)\ncatch e:\n    print "caught: " + e.message', "caught: assertion failed")
check("testing.md §T.2", "assert_eq uses display-conversion formatting for non-primitive values",
      'assert_eq([1,2], [1,3])', expect_ok=False, expect_error_substr="assertion failed: [1, 2] != [1, 3]")

# ---- T.3 CLI: discovery, filtering, exit codes, output shape ----
def cli_case(name, files, args, expect_rc, expect_substrs, unexpect_substrs=()):
    d = tempfile.mkdtemp()
    for fname, content in files.items():
        fpath = os.path.join(d, fname)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(content)
    r = subprocess.run([sys.executable, LUMEN, "test"] + args, capture_output=True, text=True, cwd=d)
    shutil.rmtree(d)
    ok = r.returncode == expect_rc and all(s in r.stdout for s in expect_substrs) and not any(s in r.stdout for s in unexpect_substrs)
    if ok:
        harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[testing.md §T.3] {name}: rc={r.returncode} (expected {expect_rc}) stdout={r.stdout!r}")

cli_case("discovers tests/ by default, all pass -> exit 0",
    files={"tests/t.lum": 'test "a":\n    assert(true)\ntest "b":\n    assert(true)'},
    args=[], expect_rc=0, expect_substrs=['test "a" ... ok', 'test "b" ... ok', "2 passed; 0 failed"])

cli_case("a failing test causes nonzero exit and prints the assertion message",
    files={"tests/t.lum": 'test "fails":\n    assert_eq(1, 2)'},
    args=[], expect_rc=1, expect_substrs=['test "fails" ... FAILED', "assertion failed: 1 != 2", "1 passed; 1 failed" if False else "0 passed; 1 failed"])

cli_case("--filter runs only matching tests",
    files={"tests/t.lum": 'test "alpha":\n    assert(true)\ntest "beta":\n    assert(true)'},
    args=["--filter", "alpha"], expect_rc=0,
    expect_substrs=['test "alpha" ... ok', "1 passed; 0 failed"],
    unexpect_substrs=['test "beta"'])

cli_case("explicit file path is honored over tests/ discovery",
    files={"tests/t.lum": 'test "wrong":\n    fail("should not run")', "other.lum": 'test "right":\n    assert(true)'},
    args=["other.lum"], expect_rc=0,
    expect_substrs=['test "right" ... ok'], unexpect_substrs=['"wrong"'])

cli_case("a test file that imports the module under test works (realistic usage)",
    files={"src/main.lum": 'fn add(a, b):\n    return a + b',
           "tests/t.lum": 'use "../src/main.lum" as m\ntest "add works":\n    assert_eq(m.add(2, 3), 5)'},
    args=[], expect_rc=0, expect_substrs=['test "add works" ... ok'])

cli_case("--vm runs the same tests through the VM backend with identical results",
    files={"tests/t.lum": 'test "a":\n    assert_eq(2 + 2, 4)'},
    args=["--vm"], expect_rc=0, expect_substrs=['test "a" ... ok', "1 passed; 0 failed"])

cli_case("no tests/ and no lumen.toml gives a clean usage message, not a crash",
    files={}, args=[], expect_rc=1, expect_substrs=["usage: lumen test"])

print_final_summary()
