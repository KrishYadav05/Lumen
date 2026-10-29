"""Conformance tests for Phase 2 Milestone 2: Typed IR.

Verifies (a) IR is built without error for every language construct, (b) the
`.type` attached to each IR expression node matches TypeChecker's own
inference (never re-derived independently — cross-checked), and (c)
IRValidator actually catches malformed IR rather than being a no-op.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from harness import check, print_final_summary
import harness
import lumen


def build_ir(src):
    ast = lumen.Parser(lumen.lex(src)).parse_program()
    resolver = lumen.Resolver()
    resolver.resolve_program(ast)
    lumen.TypeChecker().check_program(ast)
    return lumen.IRBuilder(resolver.module_globals).build_program(ast)


def ir_check(name, src, assertion_fn):
    global harness
    try:
        program = build_ir(src)
        ok, detail = assertion_fn(program)
    except Exception as e:
        ok, detail = False, f"raised {type(e).__name__}: {e}"
    if ok:
        harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[typed IR] {name}: {detail}")


# ---- IR builds cleanly for every construct, and IRValidator finds nothing wrong ----
FULL_PROGRAM = '''
let x = 5
let y: Int = 10
print x + y

fn square(n: Int) -> Int:
    return n * n

if x < y:
    print square(x)
else:
    print 0

for i in [1, 2, 3]:
    print i

let w = 0
while w < 3:
    print w
    w = w + 1

struct P:
    a
    b
let p = P { a: 1, b: 2 }
print p.a

enum E:
    Some(v)
    None
match Some(5):
    Some(v):
        print v
    None:
        print 0

try:
    print 1 / 0
catch e:
    print e.message
'''

def _full_program_ok(program):
    errors = lumen.IRValidator().validate(program)
    ok = (errors == [] and len(program.functions) == 1 and program.functions[0].name == "square"
          and len(program.structs) == 1 and program.structs[0].name == "P"
          and len(program.enums) == 1 and program.enums[0].name == "E")
    return ok, f"validator errors={errors}, functions={[f.name for f in program.functions]}"
ir_check("full-feature program builds and validates with zero errors", FULL_PROGRAM, _full_program_ok)

# ---- .type on IR nodes matches TypeChecker's own inference (not re-derived) ----
def _check_binop_type(expected_type, expr_src):
    def assertion(program):
        # top_level[0] is `let __t = <expr>`
        let_node = program.top_level[0]
        ok = isinstance(let_node, lumen.IRLet) and let_node.expr.type == expected_type
        return ok, f"got type {getattr(let_node.expr, 'type', None)!r}, expected {expected_type!r}"
    return assertion

ir_check("Int + Int -> IR type Int", "let __t = 1 + 2", _check_binop_type("Int", None))
ir_check("Int / Int -> IR type Float (division always promotes)", "let __t = 1 / 2", _check_binop_type("Float", None))
ir_check("Str + Int -> IR type Str (coercion rule)", 'let __t = "a" + 1', _check_binop_type("Str", None))
ir_check("comparison -> IR type Bool always", "let __t = 1 < 2", _check_binop_type("Bool", None))
ir_check("Int + Float -> IR type Float (numeric promotion, per types.md §3.6.1 — corrected during Phase 2 IR work: "
         "the checker previously, incorrectly, inferred 'Any' here instead of the runtime-accurate 'Float')",
         "let __t = 1 + 2.5", _check_binop_type("Float", None))

def _check_const_type(expected_type, src):
    def assertion(program):
        let_node = program.top_level[0]
        ok = isinstance(let_node, lumen.IRConst) is False and let_node.expr.type == expected_type
        return ok, f"got {getattr(let_node.expr, 'type', None)!r}"
    return assertion
ir_check("string literal -> IRConst type Str", 'let __t = "hi"', _check_const_type("Str", None))
ir_check("bool literal -> IRConst type Bool", 'let __t = true', _check_const_type("Bool", None))

def _check_generic_call_type():
    def assertion(program):
        # program: fn id<T>(x:T)->T: return x \n let __t = id(5)
        let_node = program.top_level[1]
        ok = isinstance(let_node, lumen.IRLet) and let_node.expr.type == "Int"
        return ok, f"got {getattr(let_node.expr, 'type', None)!r}"
    return assertion
ir_check("generic call return type resolved via call-site binding (matches types.md §3.8)",
         "fn id<T>(x: T) -> T:\n    return x\nlet __t = id(5)", _check_generic_call_type())

# ---- IRWhile (added Language Specification 2.2) lowers to the right shape ----
def _check_irwhile_shape():
    def assertion(program):
        # let i = 0 \n while i < 3: \n     print i \n     i = i + 1
        while_node = program.top_level[1]
        ok = (isinstance(while_node, lumen.IRWhile)
              and isinstance(while_node.cond, lumen.IRNode) and while_node.cond.type == "Bool"
              and len(while_node.body) == 2
              and isinstance(while_node.body[0], lumen.IRPrint))
        return ok, f"got {type(while_node).__name__}, cond.type={getattr(getattr(while_node, 'cond', None), 'type', None)!r}"
    return assertion
ir_check("IRWhile lowers with a Bool-typed .cond and its body statements intact",
         "let i = 0\nwhile i < 3:\n    print i\n    i = i + 1", _check_irwhile_shape())

# ---- IR node .kind fields correctly capture what Resolver already proved ----
def _check_call_kinds():
    def assertion(program):
        # fn f(): return 1 \n enum E: V \n let a = f() \n let b = V
        calls = []
        for node in program.top_level:
            if isinstance(node, lumen.IRLet) and isinstance(node.expr, lumen.IRCall):
                calls.append(node.expr.kind)
        ok = calls == ["function"]
        return ok, f"got kinds {calls}"
    return assertion
ir_check("IRCall.kind == 'function' for an ordinary user function",
         "fn f():\n    return 1\nlet a = f()", _check_call_kinds())

def _check_load_kind():
    def assertion(program):
        # let g = 1 \n fn f(): return g  -- inside f, `g` should be IRLoad kind "global"
        fn = program.functions[0]
        ret = fn.body[0]
        ok = isinstance(ret, lumen.IRReturn) and isinstance(ret.expr, lumen.IRLoad) and ret.expr.kind == "global"
        return ok, f"got kind {getattr(ret.expr, 'kind', None)!r}"
    return assertion
ir_check("IRLoad.kind == 'global' for a Rule-C-visible module global read from inside a function",
         "let g = 1\nfn f():\n    return g", _check_load_kind())

def _check_local_load_kind():
    def assertion(program):
        fn = program.functions[0]
        ret = fn.body[0]
        ok = isinstance(ret, lumen.IRReturn) and isinstance(ret.expr, lumen.IRLoad) and ret.expr.kind == "local"
        return ok, f"got kind {getattr(ret.expr, 'kind', None)!r}"
    return assertion
ir_check("IRLoad.kind == 'local' for a function's own parameter",
         "fn f(p):\n    return p", _check_local_load_kind())

# ---- IRValidator actually catches malformed IR (not a no-op) ----
def _validator_catches_stray_return():
    bad_program = lumen.IRProgram(
        top_level=[lumen.IRReturn(lumen.IRConst(1, "Int"))],  # return outside any function
        functions=[], structs=[], enums=[]
    )
    errors = lumen.IRValidator().validate(bad_program)
    ok = any("outside any IRFunctionDef" in e for e in errors)
    return ok, f"errors={errors}"
def wrap(fn):
    def assertion(_program):
        return fn()
    return assertion
ir_check("IRValidator rejects a bare IRReturn outside any function", "print 1", wrap(_validator_catches_stray_return))

def _validator_catches_missing_type():
    class FakeExpr(lumen.IRNode):
        pass  # deliberately no .type attribute
    bad_program = lumen.IRProgram(
        top_level=[lumen.IRPrint(FakeExpr())],
        functions=[], structs=[], enums=[]
    )
    errors = lumen.IRValidator().validate(bad_program)
    ok = any("missing a `.type`" in e for e in errors)
    return ok, f"errors={errors}"
ir_check("IRValidator rejects an expression node with no `.type` (typed-IR invariant)", "print 1",
         wrap(_validator_catches_missing_type))

print_final_summary()
