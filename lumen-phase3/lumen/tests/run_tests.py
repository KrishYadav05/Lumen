#!/usr/bin/env python3
"""Automated tests for the Lumen language implementation.
Run: python3 tests/run_tests.py
"""
import subprocess, sys, os, tempfile

LUMEN = os.path.join(os.path.dirname(__file__), "..", "lumen.py")
passed = failed = 0

def run_src(src):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    r = subprocess.run([sys.executable, LUMEN, "run", path], capture_output=True, text=True)
    os.unlink(path)
    return r.stdout.strip(), r.returncode

def case(name, src, expect_stdout=None, expect_fail=False):
    global passed, failed
    out, code = run_src(src)
    ok = True
    if expect_fail and code == 0: ok = False
    if not expect_fail and code != 0: ok = False
    if expect_stdout is not None and out != expect_stdout.strip(): ok = False
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name}\n  got code={code} stdout={out!r}")

# --- v0.1: core ---
case("arithmetic", 'print 2 + 3 * 4', "14")
case("if_else", 'let a = 10\nif a >= 18:\n    print "adult"\nelse:\n    print "minor"', "minor")
case("for_loop", 'for n in [1,2,3]:\n    print n', "1\n2\n3")
case("function", 'fn square(x):\n    return x * x\nprint square(5)', "25")
case("undefined_var_fails", 'print nope', expect_fail=True)

# --- v0.2: structs, try/catch, logic ---
case("struct", 'struct P:\n    x\n    y\nlet p = P { x: 1, y: 2 }\nprint p.x + p.y', "3")
case("struct_missing_field_fails", 'struct P:\n    x\n    y\nlet p = P { x: 1 }', expect_fail=True)
case("try_catch_div0", 'try:\n    let x = 1 / 0\ncatch err:\n    print "caught"', "caught")
case("logic_ops", 'if true and not false:\n    print "yes"', "yes")

# --- v0.3: capabilities ---
case("fs_denied_without_use", 'print read_file("/tmp/x")', expect_fail=True)
case("fs_allowed_with_use", 'use fs\nwrite_file("/tmp/_lumen_t.txt","hi")\nprint read_file("/tmp/_lumen_t.txt")', "hi")

# --- v0.5: collections ---
case("list_index", 'let a = [1,2,3]\nprint a[1]', "2")
case("dict_index", 'let d = {"k": 9}\nprint d["k"]', "9")
case("index_out_of_range_fails", 'print [1,2][9]', expect_fail=True)
case("push", 'let a = [1]\npush(a, 2)\nprint a', "[1, 2]")

# --- v0.6: static types ---
case("typed_ok", 'fn sq(x: Int) -> Int:\n    return x * x\nprint sq(4)', "16")
case("typed_let_mismatch_fails", 'let x: Int = "hi"', expect_fail=True)
case("typed_arg_mismatch_fails", 'fn sq(x: Int) -> Int:\n    return x * x\nprint sq("hi")', expect_fail=True)
case("typed_return_mismatch_fails", 'fn f() -> Int:\n    return "no"', expect_fail=True)

# --- v1.1: enums + pattern matching ---
case("enum_match_variant", 'enum Opt:\n    Some(v)\n    None\nmatch Some(9):\n    Some(v):\n        print v\n    None:\n        print "no"', "9")
case("enum_unit_variant", 'enum X:\n    A\n    B\nmatch A:\n    A:\n        print "a"\n    B:\n        print "b"', "a")
case("match_no_case_fails", 'enum X:\n    A\n    B\nmatch A:\n    B:\n        print "b"', expect_fail=True)

# --- v1.3: concurrency ---
case("spawn_wait", 'fn sq(n):\n    return n * n\nlet t = spawn sq(6)\nprint wait(t)', "36")
case("channel", 'fn p(ch):\n    send(ch, 42)\n    return 0\nlet ch = chan()\nlet t = spawn p(ch)\nprint receive(ch)\nwait(t)', "42")
case("spawn_undefined_fails", 'let t = spawn nope(1)', expect_fail=True)

