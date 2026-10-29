"""Conformance tests for Phase 2 Milestone 1: static Name Resolution.

Every test here cross-checks the Resolver's verdict against ACTUAL interpreter
behavior (never asserted from assumption) -- soundness relative to the frozen
Phase 1 spec is the entire point of this pass.
"""
from harness import check, print_final_summary

# ---- Tier 1: block-local variables — sound AND complete, matches Env chain exactly ----
check("resolver Tier 1", "simple defined variable resolves", 'let x = 5\nprint x', "5")
check("resolver Tier 1", "undefined variable rejected at compile time now (was runtime-only pre-Phase-2)",
      'print totally_undefined', expect_ok=False, expect_error_substr="unresolved by static name resolution")
check("resolver Tier 1", "if-block shadow does not leak (Rule B) -- resolver must not falsely accept outer read of inner-only name",
      'if true:\n    let inner_only = 1\nprint inner_only', expect_ok=False)
check("resolver Tier 1", "if-block shadow read inside the block itself is fine",
      'if true:\n    let inner_only = 1\n    print inner_only', "1")
check("resolver Tier 1", "for-loop var not visible after the loop",
      'for i in [1,2]:\n    let y = i\nprint y', expect_ok=False)
check("resolver Tier 1", "catch err-name not visible after the try/catch",
      'try:\n    fail("x")\ncatch e:\n    let z = 1\nprint z', expect_ok=False)
check("resolver Tier 1", "catch err-name IS visible inside the catch block",
      'try:\n    fail("boom")\ncatch e:\n    print e.message', "boom")

# ---- Tier 1 / Rule C: function body isolation from enclosing lexical scope ----
check("resolver Rule C", "fn cannot see outer fn's local let (compile-time rejection now)",
      'fn outer():\n    let y = 5\n    fn inner():\n        return y\n    return inner()\nprint outer()',
      expect_ok=False, expect_error_substr="unresolved by static name resolution")
check("resolver Rule C", "fn cannot see outer fn's PARAM either",
      'fn outer(p):\n    fn inner():\n        return p\n    return inner()\nprint outer(5)',
      expect_ok=False)
check("resolver Rule C", "fn CAN see a true top-level global let",
      'let g = 42\nfn f():\n    return g\nprint f()', "42")
check("resolver Rule C", "fn does NOT see a let nested inside a top-level if-block (not a true global)",
      'if true:\n    let g = 1\nfn f():\n    return g\nprint f()', expect_ok=False)

# ---- Tier 2: functions/structs/enum variants — sound-for-rejection only, permissive for order ----
check("resolver Tier 2", "calling a genuinely nonexistent function is rejected at compile time",
      'print nope(1)', expect_ok=False, expect_error_substr="no 'fn nope'")
check("resolver Tier 2", "calling a function BEFORE its defining statement runs at top level is still a "
      "genuine runtime failure (top-level order matters, per language.md §1.9 — Tier 2 permissiveness is "
      "only about STATIC rejection, not about making order stop mattering)",
      'print later()\nfn later():\n    return 1', expect_ok=False, expect_error_substr="NameError: function 'later' is not defined")
check("resolver Tier 2", "calling a function declared LATER in source but invoked from inside a "
      "function that itself runs after the declaration succeeds (genuine order-independent existence case)",
      'fn wrapper():\n    return later()\nfn later():\n    return 1\nprint wrapper()', "1")
check("resolver Tier 2", "calling a nested fn AFTER its registering statement ran is accepted by both resolver and runtime",
      ('fn outer():\n    fn inner():\n        return 42\n    return 0\nouter()\nprint inner()'),
      "42")
check("resolver Tier 2", "the SAME program's call BEFORE the registering statement runs is accepted "
      "by the resolver (existence-only) but still correctly fails at RUNTIME (order-dependent, per types.md §3.10) "
      "-- this is the documented, deliberate soundness boundary, not a bug",
      ('fn outer():\n    fn inner():\n        return 42\n    return 0\nprint inner()'),
      expect_ok=False, expect_error_substr="NameError: function 'inner' is not defined")
check("resolver Tier 2", "constructing a struct that was never declared is rejected at compile time",
      'let p = Ghost { x: 1 }', expect_ok=False, expect_error_substr="unknown struct 'Ghost'")
check("resolver Tier 2", "constructing a declared struct is accepted",
      'struct P:\n    x\nprint P { x: 1 }.x', "1")
check("resolver Tier 2", "enum variant constructor call resolves via the flat variant table",
      'enum E:\n    V(x)\nprint V(5)', "V(5)")
check("resolver Tier 2", "bare unit-variant Var reference resolves via the flat variant table",
      'enum E:\n    A\nmatch A:\n    A:\n        print "ok"', "ok")
check("resolver Tier 2", "builtin function names are always resolvable without declaration",
      'print len([1,2,3])', "3")
check("resolver Tier 2", "spawn's call target is resolved through the same Tier-2 rules as an ordinary call",
      'fn f(n):\n    return n\nlet t = spawn f(5)\nprint wait(t)', "5")
check("resolver Tier 2", "spawn of a genuinely undefined function is rejected at compile time",
      'let t = spawn nope(1)', expect_ok=False, expect_error_substr="no 'fn nope'")

# ---- match patterns: resolver must bind exactly the names the interpreter binds ----
check("resolver patterns", "variant pattern names are bound and visible in the case body",
      'enum E:\n    Pair(a, b)\nmatch Pair(1, 2):\n    Pair(x, y):\n        print x + y', "3")
check("resolver patterns", "a variant pattern's bound names are NOT visible outside their case",
      'enum E:\n    Pair(a, b)\nmatch Pair(1, 2):\n    Pair(x, y):\n        let z = x\nprint z', expect_ok=False)
check("resolver patterns", "a plain-binding pattern name is usable in its case body",
      'match 42:\n    n:\n        print n', "42")
check("resolver patterns", "wildcard _ binds nothing (using it as a name elsewhere still fails)",
      'match 1:\n    _:\n        print "matched"\nprint _', expect_ok=False)

# ---- top-level sequential order genuinely matters (unlike inside a function) ----
check("resolver top-level order", "forward reference to a not-yet-declared top-level global is rejected",
      'print g\nlet g = 5', expect_ok=False, expect_error_substr="unresolved by static name resolution")
check("resolver top-level order", "backward reference to an already-declared top-level global is fine",
      'let g = 5\nprint g', "5")

# ---- module alias (use ... as) counts as a Rule-C-visible global too ----
import os, tempfile, shutil
import harness
def module_alias_visible_from_fn():
    d = tempfile.mkdtemp()
    try:
        with open(os.path.join(d, "main.lum"), "w") as f:
            f.write('use "helper.lum" as h\nfn f():\n    return h.value()\nprint f()')
        with open(os.path.join(d, "helper.lum"), "w") as f:
            f.write('fn value():\n    return 7')
        out, err = harness.run_source(open(os.path.join(d, "main.lum")).read(), base_dir=d)
        ok = err is None and out.strip() == "7"
        if ok: harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[resolver Rule C] module alias visible from fn body: out={out!r} err={err!r}")
    finally:
        shutil.rmtree(d)
module_alias_visible_from_fn()

print_final_summary()
