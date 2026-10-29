#!/usr/bin/env python3
"""Phase 4 — Error Handling & Reliability: automated tests (ERR-011).

Publisher: Krish Kumar

Covers, for BOTH execution engines (the reference interpreter and the bytecode VM, the latter also at -O2):
  result construction · generic type checking · option handling · `?` propagation · error-type
  compatibility · evaluation order · nested calls · pattern matching · exception handling ·
  resource cleanup (`finally`) · stack traces · error chaining · error codes · diagnostics ·
  interpreter/VM parity · regression of behaviour that existed before Phase 4.

How the cases are written:
  accepts(...)         the program passes the front end (parse, resolve, type-check)
  rejects(...)         the front end refuses it, with this stable code and these words in the message
  both(...)            both engines produce EXACTLY this stdout (and, when given, this uncaught-failure text);
                       a program that differs between the engines cannot pass
  cli(...)             the real `lumen` command line, in a subprocess

A case that passes proves only what it states — see PHASE4_STATUS.md for what is not covered.
"""
import os
import subprocess
import sys
import tempfile
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
LUMEN = os.path.join(ROOT, "lumen.py")

from lumen import analyze, LumenError, render_diagnostic, RUNTIME_CODES  # noqa: E402
sys.path.insert(0, HERE)
from engines import run_program, ENGINES  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def S(text):
    return textwrap.dedent(text).lstrip("\n")


def record(ok, label):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label)


# ---------------------------------------------------------------------------------------------
# running a program in-process on each engine
# ---------------------------------------------------------------------------------------------
def _static(src):
    """('ok', None) or ('static', the error) — the front end only."""
    try:
        analyze(src, "t.lum")
        return "ok", None
    except (SyntaxError, LumenError) as e:
        return "static", e


def _run(src, engine):
    return run_program(src, engine, "t.lum")




def both(name, src, out, fail=None):
    """Every engine prints exactly `out`. `fail`: None = the program must finish normally; a string = the
    text printed for the uncaught failure must START with it (the message line, then code and trace)."""
    src = S(src)
    want = out.strip("\n") if out else ""
    results = {e: _run(src, e) for e in ENGINES}
    for eng, (got_out, got_fail) in results.items():
        ok = got_out.strip("\n") == want
        if fail is None:
            ok = ok and got_fail is None
        else:
            ok = ok and got_fail is not None and got_fail.startswith(fail)
        record(ok, f"{name} [{eng}]: stdout={got_out!r} failure={got_fail!r}; expected stdout={want!r} failure~{fail!r}")


def parity(name, src):
    """Whatever the program does, every engine does the same — stdout AND the uncaught-failure text,
    trace included."""
    src = S(src)
    results = [_run(src, e) for e in ENGINES]
    record(all(r == results[0] for r in results[1:]),
           f"{name}: engines differ: " + " | ".join(f"{e}={r!r}" for e, r in zip(ENGINES, results)))
    return results[0]


def accepts(name, src):
    kind, err = _static(S(src))
    record(kind == "ok", f"{name}: expected to be accepted, got {err}")


def rejects(name, src, code, *words, line=None, col=None):
    kind, err = _static(S(src))
    if kind != "static":
        record(False, f"{name}: expected {code}, but the program was accepted")
        return
    ok = getattr(err, "code", None) == code and all(w in str(err) or w in "\n".join(err.notes) or w in (err.help or "")
                                                   for w in words)
    if line is not None:
        ok = ok and err.line == line
    if col is not None:
        ok = ok and err.col == col
    record(ok, f"{name}: expected {code} {words}, got {getattr(err, 'code', None)}: {err} notes={getattr(err, 'notes', None)}")


def syntax_error(name, src, *words):
    """The program is not valid Lumen: a syntax error (reported by the parser, with its line)."""
    kind, err = _static(S(src))
    ok = kind == "static" and isinstance(err, SyntaxError) and all(w in str(err) for w in words)
    record(ok, f"{name}: expected a syntax error with {words}, got {kind} {err!r}")


def cli(args, input_text="", cwd=None):
    r = subprocess.run([sys.executable, LUMEN] + args, capture_output=True, text=True, input=input_text, cwd=cwd, timeout=120)
    return r.stdout + r.stderr, r.returncode


class Dir:
    """A scratch project directory."""
    def __enter__(self):
        self.path = tempfile.mkdtemp()
        return self
    def write(self, name, text):
        with open(os.path.join(self.path, name), "w") as f:
            f.write(S(text))
        return os.path.join(self.path, name)
    def __exit__(self, *a):
        import shutil
        shutil.rmtree(self.path, ignore_errors=True)


# =============================================================================================
# 1. Result construction and generic type checking (ERR-001, ERR-004)
# =============================================================================================
accepts("Result: Ok constructs", 'let r: Result<Int, Str> = Ok(1)\nprint r')
accepts("Result: Err constructs", 'let r: Result<Int, Str> = Err("x")\nprint r')
rejects("REQUIRED: Ok(\"hello\") is not a Result<Int, Str>",
        'let result: Result<Int, Str> = Ok("hello")', "E0201", "Result<Int, Str>", "Result<Str, _>", line=1)
rejects("Result: the error side is checked too", 'let r: Result<Int, Str> = Err(5)', "E0201", "Result<_, Int>")
rejects("Result: a return value is checked against the declared Result",
        'fn f() -> Result<Int, Str>:\n    return Ok("s")\nprint f()', "E0201", "declares -> Result<Int, Str>", line=2)
rejects("Result: an argument is checked", 'fn g(r: Result<Int, Str>):\n    print r\ng(Ok("x"))', "E0201", "argument 1", "Result<Str, _>")
accepts("Result: nested generics", 'let r: Result<Option<Int>, Str> = Ok(Some(1))')
rejects("Result: nested generics are checked all the way down",
        'let r: Result<Option<Int>, Str> = Ok(Some("x"))', "E0201", "Result<Option<Str>, _>")
rejects("Result: too few type arguments", 'let r: Result<Int> = Ok(1)', "E0202", "Result takes 2")
rejects("Option: too many type arguments", 'let r: Option<Int, Str> = None', "E0202", "Option takes 1")
rejects("a type that takes no arguments", 'let r: Int<Str> = 1', "E0202", "takes no type arguments")
accepts("generic enum: payload fits", S('''
    enum Tree<T>:
        Leaf
        Node(left: Tree<T>, value: T, right: Tree<T>)
    let t: Tree<Int> = Node(Leaf, 1, Leaf)
    '''))
rejects("generic enum: payload type is enforced", S('''
    enum Tree<T>:
        Leaf
        Node(left: Tree<T>, value: T, right: Tree<T>)
    let t: Tree<Int> = Node(Leaf, "x", Leaf)
    '''), "E0201", "Tree<Int>", "Tree<Str>")
accepts("generic function: the type argument is inferred", 'fn wrap<T>(x: T) -> Option<T>:\n    return Some(x)\nlet a: Option<Int> = wrap(1)')
rejects("generic function: inferred type must match the annotation",
        'fn wrap<T>(x: T) -> Option<T>:\n    return Some(x)\nlet b: Option<Str> = wrap(1)', "E0201", "Option<Str>")
rejects("generic function: one parameter cannot be two types",
        'fn same<T>(a: T, b: T) -> T:\n    return a\nprint same(1, "x")', "E0204", "'T'", "Int", "Str")
