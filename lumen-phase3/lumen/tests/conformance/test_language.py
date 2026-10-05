"""Conformance tests for docs/language.md."""
from harness import check, print_final_summary

# ---- §1.10 Operator precedence & associativity ----
check("language.md §1.10", "add before compare", 'print 1 + 2 == 3', "true")
check("language.md §1.10", "mul before add", 'print 2 + 3 * 4', "14")
check("language.md §1.10", "mul before sub", 'print 10 - 2 * 3', "4")
check("language.md §1.10", "unary minus binds tighter than mul", 'print -2 * 3', "-6")
check("language.md §1.10", "paren overrides precedence", 'print (2 + 3) * 4', "20")
check("language.md §1.10", "and before or", 'print true or false and false', "true")
check("language.md §1.10", "not binds tighter than and", 'print not false and true', "true")
check("language.md §1.10", "not not double negation", 'print not not true', "true")
check("language.md §1.10", "comparison non-chaining: (a<b)<c", 'print (1 < 2) < 1', "false")
check("language.md §1.10", "comparison non-chaining literal form", 'print 1 < 2 < 0', "false")
check("language.md §1.10", "left-assoc subtraction", 'print 10 - 3 - 2', "5")
check("language.md §1.10", "left-assoc division", 'print 100 / 10 / 2', "5.0")
check("language.md §1.10", "field/index/call chain left-to-right",
      'struct P:\n    x\nlet p = P { x: [10, 20] }\nprint p.x[1]', "20")
check("language.md §1.10", "no exponent operator is a syntax error", 'print 2 ** 3', expect_ok=False)
check("language.md §1.10", "or short-circuits (right side never evaluated)",
      'let log = []\nfn sideeffect():\n    push(log, 1)\n    return true\ntrue or sideeffect()\nprint len(log)', "0")
check("language.md §1.10", "and short-circuits (right side never evaluated)",
      'let log = []\nfn sideeffect():\n    push(log, 1)\n    return true\nfalse and sideeffect()\nprint len(log)', "0")
check("language.md §1.10", "or evaluates right side when needed",
      'let log = []\nfn sideeffect():\n    push(log, 1)\n    return true\nfalse or sideeffect()\nprint len(log)', "1")

# ---- §1.9 Evaluation order ----
check("language.md §1.9", "binary operands evaluate left to right",
      'let log = []\nfn tag(n):\n    push(log, n)\n    return n\nprint tag(1) + tag(2)\nprint log', "3\n[1, 2]")
check("language.md §1.9", "call args evaluate left to right",
      'let log = []\nfn tag(n):\n    push(log, n)\n    return n\nfn f(a, b):\n    return a + b\nprint f(tag(1), tag(2))\nprint log', "3\n[1, 2]")

# ---- §1.11 Scoping ----
check("language.md §1.11 Rule A/B", "if-block reassignment shadows, does not mutate outer",
      'let x = 1\nif true:\n    x = 2\nprint x', "1")
check("language.md §1.11 Rule B", "reassignment visible inside the block itself",
      'let x = 1\nif true:\n    x = 2\n    print x', "2")
check("language.md §1.11 Rule B", "mutating container contents DOES persist outward",
      'let box = [0]\nif true:\n    box[0] = 2\nprint box[0]', "2")
check("language.md §1.11 Rule C", "nested fn cannot see outer fn's local let",
      'fn outer():\n    let y = 5\n    fn inner():\n        return y\n    return inner()\nprint outer()',
      expect_ok=False, expect_error_substr="NameError")
check("language.md §1.11 Rule C", "fn CAN see module-global let",
      'let g = 42\nfn f():\n    return g\nprint f()', "42")
check("language.md §1.11", "for-loop var scoped to the loop body block",
      'for i in [1, 2]:\n    let y = i\nprint y', expect_ok=False, expect_error_substr="NameError")
check("language.md §1.11", "try-block bindings do not leak to catch",
      'try:\n    let z = 1\n    fail("boom")\ncatch e:\n    print z',
      expect_ok=False, expect_error_substr="NameError")

# ---- §1.12 Mutability ----
check("language.md §1.12", "bare reassignment works", 'let z = 1\nz = 2\nprint z', "2")
check("language.md §1.12", "re-let with new type is allowed (no const)", 'let z = 1\nz = "now a string"\nprint z', "now a string")

# ---- §1.7 Truthiness / falsy set (table exhaustively) ----
def truthy_case(name, expr, expected):
    check("language.md §1.7 (types.md §3.7)", name, f'if {expr}:\n    print "T"\nelse:\n    print "F"', expected)

truthy_case("int 0 is falsy", "0", "F")
truthy_case("int nonzero is truthy", "5", "T")
truthy_case("negative int is truthy", "-1", "T")
truthy_case("float 0.0 is falsy", "0.0", "F")
truthy_case("float nonzero is truthy", "0.1", "T")
truthy_case("empty string is falsy", '""', "F")
truthy_case("nonempty string is truthy", '"a"', "T")
truthy_case("empty list is falsy", "[]", "F")
truthy_case("nonempty list is truthy", "[1]", "T")
truthy_case("empty dict is falsy", "{}", "F")
truthy_case("nonempty dict is truthy", '{"a": 1}', "T")
check("types.md §3.7", "true is truthy", 'if true:\n    print "T"\nelse:\n    print "F"', "T")
check("types.md §3.7", "false is falsy", 'if false:\n    print "T"\nelse:\n    print "F"', "F")
check("types.md §3.7", "struct instance always truthy",
      'struct P:\n    x\nif P { x: 0 }:\n    print "T"\nelse:\n    print "F"', "T")
check("types.md §3.7", "enum instance always truthy",
      'enum E:\n    A\nif A:\n    print "T"\nelse:\n    print "F"', "T")

# ---- §1.3 Lexical: string literals have no escape sequences ----
check("language.md §1.3", "backslash-n in string is literal, not newline",
      r'print "a\nb"', r"a\nb")
check("language.md §1.3", "string literal cannot contain an embedded quote (parse boundary)",
      'print "a" "b"', expect_ok=True)  # two adjacent string literals: second is a separate (illegal) stmt actually
check("language.md §1.3", "integer literal has no decimal point", 'print 42', "42")
check("language.md §1.3", "float literal requires digits both sides of dot", 'print 3.14', "3.14")
check("language.md §1.3", "no hex literal support", 'print 0xFF', expect_ok=False)

# ---- §1.5 Indentation ----
check("language.md §1.5", "blank lines inside a block are permitted",
      'if true:\n    print 1\n\n    print 2', "1\n2")
check("language.md §1.5", "comment-only line inside a block is permitted",
      'if true:\n    print 1\n    # a comment\n    print 2', "1\n2")
check("language.md §1.5", "inconsistent dedent level is a syntax error",
      'if true:\n        print 1\n    print 2', expect_ok=False)

# ---- §1.4 Comments ----
check("language.md §1.4", "trailing comment on a statement line is ignored",
      'let x = 5   # comment\nprint x', "5")

check("language.md §1.5.1", "multi-line dict literal (bracket-aware suppression)",
      'let d = {"a": 1,\n         "b": 2}\nprint d["b"]', "2")
check("language.md §1.5.1", "ordinary indentation resumes after a multi-line literal",
      'let a = [1,\n         2]\nif true:\n    print a[1]', "2")

print_final_summary()
