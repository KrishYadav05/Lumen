"""Conformance tests for docs/language.md §1.13 — Lumen 2.1's low-punctuation
syntax. Every check here has a direct counterpart using the OLD (still legal)
spelling elsewhere in this suite; these specifically exercise the NEW,
additive spellings and the few documented exceptions/ambiguity errors."""
from harness import check, print_final_summary

# ---- §1.13.1 optional colons ----
check("language.md §1.13.1", "if/else with no colon at all",
      'if true\n    print "a"\nelse\n    print "b"', "a")
check("language.md §1.13.1", "for with no colon",
      'let t = 0\nfor n in [1, 2, 3]\n    t = t + n\nprint t', "6")
check("language.md §1.13.1", "while with no colon (added 2.2 -- while was included in the "
      "'every block-opening header' list from the day it was added)",
      'let i = 0\nwhile i < 3\n    print i\n    i = i + 1', "0\n1\n2")
check("language.md §1.13.1", "fn with no colon and old-style parens/commas",
      'fn add(a, b)\n    return a + b\nprint add(2, 3)', "5")
check("language.md §1.13.1", "struct def with no colon",
      'struct Point\n    x\n    y\nlet p = Point { x: 1, y: 2 }\nprint p.x + p.y', "3")
check("language.md §1.13.1", "enum def with no colon",
      'enum Opt\n    A\n    B\nlet o = A\nprint o', "A")
check("language.md §1.13.1", "match/case with no colon",
      'match 5\n    5\n        print "five"\n    _\n        print "other"', "five")
check("language.md §1.13.1", "try/catch with no colon",
      'try\n    let r = 10 / 0\ncatch e\n    print "caught"', "caught")
check("language.md §1.13.1", "test header with no colon",
      'test "t1"\n    assert(true)', "")

# ---- §1.13.1 optional commas ----
check("language.md §1.13.1", "list literal with space-separated items, no commas",
      'print [1 2 3 4 5]', "[1, 2, 3, 4, 5]")
check("language.md §1.13.1", "list literal mixing comma and no-comma separators",
      'print [1, 2 3, 4]', "[1, 2, 3, 4]")
check("language.md §1.13.1", "fn params with no parens and no commas",
      'fn add a b\n    return a + b\nprint add(2, 3)', "5")
check("language.md §1.13.1", "dict literal with no commas",
      'let d = {"a": 1 "b": 2}\nprint d["a"] + d["b"]', "3")
check("language.md §1.13.1", "struct literal with no commas",
      'struct P\n    x\n    y\nlet p = P { x: 1 y: 2 }\nprint p.x + p.y', "3")
check("language.md §1.13.1", "trailing comma now accepted in a list literal",
      'print [1, 2,]', "[1, 2]")
check("language.md §1.13.1", "trailing comma now accepted in a call's argument list",
      'fn f(a, b)\n    return a\nprint f(1, 2,)', "1")

# ---- §1.13.1 'as' / 'returns' / 'is' alternative spellings ----
check("language.md §1.13.1", "'as' as an alternative to ':' for a let's type annotation",
      'let x as Int = 5\nprint x', "5")
check("language.md §1.13.1", "'as' as an alternative to ':' for a fn param's type",
      'fn double(n as Int) returns Int\n    return n * 2\nprint double(21)', "42")
check("language.md §1.13.1", "'returns' as an alternative to '->' for a fn's return type",
      'fn square(x: Int) returns Int\n    return x * x\nprint square(6)', "36")
check("language.md §1.13.1", "'is' as an alternative to ':' in a dict literal",
      'let d = {"a" is 1}\nprint d["a"]', "1")
check("language.md §1.13.1", "'is' as an alternative to ':' in a struct literal",
      'struct P\n    x\nlet p = P { x is 9 }\nprint p.x', "9")

# ---- §1.13.2 paren-free calls (application) ----
check("language.md §1.13.2", "bare call with no parens",
      'fn add a b\n    return a + b\nprint add 2 3', "5")
check("language.md §1.13.2", "application binds tighter than '+': f x + y means (f x) + y",
      'fn add a b\n    return a + b\nprint add 2 3 + 1', "6")
check("language.md §1.13.2", "a compound argument needs its own parens",
      'fn add a b\n    return a + b\nprint add 2 (3 + 1)', "6")
check("language.md §1.13.2", "nested bare calls each need parens around the inner one",
      'fn add a b\n    return a + b\nprint add 2 (add 1 2)', "5")
check("language.md §1.13.2", "enum variant with a compound-expression payload needs parens",
      'enum Opt\n    Some value\n    None\nfn half(n)\n    return Some (n / 2)\nmatch half(10)\n    Some v\n        print v\n    None\n        print "none"',
      "5.0")
check("language.md §1.13.2", "paren-free variant pattern in match binds the payload",
      'enum Opt\n    Some value\n    None\nmatch Some(7)\n    Some v\n        print v\n    None\n        print "none"',
      "7")
check("language.md §1.13.2", "paren-free enum variant field declaration (no parens in the enum body)",
      'enum Opt\n    Some value\n    None\nprint Some(3)', "Some(3)")
check("language.md §1.13.2", "a bare multi-argument call as one of several list items is a SyntaxError",
      'fn add a b\n    return a + b\nprint [add 2 3 10]', expect_ok=False,
      expect_error_substr="needs parens")
check("language.md §1.13.2", "parenthesizing the bare call resolves the same list-item ambiguity",
      'fn add a b\n    return a + b\nprint [(add 2 3) 10]', "[5, 10]")
check("language.md §1.13.2", "a plain bare name (no following atoms) is still just a variable reference",
      'fn add a b\n    return a + b\nlet x = 1\nprint x', "1")
check("language.md §1.13.2", "a zero-argument call still requires explicit parens",
      'print now() >= 0', "true")

# ---- §1.13.3 implicit (auto) generics ----
check("language.md §1.13.3", "fn with no <T> still type-checks when a non-concrete type name is used",
      'fn identity x as T returns T\n    return x\nprint identity(42)', "42")
check("language.md §1.13.3", "auto-generic fn works the same as the explicit <T> spelling",
      'fn identity<T>(x: T) -> T:\n    return x\nprint identity("hi")', "hi")

# ---- §1.13.4 print with multiple values ----
check("language.md §1.13.4", "print with two space-separated values",
      'print "total:" 15', "total: 15")
check("language.md §1.13.4", "print with three space-separated values of mixed types",
      'print "a" 1 true', "a 1 true")
check("language.md §1.13.4", "print of a single expression containing '+' is unaffected (one value, not two)",
      'let a = 2\nlet b = 3\nprint a + b', "5")

print_final_summary()