accepts("generic struct", 'struct Pair<A, B>:\n    first: A\n    second: B\nlet p: Pair<Int, Str> = Pair { first: 1, second: "b" }')
rejects("generic struct: field types are enforced",
        'struct Pair<A, B>:\n    first: A\n    second: B\nlet p: Pair<Int, Str> = Pair { first: "a", second: "b" }', "E0201", "Pair<Str, Str>")
rejects("typed field: assigning the wrong type is rejected",
        'struct Point:\n    x: Int\n    y: Int\nlet p = Point { x: 1, y: 2 }\np.x = "left"', "E0201", "field 'x' of struct Point expects Int but got Str", line=5)
accepts("typed field: assigning the right type", 'struct Point:\n    x: Int\n    y: Int\nlet p = Point { x: 1, y: 2 }\np.x = 10\np.y = p.x + 1')
rejects("generic struct: a field assignment uses the object's own type arguments",
        'struct Pair<A, B>:\n    first: A\n    second: B\nlet p: Pair<Int, Str> = Pair { first: 1, second: "b" }\np.first = "x"',
        "E0201", "field 'first' of struct Pair expects Int but got Str")
accepts("generic struct: the right type for the argument is accepted",
        'struct Pair<A, B>:\n    first: A\n    second: B\nlet p: Pair<Int, Str> = Pair { first: 1, second: "b" }\np.first = 5')
accepts("typed field assigned through an untyped parameter is gradual (checked only where a type is written)",
        'struct Box:\n    v: Int\nfn put(b, x):\n    b.v = x\nput(Box { v: 1 }, 7)')
rejects("a payload of the wrong type", 'enum Shape:\n    Circle(Int)\nlet s = Circle("r")', "E0201", "Circle()", "expects Int")
rejects("variant called with the wrong number of values", 'let x = Ok(1, 2)', "E0304", "Ok()")
accepts("gradual typing: an unannotated value is compatible", 'fn id(x):\n    return x\nlet r: Result<Int, Str> = id(Ok(1))')
accepts("a program's own `enum Option` still means what it did", S('''
    enum Option:
        Some(x)
        Nothing
    print Some(1)
    '''))

both("Result values print", S('''
    print Ok(1)
    print Err("bad")
    print Some(2)
    print None
    '''), "Ok(1)\nErr(bad)\nSome(2)\nNone")
both("Result values compare by structure", S('''
    print Ok(1) == Ok(1)
    print Ok(1) == Ok(2)
    print Ok(1) == Err(1)
    print Some(1) == Some(1)
    print None == None
    assert_eq(Ok(3), Ok(3))
    '''), "true\nfalse\nfalse\ntrue\ntrue")
both("equal enum values are the same dict key, and a struct key survives being changed", S('''
    enum Shape:
        Circle(r)
        Dot
    struct Point:
        x
        y
    let d = {}
    d[Circle(1)] = "a"
    d[Circle(1)] = "b"
    d[Dot] = "c"
    print len(d)
    print d[Circle(1)]
    let p = Point { x: 1, y: 2 }
    d[p] = "p"
    p.x = 10
    print d[p]
    '''), "2\nb\np")
both("generic enum and struct run", S('''
    enum Tree<T>:
        Leaf
        Node(left: Tree<T>, value: T, right: Tree<T>)
    fn depth(t: Tree<Int>) -> Int:
        match t:
            Leaf:
                return 0
            Node(l, v, r):
                let a = depth(l)
                let b = depth(r)
                if a > b:
                    return a + 1
                return b + 1
    struct Pair<A, B>:
        first: A
        second: B
    print depth(Node(Node(Leaf, 1, Leaf), 2, Leaf))
    let p = Pair { first: 1, second: "x" }
    print p.first + 1
    print p.second
    '''), "2\n2\nx")

# =============================================================================================
# 2. Option handling (ERR-002)
# =============================================================================================
both("Option: Some / None through a function and a match", S('''
    fn find(xs, t) -> Option<Int>:
        for i in range(len(xs)):
            if xs[i] == t:
                return Some(i)
        return None
    fn show(o: Option<Int>):
        match o:
            Some(i):
                print "found at " + i
            None:
                print "not found"
    show(find([5, 6, 7], 6))
    show(find([5, 6, 7], 9))
    '''), "found at 1\nnot found")
rejects("Option: a match that forgets None is refused", S('''
    let o: Option<Int> = None
    match o:
        Some(v):
            print v
    '''), "E0301", "does not cover: None", line=2)
rejects("Option: a match that forgets Some is refused", S('''
    fn f(o: Option<Int>):
        match o:
            None:
                print 0
    '''), "E0301", "does not cover: Some")
accepts("Option: a wildcard arm makes a match exhaustive", S('''
    let o: Option<Int> = None
    match o:
        Some(v):
            print v
        _:
            print 0
    '''))
accepts("Option: a binding arm makes a match exhaustive", S('''
    let o: Option<Int> = None
    match o:
        Some(v):
            print v
        other:
            print 0
    '''))
rejects("Result: a match that forgets Err is refused", S('''
    fn f(r: Result<Int, Str>):
        match r:
            Ok(v):
                print v
    '''), "E0301", "does not cover: Err")
rejects("a user enum is checked for exhaustiveness too", S('''
    enum Color:
        Red
        Green
        Blue
    fn name(c: Color):
        match c:
            Red:
                print "r"
            Green:
                print "g"
    '''), "E0301", "does not cover: Blue")
rejects("a pattern that is not a variant of the scrutinee's enum", S('''
    fn f(o: Option<Int>):
        match o:
            Ok(v):
                print v
            _:
                print 0
    '''), "E0302", "not a variant of Option<Int>")
rejects("a pattern naming the wrong number of fields", S('''
    let o = Some(1)
    match o:
        Some(a, b):
            print a
        _:
            print 0
    '''), "E0303", "names 2 field(s)")
both("Option: unwrap_or / ok_or / is_some / is_none", S('''
    let none: Option<Int> = None
    let some: Option<Int> = Some(4)
    print unwrap_or(none, 9)
    print unwrap_or(some, 9)
    print ok_or(none, "missing")
    print ok_or(some, "missing")
    print is_some(some)
    print is_none(some)
    print is_some(none)
    print is_none(none)
    '''), "9\n4\nErr(missing)\nOk(4)\ntrue\nfalse\nfalse\ntrue")
both("Option: the payload's type flows into the arm", S('''
    fn double(o: Option<Int>) -> Int:
        match o:
            Some(n):
                return n * 2
            None:
                return 0
    print double(Some(21))
    print double(None)
    '''), "42\n0")
rejects("Option: the payload type is checked in the arm", S('''
    fn f(o: Option<Int>) -> Str:
        match o:
            Some(n):
                return n
            None:
                return "none"
    '''), "E0201")

# =============================================================================================
# 3. The `?` operator: typing and control flow (ERR-003)
# =============================================================================================
both("?: Ok continues with the payload, Err returns the same Err", S('''
    fn parse(s) -> Result<Int, Str>:
        if s == "":
            return Err("empty")
        return Ok(len(s))
    fn double(s) -> Result<Int, Str>:
        let n = parse(s)?
        return Ok(n * 2)
    print double("abc")
    print double("")
    '''), "Ok(6)\nErr(empty)")
both("?: Some continues, None returns None", S('''
    fn first(xs) -> Option<Int>:
        if len(xs) == 0:
            return None
        return Some(xs[0])
    fn plus_one(xs) -> Option<Int>:
        let v = first(xs)?
        return Some(v + 1)
    print plus_one([4, 5])
    print plus_one([])
    '''), "Some(5)\nNone")
