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


def run_backend(src, vm, stdin_text=None, flags=()):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + list(flags) + [path]
    r = subprocess.run(args, capture_output=True, text=True, input=stdin_text)
    os.unlink(path)
    # Capability errors name the file ("which 'x.lum' has not declared"); each run's temp file
    # has a random name, so normalise it before comparing the two backends' output.
    return r.stdout.replace(os.path.basename(path), "<program>"), r.returncode


def run_backend_multifile(files, entry, vm, flags=()):
    d = tempfile.mkdtemp()
    for fname, content in files.items():
        fpath = os.path.join(d, fname)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, "w") as f:
            f.write(content)
    args = [sys.executable, LUMEN, "run"] + (["--vm"] if vm else []) + list(flags) + [os.path.join(d, entry)]
    r = subprocess.run(args, capture_output=True, text=True)
    shutil.rmtree(d)
    return r.stdout, r.returncode


def equivalent(name, src, stdin_text=None, flags=()):
    """`flags` are the host's permission flags (--allow-read=... etc.): the
    same flags go to both backends, since the policy is the host's, not the
    program's."""
    global PASS, FAIL
    interp_out, interp_rc = run_backend(src, vm=False, stdin_text=stdin_text, flags=flags)
    vm_out, vm_rc = run_backend(src, vm=True, stdin_text=stdin_text, flags=flags)
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


def equivalent_multifile(name, files, entry, flags=()):
    global PASS, FAIL
    interp_out, interp_rc = run_backend_multifile(files, entry, vm=False, flags=flags)
    vm_out, vm_rc = run_backend_multifile(files, entry, vm=True, flags=flags)
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
# an undefined function called inside a `try` body is a catchable NameError on BOTH backends (the VM compiler used to
# crash with a raw Python KeyError, because Name Resolution defers the error to run time inside try bodies)
equivalent("undefined_call_in_try_is_catchable_nameerror",
           'fn f(x):\n    print "arg"\n    return x\ntry:\n    print nosuch(f(1))\ncatch e:\n    print e.kind + "|" + e.message\nprint "after"\n', "")
equivalent("undefined_call_in_try_inside_function",
           'fn g():\n    try:\n        return "a" + str(1)\n    catch e:\n        return e.message\nprint g()\n', "")

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
# Phase 5 (3.0): `use` declares, the host's flags grant. Both backends call the one
# `capability_call`, and these cases keep them from ever drifting apart on it.
ALL_FLAGS = ("--allow-fs", "--allow-net")
equivalent("capability: read_file denied without a declaration or a grant", 'print read_file("/tmp/nope_equiv_test")')
equivalent("capability: http_get denied without a declaration or a grant", 'print http_get("http://example.com")')
equivalent("capability: use fs does not declare net", 'use fs\nprint http_get("http://example.com")', flags=ALL_FLAGS)
equivalent("capability: unknown capability name fails identically", 'use bogus_capability')
equivalent("capability: unknown dotted capability fails identically", 'use fs.chmod')
equivalent("capability: reserved capability fails identically", 'use process.spawn')
equivalent("capability: declaration inside a function fails identically", 'fn f():\n    use fs.read\n    return 1\nprint f()')
equivalent("capability: declared but NOT granted is denied", 'use fs.read\nprint read_file("/tmp/nope_equiv_test")')
equivalent("capability: granted but NOT declared is denied", 'print read_file("/tmp/nope_equiv_test")', flags=("--allow-read",))
for cap, call, flag in [("fs.read", 'read_file("/tmp/nope_equiv_test")', "--allow-read"),
                        ("fs.write", 'write_file("/tmp/nope_equiv_test", "x")', "--allow-write"),
                        ("fs.list", 'list_dir("/tmp")', "--allow-list"),
                        ("net.http", 'http_get("http://127.0.0.1:9/")', "--allow-net")]:
    equivalent(f"capability: {cap} declared but not granted, structured error fields",
               f'use {cap}\ntry:\n    {call}\ncatch e:\n    print e.kind\n    print e.capability\n    print e.operation\n    print e.reason')
    others = [f for f in ("--allow-read", "--allow-write", "--allow-list", "--allow-net") if f != flag]
    equivalent(f"capability: only {flag} granted does not grant {cap}'s neighbours", f'use fs\nuse net\nprint {call}', flags=tuple(others))
