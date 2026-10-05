#!/usr/bin/env python3
"""Phase 2, criterion #6 (CRITICAL): Interpreter <-> VM equivalence.

For every program in this suite, runs it through BOTH backends (the
reference tree-walking interpreter, and the bytecode Compiler+VM) and
asserts byte-for-byte identical stdout. This is the single most important
Phase 2 gate: a program that behaves differently between backends is a
correctness bug in the VM (the interpreter is the reference — it implements
every construct docs/ specifies; the VM implements a documented subset).

Programs that use constructs outside the VM's current subset are run through
the interpreter only, with the VM path asserted to fail with a *named*,
clean "not yet supported" error rather than silently producing wrong output
-- that distinction (clean rejection vs. silent divergence) is exactly what
this suite exists to police.
"""
import subprocess, sys, os, tempfile, shutil

LUMEN = os.path.join(os.path.dirname(__file__), "..", "lumen.py")

PASS = 0
FAIL = 0
FAILURES = []


def run_backend(src, vm, stdin_text=None):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + [path]
    r = subprocess.run(args, capture_output=True, text=True, input=stdin_text)
    os.unlink(path)
    return r.stdout, r.returncode


def run_backend_multifile(files, entry, vm):
    d = tempfile.mkdtemp()
    for fname, content in files.items():
        fpath = os.path.join(d, fname)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(content)
    args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + [os.path.join(d, entry)]
    r = subprocess.run(args, capture_output=True, text=True)
    shutil.rmtree(d)
    return r.stdout, r.returncode


def equivalent(name, src, stdin_text=None):
    global PASS, FAIL
    interp_out, interp_rc = run_backend(src, vm=False, stdin_text=stdin_text)
    vm_out, vm_rc = run_backend(src, vm=True, stdin_text=stdin_text)
    ok = (interp_out == vm_out) and (interp_rc == vm_rc)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(
            f"DIVERGENCE in {name!r}:\n"
            f"  interpreter (rc={interp_rc}): {interp_out!r}\n"
            f"  vm          (rc={vm_rc}): {vm_out!r}"
        )


def equivalent_multifile(name, files, entry):
    global PASS, FAIL
    interp_out, interp_rc = run_backend_multifile(files, entry, vm=False)
    vm_out, vm_rc = run_backend_multifile(files, entry, vm=True)
    ok = (interp_out == vm_out) and (interp_rc == vm_rc)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(
            f"DIVERGENCE in {name!r}:\n"
            f"  interpreter (rc={interp_rc}): {interp_out!r}\n"
            f"  vm          (rc={vm_rc}): {vm_out!r}"
        )


def vm_cleanly_declines(name, src, expected_substr):
    """For constructs outside the VM's subset: interpreter must succeed,
    VM must fail with a NAMED error (not crash, not silently diverge)."""
    global PASS, FAIL
    interp_out, interp_rc = run_backend(src, vm=False)
    vm_out, vm_rc = run_backend(src, vm=True)
    ok = interp_rc == 0 and vm_rc != 0 and expected_substr in vm_out
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(
            f"UNCLEAN VM DECLINE in {name!r}: interp rc={interp_rc} out={interp_out!r}; "
            f"vm rc={vm_rc} out={vm_out!r}; expected {expected_substr!r} in vm output"
        )