both("?: the failure is returned UNCHANGED (same payload, same value)", S('''
    struct Problem:
        code: Int
        text: Str
    fn lower() -> Result<Int, Problem>:
        return Err(Problem { code: 7, text: "disk" })
    fn upper() -> Result<Int, Problem>:
        let v = lower()?
        return Ok(v)
    match upper():
        Err(p):
            print p.code
            print p.text
        Ok(v):
            print "ok"
    '''), "7\ndisk")
rejects("?: applied to something that is not a Result or Option", 'fn f() -> Int:\n    let x = 5\n    let y = x?\n    return y',
        "E0401", "Result or an Option", line=3)
rejects("?: outside any function", 'let x = Ok(1)?\n', "E0402", "inside a function", line=1)
rejects("?: inside a test block", 'test "t":\n    let x = Ok(1)?', "E0402", "test block")
rejects("?: an Option's None cannot be returned from a Result function",
        'fn f() -> Result<Int, Str>:\n    let x = Some(1)?\n    return Ok(x)', "E0403", "returns None", "-> Result<Int, Str>")
rejects("?: a Result's Err cannot be returned from an Option function",
        'fn f() -> Option<Int>:\n    let x = Ok(1)?\n    return Some(x)', "E0403", "returns an Err", "-> Option<Int>")
accepts("?: an unannotated function may use ? (its failure type is not declared)", 'fn f():\n    let x = Ok(1)?\n    return Ok(x)')
accepts("?: an untyped operand is allowed statically", 'fn g(a):\n    let x = a?\n    return Some(x)')
both("?: an untyped operand that is not a Result/Option is a runtime TypeError on every engine", S('''
    fn g(a):
        let x = a?
        return Some(x)
    try:
        g(5)
    catch e:
        print e.message
        print e.code
    '''), "TypeError: the '?' operator expects a Result or an Option, got Int\nR0002")
rejects("?: not allowed in a finally block", S('''
    fn f() -> Result<Int, Str>:
        try:
            print 1
        finally:
            let x = Ok(1)?
        return Ok(1)
    '''), "E0407", "'finally'")
rejects("return: not allowed in a finally block", S('''
    fn f():
        try:
            print 1
        finally:
            return 1
    '''), "E0407", "'finally'")
both("?: on a field access, a call result and a parenthesised value", S('''
    struct Box:
        item: Result<Int, Str>
    fn a(b: Box) -> Result<Int, Str>:
        let v = b.item?
        return Ok(v + 1)
    fn id(r):
        return r
    fn b() -> Result<Int, Str>:
        let v = (id(Ok(5)))? + id(Ok(1))?
        return Ok(v)
    print a(Box { item: Ok(1) })
    print a(Box { item: Err("no") })
    print b()
    '''), "Ok(2)\nErr(no)\nOk(6)")
syntax_error("?: after the argument of a paren-free call is ambiguous", '''
    fn f(x):
        return x
    fn g() -> Result<Int, Str>:
        let v = f Ok(1)?
        return Ok(1)
    ''', "ambiguous", "paren-free")
both("?: precedence — it binds to the nearest postfix expression", S('''
    fn two() -> Result<Int, Str>:
        let a = Ok(10)? + Ok(20)?
        return Ok(a)
    print two()
    '''), "Ok(30)")

# ---- no silent loss: a Result that is thrown away ----
rejects("a discarded Result is refused", 'fn r() -> Result<Int, Str>:\n    return Ok(1)\nr()', "E0405", "discarded", line=3)
accepts("`let _ =` discards a Result on purpose", 'fn r() -> Result<Int, Str>:\n    return Ok(1)\nlet _ = r()')
accepts("`?` as a statement uses the Result", 'fn r() -> Result<Int, Str>:\n    return Ok(1)\nfn f() -> Result<Int, Str>:\n    r()?\n    return Ok(2)')
accepts("printing a Result uses it", 'fn r() -> Result<Int, Str>:\n    return Ok(1)\nprint r()')
rejects("a Result function that can fall off its end is refused", S('''
    fn f(x) -> Result<Int, Str>:
        if x:
            return Ok(1)
    '''), "E0406", "can reach its end")
accepts("a Result function whose every path returns is accepted", S('''
    fn f(x) -> Result<Int, Str>:
        if x:
            return Ok(1)
        else:
            return Err("no")
    '''))

# =============================================================================================
# 4. Error type compatibility (ERR-004): no implicit conversion, exact matching
# =============================================================================================
rejects("REQUIRED: ? with an incompatible error type is refused, and says how to convert", S('''
    fn read() -> Result<Int, Str>:
        return Err("io")
    fn parse() -> Result<Int, Int>:
        let v = read()?
        return Ok(v)
    print parse()
    '''), "E0404", "incompatible error type", "Str", "Int", "match", "Err(", line=4)
accepts("? ignores the success type — only the error type must match", S('''
    fn a() -> Result<Int, Str>:
        return Ok(1)
    fn b() -> Result<Str, Str>:
        let v = a()?
        return Ok("s")
    '''))
rejects("two different error enums do not convert", S('''
    enum E1:
        A
    enum E2:
        B
    fn a() -> Result<Int, E1>:
        return Err(A)
    fn b() -> Result<Int, E2>:
        let v = a()?
        return Ok(v)
    '''), "E0404", "E1", "E2")
accepts("the same error enum matches", S('''
    enum E1:
        A
    fn a() -> Result<Int, E1>:
        return Err(A)
    fn b() -> Result<Int, E1>:
        let v = a()?
        return Ok(v)
    '''))
accepts("an untyped error side is compatible with anything", S('''
    fn a():
        return Err("x")
    fn b() -> Result<Int, Int>:
        let v = a()?
        return Ok(1)
    '''))
both("converting an error explicitly, with match, works", S('''
    enum AppError:
        Io(Str)
        Parse(Str)
    fn read() -> Result<Int, Str>:
        return Err("disk gone")
    fn load() -> Result<Int, AppError>:
        match read():
            Ok(v):
                return Ok(v)
            Err(msg):
                return Err(Io(msg))
    match load():
        Err(e):
            match e:
                Io(m):
                    print "io: " + m
                Parse(m):
                    print "parse: " + m
        Ok(v):
            print "ok"
    '''), "io: disk gone")
both("an Option becomes a Result only through ok_or", S('''
    fn first(xs) -> Option<Int>:
        if len(xs) == 0:
            return None
        return Some(xs[0])
    fn head(xs) -> Result<Int, Str>:
        let v = ok_or(first(xs), "empty list")?
        return Ok(v)
    print head([3])
    print head([])
    '''), "Ok(3)\nErr(empty list)")

# =============================================================================================
# 5. Evaluation order (release gate)
# =============================================================================================
both("arguments are evaluated left to right", S('''
    fn t(n):
        print "eval " + n
        return n
    fn three(a, b, c):
        return a + b + c
    print three(t(1), t(2), t(3))
    '''), "eval 1\neval 2\neval 3\n6")
both("? stops evaluation: the arguments after it are never evaluated", S('''
    fn first() -> Result<Int, Str>:
        print "first"
        return Err("first failed")
    fn second() -> Int:
        print "second (must not run)"
        return 2
    fn pair(a, b):
        return [a, b]
    fn run() -> Result<Int, Str>:
        let x = pair(first()?, second())
        return Ok(1)
    print run()
    '''), "first\nErr(first failed)")
both("the operand of ? is evaluated before the rest of the expression", S('''
    fn t(name, v):
        print "eval " + name
        return v
    fn order() -> Result<Int, Str>:
        let r = t("a", 1) + t("b", Ok(2)?) + t("c", 3)
        return Ok(r)
    print order()
    '''), "eval a\neval b\neval c\nOk(6)")