equivalent("capability: a caught denial exposes no data and the program carries on",
           'use fs.read\nlet got = "none"\ntry:\n    got = read_file("/etc/hostname")\ncatch e:\n    print e.reason\nprint got')
equivalent("capability: file:// is refused even with net granted everywhere",
           'use net.http\ntry:\n    print http_get("file:///etc/hostname")\ncatch e:\n    print e.message', flags=("--allow-net",))
equivalent("capability: read_file(0) is a TypeError (no descriptor access)", 'use fs.read\nread_file(0)', flags=("--allow-read",))
equivalent("capability: write_file with a non-Str body is a TypeError", 'use fs.write\nwrite_file("/tmp/nope_equiv", 5)', flags=("--allow-write",))
equivalent_multifile("capability: importer's declaration does not extend to a module that did not declare",
    files={"main.lum": 'use fs\nuse "r.lum" as r\nprint r.try_read()',
           "r.lum": 'fn try_read():\n    return read_file("/tmp/whatever_equiv")'},
    entry="main.lum", flags=ALL_FLAGS)
equivalent_multifile("capability: declaration inside an imported module does not extend to the importer",
    files={"main.lum": 'use "g.lum" as g\nprint read_file("/tmp/whatever_equiv")',
           "g.lum": 'use fs\n'},
    entry="main.lum", flags=ALL_FLAGS)
equivalent_multifile("capability: a library cannot hold authority its importer did not declare",
    files={"main.lum": 'use "lib.lum" as l\nprint l.go()',
           "lib.lum": 'use fs.read\nfn go():\n    return read_file("/etc/hostname")'},
    entry="main.lum", flags=ALL_FLAGS)
equivalent_multifile("capability: a transitive import chain is limited by every link",
    files={"main.lum": 'use fs.read\nuse "b.lum" as b\nprint b.go()',
           "b.lum": 'use "c.lum" as c\nfn go():\n    return c.go()',
           "c.lum": 'use fs.read\nfn go():\n    return read_file("/etc/hostname")'},
    entry="main.lum", flags=ALL_FLAGS)
equivalent_multifile("capability: importer and library both declare, host grants: the library works",
    files={"main.lum": 'use fs.read\nuse "lib.lum" as l\nprint l.go()',
           "lib.lum": 'use fs.read\nfn go():\n    return "library-ran"'},
    entry="main.lum", flags=ALL_FLAGS)
equivalent_multifile("capability: a spawned task holds exactly its spawner's authority",
    files={"main.lum": 'use fs.read\nfn job():\n    return read_file("/etc/hostname")\nlet t = spawn job()\ntry:\n    wait(t)\ncatch e:\n    print e.reason'},
    entry="main.lum")