# --- Phase 2 fix: deep recursion must not leak a raw Python traceback ---
def deep_recursion_case():
    global passed, failed
    r = subprocess.run([sys.executable, LUMEN, "run", "/dev/stdin"],
                        input='fn countdown(n):\n    if n <= 0:\n        return 0\n    return countdown(n - 1)\nprint countdown(5000)\n',
                        capture_output=True, text=True, timeout=15)
    ok = "Traceback" not in r.stderr and "stack overflow" in r.stdout
    if ok: passed += 1
    else:
        failed += 1
        print(f"FAIL: deep_recursion_no_python_leak\n  stdout={r.stdout!r} stderr_tail={r.stderr[-200:]!r}")
deep_recursion_case()
case("deep_recursion_catchable",
     'fn countdown(n):\n    if n <= 0:\n        return 0\n    return countdown(n - 1)\n'
     'try:\n    print countdown(5000)\ncatch e:\n    print "caught"', "caught")

# --- v1.4: generics ---
case("generic_identity", 'fn id<T>(x: T) -> T:\n    return x\nprint id(5)\nprint id("hi")', "5\nhi")
case("generic_mismatch_fails", 'fn first<T>(a: T, b: T) -> T:\n    return a\nprint first(1, "two")', expect_fail=True)

# --- v1.5: CLI (new/install) smoke test ---
def cli_smoke():
    global passed, failed
    import shutil
    d = tempfile.mkdtemp()
    proj = os.path.join(d, "proj")
    r1 = subprocess.run([sys.executable, LUMEN, "new", proj], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, LUMEN, "run"], capture_output=True, text=True, cwd=proj)
    ok = r1.returncode == 0 and r2.returncode == 0 and "Hello from" in r2.stdout
    shutil.rmtree(d)
    if ok: passed += 1
    else:
        failed += 1
        print(f"FAIL: cli_new_and_run\n  new rc={r1.returncode} run rc={r2.returncode} stdout={r2.stdout!r}")
cli_smoke()

# --- v1.6: circular module import must not crash ---
def cyclic_import_case():
    global passed, failed
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.lum"), "w") as f: f.write('use "b.lum" as b\n')
    with open(os.path.join(d, "b.lum"), "w") as f: f.write('use "a.lum" as a\n')
    with open(os.path.join(d, "main.lum"), "w") as f: f.write('use "a.lum" as a\nprint "unreachable"\n')
    r = subprocess.run([sys.executable, LUMEN, "run", os.path.join(d, "main.lum")],
                        capture_output=True, text=True, timeout=10)
    ok = r.returncode != 0 and "circular import" in r.stdout and "RecursionError" not in r.stderr
    if ok: passed += 1
    else:
        failed += 1
        print(f"FAIL: cyclic_import\n  rc={r.returncode} stdout={r.stdout!r} stderr_tail={r.stderr[-200:]!r}")
cyclic_import_case()

# --- v1.6: builtins reject wrong argument types cleanly (no raw crash) ---
case("push_wrong_type_fails", 'push("hi", 1)', expect_fail=True)
case("keys_wrong_type_fails", 'print keys([1,2,3])', expect_fail=True)
case("len_wrong_type_fails", 'print len(5)', expect_fail=True)
case("send_wrong_type_fails", 'send(5, 1)', expect_fail=True)

# --- v1.0: --version output is exact and consistent ---
def version_flag_case():
    global passed, failed
    r = subprocess.run([sys.executable, LUMEN, "--version"], capture_output=True, text=True)
    lines = r.stdout.strip().split("\n")
    ok = (r.returncode == 0 and len(lines) == 3
          and lines[0].startswith("Lumen ") and lines[0].count(".") == 2
          and lines[1] == "Language specification: 1.1"
          and lines[2] == "Runtime: interpreter")
    if ok: passed += 1
    else:
        failed += 1
        print(f"FAIL: version_flag\n  stdout={r.stdout!r}")
version_flag_case()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