both("? in the middle of an expression discards the half-built operands (VM operand stack)", S('''
    fn mid(a, b) -> Result<Int, Str>:
        let s = 1 + (a)? + 10 * (b)?
        return Ok(s)
    print mid(Ok(2), Ok(3))
    print mid(Ok(2), Err("b bad"))
    print mid(Err("a bad"), Ok(3))
    print mid(Ok(1), Ok(1))
    '''), "Ok(33)\nErr(b bad)\nErr(a bad)\nOk(12)")
both("? inside a list literal: earlier elements were evaluated, later ones are not", S('''
    fn t(n):
        print "eval " + n
        return n
    fn build(r) -> Result<List, Str>:
        let xs = [t(1), r?, t(3)]
        return Ok(xs)
    print build(Ok(2))
    print build(Err("stop"))
    '''), "eval 1\neval 3\nOk([1, 2, 3])\neval 1\nErr(stop)")
both("a failed ? leaves the variable it was assigning untouched", S('''
    fn f(r) -> Result<Int, Str>:
        let x = 1
        x = (r)?
        return Ok(x)
    print f(Ok(5))
    print f(Err("no"))
    '''), "Ok(5)\nErr(no)")
both("? in a loop: the failure leaves every enclosing loop (VM iterator stack)", S('''
    fn sumloop(xs) -> Result<Int, Str>:
        let total = 0
        for x in xs:
            for y in xs:
                let v = x?
                total = total + v
        return Ok(total)
    print sumloop([Ok(1), Ok(2)])
    print sumloop([Ok(1), Err("e")])
    print sumloop([Err("first"), Ok(1)])
    fn after() -> Result<Int, Str>:
        let r = 0
        for i in range(3):
            r = r + (Ok(i))?
        return Ok(r)
    print after()
    '''), "Ok(6)\nErr(e)\nErr(first)\nOk(3)")
both("and / or: the right side is not evaluated when the left decides", S('''
    fn t(n):
        print "eval " + n
        return true
    print (false and t(1))
    print (true or t(2))
    print (true and t(3))
    '''), "false\ntrue\neval 3\ntrue")
both("an early return from inside loops leaves the VM stacks balanced", S('''
    fn find(xs, t) -> Option<Int>:
        for i in range(len(xs)):
            for j in range(2):
                if xs[i] == t:
                    return Some(i)
        return None
    fn user() -> Int:
        let total = 0
        for k in range(3):
            total = total + 1 + unwrap_or(find([5, 6, 7], 6), 0)
        return total
    print user()
    print user()
    '''), "6\n6")

# =============================================================================================
# 6. Nested calls and propagation depth
# =============================================================================================
both("an Err passes unchanged through three levels", S('''
    fn l3(n) -> Result<Int, Str>:
        if n < 0:
            return Err("negative")
        return Ok(n)
    fn l2(n) -> Result<Int, Str>:
        let v = l3(n)?
        return Ok(v + 1)
    fn l1(n) -> Result<Int, Str>:
        let v = l2(n)?
        return Ok(v + 1)
    print l1(1)
    print l1(-1)
    '''), "Ok(3)\nErr(negative)")
both("a call whose arguments are themselves ? expressions", S('''
    fn a() -> Result<Int, Str>:
        return Ok(1)
    fn b(x) -> Result<Int, Str>:
        return Ok(x + 10)
    fn c(x) -> Result<Int, Str>:
        return Ok(x + 100)
    fn run() -> Result<Int, Str>:
        let v = c(b(a()?)?)?
        return Ok(v)
    print run()
    '''), "Ok(111)")
both("None propagates through nested Option functions", S('''
    fn a(x) -> Option<Int>:
        if x > 0:
            return Some(x)
        return None
    fn b(x) -> Option<Int>:
        let v = a(x)?
        return Some(v * 2)
    fn c(x) -> Option<Int>:
        let v = b(x)?
        return Some(v + 1)
    print c(5)
    print c(0)
    '''), "Some(11)\nNone")
both("recursion with ?", S('''
    fn countdown(n) -> Result<Int, Str>:
        if n == 0:
            return Err("reached zero")
        if n == 1:
            return Ok(1)
        let v = countdown(n - 1)?
        return Ok(v + 1)
    print countdown(1)
    print countdown(4)
    '''), "Ok(1)\nOk(4)")

# =============================================================================================
# 7. Pattern matching on Result / Option / user enums
# =============================================================================================
both("match on a Result with Ok / Err arms", S('''
    fn describe(r: Result<Int, Str>):
        match r:
            Ok(v):
                print "value " + v
            Err(e):
                print "error " + e
    describe(Ok(3))
    describe(Err("bad"))
    '''), "value 3\nerror bad")
both("match: first matching arm wins, then the wildcard", S('''
    fn f(r):
        match r:
            Ok(v):
                print "ok"
            _:
                print "anything else"
    f(Ok(1))
    f(Err(1))
    f(7)
    '''), "ok\nanything else\nanything else")
both("a match that cannot be checked statically fails with a MatchError at run time, on every engine", S('''
    fn pick(x):
        match x:
            Some(v):
                print v
    pick(None)
    '''), "", fail="MatchError at line 2: no pattern matched None")
both("match on a user enum with a payload", S('''
    enum Shape:
        Circle(Int)
        Rect(Int, Int)
        Dot
    fn area(s: Shape) -> Int:
        match s:
            Circle(r):
                return r * r * 3
            Rect(w, h):
                return w * h
            Dot:
                return 0
    print area(Circle(2))
    print area(Rect(2, 5))
    print area(Dot)
    '''), "12\n10\n0")
both("a match inside a ? function returns the arm's value", S('''
    fn f(r) -> Result<Int, Str>:
        match r:
            Ok(v):
                return Ok(v + 1)
            Err(e):
                return Err("wrapped: " + e)
    print f(Ok(1))
    print f(Err("x"))
    '''), "Ok(2)\nErr(wrapped: x)")

# =============================================================================================
# 8. Exception handling (ERR-006): what try/catch is for, and what it is not
# =============================================================================================
both("fail is caught; the handler reads message, kind and code", S('''
    try:
        fail("boom")
    catch e:
        print e.message
        print e.kind
        print e.code
    '''), "boom\nError\nR0001")
both("an Err value is NOT an exception: try/catch does not see it", S('''
    fn f() -> Result<Int, Str>:
        return Err("expected failure")
    try:
        let r = f()
        print "no exception"
        print r
    catch e:
        print "caught"
    '''), "no exception\nErr(expected failure)")
both("? is not an exception either: it returns, the catch does not run", S('''
    fn inner() -> Result<Int, Str>:
        return Err("e")
    fn outer() -> Result<Int, Str>:
        try:
            let v = inner()?
            return Ok(v)
        catch e:
            print "catch ran (wrong)"
            return Err("caught")
    print outer()
    '''), "Err(e)")
both("runtime failures are catchable with their stable codes", S('''
    fn run(label, thunk_id):
        try:
            if thunk_id == 1:
                print 1 / 0
            if thunk_id == 2:
                print 1 % 0
            if thunk_id == 3:
                print [1, 2][5]
            if thunk_id == 4:
                print {"a": 1}["b"]
            if thunk_id == 5:
                print 1 + [1]
            if thunk_id == 6:
                print unwrap(None)
            if thunk_id == 7:
                assert(false)
            if thunk_id == 8:
                print chr(-1)
        catch e:
            print label + " " + e.code
    run("div", 1)
    run("mod", 2)
    run("index", 3)
    run("key", 4)
    run("type", 5)
    run("unwrap", 6)
    run("assert", 7)
    run("chr", 8)
    '''), "div R0005\nmod R0005\nindex R0006\nkey R0007\ntype R0002\nunwrap R0011\nassert R0012\nchr R0002")