def scoped_fs_equivalence():
    """Scoped grants (--allow-read=DIR): the same allow/deny decisions on both backends, including
    traversal and symlink escapes, checked against the real disk."""
    d = tempfile.mkdtemp()
    try:
        inside, outside = os.path.join(d, "inside"), os.path.join(d, "outside")
        os.makedirs(inside); os.makedirs(outside)
        with open(os.path.join(inside, "a.txt"), "w") as f: f.write("A")
        with open(os.path.join(outside, "b.txt"), "w") as f: f.write("B")
        flag = f"--allow-read={inside}"
        prog = lambda path: (f'use fs.read\ntry:\n    print read_file("{path}")\ncatch e:\n'
                             f'    print e.kind + ":" + e.reason')
        equivalent("scoped grant: a file inside is readable", prog(os.path.join(inside, "a.txt")), flags=(flag,))
        equivalent("scoped grant: a file outside is denied", prog(os.path.join(outside, "b.txt")), flags=(flag,))
        equivalent("scoped grant: `..` traversal is denied", prog(os.path.join(inside, "..", "outside", "b.txt")), flags=(flag,))
        equivalent("scoped grant: a sibling with the same text prefix is denied", prog(inside + "-evil/x"), flags=(flag,))
        equivalent("scoped grant: file_exists outside is denied, not false",
                   f'use fs.read\ntry:\n    print file_exists("{os.path.join(outside, "b.txt")}")\ncatch e:\n    print e.reason', flags=(flag,))
        if hasattr(os, "symlink"):
            os.symlink(os.path.join(outside, "b.txt"), os.path.join(inside, "escape.txt"))
            equivalent("scoped grant: a symlink escaping the scope is denied", prog(os.path.join(inside, "escape.txt")), flags=(flag,))
            equivalent("scoped grant: writing through an escaping symlink is denied",
                       f'use fs.write\ntry:\n    write_file("{os.path.join(inside, "escape.txt")}", "X")\ncatch e:\n    print e.reason',
                       flags=(f"--allow-write={inside}",))
            with open(os.path.join(outside, "b.txt")) as f:
                if f.read() != "B":
                    global FAIL
                    FAIL += 1
                    FAILURES.append("a write through an escaping symlink modified the outside file")
        equivalent("scoped grant: a denied write creates nothing",
                   f'use fs.write\ntry:\n    write_file("{os.path.join(outside, "new.txt")}", "x")\ncatch e:\n    print e.reason',
                   flags=(f"--allow-write={inside}",))
        if os.path.exists(os.path.join(outside, "new.txt")):
            FAIL += 1
            FAILURES.append("a denied write_file created a file")
    finally:
        shutil.rmtree(d)
scoped_fs_equivalence()

# ---- 2.3's new fs-gated builtins (file_exists/append_file/list_dir/delete_file) ----
equivalent("capability: file_exists denied without use fs", 'print file_exists("/tmp/nope_equiv_test")')
equivalent("capability: append_file denied without use fs", 'append_file("/tmp/nope_equiv_test", "x")')
equivalent("capability: list_dir denied without use fs", 'print list_dir("/tmp")')
equivalent("capability: delete_file denied without use fs", 'delete_file("/tmp/nope_equiv_test")')
equivalent("capability: is_dir denied without use fs", 'print is_dir("/tmp")')

def fs_new_builtins_equivalence():
    """A real, successful round trip through all four new builtins — not
    just the denial path above — since the Interpreter and VM each have
    their own thin `call_capability` wrapper (3.0: both now delegate to the
    one shared `capability_call`, so this is a guard on the wiring — the
    argument order, the capability context — not on duplicated logic), and a
    divergence would only show up by actually exercising the I/O, not by
    checking the error message shape alone."""
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "t.txt")
        src = (f'use fs\n'
               f'print file_exists("{path}")\n'
               f'append_file("{path}", "a")\n'
               f'append_file("{path}", "b")\n'
               f'print read_file("{path}")\n'
               f'print is_dir("{path}")\n'
               f'print is_dir("{d}")\n'
               f'print list_dir("{d}")\n'
               f'delete_file("{path}")\n'
               f'print file_exists("{path}")')
        equivalent("fs new builtins roundtrip (file_exists/append_file/list_dir/delete_file/is_dir)", src,
                   flags=("--allow-fs",))
    finally:
        shutil.rmtree(d)
fs_new_builtins_equivalence()

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
    flags = ("--allow-read", "--allow-write")
    interp_out, interp_rc = run_backend(src1, vm=False, flags=flags)
    vm_out, vm_rc = run_backend(src2, vm=True, flags=flags)
    ok = interp_out == vm_out == "hello\n" and interp_rc == vm_rc == 0
    shutil.rmtree(d1); shutil.rmtree(d2)
    if ok: PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"DIVERGENCE in 'fs roundtrip': interp=({interp_out!r},{interp_rc}) vm=({vm_out!r},{vm_rc})")
fs_roundtrip_equivalence()


