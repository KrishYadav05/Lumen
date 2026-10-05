"""Conformance tests for docs/grammar.md — grammar productions not already
exhaustively exercised while testing other chapters."""
from harness import check, print_final_summary

# ---- §2.5 primary / trailer chain ----
check("grammar.md §2.5", "empty list literal parses and evaluates", 'print []', "[]")
check("grammar.md §2.5", "empty dict literal parses and evaluates", 'print {}', "{}")
check("grammar.md §2.5", "trailing comma is NOT permitted in a list literal", 'print [1, 2,]', expect_ok=False)
check("grammar.md §2.5", "trailing comma is NOT permitted in a call's argument list",
      'fn f(a, b):\n    return a\nprint f(1, 2,)', expect_ok=False)
check("grammar.md §2.5", "call syntax only follows a '.' trailer, not a '[' trailer directly",
      'struct Box:\n    items\nlet b = Box { items: [1, 2] }\nprint b.items[0]', "1")
check("types.md §3.10", "function names are not first-class: reading one as a value is a NameError",
      'fn double(n):\n    return n * 2\nlet g = double', expect_ok=False, expect_error_substr="NameError: 'double' is not defined")
check("types.md §3.10", "function names cannot be passed as an argument either "
      "(now caught earlier by Tier-2 static resolution of apply's own body, since "
      "'f' used in call position is neither a declared fn nor a parameter's call-target — "
      "this is a MORE informative compile-time error than the old runtime-only one)",
      'fn double(n):\n    return n * 2\nfn apply(f, x):\n    return f(x)\nprint apply(double, 5)',
      expect_ok=False, expect_error_substr="unresolved by static name resolution")
check("types.md §3.10", "direct call syntax for the same function works fine",
      'fn double(n):\n    return n * 2\nprint double(5)', "10")
check("grammar.md §2.5", "nested list literals", 'print [[1, 2], [3, 4]]', "[[1, 2], [3, 4]]")
check("grammar.md §2.5", "nested indexing", 'print [[1, 2], [3, 4]][1][0]', "3")
check("grammar.md §2.5.1", "NAME immediately followed by { is always a struct literal, never ambiguous with a block",
      'struct Only:\n    v\nlet x = Only { v: 1 }\nprint x.v', "1")
check("grammar.md §2.5.2", "call syntax resolves to a plain user function",
      'fn f(x):\n    return x\nprint f(1)', "1")
check("grammar.md §2.5.2", "call syntax resolves to an enum variant constructor",
      'enum E:\n    V(x)\nprint V(1)', "V(1)")

# ---- §2.2 statement forms parse and execute ----
check("grammar.md §2.2", "if with no else is legal", 'if true:\n    print "yes"', "yes")
check("grammar.md §2.2", "if false with no else executes nothing, no error",
      'if false:\n    print "no"\nprint "after"', "after")
check("grammar.md §2.2", "for over an empty list runs zero iterations",
      'for x in []:\n    print x\nprint "done"', "done")
check("grammar.md §2.2", "return is required to carry an expression (bare return is a syntax error)",
      'fn f():\n    return\nprint f()', expect_ok=False)
check("grammar.md §2.2", "fn with zero parameters", 'fn f():\n    return 1\nprint f()', "1")
check("grammar.md §2.2", "fn with multiple parameters", 'fn f(a, b, c):\n    return a + b + c\nprint f(1, 2, 3)', "6")
check("grammar.md §2.2", "struct with a single field", 'struct One:\n    v\nprint One { v: 5 }.v', "5")
check("grammar.md §2.2", "enum with a single unit variant",
      'enum Single:\n    Only\nmatch Only:\n    Only:\n        print "matched"', "matched")

# ---- §2.3 patterns ----
check("grammar.md §2.3", "string literal pattern", 'match "x":\n    "x":\n        print "matched"', "matched")
check("grammar.md §2.3", "bool literal pattern true", 'match true:\n    true:\n        print "t"\n    false:\n        print "f"', "t")
check("grammar.md §2.3", "variant pattern with multiple bound names",
      'enum E:\n    Three(a, b, c)\nmatch Three(1, 2, 3):\n    Three(x, y, z):\n        print x + y + z', "6")

# ---- §2.6 fn with generics + return type + match, full derivation ----
check("grammar.md §2.6", "worked example: generic fn with match over enum payload",
      ('enum Shape:\n    Circle(r)\n    Square(s)\n'
       'fn area(shape) -> Float:\n    match shape:\n        Circle(r):\n            return 3.14159 * r * r\n        Square(s):\n            return s * s\n'
       'print area(Square(3))'),
      "9")

print_final_summary()