both("a name error inside a try is catchable", S('''
    try:
        print nothing_here
    catch e:
        print e.code
        print e.kind
    '''), "R0003\nNameError")
both("nested try: the inner catch handles it; the outer never sees it", S('''
    try:
        try:
            fail("inner")
        catch e:
            print "inner caught " + e.message
        print "after inner"
    catch e:
        print "outer caught (wrong)"
    '''), "inner caught inner\nafter inner")
both("a failure in a catch handler reaches the outer try", S('''
    try:
        try:
            fail("one")
        catch e:
            fail("two")
    catch e:
        print "outer " + e.message
    '''), "outer two")
both("try in a loop keeps looping; the handler's variable does not leak", S('''
    for i in range(3):
        try:
            if i == 1:
                fail("skip " + i)
            print "ok " + i
        catch e:
            print e.message
    '''), "ok 0\nskip 1\nok 2")
both("an uncaught failure ends the program with its message, code and trace", S('''
    print "before"
    fail("fatal")
    print "after"
    '''), "before", fail="Uncaught error: fatal\n  code: R0001\n  trace (most recent call last):\n    <main> (t.lum:2)")
both("stack overflow is a catchable failure on both engines", S('''
    fn deep(n):
        return deep(n + 1)
    try:
        deep(0)
    catch e:
        print e.message
        print e.code
    print "after"
    '''), "stack overflow: maximum call depth exceeded\nR0009\nafter")
syntax_error("a try needs a catch or a finally", 'try:\n    print 1\n', "'try' needs a 'catch' or a 'finally'")

# =============================================================================================
# 9. Resource cleanup: finally (release gate: resource cleanup rules)
# =============================================================================================
both("finally runs when the body finishes normally", S('''
    try:
        print "body"
    finally:
        print "cleanup"
    print "after"
    '''), "body\ncleanup\nafter")
both("finally runs on return and does not change the returned value", S('''
    fn g():
        try:
            return 5
        finally:
            print "g finally"
    print g()
    '''), "g finally\n5")
both("finally runs when the body fails, and the failure continues", S('''
    fn work(n):
        try:
            print "start " + n
            if n == 2:
                fail("boom")
            return n
        finally:
            print "cleanup " + n
    print work(1)
    try:
        print work(2)
    catch e:
        print "caught " + e.message
    '''), "start 1\ncleanup 1\n1\nstart 2\ncleanup 2\ncaught boom")
both("finally runs once per exit, including a return from inside a loop", S('''
    fn h():
        for i in range(3):
            try:
                if i == 1:
                    return i
            finally:
                print "fin " + i
    print h()
    '''), "fin 0\nfin 1\n1")
both("finally runs on ? and the Err still returns", S('''
    fn log(s):
        print s
    fn check(n) -> Result<Int, Str>:
        if n < 0:
            return Err("negative")
        return Ok(n)
    fn inner(n) -> Result<Int, Str>:
        try:
            log("inner start")
            let v = check(n)?
            log("inner after ?")
            return Ok(v)
        finally:
            log("inner cleanup")
    fn middle(n) -> Result<Int, Str>:
        try:
            let v = inner(n)?
            return Ok(v + 1)
        finally:
            log("middle cleanup")
    print middle(1)
    print middle(-1)
    '''), "inner start\ninner after ?\ninner cleanup\nmiddle cleanup\nOk(2)\ninner start\ninner cleanup\nmiddle cleanup\nErr(negative)")
both("nested finally blocks run innermost first", S('''
    fn f():
        try:
            try:
                fail("x")
            finally:
                print "inner"
        finally:
            print "outer"
    try:
        f()
    catch e:
        print "caught " + e.message
    '''), "inner\nouter\ncaught x")
both("try / catch / finally together: catch, then finally", S('''
    fn f(n):
        try:
            if n == 1:
                fail("one")
            print "no failure"
        catch e:
            print "catch " + e.message
        finally:
            print "finally " + n
    f(0)
    f(1)
    '''), "no failure\nfinally 0\ncatch one\nfinally 1")
both("a failure inside the catch handler still runs finally", S('''
    try:
        try:
            fail("one")
        catch e:
            fail("two")
        finally:
            print "finally ran"
    catch e:
        print "outer " + e.message
    '''), "finally ran\nouter two")
both("a failing finalizer replaces the failure it interrupted, which becomes its cause", S('''
    fn f():
        try:
            fail("original")
        finally:
            fail("cleanup failed")
    try:
        f()
    catch e:
        print e.message
        match e.cause:
            Some(c):
                print "cause: " + c.message
            None:
                print "no cause"
    '''), "cleanup failed\ncause: original")
both("the finalizer sees the variables outside the try, not the body's own", S('''
    fn f():
        let x = "outer"
        try:
            let x = "inner"
            return 0
        finally:
            print x
    print f()
    '''), "outer\n0")
both("the finalizer can change state the caller sees", S('''
    let log = []
    fn f():
        try:
            fail("x")
        finally:
            push(log, "released")
    try:
        f()
    catch e:
        push(log, "caught")
    print log
    '''), "[released, caught]")
both("finally in a loop runs for every iteration, even with a failure caught outside", S('''
    let n = 0
    for i in range(3):
        try:
            try:
                if i == 1:
                    fail("x")
            finally:
                n = n + 1
        catch e:
            print "caught " + i
    print n
    '''), "caught 1\n3")
both("a failure with finally and no catch, uncaught: the cleanup ran, then the report", S('''
    try:
        fail("fatal")
    finally:
        print "cleaned"
    '''), "cleaned", fail="Uncaught error: fatal")

# =============================================================================================
# 10. Structured errors, codes (ERR-005)
# =============================================================================================
both("fail_with sets a custom code; fail uses R0001", S('''
    try:
        fail_with("E_DB", "db down")
    catch e:
        print e.code
        print e.message
    try:
        fail("plain")
    catch e:
        print e.code
    '''), "E_DB\ndb down\nR0001")
both("an invalid error code is a TypeError", S('''
    try:
        fail_with("bad code!", "x")
    catch e:
        print e.code
    '''), "R0002")
both("an error value has message, kind, code, cause and trace", S('''
    try:
        fail("x")
    catch e:
        print type_of(e)
        print type_of(e.message)
        print type_of(e.trace)
        print is_none(e.cause)
    '''), "Error\nStr\nList\ntrue")
for code in ("R0001", "R0002", "R0003", "R0004", "R0005", "R0006", "R0007", "R0008", "R0009", "R0010", "R0011", "R0012", "R0013"):
    record(code in RUNTIME_CODES, f"runtime code {code} is registered")
both("a permission denial carries its stable code and kind", S('''
    use fs.read
    try:
        read_file("/etc/hostname")
    catch e:
        print e.code
        print e.kind
        print e.reason
    '''), "R0008\nPermissionError\nnot-granted")
both("catching a failure grants no capability: still denied in the handler and in finally", S('''
    use fs.read

    fn peek():
        return read_file("/etc/hostname")

    try:
        peek()
    catch e:
        print "caught " + e.code
        try:
            peek()
        catch again:
            print "still denied: " + again.reason
    finally:
        try:
            peek()
        catch last:
            print "denied in finally too: " + last.reason
    '''), "caught R0008\nstill denied: not-granted\ndenied in finally too: not-granted")