def range_and_assignment_equivalence():
    """2.0: the native `range()` builtin, and the corrected assignment
    semantics (language.md §1.11.1 — bare reassignment mutates the nearest
    enclosing scope instead of always shadowing). Both backends must agree."""
    equivalent("range(n) produces [0..n-1]", 'for i in range(5):\n    print i')
    equivalent("range(n) with n <= 0 is empty", 'print range(0)\nprint range(-3)')
    equivalent("range(start, end) produces [start..end-1]", 'for i in range(3, 7):\n    print i')
    equivalent("range(start, end) with end <= start is empty", 'print range(5, 5)\nprint range(5, 2)')
    equivalent("range() on a non-Int is a clean TypeError", 'print range("x")')
    equivalent("range() with the wrong number of arguments is a clean TypeError", 'print range(1, 2, 3)')
    equivalent("a large counted loop via range() does not risk recursion depth",
               'let total = 0\nfor i in range(5000):\n    total = total + 1\nprint total')
    equivalent("bare reassignment in a for-loop body mutates the outer variable",
               'let total = 0\nfor n in [1, 2, 3, 4, 5]:\n    total = total + n\nprint total')
    equivalent("bare reassignment in an if-block mutates the outer variable",
               'let x = 1\nif true:\n    x = 2\nprint x')
    equivalent("`let` in a nested block still shadows (does not mutate outer)",
               'let x = 1\nif true:\n    let x = 2\nprint x')
    equivalent("bare reassignment in a try-block mutates the outer variable",
               'let y = 1\ntry:\n    y = 42\ncatch e:\n    print "err"\nprint y')
    equivalent("assigning to a name never declared with `let` is a clean NameError",
               'nope = 5')
    equivalent("reassignment inside a function mutates a module-level global (Rule C)",
               'let n = 0\nfn bump():\n    n = n + 1\nbump()\nbump()\nprint n')
range_and_assignment_equivalence()


def concurrency_equivalence():
    """VM concurrency support (SPAWN opcode + wait/chan/send/receive builtins)
    mirrored from docs/concurrency.md's conformance cases (tests/conformance/
    test_concurrency.py), which only exercise the interpreter. Every case here
    is deterministic regardless of thread interleaving: wait() blocks until
    the spawned thread finishes, and chan()'s queue.Queue blocks receive()
    until a value is sent, so stdout order never depends on scheduling."""
    equivalent("spawn + wait returns the function's return value",
               'fn sq(n):\n    return n * n\nlet t = spawn sq(6)\nprint wait(t)')
    equivalent("spawn args are evaluated on the calling thread before handoff",
               'let log = []\nfn tag(n):\n    push(log, n)\n    return n\n'
               'fn f(x):\n    return x\nlet t = spawn f(tag(1))\nwait(t)\nprint log')
    equivalent("wait() is idempotent (same result twice)",
               'fn sq(n):\n    return n * n\nlet t = spawn sq(7)\nprint wait(t)\nprint wait(t)')
    equivalent("wait() re-raises the spawned call's exact failure (division by zero)",
               'fn boom():\n    return 1 / 0\nlet t = spawn boom()\ntry:\n    wait(t)\ncatch e:\n    print e.message')
    equivalent("wait() re-raises a custom fail() message unmodified",
               'fn boom():\n    fail("custom failure")\nlet t = spawn boom()\ntry:\n    wait(t)\ncatch e:\n    print e.message')
    equivalent("wait() on a non-task value is a clean TypeError", 'print wait(42)')
    equivalent("spawn of an undefined function fails immediately (not deferred to wait)",
               'let t = spawn nope(1)')
    equivalent("an un-waited spawned task is not an error",
               'fn f():\n    return 1\nlet t = spawn f()\nprint "done"')
    equivalent("single send/receive round-trip",
               'let ch = chan()\nsend(ch, 42)\nprint receive(ch)')
    equivalent("channel preserves FIFO order",
               'let ch = chan()\nsend(ch, 1)\nsend(ch, 2)\nsend(ch, 3)\n'
               'print receive(ch)\nprint receive(ch)\nprint receive(ch)')
    equivalent("producer/consumer pattern across a spawned thread",
               'fn producer(ch):\n    send(ch, 1)\n    send(ch, 2)\n    return 0\n'
               'let ch = chan()\nlet t = spawn producer(ch)\n'
               'print receive(ch)\nprint receive(ch)\nwait(t)')
    equivalent("send() never blocks even with no receiver yet (queue is unbounded)",
               'let ch = chan()\nsend(ch, 1)\nsend(ch, 2)\nsend(ch, 3)\nprint "all sent"')
    equivalent("send() on a non-channel value is a clean TypeError", 'send(5, 1)')
    equivalent("receive() on a non-channel value is a clean TypeError", 'print receive(5)')
    equivalent("a channel is a reference type shared across holders",
               'fn drain(ch):\n    return receive(ch)\n'
               'let ch = chan()\nsend(ch, 99)\nprint drain(ch)')
    equivalent("spawned tasks mutating a shared List do not corrupt/crash the process",
               'fn adder(box):\n    push(box, 1)\n    return 0\n'
               'let box = []\nlet t1 = spawn adder(box)\nlet t2 = spawn adder(box)\n'
               'wait(t1)\nwait(t2)\nprint len(box)')
    equivalent("multiple concurrent tasks all complete and are individually retrievable",
               'fn sq(n):\n    return n * n\n'
               'let t1 = spawn sq(2)\nlet t2 = spawn sq(3)\nlet t3 = spawn sq(4)\n'
               'print wait(t1)\nprint wait(t2)\nprint wait(t3)')
    equivalent("a global written inside a spawned function is visible after wait() "
               "(VM.spawn shares scopes[0] by reference, matching the interpreter sharing self.globals)",
               'let counter = 0\nfn bump():\n    counter = counter + 1\n    return counter\n'
               'let t = spawn bump()\nwait(t)\nprint counter')