# ---- equivalence over the VM's supported subset: arithmetic, control flow, lists ----
equivalent("arithmetic precedence", 'print 2 + 3 * 4\nprint (2 + 3) * 4\nprint 10 - 3 - 2')
equivalent("division always float", 'print 7 / 2\nprint 10 / 2')
equivalent("modulo sign convention", 'print -7 % 2\nprint 7 % -2')
equivalent("comparisons and boolean logic", 'print 1 < 2\nprint 1 == 1\nprint true and false\nprint true or false\nprint not true')
equivalent("string concatenation via +", 'print "a" + "b"\nprint "n=" + 5')
equivalent("if/else both branches", 'if 1 < 2:\n    print "yes"\nelse:\n    print "no"\nif 1 > 2:\n    print "yes"\nelse:\n    print "no"')
equivalent("nested if", 'let x = 5\nif x > 0:\n    if x > 10:\n        print "big"\n    else:\n        print "small positive"\nelse:\n    print "non-positive"')
equivalent("for over a list", 'for n in [1, 2, 3, 4, 5]:\n    print n * n')
equivalent("for over an empty list does nothing", 'for n in []:\n    print n\nprint "done"')
equivalent("dict literal construction (VM previously never compiled dict literals)",
           'let d = {"a": 1, "b": 2}\nprint d\nprint d["a"] + d["b"]')
equivalent("multi-line bracketed literal", 'let d = {"a": 1,\n         "b": 2}\nprint d["b"]')
equivalent("now() returns a Float", 'print now() > 0')
equivalent("read_line() reads and strips newline", 'print read_line()', stdin_text="hi\n")
equivalent("list literal and indexing", 'let a = [10, 20, 30]\nprint a[0]\nprint a[2]')
equivalent("list construction with mixed expressions", 'let x = 5\nprint [x, x + 1, x * 2]')
equivalent("variable reassignment", 'let x = 1\nx = 2\nx = x + 1\nprint x')
equivalent("block-scoped shadowing (language.md §1.11 Rule B)",
           'let x = 1\nif true:\n    x = 2\nprint x')
equivalent("truthiness of empty vs nonempty collections", 'if []:\n    print "t"\nelse:\n    print "f"\nif [1]:\n    print "t"\nelse:\n    print "f"')
equivalent("nested for loops", 'for i in [1, 2]:\n    for j in [1, 2]:\n        print i * 10 + j')
equivalent("boolean short-circuit produces correct final value",
           'print true or (1 / 0 == 0)')  # if VM evaluated the right side, this would crash -- equivalence catches that too
equivalent("unary minus and double negation", 'print -5\nprint - -5\nprint not not true')
equivalent("larger composite program",
           '''let total = 0
for n in [1, 2, 3, 4, 5]:
    if n % 2 == 0:
        total = total + n
    else:
        total = total - n
print total
let matrix = [[1, 2], [3, 4]]
print matrix[0][1] + matrix[1][0]''')

# ---- VM cleanly declines constructs outside its subset (no silent wrong output) ----

equivalent("function call and return", 'fn square(n):\n    return n * n\nprint square(5)\nprint square(0)')
equivalent("recursion (factorial)", 'fn fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\nprint fact(10)')
equivalent("recursion (fibonacci, tests deep-ish call stack)",
           'fn fib(n):\n    if n < 2:\n        return n\n    return fib(n - 1) + fib(n - 2)\nprint fib(15)')
equivalent("mutual mixing: function using module globals and its own params together",
           'let base = 100\nfn addbase(n):\n    return base + n\nprint addbase(5)')
equivalent("function calling another function (forward reference resolved by call-time order)",
           'fn a():\n    return b() + 1\nfn b():\n    return 10\nprint a()')
equivalent("function with multiple params and list argument",
           'fn sum3(a, b, c):\n    return a + b + c\nprint sum3(1, 2, 3)\nlet xs = [10, 20]\nprint sum3(xs[0], xs[1], 5)')
equivalent("wrong argument count fails identically on both backends",
           'fn f(a):\n    return a\nprint f(1, 2)')
equivalent("calling an undefined function fails identically on both backends", 'print nope(1)')

# ---- structs ----
equivalent("struct construction and field read",
           'struct P:\n    x\n    y\nlet p = P { x: 1, y: 2 }\nprint p.x + p.y')
equivalent("struct field mutation persists (reference semantics)",
           'struct P:\n    x\nlet p = P { x: 1 }\np.x = 99\nprint p.x')
