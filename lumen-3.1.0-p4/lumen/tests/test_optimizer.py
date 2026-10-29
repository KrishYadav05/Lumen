#!/usr/bin/env python3
"""Phase 4: optimizer correctness gate.

IROptimizer (-O1/-O2) must never change observable behavior — only how fast
the VM gets there. For every program in this suite, runs it through the VM
at -O0, -O1, and -O2 and asserts byte-for-byte identical stdout and exit
code across all three, exactly the same discipline test_equivalence.py
applies to interpreter<->VM agreement. A handful of cases also cross-check
against the plain interpreter (which never optimizes, by construction —
it walks the AST, not IR), as a second, independent reference.

Deliberately includes several "trap" cases designed to catch an unsafe
fold if one were ever introduced: an expression that would raise (division
by zero, a type error) inside a branch that may or may not run, and a
boolean short-circuit with a side-effecting right operand — these exist to
prove the optimizer does NOT change error timing or skip/run side effects
differently than an unoptimized run would.
"""
import subprocess, sys, os, tempfile

LUMEN = os.path.join(os.path.dirname(__file__), "..", "lumen.py")

PASS = 0
FAIL = 0
FAILURES = []


def run_vm(src, opt_flag):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    args = [sys.executable, LUMEN, "run", "--vm", opt_flag, path]
    r = subprocess.run(args, capture_output=True, text=True)
    os.unlink(path)
    return r.stdout.replace(os.path.basename(path), "<file>"), r.returncode   # a trace names the file


def run_interp(src):
    with tempfile.NamedTemporaryFile("w", suffix=".lum", delete=False) as f:
        f.write(src)
        path = f.name
    args = [sys.executable, LUMEN, "run", path]
    r = subprocess.run(args, capture_output=True, text=True)
    os.unlink(path)
    return r.stdout.replace(os.path.basename(path), "<file>"), r.returncode


def opt_stable(name, src, also_check_interpreter=True):
    """Asserts -O0 == -O1 == -O2 (and optionally == the interpreter)."""
    global PASS, FAIL
    out0, rc0 = run_vm(src, "-O0")
    out1, rc1 = run_vm(src, "-O1")
    out2, rc2 = run_vm(src, "-O2")
    ok = (out0 == out1 == out2) and (rc0 == rc1 == rc2)
    detail = (f"DIVERGENCE in {name!r} across opt levels:\n"
              f"  -O0 (rc={rc0}): {out0!r}\n  -O1 (rc={rc1}): {out1!r}\n  -O2 (rc={rc2}): {out2!r}")
    if ok and also_check_interpreter:
        outi, rci = run_interp(src)
        if not (outi == out0 and rci == rc0):
            ok = False
            detail = (f"DIVERGENCE in {name!r}: interpreter (rc={rci}): {outi!r} "
                      f"vs VM -O0 (rc={rc0}): {out0!r}")
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(detail)


# ---- constant folding: arithmetic, comparisons, string concat, `not` ----
opt_stable("arithmetic constant folding", 'print 2 + 3 * 4\nprint (10 - 4) / 2\nprint 7 % 3')
opt_stable("string concat constant folding", 'print "a" + "b" + "c"')
opt_stable("comparison constant folding", 'print 3 < 5\nprint 3 >= 5\nprint 3 == 3')
opt_stable("not over a constant", 'print not true\nprint not (3 > 5)')
opt_stable("mixed Int/Float folds to Float (matches runtime coercion)", 'print 1 / 2\nprint 4 / 2')
opt_stable("nested constant expressions fold all the way down",
           'fn f():\n    return (2 + 3) * (10 - 6)\nprint f()')

# ---- the trap: folding must NOT change error timing ----
opt_stable("division by zero inside a never-taken branch is never evaluated",
           'fn f(x):\n    if x > 100:\n        return 1 / 0\n    return x\nprint f(5)')
opt_stable("division by zero inside a taken branch still raises at the right point",
           'print "before"\nfn f():\n    return 1 / 0\nprint f()\nprint "never"')