concurrency_equivalence()


def low_punctuation_equivalence():
    """2.1: the low-punctuation syntax (language.md §1.13) is pure parser
    sugar -- every new spelling desugars to the exact same AST/IR a 1.x/2.0
    program would have produced, so the VM needs no new opcode or compiler
    support at all. These checks exist precisely to verify that claim is
    actually true end-to-end, not just "the parser accepts it"."""
    equivalent("if/for/fn/struct/enum/try with no colon at all",
               'fn add a b\n    return a + b\n'
               'struct P\n    x\n    y\n'
               'let p = P { x: 1 y: 2 }\n'
               'let total = 0\n'
               'for n in [1 2 3]\n'
               '    total = total + n\n'
               'if total > 0\n'
               '    print add(p.x, p.y)\n'
               'try\n'
               '    let r = 10 / 0\n'
               'catch e\n'
               '    print "caught"')
    equivalent("list/call/dict/struct literals with optional, mixed, and trailing commas",
               'print [1, 2 3, 4,]\n'
               'fn f(a, b,)\n    return a + b\n'
               'print f(1, 2,)\n'
               'let d = {"a": 1 "b": 2,}\n'
               'print d["a"] + d["b"]')
    equivalent("'as'/'returns'/'is' alternative spellings match ':'/'->' exactly",
               'let x as Int = 5\n'
               'fn sq(n as Int) returns Int\n    return n * n\n'
               'let d = {"k" is sq(x)}\n'
               'print d["k"]')
    equivalent("paren-free calls, including application binding tighter than '+'",
               'fn add a b\n    return a + b\n'
               'print add 2 3\n'
               'print add 2 3 + 1\n'
               'print add 2 (3 + 1)\n'
               'print add 2 (add 1 2)')
    equivalent("paren-free enum variant fields and paren-free match patterns",
               'enum Opt\n    Some value\n    None\n'
               'fn half(n)\n    return Some (n / 2)\n'
               'match half(10)\n'
               '    Some v\n        print v\n'
               '    None\n        print "none"')
    equivalent("a bare multi-argument call as one of several list items is a clean SyntaxError",
               'fn add a b\n    return a + b\nprint [add 2 3 10]')
    equivalent("auto-generics: a fn with no <T> still works when T is not a concrete type",
               'fn identity x as T returns T\n    return x\nprint identity(42)\nprint identity("hi")')
    equivalent("print with multiple space-separated values, mixed types",
               'print "total:" 15\nprint "a" 1 true\nlet a = 2\nlet b = 3\nprint a + b')
