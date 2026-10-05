"""Conformance tests for docs/errors.md."""
from harness import check, print_final_summary

# ---- §4.1/§4.2 static vs runtime, uniform catchability ----
check("errors.md §4.1", "static (parse) error prevents any execution",
      'print "before"\nlet x = ', expect_ok=False)
check("errors.md §4.1", "static (type) error prevents any execution",
      'print "before"\nlet x: Int = "s"', expect_ok=False)
check("errors.md §4.2", "NameError is catchable (uniform failure model)",
      'try:\n    print nope\ncatch e:\n    print "ok"', "ok")
check("errors.md §4.2", "wrong-arity call is catchable",
      'fn f(a):\n    return a\ntry:\n    print f(1, 2)\ncatch e:\n    print "ok"', "ok")
check("errors.md §4.2", "division by zero is catchable",
      'try:\n    print 1 / 0\ncatch e:\n    print "ok"', "ok")
check("errors.md §4.2", "uncaught failure at top level exits nonzero", 'print nope', expect_ok=False)

# ---- §4.4.1 arithmetic ----
check("errors.md §4.4.1", "division by zero message exact", 'print 1 / 0', expect_ok=False, expect_error_substr="division by zero")
check("errors.md §4.4.1", "incompatible binop message names both types",
      'print "a" - 1', expect_ok=False, expect_error_substr="cannot apply '-' to str and int")

# ---- §4.4.2 name resolution ----
check("errors.md §4.4.2", "unbound variable message", 'print xyz', expect_ok=False, expect_error_substr="NameError: 'xyz' is not defined")
check("errors.md §4.4.2", "unbound function call message", 'print xyz(1)', expect_ok=False, expect_error_substr="NameError: function 'xyz' is not defined")
check("errors.md §4.4.2", "nonexistent module value message",
      'use "nope_mod.lum" as m\nprint m.x', expect_ok=False, expect_error_substr="ModuleError")
check("errors.md §4.4.2", "nonexistent struct field message",
      'struct P:\n    x\nlet p = P { x: 1 }\nprint p.y', expect_ok=False, expect_error_substr="has no field 'y'")

# ---- §4.4.3 arity / shape ----
check("errors.md §4.4.3", "wrong arg count message exact",
      'fn f(a):\n    return a\nprint f(1, 2, 3)', expect_ok=False, expect_error_substr="f() expects 1 arguments, got 3")
check("errors.md §4.4.3", "enum variant wrong payload count message",
      'enum E:\n    P(a, b)\nlet x = P(1)', expect_ok=False, expect_error_substr="expects 2 value(s), got 1")
check("errors.md §4.4.3", "struct missing-field message names the field",
      'struct P:\n    x\n    y\nlet p = P { x: 1 }', expect_ok=False, expect_error_substr="missing")

# ---- §4.4.4 collections/indexing ----
check("errors.md §4.4.4", "list index out of range message",
      'print [1,2][5]', expect_ok=False, expect_error_substr="index 5 is out of range (length 2)")
check("errors.md §4.4.4", "dict missing key message",
      'print {"a":1}["b"]', expect_ok=False, expect_error_substr="key 'b' not found")
check("errors.md §4.4.4", "index on non-indexable value",
      'print (5)[0]', expect_ok=False)
check("errors.md §4.4.4", "index assignment out of range is catchable",
      'let a = [1]\ntry:\n    a[5] = 1\ncatch e:\n    print "ok"', "ok")

# ---- §4.4.5 field access on non-structs ----
check("errors.md §4.4.5", "field read on non-struct value",
      'let x = 5\nprint x.field', expect_ok=False, expect_error_substr="cannot read field")
check("errors.md §4.4.5", "field write on non-struct value",
      'let x = 5\nx.field = 1', expect_ok=False, expect_error_substr="cannot set field")

# ---- §4.4.6 match exhaustiveness ----
check("errors.md §4.4.6", "non-exhaustive match raises MatchError at runtime",
      'enum E:\n    A\n    B\nmatch B:\n    A:\n        print "a"', expect_ok=False, expect_error_substr="MatchError")
check("errors.md §4.4.6", "match is not statically checked for exhaustiveness (parses/typechecks fine)",
      'enum E:\n    A\n    B\nmatch A:\n    A:\n        print "a"', "a")

# ---- §4.4.7 / §4.4.8 capabilities & I/O (see also security tests) ----
check("errors.md §4.4.7", "PermissionError exact wording",
      'print read_file("/tmp/x")', expect_ok=False,
      expect_error_substr="PermissionError: read_file() needs the 'fs' capability")

# ---- §4.4.9 modules ----
check("errors.md §4.4.9", "missing module message",
      'use "does_not_exist.lum" as m', expect_ok=False, expect_error_substr="cannot find module")
check("errors.md §4.4.9", "unknown capability name message",
      'use bogus', expect_ok=False, expect_error_substr="ModuleError: unknown module 'bogus'")

# ---- §4.4.10 concurrency ----
check("errors.md §4.4.10", "wait() on non-task raises TypeError",
      'print wait(5)', expect_ok=False, expect_error_substr="wait() expects a task")

# ---- §4.4.11 explicit fail() ----
check("errors.md §4.4.11", "fail() message has no added prefix",
      'try:\n    fail("custom message")\ncatch e:\n    print e.message', "custom message")
check("errors.md §4.4.11", "fail() propagates uncaught", 'fail("boom")', expect_ok=False, expect_error_substr="boom")

# ---- §4.5 match pattern resolution order ----
check("errors.md §4.5", "wildcard _ matches anything and binds nothing",
      'match 42:\n    _:\n        print "matched"', "matched")
check("errors.md §4.5", "bare NAME binds when it is not a known unit variant",
      'match 42:\n    n:\n        print n', "42")
check("errors.md §4.5", "bare NAME resolves to variant-equality when it IS a unit variant",
      'enum E:\n    A\n    B\nmatch B:\n    A:\n        print "wrong"\n    B:\n        print "right"', "right")
check("errors.md §4.5", "literal pattern matches by equality",
      'match 5:\n    5:\n        print "five"\n    _:\n        print "other"', "five")
check("errors.md §4.5", "literal pattern falls through to next case on mismatch",
      'match 6:\n    5:\n        print "five"\n    _:\n        print "other"', "other")

check("errors.md §4.9", "Phase 2 addendum: undefined name outside try now fails BEFORE prior statements' output",
      'print "before"\nprint nope', expect_ok=False)
def timing_check():
    import harness
    out, err = harness.run_source('print "before"\nprint nope')
    ok = err is not None and out == ""
    if ok: harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[errors.md §4.9] no output before static rejection: out={out!r} err={err!r}")
timing_check()
check("errors.md §4.9", "same undefined name INSIDE a try still executes prior statements and is catchable",
      'print "before"\ntry:\n    print nope\ncatch e:\n    print "caught"', 'before\ncaught')

print_final_summary()