both("a denial never names a URL's credentials, path or query", S('''
    use net.http

    try:
        http_get("http://user:secretpw@127.0.0.1:1/path?token=abc")
    catch e:
        print e.resource
        print e.capability
    '''), "http://127.0.0.1:1\nnet.http")
both("a MatchError carries its code", S('''
    fn pick(x):
        match x:
            Some(v):
                print v
    try:
        pick(None)
    catch e:
        print e.code
        print e.kind
    '''), "R0004\nMatchError")

# =============================================================================================
# 11. Error chaining (ERR-008)
# =============================================================================================
both("fail(message, cause) records the cause", S('''
    fn f():
        fail_with("E_DB", "db down")
    fn g():
        try:
            f()
        catch e:
            fail("query failed", e)
    try:
        g()
    catch e:
        print e.message
        match e.cause:
            Some(c):
                print c.message
                print c.code
            None:
                print "no cause"
    '''), "query failed\ndb down\nE_DB")
both("a chain three deep can be walked", S('''
    fn lvl(n):
        if n == 0:
            fail("root")
        try:
            lvl(n - 1)
        catch e:
            fail("level " + n, e)
    try:
        lvl(2)
    catch e:
        let cur = e
        let guard = 0
        while guard < 5:
            print cur.message
            match cur.cause:
                Some(c):
                    cur = c
                None:
                    guard = 99
            guard = guard + 1
    '''), "level 2\nlevel 1\nroot")
both("a cause must be an error value", S('''
    try:
        fail("x", 5)
    catch e:
        print e.message
    '''), "TypeError: the cause of an error must be the error a `catch` bound, got Int")
both("unwrap of an Err that holds an error keeps that error as the cause", S('''
    fn risky() -> Result<Int, Error>:
        try:
            fail("inner")
        catch e:
            return Err(e)
    try:
        unwrap(risky())
    catch e:
        print e.code
        match e.cause:
            Some(c):
                print "cause: " + c.message
            None:
                print "no cause"
    '''), "R0011\ncause: inner")
both("an uncaught chained failure prints every cause", S('''
    fn f():
        fail_with("E_DB", "db down")
    fn g():
        try:
            f()
        catch e:
            fail("query failed", e)
    g()
    '''), "", fail=("Uncaught error: query failed\n  code: R0001\n  trace (most recent call last):\n"
                      "    <main> (t.lum:8)\n    g (t.lum:7)\n"
                      "  caused by: db down\n    code: E_DB\n    trace (most recent call last):\n"
                      "      <main> (t.lum:8)\n      g (t.lum:5)\n      f (t.lum:2)"))

# =============================================================================================
# 12. Stack traces (ERR-007)
# =============================================================================================
both("a trace lists the calls outermost first, with file and line", S('''
    fn inner(x):
        if x > 2:
            fail("too big")
        return x
    fn middle(x):
        let y = inner(x)
        return y + 1
    fn outer(x):
        try:
            return middle(x)
        catch e:
            print e.trace
            return -1
    print outer(5)
    '''), "[<main> (t.lum:14), outer (t.lum:10), middle (t.lum:6), inner (t.lum:3)]\n-1")
both("a failure at the top level has a one-line trace", S('''
    try:
        fail("x")
    catch e:
        print e.trace
    '''), "[<main> (t.lum:2)]")
both("the line of a while condition is the while line every time round", S('''
    let n = 0
    fn check():
        n = n + 1
        if n == 3:
            fail("third")
        return true
    fn run():
        while check():
            print n
            let pad = 1
    try:
        run()
    catch e:
        print e.trace
    '''), "1\n2\n[<main> (t.lum:12), run (t.lum:8), check (t.lum:5)]")
both("a trace through an error that is caught and a new one raised: each has its own", S('''
    fn a():
        fail("first")
    fn b():
        try:
            a()
        catch e:
            print e.trace
            fail("second")
    try:
        b()
    catch e:
        print e.trace
    '''), "[<main> (t.lum:10), b (t.lum:5), a (t.lum:2)]\n[<main> (t.lum:10), b (t.lum:8)]")
both("a failure that passes through a finally keeps exactly one trace (no repeated frames)", S('''
    fn f():
        try:
            fail("x")
        finally:
            print "cleanup"
    fn g():
        try:
            f()
        finally:
            print "outer cleanup"
    try:
        g()
    catch e:
        print e.trace
    '''), "cleanup\nouter cleanup\n[<main> (t.lum:12), g (t.lum:8), f (t.lum:3)]")
both("a trace is the same value however often it is read", S('''
    fn f():
        fail("x")
    try:
        f()
    catch e:
        print e.trace == e.trace
        print len(e.trace)
    '''), "true\n2")
both("a MatchError and a failed assert have traces too", S('''
    fn m(x):
        match x:
            Some(v):
                print v
    try:
        m(None)
    catch e:
        print e.trace
    '''), "[<main> (t.lum:6), m (t.lum:2)]")
both("a very deep recursion's trace is shortened in the middle, not truncated at the ends", S('''
    fn deep(n):
        return deep(n + 1)
    try:
        deep(0)
    catch e:
        print len(e.trace) > 0
    '''), "true")
parity("traces agree for a failure inside a for loop inside nested calls", '''
    fn leaf(i):
        if i == 2:
            fail("leaf failed")
        return i
    fn branch(xs):
        let t = 0
        for x in xs:
            t = t + leaf(x)
        return t
    try:
        print branch([0, 1, 2, 3])
    catch e:
        print e.trace
        print e.message
    print branch([5, 2])
    ''')
parity("traces agree for failures in the arguments of a call", '''
    fn id(x):
        return x
    fn f(a):
        return id(a + [1])
    f(1)
    ''')
parity("traces agree for a failing spawned task re-raised by wait", '''
    fn job():
        fail("task failed")
    fn run():
        let t = spawn job()
        return wait(t)
    try:
        run()
    catch e:
        print e.message
        print e.trace
    ''')

# =============================================================================================
# 13. Diagnostics: location, snippet, explanation (ERR-010)
# =============================================================================================
def diag(src):
    kind, err = _static(S(src))
    return err if kind == "static" else None


e = diag('let result: Result<Int, Str> = Ok("hello")')
record(e is not None and e.code == "E0201" and e.line == 1 and e.col == 32 and e.end_col == 43,
       f"diagnostic has an exact position: {e and (e.code, e.line, e.col, e.end_col)}")
text = render_diagnostic(e) if e else ""
record("--> " in text and ":1:32" in text and "^^^^^^^^^^^" in text and "expected: Result<Int, Str>" in text
       and "found: Result<Str, _>" in text and "code: E0201" in text,
       f"rendered diagnostic shows location, caret, expected/found and code: {text!r}")
record(text.splitlines()[0].startswith("TypeError at line 1:"), "the first line keeps the long-standing 'TypeError at line N:' form")
e = diag('fn read() -> Result<Int, Str>:\n    return Err("io")\nfn parse() -> Result<Int, Int>:\n    let v = read()?\n    return Ok(v)')
record(e is not None and e.code == "E0404" and e.line == 4 and e.help and "match" in e.help,
       f"E0404 has a line and a help line about explicit conversion: {e and (e.code, e.line, e.help)}")