low_punctuation_equivalence()


def chr_ord_equivalence():
    """Phase 3: chr()/ord() are implemented separately in the Interpreter's
    _eval_Call and the VM's CALL_BUILTIN opcode handler -- two independent
    code paths, so this is exactly the kind of claim that needs byte-for-byte
    verification rather than "it works on one backend so it's fine"."""
    equivalent("chr() of ordinary ASCII code points", 'print chr(65)\nprint chr(97)\nprint chr(48)')
    equivalent("ord() of ordinary ASCII characters", 'print ord("A")\nprint ord("a")\nprint ord("0")')
    equivalent("chr()/ord() round-trip", 'print ord(chr(200))\nprint chr(ord("z"))')
    equivalent("chr(34) builds a usable double-quote character",
               'let q = chr(34)\nprint q + "inside quotes" + q\nprint len(q)')
    equivalent("chr() TypeError on non-Int argument (Float)", 'print chr(65.0)')
    equivalent("chr() TypeError on non-Int argument (Bool)", 'print chr(true)')
    equivalent("chr() TypeError on out-of-range code point (negative)", 'print chr(-1)')
    equivalent("chr() TypeError on out-of-range code point (too large)", 'print chr(9999999)')
    equivalent("ord() TypeError on multi-character Str", 'print ord("ab")')
    equivalent("ord() TypeError on empty Str", 'print ord("")')
    equivalent("ord() TypeError on non-Str argument", 'print ord(65)')
chr_ord_equivalence()


def type_of_equivalence():
    """type_of() is implemented separately in the Interpreter (LumenModule
    check) and the VM (VMModule check) -- the one place its two
    implementations actually differ -- so this is exactly the claim that
    needs byte-for-byte cross-backend verification."""
    equivalent("type_of() on every primitive kind",
               'print type_of(1)\nprint type_of(1.0)\nprint type_of(true)\nprint type_of(false)\n'
               'print type_of("s")\nprint type_of([1])\nprint type_of({"a": 1})')
    equivalent("type_of() distinguishes Bool from Int (0==false, 1==true landmine)",
               'print type_of(0)\nprint type_of(1)\nprint type_of(false)\nprint type_of(true)')
    equivalent("type_of() on Nothing (function falling off its end without return)",
               'fn f():\n    let x = 1\nprint type_of(f())')
    equivalent("type_of() on a struct value returns its declared type name",
               'struct P:\n    x\nprint type_of(P { x: 1 })')
    equivalent("type_of() on an enum value returns the enum name, not the variant name",
               'enum Opt:\n    Some(v)\n    None\nprint type_of(Some(1))\nprint type_of(None)')
    equivalent("type_of() on a channel", 'print type_of(chan())')
    equivalent("type_of() on a task handle",
               'fn f():\n    return 1\nlet t = spawn f()\nprint type_of(t)\nwait(t)')
    equivalent_multifile("type_of() on a module import",
               files={"main.lum": 'use "m.lum" as m\nprint type_of(m)',
                      "m.lum": 'let PI = 3.14'},
               entry="main.lum")
type_of_equivalence()


