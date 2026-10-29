"""Conformance tests for docs/types.md."""
from harness import check, print_final_summary

# ---- §3.2 Primitives: literal -> inferred type, checked via `let` annotation ----
for lit, ty in [("5", "Int"), ("5.0", "Float"), ('"hi"', "Str"), ("true", "Bool")]:
    check("types.md §3.2/§3.6", f"let with correct annotation ({ty})", f'let x: {ty} = {lit}\nprint x', expect_ok=True)

# every (literal, wrong-annotation) pair must be a static TypeError
LITS = {"Int": "5", "Float": "5.0", "Str": '"hi"', "Bool": "true"}
for lit_ty, lit in LITS.items():
    for ann_ty in LITS:
        if ann_ty == lit_ty:
            continue
        check("types.md §3.6 #1", f"let {ann_ty} = {lit_ty} literal rejected",
              f'let x: {ann_ty} = {lit}', expect_ok=False, expect_error_substr="TypeError")

# ---- §3.4.1 struct: exact field set ----
check("types.md §3.4.1", "struct exact match succeeds",
      'struct P:\n    x\n    y\nlet p = P { x: 1, y: 2 }\nprint p.x + p.y', "3")
check("types.md §3.4.1", "struct missing field rejected",
      'struct P:\n    x\n    y\nlet p = P { x: 1 }', expect_ok=False, expect_error_substr="missing")
check("types.md §3.4.1", "struct extra field rejected",
      'struct P:\n    x\nlet p = P { x: 1, y: 2 }', expect_ok=False, expect_error_substr="unexpected")
check("types.md §3.4.1", "struct both missing and extra reported",
      'struct P:\n    x\n    y\nlet p = P { x: 1, z: 9 }', expect_ok=False, expect_error_substr="missing")
check("types.md §3.4.1", "struct field mutation persists (reference type)",
      'struct P:\n    x\nlet p = P { x: 1 }\np.x = 99\nprint p.x', "99")
check("types.md §3.4.1", "struct is a reference type across bindings",
      'struct P:\n    x\nlet a = P { x: 1 }\nlet b = a\nb.x = 5\nprint a.x', "5")

# ---- §3.4.2 enum: unit and payload variants ----
check("types.md §3.4.2", "unit variant constructed and matched",
      'enum E:\n    A\n    B\nmatch A:\n    A:\n        print "a"\n    B:\n        print "b"', "a")
check("types.md §3.4.2", "payload variant carries values in order",
      'enum E:\n    Pair(a, b)\nlet p = Pair(1, 2)\nmatch p:\n    Pair(x, y):\n        print x + y', "3")
check("types.md §3.4.2", "variant constructor wrong arity rejected",
      'enum E:\n    Pair(a, b)\nlet p = Pair(1)', expect_ok=False, expect_error_substr="TypeError")
check("types.md §3.4.2", "variant constructor too many args rejected",
      'enum E:\n    Pair(a, b)\nlet p = Pair(1, 2, 3)', expect_ok=False, expect_error_substr="TypeError")

# ---- §3.8 Generics ----
check("types.md §3.8", "identity<T> works across Int",
      'fn id<T>(x: T) -> T:\n    return x\nprint id(5)', "5")
check("types.md §3.8", "identity<T> works across Str",
      'fn id<T>(x: T) -> T:\n    return x\nprint id("hi")', "hi")
check("types.md §3.8", "first<T> consistent Int args ok",
      'fn first<T>(a: T, b: T) -> T:\n    return a\nprint first(1, 2)', "1")
check("types.md §3.8", "first<T> consistent Str args ok",
      'fn first<T>(a: T, b: T) -> T:\n    return a\nprint first("x", "y")', "x")
check("types.md §3.8", "first<T> conflicting Int/Str args rejected",
      'fn first<T>(a: T, b: T) -> T:\n    return a\nprint first(1, "two")',
      expect_ok=False, expect_error_substr="type parameter")
check("types.md §3.8", "first<T> conflicting Bool/Float args rejected",
      'fn first<T>(a: T, b: T) -> T:\n    return a\nprint first(true, 1.5)',
      expect_ok=False, expect_error_substr="type parameter")