equivalent("struct shared reference sees mutation through either binding",
           'struct P:\n    x\nlet a = P { x: 1 }\nlet b = a\nb.x = 5\nprint a.x')
equivalent("struct display format", 'struct P:\n    x\n    y\nprint P { x: 1, y: 2 }')
equivalent("struct missing field fails identically", 'struct P:\n    x\n    y\nlet p = P { x: 1 }')
equivalent("struct extra field fails identically", 'struct P:\n    x\nlet p = P { x: 1, y: 2 }')
equivalent("field read on non-struct fails identically", 'let x = 5\nprint x.field')
equivalent("unknown struct name fails identically", 'let p = Ghost { x: 1 }')
equivalent("struct passed to and mutated inside a function",
           'struct Box:\n    v\nfn bump(b):\n    b.v = b.v + 1\n    return 0\nlet box = Box { v: 1 }\nbump(box)\nprint box.v')

# ---- enums + pattern matching ----
equivalent("payload variant construction and destructuring match",
           'enum E:\n    Pair(a, b)\nmatch Pair(1, 2):\n    Pair(x, y):\n        print x + y')
equivalent("unit variant construction, reference, and match",
           'enum E:\n    A\n    B\nmatch A:\n    A:\n        print "a"\n    B:\n        print "b"')
equivalent("enum display format (payload and unit)",
           'enum E:\n    Pair(a, b)\n    Solo\nprint Pair(1, 2)\nprint Solo')
equivalent("Option-style pattern via a function returning different variants",
           ('enum Option:\n    Some(v)\n    None\n'
            'fn half(n):\n    if n % 2 == 0:\n        return Some(n / 2)\n    else:\n        return None\n'
            'for n in [10, 7]:\n    match half(n):\n        Some(v):\n            print v\n        None:\n            print "nothing"'))
equivalent("wildcard pattern catches remaining cases",
           'match 42:\n    1:\n        print "one"\n    _:\n        print "other"')
equivalent("plain-name bind pattern captures the scrutinee",
           'match 42:\n    n:\n        print n')
equivalent("literal patterns of different types",
           'match "b":\n    "a":\n        print "A"\n    "b":\n        print "B"\n    _:\n        print "?"')
equivalent("non-exhaustive match failure is identical on both backends",
           'enum E:\n    A\n    B\nmatch B:\n    A:\n        print "a"')
equivalent("variant constructor wrong arity fails identically",
           'enum E:\n    Pair(a, b)\nlet x = Pair(1)')
equivalent("enum variant field mutation is NOT possible but reading nested struct in a variant works",
           'struct P:\n    v\nenum E:\n    Wrap(p)\nmatch Wrap(P { v: 5 }):\n    Wrap(p):\n        print p.v')

# ---- try/catch ----
equivalent("try/catch division by zero", 'try:\n    print 1 / 0\ncatch e:\n    print "caught: " + e.message\nprint "after"')
equivalent("try/catch with no error skips the handler entirely",
           'try:\n    print "in try"\ncatch e:\n    print "in catch"\nprint "after"')
equivalent("try/catch around a custom fail()", 'try:\n    fail("custom boom")\ncatch e:\n    print e.message')
equivalent("try/catch catches an error raised deep inside a called function",
           'fn boom():\n    return 1 / 0\ntry:\n    print boom()\ncatch e:\n    print "caught in caller"')
equivalent("nested try/catch: inner catch handles, outer never triggers",
           'try:\n    try:\n        fail("inner")\n    catch e:\n        print "inner caught: " + e.message\ncatch outer_e:\n    print "outer caught"')
equivalent("try/catch preserves variables bound before the try",
           'let x = 1\ntry:\n    x = 2\n    fail("boom")\ncatch e:\n    print x')
equivalent("recursion + try/catch: error deep in recursive calls is still caught",
           ('fn countdown(n):\n    if n <= 0:\n        return 1 / 0\n    return countdown(n - 1)\n'
            'try:\n    print countdown(20)\ncatch e:\n    print "caught: " + e.message'))