def try_return_equivalence():
    """Regression test for a real VM-only bug found while building
    std/json.lum (Phase 3, Language Specification 2.2): `return` executed
    directly inside a `try` body jumps straight out of the function via the
    RETURN opcode, bypassing the TRY_POP that IRTryCatch's codegen only
    emits on the path where the try body falls through normally (see
    compile_stmt's IRTryCatch case). Before the fix, this left the TRY_PUSH
    entry on VM.try_stack forever -- a stale handler, still bound to a call
    frame that no longer exists -- which could then wrongly catch some
    LATER, completely unrelated exception and report its own (wrong, stale)
    catch message instead of the real one. The Interpreter has no equivalent
    bug (its try/catch is ordinary Python try/except, which unwinds
    correctly regardless of whether the try body returns), so this is
    exactly the kind of claim -- "the two backends behave identically" --
    that needs byte-for-byte cross-backend verification rather than
    "it works on one backend so it's fine". The fix: RETURN now prunes any
    try_stack entry whose recorded call depth is deeper than the call stack
    depth it just returned to, exactly as if that entry's TRY_POP had run.

    IMPORTANT, verified by actually disabling the fix and re-running this
    suite before writing it down (not assumed): the bug is *externally
    invisible* in a single top-level script, even one that calls a
    try-with-return function repeatedly (in a loop) or nests try-with-return
    inside try-with-return, because the still-live, correctly-nested
    try/catch that comes later in the SAME script is pushed onto the SAME
    try_stack *after* any stale entries and so sits on top of them --
    popping (LIFO) always hits the live one first regardless of how many
    stale entries are buried underneath. The failure only becomes externally
    visible when the stale entry is left on a *separate* VM instance that
    outlives the original call and is reused for a later, unrelated call
    whose own failure has no protecting try/catch anywhere inside that
    reused instance -- concretely, a module's sub_vm (`use "..." as alias`,
    then call two different functions, or the same function twice with
    different arguments, across two separate top-level statements). A
    spawned task does NOT carry this risk: `spawn` allocates a brand-new VM
    instance *per call* (see VM.spawn) rather than reusing one across calls,
    so there is never a second call to leave a stale entry stale *for*.
    """
    equivalent("a function whose ENTIRE try body is `return EXPR` works on its own "
               "(baseline correctness, not yet exercising the stale-handler risk)",
               'fn lookup(m, k):\n    try:\n        return m[k]\n    catch e:\n        fail("missing")\n'
               'print lookup({"a": 1}, "a")')
    equivalent_multifile(
        "REGRESSION PIN: a module function whose try body returns directly, called once and "
        "succeeding, must not leave a stale handler that swallows a LATER, unrelated failure "
        "from a different module function that has no try/catch of its own (the exact bug: "
        "before the fix, the VM printed the first function's own catch message instead)",
        files={
            "main.lum": (
                'use "mod.lum" as mod\n'
                'print mod.lookup("a")\n'
                'try:\n'
                '    mod.boom()\n'
                'catch e:\n'
                '    print "caught: " + e.message\n'
            ),
            "mod.lum": (
                'let M = {"a": 1, "b": 2}\n'
                'fn lookup(k):\n'
                '    try:\n'
                '        return M[k]\n'
                '    catch e:\n'
                '        fail("mod: no such key")\n'
                'fn boom():\n'
                '    fail("totally unrelated failure, no internal try/catch")\n'
            ),
        },
        entry="main.lum",
    )
    equivalent_multifile(
        "REGRESSION PIN (same exported function, two calls -- the literal shape "
        "std/json.lum's json_decode() hit): first call succeeds via an internal "
        "try-with-return helper, second call fails via a DIFFERENT internal path with no "
        "try/catch of its own, and must report its OWN message at the call site",
        files={
            "main.lum": (
                'use "mod.lum" as mod\n'
                'print mod.get("a")\n'
                'try:\n'
                '    mod.get("z")\n'
                'catch e:\n'
                '    print "caught: " + e.message\n'
            ),
            "mod.lum": (
                'let M = {"a": 1}\n'
                'fn get(k):\n'
                '    if k == "a":\n'
                '        try:\n'
                '            return M[k]\n'
                '        catch e:\n'
                '            fail("mod: lookup failed")\n'
                '    fail("mod: key not found, no internal try")\n'
            ),
        },
        entry="main.lum",
    )
    equivalent_multifile(
        "REGRESSION PIN: nested try-with-return (inner try's own return ALSO bypasses its "
        "TRY_POP) inside a reused module, followed by an unrelated no-internal-try failure",
        files={
            "main.lum": (
                'use "mod.lum" as mod\n'
                'print mod.f(5)\n'
                'try:\n'
                '    mod.boom()\n'
                'catch e:\n'
                '    print "caught: " + e.message\n'
            ),
            "mod.lum": (
                'fn f(x):\n'
                '    try:\n'
                '        try:\n'
                '            return x * 2\n'
                '        catch e2:\n'
                '            fail("inner")\n'
                '    catch e:\n'
                '        fail("outer")\n'
                'fn boom():\n'
                '    fail("totally unrelated, no internal try")\n'
            ),
        },
        entry="main.lum",
    )
    equivalent("try-with-return inside a spawned task, waited on, then an unrelated later "
               "failure from a SECOND spawned task still reports its own message (spawn is "
               "immune to the stale-handler bug -- each spawn gets its own fresh VM instance, "
               "never reused for a second call -- this just confirms that directly)",
               'let M = {"a": 1}\n'
               'fn lookup(k):\n    try:\n        return M[k]\n    catch e:\n        fail("spawn: no such key")\n'
               'fn boom():\n    fail("unrelated failure, no internal try")\n'
               'let t1 = spawn lookup("a")\n'
               'print wait(t1)\n'
               'let t2 = spawn boom()\n'
               'try:\n    wait(t2)\n'
               'catch e:\n    print "caught: " + e.message')