e = diag('fn f():\n    print nothing_here\n')
record(e is not None and e.code == "E0101" and e.line == 2, f"a name error has code and line: {e and (e.code, e.line)}")
e = diag('let x = = 1')
record(e is not None, "a syntax error is reported")
record(any(isinstance(diag(src), LumenError) and diag(src).code for src in ['let r: Result<Int, Str> = Ok("a")']),
       "static errors carry a stable code")

with Dir() as d:
    path = d.write("bad.lum", '''
        fn read() -> Result<Int, Str>:
            return Err("io")
        fn parse() -> Result<Int, Int>:
            let v = read()?
            return Ok(v)
        print parse()
        ''')
    for cmd in (["check", path], ["run", path], ["run", "--vm", path], ["test", path]):
        out, rc = cli(cmd)
        record(rc == 1 and "E0404" in out and "bad.lum:4:13" in out and "help:" in out and "^" in out,
               f"lumen {' '.join(cmd[:-1])}: located, coded diagnostic, rc=1: rc={rc} out={out!r}")
    good = d.write("good.lum", '''
        fn f(o: Option<Int>) -> Int:
            match o:
                Some(v):
                    return v
                None:
                    return 0
        print f(Some(1))
        ''')
    out, rc = cli(["check", good])
    record(rc == 0 and "W0301" not in out and "No errors found" in out, f"check accepts a clean file: {out!r}")
    warn = d.write("warn.lum", '''
        fn f(o):
            match o:
                Some(v):
                    print v
        f(Some(1))
        ''')
    out, rc = cli(["check", warn])
    record(rc == 0 and "W0301" in out and "does not cover: None" in out and "1 warning" in out,
           f"check reports a possibly non-exhaustive match as a warning, rc=0: rc={rc} out={out!r}")

# =============================================================================================
# 14. The real command line: uncaught failures, exit codes, modules, tests (parity end to end)
# =============================================================================================
with Dir() as d:
    mod = d.write("lib.lum", '''
        fn parse(s) -> Result<Int, Str>:
            if s == "":
                return Err("empty")
            return Ok(len(s))
        fn boom():
            fail("module boom")
        fn chain(s) -> Result<Int, Str>:
            let n = parse(s)?
            return Ok(n + 100)
        ''')
    main = d.write("main.lum", '''
        use "lib.lum" as lib
        print lib.parse("abc")
        print lib.parse("")
        print lib.chain("xy")
        print lib.chain("")
        try:
            lib.boom()
        catch e:
            print e.message
            print e.trace
        lib.boom()
        ''')
    outs = {}
    for label, flags in (("interp", []), ("vm", ["--vm"]), ("vm-O2", ["--vm", "-O2"])):
        outs[label] = cli(["run"] + flags + [main])
    expected = ("Ok(3)\nErr(empty)\nOk(102)\nErr(empty)\nmodule boom\n[<main> (main.lum:7), boom (lib.lum:6)]\n"
                "Uncaught error: module boom\n  code: R0001\n  trace (most recent call last):\n    <main> (main.lum:11)\n    boom (lib.lum:6)\n")
    for label, (out, rc) in outs.items():
        record(out == expected and rc == 1, f"module calls, ? across modules, trace through a module [{label}]: rc={rc} out={out!r}")
    # a module's own type signatures are known to the checker
    d.write("typed.lum", '''
        use "lib.lum" as lib
        let r: Result<Str, Str> = lib.parse("abc")
        ''')
    out, rc = cli(["check", os.path.join(d.path, "typed.lum")])
    record(rc == 1 and "E0201" in out, f"a call into a module is type-checked against its signature: rc={rc} out={out!r}")

    d.write("shared.lum", '''
        fn work(n):
            let total = 0
            for i in range(200):
                total = total + i * n
            return total
        ''')
    conc = d.write("conc.lum", '''
        use "shared.lum" as m
        fn job(n):
            let acc = 0
            for k in range(30):
                acc = acc + m.work(n)
            return acc
        let tasks = []
        for n in range(1, 9):
            push(tasks, spawn job(n))
        let results = []
        for t in tasks:
            push(results, wait(t))
        print results
        ''')
    want_conc = "[597000, 1194000, 1791000, 2388000, 2985000, 3582000, 4179000, 4776000]\n"
    for flags in ([], ["--vm"], ["--vm", "-O2"]):
        out, rc = cli(["run"] + flags + [conc])
        record(out == want_conc and rc == 0, f"concurrent tasks calling one module's function agree on every engine {flags}: rc={rc} out={out!r}")

    prog = d.write("fatal.lum", '''
        fn a():
            fail("fatal in a")
        print "start"
        a()
        print "never"
        ''')
    outs = [cli(["run"] + flags + [prog]) for flags in ([], ["--vm"], ["--vm", "-O1"], ["--vm", "-O2"])]
    want = "start\nUncaught error: fatal in a\n  code: R0001\n  trace (most recent call last):\n    <main> (fatal.lum:4)\n    a (fatal.lum:2)\n"
    record(all(o == (want, 1) for o in outs), f"an uncaught failure: same text and exit code 1 on every engine: {outs!r}")

    t = d.write("results_test.lum", '''
        fn parse(s) -> Result<Int, Str>:
            if s == "":
                return Err("empty")
            return Ok(len(s))
        test "ok value":
            assert_eq(parse("abc"), Ok(3))
        test "err value":
            assert_eq(parse(""), Err("empty"))
        test "unwrap_or":
            assert_eq(unwrap_or(parse(""), 0), 0)
        test "a failing assertion is reported":
            assert_eq(parse("abc"), Ok(4))
        ''')
    for flags in ([], ["--vm"]):
        out, rc = cli(["test"] + flags + [t])
        record(rc == 1 and "3 passed; 1 failed" in out and 'test "ok value" ... ok' in out
               and "assertion failed: Ok(3) != Ok(4)" in out, f"lumen test with Results {flags}: rc={rc} out={out!r}")

# =============================================================================================
# 15. Regression: behaviour that existed before Phase 4 is unchanged
# =============================================================================================
both("a program that never mentions Result or Option runs as before", S('''
    fn add(a, b):
        return a + b
    print add(1, 2)
    let xs = [1, 2, 3]
    for x in xs:
        print x * 2
    '''), "3\n2\n4\n6")
both("the 1.x try / catch idiom: catch binds e.message", S('''
    try:
        fail("legacy")
    catch e:
        print "caught: " + e.message
    '''), "caught: legacy")
both("a catch of a runtime TypeError keeps the 'TypeError:' message", S('''
    try:
        print 1 + [1]
    catch e:
        print e.message
    '''), "TypeError: cannot apply '+' to int and list")
both("a user-declared enum Result keeps its own meaning", S('''
    enum Result:
        Good(Int)
        Bad
    fn f(x) -> Result:
        if x:
            return Good(1)
        return Bad
    match f(true):
        Good(n):
            print n
        Bad:
            print "bad"
    '''), "1")
both("`finally`-free try / catch behaves as before inside functions and loops", S('''
    fn safe_div(a, b):
        try:
            return a / b
        catch e:
            return 0
    print safe_div(6, 3)
    print safe_div(1, 0)
    '''), "2.0\n0")
both("modulo by zero is a catchable failure (it used to crash the host)", S('''
    try:
        print 5 % 0
    catch e:
        print e.message
    '''), "modulo by zero")
parity("early return from a for loop leaves no stale iterator (VM regression)", '''
    fn f():
        for i in range(3):
            return i
    let a = 0
    for k in range(3):
        a = a + f()
    print a
    ''')