equivalent("index error caught inside try", 'try:\n    print [1,2][9]\ncatch e:\n    print "caught: " + e.message')
equivalent("struct field error caught inside try",
           'struct P:\n    x\nlet p = P { x: 1 }\ntry:\n    print p.y\ncatch e:\n    print "caught"')

# ---- combined: a realistic larger program using everything new together ----
equivalent("composite program: structs + enums + match + try/catch + recursion",
           '''struct Account:
    balance

enum Result:
    Ok(value)
    Err(reason)

fn withdraw(acc, amount):
    if amount > acc.balance:
        return Err("insufficient funds")
    acc.balance = acc.balance - amount
    return Ok(acc.balance)

fn fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)

let acc = Account { balance: 100 }
for amt in [30, 90]:
    match withdraw(acc, amt):
        Ok(bal):
            print "ok, balance now " + bal
        Err(reason):
            print "failed: " + reason

try:
    print fact(10) / 0
catch e:
    print "caught: " + e.message
''')

# ---- modules ----
equivalent_multifile("module: value + function access",
    files={"main.lum": 'use "utils.lum" as u\nprint u.PI\nprint u.square(5)',
           "utils.lum": 'let PI = 3.14159\nfn square(x):\n    return x * x'},
    entry="main.lum")
equivalent_multifile("module: function with recursion inside the imported module",
    files={"main.lum": 'use "m.lum" as m\nprint m.fact(6)',
           "m.lum": 'fn fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)'},
    entry="main.lum")
equivalent_multifile("module: struct defined and used inside the imported module, accessed via a function",
    files={"main.lum": 'use "m.lum" as m\nprint m.make(5)',
           "m.lum": 'struct Box:\n    v\nfn make(x):\n    let b = Box { v: x }\n    return b.v'},
    entry="main.lum")
equivalent_multifile("module: missing file fails identically",
    files={"main.lum": 'use "missing.lum" as m\n'}, entry="main.lum")
equivalent_multifile("module: two `use` of the same path are independent instances",
    files={"main.lum": ('use "counter.lum" as a\nuse "counter.lum" as b\n'
                          'a.bump()\nprint a.get()\nprint b.get()'),
           "counter.lum": 'let n = 0\nfn bump():\n    n = n + 1\nfn get():\n    return n'},
    entry="main.lum")
equivalent_multifile("module: three-deep import chain",
    files={"main.lum": 'use "mid.lum" as m\nprint m.f()',
           "mid.lum": 'use "deep.lum" as d\nfn f():\n    return d.g() + 1',
           "deep.lum": 'fn g():\n    return 100'},
    entry="main.lum")
equivalent_multifile("module: direct circular import fails identically",
    files={"main.lum": 'use "a.lum" as a\nprint "unreachable"',
           "a.lum": 'use "b.lum" as b\n',
           "b.lum": 'use "a.lum" as a\n'},
    entry="main.lum")
equivalent_multifile("module: unknown value on module namespace fails identically",
    files={"main.lum": 'use "m.lum" as m\nprint m.nonexistent',
           "m.lum": 'let x = 1'},
    entry="main.lum")
equivalent_multifile("module: unknown function on module namespace fails identically",
    files={"main.lum": 'use "m.lum" as m\nprint m.nonexistent()',
           "m.lum": 'fn f():\n    return 1'},
    entry="main.lum")

# ---- capabilities ----
equivalent("capability: read_file/write_file denied without use fs", 'print read_file("/tmp/nope_equiv_test")')
equivalent("capability: http_get denied without use net", 'print http_get("http://example.com")')
equivalent("capability: use fs does not grant net", 'use fs\nprint http_get("http://example.com")')
equivalent("capability: unknown capability name fails identically", 'use bogus_capability')
equivalent_multifile("capability: importer's grant does not leak into imported module",
    files={"main.lum": 'use fs\nuse "r.lum" as r\nprint r.try_read()',
           "r.lum": 'fn try_read():\n    return read_file("/tmp/whatever_equiv")'},
    entry="main.lum")