opt_stable("a TypeError-producing op is left unfolded, not evaluated at compile time",
           'fn f(flag):\n    if flag:\n        return 1 + [1, 2]\n    return 0\nprint f(false)')
opt_stable("an actually-reached type error still raises identically",
           'fn f():\n    return 1 + [1, 2]\nprint f()')

# ---- short-circuit folding (-O2) must not change which side effects run ----
opt_stable("`true or <side effect>` never evaluates the side effect",
           'let log = []\nfn tag():\n    push(log, 1)\n    return true\n'
           'print (true or tag())\nprint log')
opt_stable("`false and <side effect>` never evaluates the side effect",
           'let log = []\nfn tag():\n    push(log, 1)\n    return true\n'
           'print (false and tag())\nprint log')
opt_stable("`false or <side effect>` DOES evaluate the side effect (not short-circuited)",
           'let log = []\nfn tag():\n    push(log, 1)\n    return true\n'
           'print (false or tag())\nprint log')
opt_stable("`true and <side effect>` DOES evaluate the side effect (not short-circuited)",
           'let log = []\nfn tag():\n    push(log, 1)\n    return true\n'
           'print (true and tag())\nprint log')

# ---- dead-code elimination: statements after an unconditional return ----
opt_stable("statements after a top-level function return are unreachable",
           'fn f():\n    return 1\n    print "unreachable"\nprint f()')
opt_stable("statements after a return inside a for-loop body are unreachable",
           'fn f():\n    for n in [1, 2, 3]:\n        return n\n    return -1\nprint f()')

# ---- dead-branch elimination (-O2): constant `if` conditions ----
opt_stable("a literal-false branch's body never executes",
           'if false:\n    print "dead"\nelse:\n    print "alive"')
opt_stable("a literal-true branch's body always executes, else is dropped",
           'if true:\n    print "alive"\nelse:\n    print "dead"')
opt_stable("a folded-constant condition (from arithmetic) still picks the right branch",
           'if 2 + 2 == 4:\n    print "math works"\nelse:\n    print "uh oh"')

# ---- dead-loop elimination (-O2): a literal-false while condition ----
opt_stable("a literal-false while body never executes",
           'print "before"\nwhile false:\n    print "dead"\nprint "after"')
opt_stable("while false with a would-raise body is never evaluated",
           'while false:\n    print 1 / 0\nprint "survived"')
opt_stable("while loop with a real (non-constant) condition still runs normally",
           'let i = 0\nwhile i < 5:\n    print i\n    i = i + 1')
opt_stable("while loop whose condition is false from the start (runtime, not folded) runs zero times",
           'let i = 10\nwhile i < 5:\n    print "unreachable"\nprint "done"')

# ---- realistic, larger programs: recursion, loops, structs, stdlib-style code ----
opt_stable("while-loop accumulation, unaffected by folding",
           'let total = 0\nlet i = 1\nwhile i <= 5:\n    total = total + i\n    i = i + 1\nprint total')
opt_stable("while loop nested inside a for loop",
           'for n in [1, 2, 3]:\n    let j = 0\n    while j < n:\n        print n * 10 + j\n        j = j + 1')
opt_stable("recursive factorial, unaffected by folding",
           'fn fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\nprint fact(10)')
opt_stable("loop accumulation with constant-folded loop-invariant arithmetic",
           'let total = 0\nfor n in [1, 2, 3, 4, 5]:\n    total = total + n * (2 + 1)\nprint total')
opt_stable("struct construction and field math alongside constant folding",
           'struct P:\n    x\n    y\nlet p = P { x: 2 + 3, y: 10 / 2 }\nprint p.x + p.y')


print(f"\n{'='*60}\nOPTIMIZER STABILITY (-O0/-O1/-O2 agree): {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
if FAILURES:
    for f in FAILURES:
        print("\n" + f)
sys.exit(1 if FAIL else 0)