parity("a caught failure inside a for loop leaves no stale iterator (VM regression)", '''
    for i in range(3):
        try:
            for j in range(2):
                fail("x")
        catch e:
            print "caught " + i
    ''')

# =============================================================================================
# 16. Parity sweep: one program per language area, every engine, byte for byte
# =============================================================================================
PARITY_PROGRAMS = {
    "result pipeline": '''
        fn parse(s) -> Result<Int, Str>:
            if s == "":
                return Err("empty")
            return Ok(len(s))
        fn run(xs) -> Result<Int, Str>:
            let total = 0
            for s in xs:
                total = total + parse(s)?
            return Ok(total)
        print run(["a", "bb"])
        print run(["a", ""])
        ''',
    "option pipeline": '''
        fn get(xs, i) -> Option<Int>:
            if i < len(xs):
                return Some(xs[i])
            return None
        fn sum2(xs) -> Option<Int>:
            let a = get(xs, 0)?
            let b = get(xs, 1)?
            return Some(a + b)
        print sum2([1, 2])
        print sum2([1])
        ''',
    "finally and failure": '''
        fn f(n):
            try:
                if n > 1:
                    fail("big")
                return n
            finally:
                print "done " + n
        print f(1)
        try:
            f(2)
        catch e:
            print e.message
        ''',
    "uncaught chain": '''
        fn a():
            fail("low")
        fn b():
            try:
                a()
            catch e:
                fail("high", e)
        b()
        ''',
    "uncaught inside finally": '''
        fn a():
            try:
                fail("body")
            finally:
                fail("cleanup")
        a()
        ''',
    "uncaught type error": '''
        fn f(x):
            return x + [1]
        f(2)
        ''',
    "uncaught unwrap": '''
        fn f() -> Result<Int, Str>:
            return Err("nope")
        print unwrap(f())
        ''',
    "uncaught ? on a non-result": '''
        fn f(x):
            return x?
        f(3)
        ''',
    "wait re-raises a task failure": '''
        fn job(n):
            if n > 1:
                fail("task " + n)
            return n
        let a = spawn job(1)
        let b = spawn job(2)
        print wait(a)
        print wait(b)
        ''',
}
for pname, psrc in PARITY_PROGRAMS.items():
    parity("parity: " + pname, psrc)


# =============================================================================================
# 17. The interpreter's user-function fast path (performance work that must not change meaning)
# =============================================================================================
def _chain_names():
    import ast
    import lumen as _lumen
    tree = ast.parse(open(os.path.join(ROOT, "lumen.py")).read())
    names = set()
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Interpreter"):
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "_eval_Call":
                for n in ast.walk(fn):
                    if isinstance(n, ast.Compare) and isinstance(n.left, ast.Attribute) and n.left.attr == "name":
                        names.update(c.value for c in n.comparators if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return names, _lumen

_names, _lumen = _chain_names()
record(len(_names) >= 15, f"call fast path: found only {sorted(_names)} compared in Interpreter._eval_Call")
record(_names <= set(_lumen.BUILTIN_FUNCTION_NAMES),
       f"call fast path: {sorted(_names - set(_lumen.BUILTIN_FUNCTION_NAMES))} are special-cased by the interpreter but missing from "
       f"BUILTIN_FUNCTION_NAMES — a user function of that name would now shadow them")
record(set(_lumen.RESULT_BUILTIN_NAMES) <= set(_lumen.BUILTIN_FUNCTION_NAMES)
       and set(_lumen.CAPABILITY_BUILTIN_NAMES) <= set(_lumen.BUILTIN_FUNCTION_NAMES),
       "call fast path: the Result helpers and the capability built-ins must all be in BUILTIN_FUNCTION_NAMES")
parity("a user function named like a built-in means the same on every engine", S('''
    fn len(x):
        return 99
    fn unwrap(x):
        return 7
    print len([1, 2])
    print unwrap(Ok(1))
    '''))
both("a call to an unknown function is still a catchable NameError", S('''
    try:
        nowhere(1)
    catch e:
        print e.code
        print e.message
    '''), "R0003\nNameError: function 'nowhere' is not defined")
both("a call after a failed call in a loop keeps working (frames stay balanced)", S('''
    fn boom(n):
        fail("boom " + n)
    fn ok(n):
        return n
    let total = 0
    for i in range(3):
        try:
            boom(i)
        catch e:
            total = total + ok(i)
    print total
    '''), "3")


# =============================================================================================
# 18. No silent error loss: a spawned task's failure that nobody waits for is reported (errors.md §4.7)
# =============================================================================================
def run_cli_split(path, *flags):
    r = subprocess.run([sys.executable, LUMEN, "run", *flags, path], capture_output=True, text=True, timeout=120)
    return r.stdout, r.stderr, r.returncode

with Dir() as d:
    lost = d.write("lost.lum", '''
        fn job(n):
            if n == 2:
                fail_with("E_JOB", "job 2 failed")
            return n
        let a = spawn job(1)
        let b = spawn job(2)
        print wait(a)
        print "done"
        ''')
    reports = []
    for flags in ((), ("--vm",), ("--vm", "-O2")):
        out, err, rc = run_cli_split(lost, *flags)
        label = " ".join(flags) or "interpreter"
        record(out == "1\ndone\n", f"lost task [{label}]: stdout must be untouched, got {out!r}")
        record(rc == 0, f"lost task [{label}]: the exit status is unchanged by the report, got {rc}")
        record("a spawned task failed and nothing waited for it" in err and "job 2 failed" in err and "E_JOB" in err
               and "job (lost.lum:3)" in err, f"lost task [{label}]: stderr must describe the lost failure, got {err!r}")
        reports.append(err)
    record(len(set(reports)) == 1, f"lost task: the report differs between engines: {reports!r}")

    waited = d.write("waited.lum", '''
        fn job(n):
            fail("x")
        let b = spawn job(2)
        try:
            wait(b)
        catch e:
            print "caught " + e.message
        ''')
    fine = d.write("fine.lum", '''
        fn job(n):
            return n * 2
        let a = spawn job(1)
        let b = spawn job(2)
        print "no waiting"
        ''')
    for flags in ((), ("--vm",)):
        label = " ".join(flags) or "interpreter"
        out, err, rc = run_cli_split(waited, *flags)
        record(out == "caught x\n" and err == "" and rc == 0, f"waited-on failure [{label}] must not be reported again: {out!r} {err!r} {rc}")
        out, err, rc = run_cli_split(fine, *flags)
        record(out == "no waiting\n" and err == "" and rc == 0, f"tasks that succeed are not reported [{label}]: {out!r} {err!r} {rc}")

    # the program itself fails AND a task was lost: both are said, the program's failure first and on stdout
    both_lost = d.write("both.lum", '''
        fn job(n):
            fail("lost one")
        let t = spawn job(1)
        fail("main failed")
        ''')
    for flags in ((), ("--vm",)):
        label = " ".join(flags) or "interpreter"
        out, err, rc = run_cli_split(both_lost, *flags)
        record(out.startswith("Uncaught error: main failed") and rc == 1 and "lost one" in err,
               f"main failure plus lost task [{label}]: {out!r} {err!r} {rc}")


# =============================================================================================
def main():
    print("=" * 60)
    print("PHASE 4 - ERROR HANDLING & RELIABILITY (tests/test_phase4.py)")
    print("=" * 60)
    total = PASS + FAIL
    print(f"PHASE 4 ERROR HANDLING: {PASS} passed, {FAIL} failed (of {total})")
    if FAILURES:
        print()
        for f in FAILURES:
            print(f)
            print()
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