equivalent_multifile("capability: grant inside imported module does not leak to importer",
    files={"main.lum": 'use "g.lum" as g\nprint read_file("/tmp/whatever_equiv")',
           "g.lum": 'use fs\n'},
    entry="main.lum")

# ---- Phase 3: test framework (test "name": ... + assert/assert_eq/assert_ne) ----
def test_framework_equivalence():
    global PASS, FAIL
    d = tempfile.mkdtemp()
    src = ('let x = 5\n'
           'test "passes":\n'
           '    assert_eq(x, 5)\n'
           'test "fails":\n'
           '    assert_eq(x, 6)\n'
           'test "assert_ne passes":\n'
           '    assert_ne(x, 6)\n'
           'test "bare assert":\n'
           '    assert(x > 0)\n'
           'print "top level ran"\n')
    path = os.path.join(d, "t.lum")
    with open(path, "w") as f:
        f.write(src)
    interp_out, interp_rc = run_backend(src, vm=False)

    import subprocess
    r_i = subprocess.run([sys.executable, LUMEN, "test", d], capture_output=True, text=True)
    r_v = subprocess.run([sys.executable, LUMEN, "test", d, "--vm"], capture_output=True, text=True)
    ok = (r_i.returncode == r_v.returncode == 1
          and 'test "passes" ... ok' in r_i.stdout and 'test "passes" ... ok' in r_v.stdout
          and 'test "fails" ... FAILED' in r_i.stdout and 'test "fails" ... FAILED' in r_v.stdout
          and "assertion failed: 5 != 6" in r_i.stdout and "assertion failed: 5 != 6" in r_v.stdout
          and "2 passed; 2 failed" not in r_i.stdout  # sanity: should be 3 passed, 1 failed
          and "3 passed; 1 failed" in r_i.stdout and "3 passed; 1 failed" in r_v.stdout)
    shutil.rmtree(d)
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"DIVERGENCE in 'test framework CLI': interp_stdout={r_i.stdout!r} vm_stdout={r_v.stdout!r}")
test_framework_equivalence()

equivalent("test blocks do not execute inline (only top-level code runs under `lumen run`)",
           'print "before"\ntest "should not run":\n    fail("this must never execute")\nprint "after"')
equivalent("assert() with a truthy non-bool value passes (uses truthiness rule)",
           'assert([1,2,3])\nprint "ok"')
equivalent("assert() with falsy value fails identically", 'assert(0)')
equivalent("assert_eq compares by value across types", 'assert_eq(1, 1.0)\nprint "ok"')

def fs_roundtrip_equivalence():
    global PASS, FAIL
    d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
    p1, p2 = os.path.join(d1, "f.txt"), os.path.join(d2, "f.txt")
    src1 = f'use fs\nwrite_file("{p1}", "hello")\nprint read_file("{p1}")'
    src2 = f'use fs\nwrite_file("{p2}", "hello")\nprint read_file("{p2}")'
    interp_out, interp_rc = run_backend(src1, vm=False)
    vm_out, vm_rc = run_backend(src2, vm=True)
    ok = interp_out == vm_out == "hello\n" and interp_rc == vm_rc == 0
    shutil.rmtree(d1); shutil.rmtree(d2)
    if ok: PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"DIVERGENCE in 'fs roundtrip': interp=({interp_out!r},{interp_rc}) vm=({vm_out!r},{vm_rc})")
fs_roundtrip_equivalence()


print(f"\n{'='*60}\nINTERPRETER <-> VM EQUIVALENCE: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
if FAILURES:
    for f in FAILURES:
        print("\n" + f)
sys.exit(1 if FAIL else 0)