try_return_equivalence()


def while_equivalence():
    """Phase 3: `while` is implemented separately in the Interpreter's
    _exec_While (a direct Python `while` over `self.eval`/`self.exec_block`)
    and the Compiler+VM path (JUMP_IF_FALSE/JUMP/PUSH_SCOPE/POP_SCOPE bytecode,
    reusing existing opcodes -- no new VM opcode was added for `while`), so
    this is exactly the kind of claim that needs byte-for-byte cross-backend
    verification rather than "it works on one backend so it's fine"."""
    equivalent("while with a false-from-the-start condition runs zero iterations",
               'let i = 10\nwhile i < 5:\n    print "no"\nprint "done"')
    equivalent("basic counting while loop", 'let i = 0\nwhile i < 5:\n    print i\n    i = i + 1')
    equivalent("while loop with an accumulator (outer-variable reassignment each pass)",
               'let total = 0\nlet i = 1\nwhile i <= 10:\n    total = total + i\n    i = i + 1\nprint total')
    equivalent("while body gets a fresh per-iteration scope (a `let` doesn't collide across passes)",
               'let i = 0\nwhile i < 3:\n    let y = i * 10\n    print y\n    i = i + 1')
    equivalent("while loop with a nested if/else",
               'let i = 0\nwhile i < 6:\n    if i % 2 == 0:\n        print "even"\n    else:\n        print "odd"\n    i = i + 1')
    equivalent("while loop nested inside a for loop",
               'for n in [1, 2, 3]:\n    let j = 0\n    while j < n:\n        print n * 10 + j\n        j = j + 1')
    equivalent("for loop nested inside a while loop",
               'let k = 0\nwhile k < 2:\n    for x in [100, 200]:\n        print x + k\n    k = k + 1')
    equivalent("while loop nested inside a while loop",
               'let i = 0\nwhile i < 2:\n    let j = 0\n    while j < 2:\n        print i * 10 + j\n        j = j + 1\n    i = i + 1')
    equivalent("optional colon applies to while (§1.13.1)",
               'let i = 0\nwhile i < 3\n    print i\n    i = i + 1')
    equivalent("while condition short-circuits `and` identically on both backends",
               'let log = []\nfn tag():\n    push(log, 1)\n    return true\nlet i = 0\nwhile false and tag():\n    i = i + 1\nprint log')
    equivalent("return inside a while loop unwinds the function on both backends",
               'fn f():\n    let i = 0\n    while i < 100:\n        if i == 3:\n            return i\n        i = i + 1\n    return -1\nprint f()')
    equivalent("while false: with a would-raise body never evaluates it, on both backends",
               'while false:\n    print 1 / 0\nprint "survived"')
    equivalent("while loop interacting with try/catch",
               'let i = 0\nlet errs = 0\nwhile i < 3:\n    try:\n        if i == 1:\n            fail("boom")\n        print i\n    catch e:\n        errs = errs + 1\n    i = i + 1\nprint errs')
while_equivalence()


print(f"\n{'='*60}\nINTERPRETER <-> VM EQUIVALENCE: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
if FAILURES:
    for f in FAILURES:
        print("\n" + f)
sys.exit(1 if FAIL else 0)