check("types.md §3.8", "generic body treats T as Any (no false positive)",
      'fn wrap<T>(x: T) -> T:\n    let y = x\n    return y\nprint wrap(5)', "5")

# ---- §3.6 static checks: argument type & return type ----
check("types.md §3.6 #2", "wrong argument type rejected statically",
      'fn sq(x: Int) -> Int:\n    return x * x\nprint sq("hi")', expect_ok=False, expect_error_substr="argument 1")
check("types.md §3.6 #2", "correct argument type accepted",
      'fn sq(x: Int) -> Int:\n    return x * x\nprint sq(4)', "16")
check("types.md §3.6 #3", "wrong return type rejected statically",
      'fn f() -> Int:\n    return "no"', expect_ok=False, expect_error_substr="returns a Str")
check("types.md §3.6", "unannotated (Any) param accepts anything",
      'fn f(x):\n    return x\nprint f(5)\nprint f("s")', "5\ns")
check("types.md §3.6", "static errors prevent ANY execution (nothing runs)",
      'print "before"\nlet x: Int = "oops"', expect_ok=False)

# ---- §3.6.1 Runtime coercion & operator rules ----
check("types.md §3.6.1", "+ coerces to string when either side is Str", 'print "n=" + 5', "n=5")
check("types.md §3.6.1", "+ coerces string on the left too", 'print 5 + " apples"', "5 apples")
check("types.md §3.6.1", "Int + Float promotes to Float", 'print 1 + 2.5', "3.5")
check("types.md §3.6.1", "/ always produces Float even for two Ints", 'print 7 / 2', "3.5")
check("types.md §3.6.1", "/ on evenly-divisible Ints is still Float", 'print 10 / 2', "5.0")
check("types.md §3.6.1", "% follows divisor's sign convention", 'print -7 % 2', "1")
check("types.md §3.6.1", "- on incompatible types raises TypeError", 'print "a" - 1', expect_ok=False, expect_error_substr="TypeError")
check("types.md §3.6.1", "* on List+Int performs repetition (real behavior, not an error)",
      'print [1] * 2', "[1, 1]")
check("types.md §3.6.1", "* on Str+Int performs repetition", 'print "ab" * 3', "ababab")
check("types.md §3.6.1", "+ on two Lists concatenates", 'print [1] + [2]', "[1, 2]")
check("types.md §3.6.1", "- on two Lists is a TypeError (no sequence meaning for -)",
      'print [1,2,3] - [1]', expect_ok=False, expect_error_substr="TypeError")
check("types.md §3.6.1", "Bool participates in + as 0/1 (Bool is a numeric subtype at host level)",
      'print true + 1', "2")
check("types.md §3.6.1", "Bool == Int compares numerically", 'print true == 1', "true")
check("types.md §3.6.1", "< across incompatible types raises TypeError", 'print 1 < "a"', expect_ok=False, expect_error_substr="TypeError")
check("types.md §3.6.1", "== across incompatible types never raises (just false)", 'print 1 == "a"', "false")
check("types.md §3.6.1", "!= across incompatible types never raises", 'print 1 != "a"', "true")
check("types.md §3.6.1", "< on strings uses lexicographic order", 'print "a" < "b"', "true")

# ---- Phase 2 fix: static inference now matches runtime for / and + exactly (was a real bug) ----
check("types.md §3.5", "let: Float = Int/Int division is correctly accepted (was wrongly rejected before Phase 2)",
      'let x: Float = 1 / 2\nprint x', "0.5")
check("types.md §3.5", "let: Str = Str+Int is correctly accepted (coercion-aware inference)",
      'let x: Str = "n=" + 5\nprint x', "n=5")
check("types.md §3.5", "let: Int = Int/Int would now be correctly REJECTED (division is never Int)",
      'let x: Int = 4 / 2', expect_ok=False, expect_error_substr="TypeError")
check("types.md §3.5", "generic fn returning a division result infers Float correctly through the call",
      'fn half(n: Int) -> Float:\n    return n / 2\nprint half(10)', "5.0")

print_final_summary()
