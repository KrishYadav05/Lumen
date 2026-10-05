#!/usr/bin/env python3
"""Lumen 1.0.0 — reference implementation (interpreter + bytecode VM backend).
Implements Lumen Language Specification 1.0 — see docs/ for the normative spec.
"""
import sys, re, threading, queue

# Single source of truth for every version string the toolchain prints.
# If you bump one of these, this is the only place that should need editing.
LUMEN_VERSION = "1.1.0"      # this implementation's own release version (semver)
SPEC_VERSION = "1.1"         # the Lumen Language Specification version it conforms to (docs/)
RUNTIME_KIND = "interpreter"  # default execution backend selected by `lumen run` (vs `--vm`)

# ---------- Lexer ----------
TOKEN_SPEC = [
    ("NUMBER",   r"\d+(\.\d+)?"),
    ("STRING",   r'"[^"]*"'),
    ("NAME",     r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP",       r"==|!=|<=|>=|->|[+\-*/%()\[\]{},:<>=.]"),
    ("NEWLINE",  r"\n"),
    ("SKIP",     r"[ \t]+"),
    ("COMMENT",  r"#.*"),
]
MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC))
KEYWORDS = {"let","print","if","else","for","in","fn","return","true","false",
            "try","catch","struct","and","or","not","use","as","enum","match","spawn","test"}

# Single source of truth for every builtin function name — referenced by
# Resolver.BUILTIN_NAMES, IRBuilder._call_kind, and Compiler's allow-list.
# This list used to be duplicated in three places; a real bug (assert_eq
# misrouted to "variant" kind, crashing the compiler) came directly from
# that duplication getting out of sync when assert/assert_eq/assert_ne were
# added. Add a new builtin here once, not three times.
BUILTIN_FUNCTION_NAMES = {"len", "push", "keys", "values", "read_file", "write_file",
                           "http_get", "fail", "wait", "chan", "send", "receive",
                           "assert", "assert_eq", "assert_ne", "now", "read_line"}
# The subset of BUILTIN_FUNCTION_NAMES the VM backend actually compiles today
# (concurrency primitives aren't implemented in the VM yet — see Compiler's
# docstring). Kept separate from the full set so the VM's "not yet supported"
# error stays accurate as VM coverage grows, without touching Resolver/IRBuilder.
VM_SUPPORTED_BUILTINS = {"len", "push", "keys", "values", "fail",
                          "read_file", "write_file", "http_get",
                          "assert", "assert_eq", "assert_ne", "now", "read_line"}

class Token:
    def __init__(self, kind, value, line):
        self.kind, self.value, self.line = kind, value, line
    def __repr__(self): return f"{self.kind}:{self.value}"

def lex(src):
    tokens = []
    line = 1
    indents = [0]
    bracket_depth = 0  # tracks unclosed ( [ { — while > 0, indentation/NEWLINE are suppressed,
                        # exactly like Python: `let d = {\n    "a": 1,\n}` is one logical line.
    lines = src.split("\n")
    for raw in lines:
        stripped = raw.rstrip()
        if stripped.strip() == "" or stripped.strip().startswith("#"):
            line += 1
            continue
        if bracket_depth == 0:
            indent = len(raw) - len(raw.lstrip(" "))
            if indent > indents[-1]:
                indents.append(indent)
                tokens.append(Token("INDENT", indent, line))
            elif indent < indents[-1]:
                while indent < indents[-1]:
                    indents.pop()
                    tokens.append(Token("DEDENT", indent, line))
                if indent != indents[-1]:
                    raise SyntaxError(
                        f"Lumen: inconsistent indentation at line {line} — {indent} spaces "
                        f"does not match any enclosing block's indentation level"
                    )
            pos = indent
        else:
            pos = len(raw) - len(raw.lstrip(" "))  # still skip leading whitespace on continuation lines
        while pos < len(stripped):
            m = MASTER_RE.match(stripped, pos)
            if not m:
                raise SyntaxError(f"Lumen: unexpected character {stripped[pos]!r} at line {line}")
            kind = m.lastgroup
            val = m.group()
            if kind == "SKIP" or kind == "COMMENT":
                pos = m.end(); continue
            if kind == "NAME" and val in KEYWORDS:
                kind = val.upper()
            if kind == "OP" and val in ("(", "[", "{"):
                bracket_depth += 1
            elif kind == "OP" and val in (")", "]", "}"):
                bracket_depth = max(0, bracket_depth - 1)
            tokens.append(Token(kind, val, line))
            pos = m.end()
        if bracket_depth == 0:
            tokens.append(Token("NEWLINE", "\n", line))
        line += 1
    while len(indents) > 1:
        indents.pop()
        tokens.append(Token("DEDENT", 0, line))
    tokens.append(Token("EOF", None, line))
    return tokens

# ---------- AST ----------
class Node: pass
class Num(Node):
    def __init__(self, v): self.v = v
class Str(Node):
    def __init__(self, v): self.v = v
class Bool(Node):
    def __init__(self, v): self.v = v
class Var(Node):
    def __init__(self, name): self.name = name
class BinOp(Node):
    def __init__(self, op, l, r): self.op, self.l, self.r = op, l, r
class ListLit(Node):
    def __init__(self, items): self.items = items
class Call(Node):
    def __init__(self, name, args, line=0): self.name, self.args, self.line = name, args, line
class Let(Node):
    def __init__(self, name, expr, type_ann=None, line=0):
        self.name, self.expr, self.type_ann, self.line = name, expr, type_ann, line
class Print(Node):
    def __init__(self, expr): self.expr = expr
class If(Node):
    def __init__(self, cond, body, orelse): self.cond, self.body, self.orelse = cond, body, orelse
class For(Node):
    def __init__(self, var, iterable, body): self.var, self.iterable, self.body = var, iterable, body
class FnDef(Node):
    def __init__(self, name, params, body, ret_type=None, line=0, type_params=None):
        self.name, self.params, self.body, self.ret_type, self.line = name, params, body, ret_type, line
        self.type_params = type_params or []
class Return(Node):
    def __init__(self, expr, line=0): self.expr, self.line = expr, line
class ExprStmt(Node):
    def __init__(self, expr): self.expr = expr
class StructDef(Node):
    def __init__(self, name, fields): self.name, self.fields = name, fields
class StructLit(Node):
    def __init__(self, name, values): self.name, self.values = name, values
class FieldGet(Node):
    def __init__(self, obj, field): self.obj, self.field = obj, field
class FieldSet(Node):
    def __init__(self, obj, field, expr): self.obj, self.field, self.expr = obj, field, expr
class Try(Node):
    def __init__(self, body, err_name, handler): self.body, self.err_name, self.handler = body, err_name, handler
class BoolOp(Node):
    def __init__(self, op, l, r): self.op, self.l, self.r = op, l, r
class Not(Node):
    def __init__(self, e): self.e = e
class Use(Node):
    def __init__(self, name, line): self.name, self.line = name, line
class UseModule(Node):
    def __init__(self, path, alias, line): self.path, self.alias, self.line = path, alias, line
class MethodCall(Node):
    def __init__(self, obj, method, args): self.obj, self.method, self.args = obj, method, args
class DictLit(Node):
    def __init__(self, pairs): self.pairs = pairs
class Index(Node):
    def __init__(self, obj, key): self.obj, self.key = obj, key
class IndexSet(Node):
    def __init__(self, obj, key, expr): self.obj, self.key, self.expr = obj, key, expr
class EnumDef(Node):
    def __init__(self, name, variants): self.name, self.variants = name, variants
class Match(Node):
    def __init__(self, expr, cases, line): self.expr, self.cases, self.line = expr, cases, line
class PLit(Node):
    def __init__(self, value): self.value = value
class PWildcard(Node): pass
class PBind(Node):
    def __init__(self, name): self.name = name
class PVariant(Node):
    def __init__(self, name, subnames): self.name, self.subnames = name, subnames
class SpawnExpr(Node):
    def __init__(self, call, line): self.call, self.line = call, line

class TestDef(Node):
    """`test "name": <block>` — Phase 3. Declares a named test; the body does
    NOT execute as part of ordinary program flow (matching real test-framework
    convention: test code doesn't run unless a test runner asks for it). It is
    collected and only run by `lumen test`. See Interpreter.run_tests /
    VM.run_one_test."""
    def __init__(self, name, body, line):
        self.name, self.body, self.line = name, body, line

class TaskHandle:
    def __init__(self):
        self.thread = None
        self.result = None
        self.error = None
        self.done = threading.Event()
    def __repr__(self): return "<task>"

class LumenChannel:
    def __init__(self):
        self.q = queue.Queue()
    def __repr__(self): return "<channel>"

class EnumInstance:
    def __init__(self, enum_name, variant, fields):
        self.enum_name, self.variant, self.fields = enum_name, variant, fields
    def __repr__(self):
        if not self.fields: return self.variant
        return f"{self.variant}(" + ", ".join(str(v) for v in self.fields.values()) + ")"

class LumenModule:
    def __init__(self, name, interp):
        self.name = name
        self.interp = interp
    def __repr__(self):
        return f"<module {self.name}>"

class LumenInstance:
    def __init__(self, struct_name, fields):
        self.struct_name = struct_name
        self.fields = fields
    def __repr__(self):
        body = ", ".join(f"{k}: {v}" for k, v in self.fields.items())
        return f"{self.struct_name} {{ {body} }}"

# ---------- Parser ----------
class Parser:
    def __init__(self, tokens):
        self.toks = [t for t in tokens]
        self.i = 0

    def peek(self): return self.toks[self.i]
    def advance(self):
        t = self.toks[self.i]; self.i += 1; return t
    def check(self, kind): return self.peek().kind == kind
    def expect(self, kind):
        if not self.check(kind):
            t = self.peek()
            raise SyntaxError(f"Lumen: expected {kind} but found {t.kind} ({t.value!r}) at line {t.line}")
        return self.advance()
    def skip_newlines(self):
        while self.check("NEWLINE"):
            self.advance()

    def parse_program(self):
        stmts = []
        self.skip_newlines()
        while not self.check("EOF"):
            stmts.append(self.parse_stmt())
            self.skip_newlines()
        return stmts

    def parse_block(self):
        self.skip_newlines()
        self.expect("INDENT")
        stmts = []
        self.skip_newlines()
        while not self.check("DEDENT"):
            stmts.append(self.parse_stmt())
            self.skip_newlines()
        self.expect("DEDENT")
        return stmts

    def parse_stmt(self):
        if self.check("LET"):
            t = self.advance()
            name = self.expect("NAME").value
            type_ann = None
            if self.peek().value == ":":
                self.advance()
                type_ann = self.expect("NAME").value
            self.expect("OP")  # '='
            expr = self.parse_expr()
            return Let(name, expr, type_ann, t.line)
        if self.check("PRINT"):
            self.advance()
            expr = self.parse_expr()
            return Print(expr)
        if self.check("IF"):
            return self.parse_if()
        if self.check("FOR"):
            self.advance()
            var = self.expect("NAME").value
            self.expect("IN")
            it = self.parse_expr()
            self.expect("OP")  # ':'
            body = self.parse_block()
            return For(var, it, body)
        if self.check("TEST"):
            t = self.advance()
            name_tok = self.expect("STRING")
            self.expect("OP")  # :
            body = self.parse_block()
            return TestDef(name_tok.value[1:-1], body, t.line)
        if self.check("FN"):
            t = self.advance()
            name = self.expect("NAME").value
            type_params = []
            if self.peek().value == "<":
                self.advance()
                type_params.append(self.expect("NAME").value)
                while self.peek().value == ",":
                    self.advance()
                    type_params.append(self.expect("NAME").value)
                self.expect("OP")  # >
            self.expect("OP")  # (
            params = []
            def parse_param():
                pname = self.expect("NAME").value
                ptype = None
                if self.peek().value == ":":
                    self.advance()
                    ptype = self.expect("NAME").value
                params.append((pname, ptype))
            if self.peek().value != ")":
                parse_param()
                while self.peek().value == ",":
                    self.advance()
                    parse_param()
            self.expect("OP")  # )
            ret_type = None
            if self.peek().value == "->":
                self.advance()
                ret_type = self.expect("NAME").value
            self.expect("OP")  # :
            body = self.parse_block()
            return FnDef(name, params, body, ret_type, t.line, type_params)
        if self.check("RETURN"):
            t = self.advance()
            expr = self.parse_expr()
            return Return(expr, t.line)
        if self.check("USE"):
            t = self.advance()
            if self.check("STRING"):
                path = self.advance().value[1:-1]
                self.expect("AS")
                alias = self.expect("NAME").value
                return UseModule(path, alias, t.line)
            name = self.expect("NAME").value
            return Use(name, t.line)
        if self.check("ENUM"):
            self.advance()
            name = self.expect("NAME").value
            self.expect("OP")  # :
            self.skip_newlines(); self.expect("INDENT")
            variants = []
            self.skip_newlines()
            while not self.check("DEDENT"):
                vname = self.expect("NAME").value
                fields = []
                if self.peek().value == "(":
                    self.advance()
                    fields.append(self.expect("NAME").value)
                    while self.peek().value == ",":
                        self.advance()
                        fields.append(self.expect("NAME").value)
                    self.expect("OP")  # )
                variants.append((vname, fields))
                self.skip_newlines()
            self.expect("DEDENT")
            return EnumDef(name, variants)
        if self.check("MATCH"):
            t = self.advance()
            expr = self.parse_expr()
            self.expect("OP")  # :
            self.skip_newlines(); self.expect("INDENT")
            cases = []
            self.skip_newlines()
            while not self.check("DEDENT"):
                pattern = self.parse_pattern()
                self.expect("OP")  # :
                body = self.parse_block()
                cases.append((pattern, body))
                self.skip_newlines()
            self.expect("DEDENT")
            return Match(expr, cases, t.line)
        if self.check("STRUCT"):
            self.advance()
            name = self.expect("NAME").value
            self.expect("OP")  # :
            self.skip_newlines(); self.expect("INDENT")
            fields = []
            self.skip_newlines()
            while not self.check("DEDENT"):
                fields.append(self.expect("NAME").value)
                self.skip_newlines()
            self.expect("DEDENT")
            return StructDef(name, fields)
        if self.check("TRY"):
            self.advance()
            self.expect("OP")  # :
            body = self.parse_block()
            self.skip_newlines_peekahead_kw("CATCH")
            self.expect("CATCH")
            err_name = self.expect("NAME").value
            self.expect("OP")  # :
            handler = self.parse_block()
            return Try(body, err_name, handler)
        # assignment or expression
        expr = self.parse_expr()
        if self.peek().value == "=" and self.peek().kind == "OP":
            self.advance()
            value = self.parse_expr()
            if isinstance(expr, Var):
                return Let(expr.name, value)
            if isinstance(expr, FieldGet):
                return FieldSet(expr.obj, expr.field, value)
            if isinstance(expr, Index):
                return IndexSet(expr.obj, expr.key, value)
            raise SyntaxError("Lumen: invalid assignment target")
        return ExprStmt(expr)

    def parse_if(self):
        self.expect("IF")
        cond = self.parse_expr()
        self.expect("OP")  # :
        body = self.parse_block()
        orelse = []
        self.skip_newlines_peekahead()
        if self.check("ELSE"):
            self.advance()
            self.expect("OP")  # :
            orelse = self.parse_block()
        return If(cond, body, orelse)

    def skip_newlines_peekahead(self):
        j = self.i
        while self.toks[j].kind == "NEWLINE":
            j += 1
        if self.toks[j].kind == "ELSE":
            self.i = j

    def skip_newlines_peekahead_kw(self, kind):
        j = self.i
        while self.toks[j].kind == "NEWLINE":
            j += 1
        if self.toks[j].kind == kind:
            self.i = j

    # expressions (precedence climbing)
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        node = self.parse_and()
        while self.check("OR"):
            self.advance()
            node = BoolOp("or", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.check("AND"):
            self.advance()
            node = BoolOp("and", node, self.parse_not())
        return node

    def parse_not(self):
        if self.check("NOT"):
            self.advance()
            return Not(self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        node = self.parse_add()
        while self.peek().value in ("==", "!=", "<", ">", "<=", ">="):
            op = self.advance().value
            right = self.parse_add()
            node = BinOp(op, node, right)
        return node

    def parse_add(self):
        node = self.parse_mul()
        while self.peek().value in ("+", "-"):
            op = self.advance().value
            right = self.parse_mul()
            node = BinOp(op, node, right)
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.peek().value in ("*", "/", "%"):
            op = self.advance().value
            right = self.parse_unary()
            node = BinOp(op, node, right)
        return node

    def parse_unary(self):
        if self.peek().value == "-":
            self.advance()
            return BinOp("-", Num(0), self.parse_unary())
        return self.parse_primary()

    def parse_pattern(self):
        t = self.peek()
        if t.kind == "NUMBER":
            self.advance()
            return PLit(float(t.value) if "." in t.value else int(t.value))
        if t.kind == "STRING":
            self.advance()
            return PLit(t.value[1:-1])
        if t.kind == "TRUE":
            self.advance(); return PLit(True)
        if t.kind == "FALSE":
            self.advance(); return PLit(False)
        if t.kind == "NAME":
            name = self.advance().value
            if name == "_":
                return PWildcard()
            if self.peek().value == "(":
                self.advance()
                subs = [self.expect("NAME").value]
                while self.peek().value == ",":
                    self.advance()
                    subs.append(self.expect("NAME").value)
                self.expect("OP")  # )
                return PVariant(name, subs)
            return PBind(name)
        raise SyntaxError(f"Lumen: invalid pattern at line {t.line}")

    def parse_trailers(self, node):
        while True:
            if self.peek().value == ".":
                self.advance()
                field = self.expect("NAME").value
                node = FieldGet(node, field)
            elif self.peek().value == "[":
                self.advance()
                key = self.parse_expr()
                self.expect("OP")  # ]
                node = Index(node, key)
            else:
                break
        if self.peek().value == "(" and isinstance(node, FieldGet):
            self.advance()
            args = []
            if self.peek().value != ")":
                args.append(self.parse_expr())
                while self.peek().value == ",":
                    self.advance()
                    args.append(self.parse_expr())
            self.expect("OP")  # )
            node = MethodCall(node.obj, node.field, args)
        return node

    def parse_primary(self):
        t = self.peek()
        if t.kind == "SPAWN":
            self.advance()
            call_node = self.parse_primary()
            if not isinstance(call_node, Call):
                raise SyntaxError(f"Lumen: 'spawn' must be followed by a function call, e.g. spawn f(1, 2) (line {t.line})")
            return SpawnExpr(call_node, t.line)
        if t.kind == "NUMBER":
            self.advance()
            return Num(float(t.value) if "." in t.value else int(t.value))
        if t.kind == "STRING":
            self.advance()
            return Str(t.value[1:-1])
        if t.kind == "TRUE":
            self.advance(); return Bool(True)
        if t.kind == "FALSE":
            self.advance(); return Bool(False)
        if t.value == "[":
            self.advance()
            items = []
            if self.peek().value != "]":
                items.append(self.parse_expr())
                while self.peek().value == ",":
                    self.advance()
                    items.append(self.parse_expr())
            self.expect("OP")  # ]
            return self.parse_trailers(ListLit(items))
        if t.value == "{":
            self.advance()
            pairs = []
            self.skip_newlines()
            while self.peek().value != "}":
                key = self.parse_expr()
                self.expect("OP")  # :
                val = self.parse_expr()
                pairs.append((key, val))
                if self.peek().value == ",":
                    self.advance()
                self.skip_newlines()
            self.expect("OP")  # }
            node = DictLit(pairs)
            return self.parse_trailers(node)
        if t.kind == "NAME":
            self.advance()
            node = None
            if self.peek().value == "(":
                self.advance()
                args = []
                if self.peek().value != ")":
                    args.append(self.parse_expr())
                    while self.peek().value == ",":
                        self.advance()
                        args.append(self.parse_expr())
                self.expect("OP")  # )
                node = Call(t.value, args, t.line)
            elif self.peek().value == "{":
                self.advance()
                values = {}
                self.skip_newlines()
                while self.peek().value != "}":
                    fname = self.expect("NAME").value
                    self.expect("OP")  # :
                    values[fname] = self.parse_expr()
                    if self.peek().value == ",":
                        self.advance()
                    self.skip_newlines()
                self.expect("OP")  # }
                node = StructLit(t.value, values)
            else:
                node = Var(t.value)
            return self.parse_trailers(node)
        if t.value == "(":
            self.advance()
            e = self.parse_expr()
            self.expect("OP")  # )
            return self.parse_trailers(e)
        raise SyntaxError(f"Lumen: unexpected token {t.kind} ({t.value!r}) at line {t.line}")

# ---------- Interpreter ----------
class Resolver:
    """Phase 2, Milestone 1: static Name Resolution.

    Runs after parsing, before type-checking. Gives every variable reference a
    deterministic, statically-known binding (module.md-equivalent for names) and
    turns a class of failures that Phase 1 could only detect at *runtime*
    (NameError) into *compile-time* errors — without ever rejecting a program
    that the reference interpreter actually runs successfully (soundness is
    non-negotiable: see the "two-tier" model below).

    THE TWO-TIER MODEL (this is the key architectural fact that makes a naive
    "just check every name exists" resolver unsound for Lumen specifically):

    Tier 1 — block-local variables (`let`, params, `for` loop vars, `catch` err
    names, module aliases from `use ... as`): purely lexical, block-structured,
    and populated as control flow actually reaches each declaration
    (language.md §1.11 Rules A/B/C). This is fully static: the resolver can
    give a sound *and* complete (accept iff the interpreter would accept)
    verdict, because Env chains in the reference implementation are built
    directly from AST block structure with no runtime branching ambiguity
    affecting *reachability of the binding site itself* (which branch of an
    `if` actually executes doesn't change which block *introduces* a name).

    Tier 2 — functions, structs, and enum variants: the reference interpreter
    registers these into a single FLAT, imperatively-populated table
    (`self.functions`/`self.structs`/`self.enum_variants` — see types.md §3.10),
    not a lexically-scoped one, and registration only happens once the
    defining statement actually *executes*. This is real, verified, frozen
    Phase 1 behavior (not a resolver limitation to work around):

        fn outer():
            fn inner():
                return 42
            return 0
        outer()        # <- must run first
        print inner()  # <- only now callable; works

        fn outer():
            fn inner():
                return 42
            return 0
        print inner()  # <- NameError: outer() never ran, inner never registered

    Both programs are valid Lumen source with different, execution-order-
    dependent outcomes. A static resolver cannot decide, in general, whether a
    given call site runs before or after a given `fn` statement (that's
    control-flow/data-flow analysis, and for arbitrary programs it's
    undecidable). So Tier 2 resolution is deliberately, honestly *sound for
    rejection only*: a name is a compile-time error if and only if it is not
    declared *anywhere* in the compilation unit (i.e. could never resolve
    under *any* execution order); a name that exists somewhere but might not
    have executed yet at a given call site is accepted at compile time and
    remains, correctly, a possible *runtime* NameError — exactly matching
    frozen Phase 1 semantics. Same treatment applies to top-level (module)
    `let` globals *as seen from inside a function body* (Rule C): a function
    body sees the full, order-independent set of top-level global names,
    because by the time any function is actually *called*, arbitrary prior
    top-level execution may have already run. Top-level code itself, outside
    any function, IS fully sequential and fully statically checkable (a
    forward reference there is unconditionally rejected, exactly matching
    verified runtime behavior).

    Tier 3 — the `try`-body escape hatch (frozen-spec preservation, not a
    resolver limitation): errors.md §4.2 froze, with a worked example, the
    guarantee that *every* runtime failure — explicitly including a `NameError`
    for a name that is undefined anywhere in the program — is uniformly
    catchable by an enclosing `try`/`catch`. Statically rejecting an unresolved
    name would silently break that already-tested, already-frozen guarantee
    for any such reference that happens to be wrapped in a `try`. So: a name
    that fails Tier 1 or Tier 2 resolution is a hard compile-time error
    *unless* the reference occurs lexically inside a `try` block's own body
    (not its `catch` handler, which is not itself protected by the very `try`
    it belongs to) — in that case resolution silently defers to the existing,
    unchanged runtime check, exactly preserving Phase 1 observable behavior.
    This means Phase 2's static rejection is deliberately narrower than "every
    undefined name, everywhere": it only compile-time-rejects references that
    could not possibly be caught anyway, which is precisely the set of
    references where moving the failure earlier changes nothing about what a
    conformant Phase 1 program could observe.
    """

    BUILTIN_NAMES = BUILTIN_FUNCTION_NAMES

    def __init__(self):
        self.module_globals = set()      # Tier 1, but function-body-visible subset: flat top-level `let` names
        self.function_names = set()      # Tier 2: flat, whole-program
        self.struct_names = set()        # Tier 2: flat, whole-program
        self.enum_variant_names = set()  # Tier 2: flat, whole-program (unit AND payload variant names)
        self._repl_top_scope = set()     # persists across resolve_incremental() calls, for the REPL only
        self._try_depth = 0              # >0 while resolving inside a `try` block's own body (Tier 3, see docstring)

    # ---- Tier 2 prescan: mirrors the interpreter's flat, non-lexical tables ----
    def _prescan(self, stmts):
        for s in stmts:
            if isinstance(s, FnDef):
                self.function_names.add(s.name)
                self._prescan(s.body)  # fn defs can nest; registration is flat regardless of depth
            elif isinstance(s, StructDef):
                self.struct_names.add(s.name)
            elif isinstance(s, EnumDef):
                for vname, _fields in s.variants:
                    self.enum_variant_names.add(vname)
            elif isinstance(s, If):
                self._prescan(s.body); self._prescan(s.orelse)
            elif isinstance(s, For):
                self._prescan(s.body)
            elif isinstance(s, Try):
                self._prescan(s.body); self._prescan(s.handler)
            elif isinstance(s, Match):
                for _pattern, body in s.cases:
                    self._prescan(body)
            elif isinstance(s, TestDef):
                self._prescan(s.body)  # test bodies can, in principle, declare fn/struct/enum too

    def resolve_program(self, stmts):
        self._prescan(stmts)
        for s in stmts:
            if isinstance(s, Let):
                self.module_globals.add(s.name)
            elif isinstance(s, UseModule):
                self.module_globals.add(s.alias)  # `use ... as x` at top level is a global binding too (Rule C)
        self._resolve_block(stmts, [set()], in_function=False)

    def resolve_incremental(self, stmts):
        """Like resolve_program, but accumulates state across calls instead of
        starting fresh each time — for the REPL, where each line is parsed and
        resolved independently but shares one persistent top-level scope with
        every prior line (mirroring the REPL's one persistent Interpreter)."""
        self._prescan(stmts)
        for s in stmts:
            if isinstance(s, Let):
                self.module_globals.add(s.name)
            elif isinstance(s, UseModule):
                self.module_globals.add(s.alias)
        self._resolve_block(stmts, [self._repl_top_scope], in_function=False)

    def _resolve_block(self, stmts, scope_stack, in_function):
        for s in stmts:
            self._resolve_stmt(s, scope_stack, in_function)

    def _known(self, name, scope_stack):
        return any(name in scope for scope in reversed(scope_stack))

    def _resolve_stmt(self, node, scope_stack, in_function):
        if isinstance(node, Let):
            self._resolve_expr(node.expr, scope_stack, in_function)
            scope_stack[-1].add(node.name)
        elif isinstance(node, Print):
            self._resolve_expr(node.expr, scope_stack, in_function)
        elif isinstance(node, If):
            self._resolve_expr(node.cond, scope_stack, in_function)
            self._resolve_block(node.body, scope_stack + [set()], in_function)
            self._resolve_block(node.orelse, scope_stack + [set()], in_function)
        elif isinstance(node, For):
            self._resolve_expr(node.iterable, scope_stack, in_function)
            self._resolve_block(node.body, scope_stack + [{node.var}], in_function)
        elif isinstance(node, Try):
            self._try_depth += 1
            try:
                self._resolve_block(node.body, scope_stack + [set()], in_function)
            finally:
                self._try_depth -= 1
            self._resolve_block(node.handler, scope_stack + [{node.err_name}], in_function)
        elif isinstance(node, FnDef):
            fresh_stack = [set(self.module_globals), {p for p, _t in node.params}]
            # A function's own try-depth is independent of the textual context
            # its `fn` statement happens to sit in (it only actually runs when
            # called, possibly from an entirely different, untried context).
            saved_try_depth = self._try_depth
            self._try_depth = 0
            try:
                self._resolve_block(node.body, fresh_stack, in_function=True)
            finally:
                self._try_depth = saved_try_depth
        elif isinstance(node, TestDef):
            # Same Rule-C isolation as a function: a test sees module globals
            # (including ones declared after it, since — like a function — it
            # doesn't run inline where it's written; `lumen test` runs it after
            # the whole file's top-level code has executed) plus nothing else.
            fresh_stack = [set(self.module_globals)]
            saved_try_depth = self._try_depth
            self._try_depth = 0
            try:
                self._resolve_block(node.body, fresh_stack, in_function=True)
            finally:
                self._try_depth = saved_try_depth
        elif isinstance(node, Match):
            self._resolve_expr(node.expr, scope_stack, in_function)
            for pattern, body in node.cases:
                self._resolve_block(body, scope_stack + [self._pattern_bindings(pattern)], in_function)
        elif isinstance(node, Return):
            self._resolve_expr(node.expr, scope_stack, in_function)
        elif isinstance(node, FieldSet):
            self._resolve_expr(node.obj, scope_stack, in_function)
            self._resolve_expr(node.expr, scope_stack, in_function)
        elif isinstance(node, IndexSet):
            self._resolve_expr(node.obj, scope_stack, in_function)
            self._resolve_expr(node.key, scope_stack, in_function)
            self._resolve_expr(node.expr, scope_stack, in_function)
        elif isinstance(node, UseModule):
            scope_stack[-1].add(node.alias)  # `use ... as name` binds exactly like `let` (Rule B)
        elif isinstance(node, Use):
            pass  # capability grants are not names
        elif isinstance(node, (StructDef, EnumDef)):
            pass  # no expressions; existence already captured by the Tier-2 prescan
        elif isinstance(node, ExprStmt):
            self._resolve_expr(node.expr, scope_stack, in_function)

    def _pattern_bindings(self, pattern):
        if isinstance(pattern, PVariant):
            return set(pattern.subnames)
        if isinstance(pattern, PBind) and pattern.name not in self.enum_variant_names:
            return {pattern.name}
        return set()  # PLit, PWildcard, or a unit-variant-equality PBind bind nothing

    def _resolve_expr(self, node, scope_stack, in_function):
        if isinstance(node, Var):
            if self._known(node.name, scope_stack):
                return
            if node.name in self.enum_variant_names:
                return  # possible unit-variant reference (Tier 2, existence-only — see class docstring)
            if self._try_depth > 0:
                return  # Tier 3: inside a try-body, defer to the runtime check (preserves errors.md §4.2)
            raise LumenError(f"NameError: '{node.name}' is not defined (unresolved by static name resolution)")
        elif isinstance(node, BinOp):
            self._resolve_expr(node.l, scope_stack, in_function)
            self._resolve_expr(node.r, scope_stack, in_function)
        elif isinstance(node, BoolOp):
            self._resolve_expr(node.l, scope_stack, in_function)
            self._resolve_expr(node.r, scope_stack, in_function)
        elif isinstance(node, Not):
            self._resolve_expr(node.e, scope_stack, in_function)
        elif isinstance(node, ListLit):
            for item in node.items:
                self._resolve_expr(item, scope_stack, in_function)
        elif isinstance(node, DictLit):
            for k, v in node.pairs:
                self._resolve_expr(k, scope_stack, in_function)
                self._resolve_expr(v, scope_stack, in_function)
        elif isinstance(node, StructLit):
            if node.name not in self.struct_names and self._try_depth == 0:
                raise LumenError(f"NameError: unknown struct '{node.name}' (unresolved by static name resolution)")
            for v in node.values.values():
                self._resolve_expr(v, scope_stack, in_function)
        elif isinstance(node, Index):
            self._resolve_expr(node.obj, scope_stack, in_function)
            self._resolve_expr(node.key, scope_stack, in_function)
        elif isinstance(node, FieldGet):
            self._resolve_expr(node.obj, scope_stack, in_function)
        elif isinstance(node, MethodCall):
            self._resolve_expr(node.obj, scope_stack, in_function)
            for a in node.args:
                self._resolve_expr(a, scope_stack, in_function)
        elif isinstance(node, SpawnExpr):
            self._resolve_call_target(node.call.name, node.line)
            for a in node.call.args:
                self._resolve_expr(a, scope_stack, in_function)
        elif isinstance(node, Call):
            self._resolve_call_target(node.name, node.line)
            for a in node.args:
                self._resolve_expr(a, scope_stack, in_function)
        # Num, Str, Bool: nothing to resolve

    def _resolve_call_target(self, name, line):
        if (name in self.function_names or name in self.enum_variant_names
                or name in self.BUILTIN_NAMES):
            return
        if self._try_depth > 0:
            return  # Tier 3: inside a try-body, defer to the runtime check (preserves errors.md §4.2)
        raise LumenError(
            f"NameError: function '{name}' is not defined (unresolved by static name "
            f"resolution — no 'fn {name}' or matching enum variant exists anywhere in "
            f"this module) at line {line}"
        )


class TypeChecker:
    """Lightweight gradual type checker: only annotated names/params/returns
    are checked. Unannotated values are 'Any' and always compatible."""
    PRIMITIVES = {"Int", "Float", "Str", "Bool"}

    def __init__(self):
        self.func_sigs = {}   # name -> (param_types, ret_type)

    def check_program(self, stmts):
        for s in stmts:
            if isinstance(s, FnDef):
                self.func_sigs[s.name] = ([t for _, t in s.params], s.ret_type, s.type_params)
        env = {}
        for s in stmts:
            self.check_stmt(s, env)

    def check_stmt(self, node, env):
        if isinstance(node, Let):
            t = self.infer(node.expr, env)
            if node.type_ann and t != "Any" and node.type_ann != t:
                raise LumenError(
                    f"TypeError at line {node.line}: '{node.name}' is declared as {node.type_ann} "
                    f"but assigned a {t} value"
                )
            env[node.name] = node.type_ann or t
        elif isinstance(node, FnDef):
            local = {p: ("Any" if pt in node.type_params else (pt or "Any")) for p, pt in node.params}
            for s in node.body:
                self.check_stmt(s, local)
            if node.ret_type and node.ret_type not in node.type_params:
                for s in node.body:
                    self.check_returns(s, local, node)
        elif isinstance(node, TestDef):
            local = {}
            for s in node.body:
                self.check_stmt(s, local)
        elif isinstance(node, If):
            for s in node.body: self.check_stmt(s, dict(env))
            for s in node.orelse: self.check_stmt(s, dict(env))
        elif isinstance(node, For):
            inner = dict(env); inner[node.var] = "Any"
            for s in node.body: self.check_stmt(s, inner)
        elif isinstance(node, Try):
            for s in node.body: self.check_stmt(s, dict(env))
            inner = dict(env); inner[node.err_name] = "Any"
            for s in node.handler: self.check_stmt(s, inner)
        elif isinstance(node, ExprStmt):
            self.infer(node.expr, env)
        elif isinstance(node, Print):
            self.infer(node.expr, env)

    def check_returns(self, node, env, fn):
        if isinstance(node, Return):
            t = self.infer(node.expr, env)
            if t != "Any" and t != fn.ret_type:
                raise LumenError(
                    f"TypeError at line {node.line}: function '{fn.name}' declares -> {fn.ret_type} "
                    f"but returns a {t} value"
                )
        elif isinstance(node, If):
            for s in node.body + node.orelse: self.check_returns(s, env, fn)
        elif isinstance(node, For):
            for s in node.body: self.check_returns(s, env, fn)
        elif isinstance(node, Try):
            for s in node.body + node.handler: self.check_returns(s, env, fn)

    def infer(self, node, env):
        if isinstance(node, Num): return "Float" if isinstance(node.v, float) else "Int"
        if isinstance(node, Str): return "Str"
        if isinstance(node, Bool): return "Bool"
        if isinstance(node, ListLit): return "List"
        if isinstance(node, DictLit): return "Dict"
        if isinstance(node, Var): return env.get(node.name, "Any")
        if isinstance(node, Not) or isinstance(node, BoolOp): return "Bool"
        if isinstance(node, BinOp):
            lt, rt = self.infer(node.l, env), self.infer(node.r, env)
            if node.op in ("==", "!=", "<", ">", "<=", ">="):
                return "Bool"
            NUMERIC = {"Int", "Float", "Bool"}  # Bool participates numerically — types.md §3.6.1
            if node.op == "+":
                if lt == "Str" or rt == "Str":
                    return "Str"  # the Str-coercion rule dominates regardless of the other operand
                if lt == "List" and rt == "List":
                    return "List"  # list concatenation
                if lt in NUMERIC and rt in NUMERIC:
                    return "Float" if "Float" in (lt, rt) else "Int"
                return "Any"
            if node.op == "*":
                if (lt == "List" and rt in ("Int", "Bool")) or (rt == "List" and lt in ("Int", "Bool")):
                    return "List"  # list repetition
                if (lt == "Str" and rt in ("Int", "Bool")) or (rt == "Str" and lt in ("Int", "Bool")):
                    return "Str"  # string repetition
                if lt in NUMERIC and rt in NUMERIC:
                    return "Float" if "Float" in (lt, rt) else "Int"
                return "Any"
            if node.op == "/":
                # division ALWAYS produces Float at runtime, for any numeric pair
                return "Float" if (lt in NUMERIC and rt in NUMERIC) else "Any"
            if node.op in ("-", "%"):
                if lt in NUMERIC and rt in NUMERIC:
                    return "Float" if "Float" in (lt, rt) else "Int"
                return "Any"
            return "Any"
        if isinstance(node, Call):
            if node.name in self.func_sigs:
                param_types, ret, type_params = self.func_sigs[node.name]
                bindings = {}
                for i, a in enumerate(node.args):
                    at = self.infer(a, env)
                    if i >= len(param_types): continue
                    pt = param_types[i]
                    if not pt or at == "Any": continue
                    if pt in type_params:
                        if pt in bindings and bindings[pt] != at:
                            raise LumenError(
                                f"TypeError at line {node.line}: type parameter '{pt}' inferred as both "
                                f"{bindings[pt]} and {at} in call to '{node.name}'"
                            )
                        bindings[pt] = at
                    elif at != pt:
                        raise LumenError(
                            f"TypeError at line {node.line}: '{node.name}' argument {i+1} "
                            f"expects {pt} but got {at}"
                        )
                if ret in type_params:
                    return bindings.get(ret, "Any")
                return ret or "Any"
            return "Any"
        return "Any"

class LumenError(Exception): pass


# =====================================================================
# Phase 2, Milestone 2: Typed Intermediate Representation (IR)
# =====================================================================
# Sits between the resolved, type-checked AST and bytecode generation.
# Every IR *expression* node carries a `.type` field (one of TypeChecker's
# type strings: "Int", "Float", "Str", "Bool", "List", "Dict", a struct/enum
# name, or "Any") computed the same way TypeChecker computes it — IRBuilder
# reuses TypeChecker.infer() directly rather than re-implementing inference,
# so the IR's types are guaranteed consistent with what already passed
# type-checking (see test_ir.py's cross-check tests).
#
# Pass condition (Phase 2 spec): "the compiler should no longer need to
# directly translate arbitrary AST nodes into VM behavior." Compiler now
# consumes IRProgram/IR* nodes exclusively; it never touches an AST node.

class IRNode:
    """Base class. Every IR node keeps a reference to the line number of the
    AST node it was lowered from, for error messages and future debug info."""
    line = 0

# ---- IR expressions (every one of these carries `.type`) ----
class IRConst(IRNode):
    def __init__(self, value, type_, line=0):
        self.value, self.type, self.line = value, type_, line

class IRLoad(IRNode):
    """Read of a name. `kind` records WHERE Name Resolution determined this
    binding lives, so bytecode generation can pick the right load instruction
    without re-deriving scope information the Resolver already computed."""
    def __init__(self, name, type_, kind, line=0):
        self.name, self.type, self.kind, self.line = name, type_, kind, line  # kind: "local" | "global"

class IRUnitVariant(IRNode):
    """A bare-name reference that resolves to a unit enum variant, not a
    variable read — see errors.md §4.5's disambiguation rule, replicated
    here exactly (a name only means this if it is NOT a known binding at
    this point AND IS a known unit variant name)."""
    def __init__(self, name, enum_name, type_, line=0):
        self.name, self.enum_name, self.type, self.line = name, enum_name, type_, line

class IRBinOp(IRNode):
    def __init__(self, op, left, right, type_, line=0):
        self.op, self.left, self.right, self.type, self.line = op, left, right, type_, line

class IRBoolOp(IRNode):
    def __init__(self, op, left, right, type_, line=0):
        self.op, self.left, self.right, self.type, self.line = op, left, right, type_, line

class IRNot(IRNode):
    def __init__(self, expr, type_, line=0):
        self.expr, self.type, self.line = expr, type_, line

class IRListLit(IRNode):
    def __init__(self, items, type_, line=0):
        self.items, self.type, self.line = items, type_, line

class IRDictLit(IRNode):
    def __init__(self, pairs, type_, line=0):
        self.pairs, self.type, self.line = pairs, type_, line  # pairs: [(IRExpr, IRExpr)]

class IRStructNew(IRNode):
    def __init__(self, struct_name, fields, type_, line=0):
        self.struct_name, self.fields, self.type, self.line = struct_name, fields, type_, line  # fields: {name: IRExpr}

class IRIndex(IRNode):
    def __init__(self, obj, key, type_, line=0):
        self.obj, self.key, self.type, self.line = obj, key, type_, line

class IRFieldGet(IRNode):
    def __init__(self, obj, field, type_, line=0):
        self.obj, self.field, self.type, self.line = obj, field, type_, line

class IRCall(IRNode):
    """kind distinguishes what Name Resolution already proved this call
    target is, so bytecode generation doesn't need to re-decide it either."""
    def __init__(self, name, args, type_, kind, line=0):
        self.name, self.args, self.type, self.kind, self.line = name, args, type_, kind, line  # kind: "function"|"variant"|"builtin"

class IRMethodCall(IRNode):
    def __init__(self, obj, method, args, type_, line=0):
        self.obj, self.method, self.args, self.type, self.line = obj, method, args, type_, line

class IRSpawn(IRNode):
    def __init__(self, call, type_, line=0):
        self.call, self.type, self.line = call, type_, line

# ---- IR statements ----
class IRLet(IRNode):
    def __init__(self, name, type_, expr, line=0):
        self.name, self.type, self.expr, self.line = name, type_, expr, line

class IRFieldSet(IRNode):
    def __init__(self, obj, field, expr, line=0):
        self.obj, self.field, self.expr, self.line = obj, field, expr, line

class IRIndexSet(IRNode):
    def __init__(self, obj, key, expr, line=0):
        self.obj, self.key, self.expr, self.line = obj, key, expr, line

class IRPrint(IRNode):
    def __init__(self, expr, line=0):
        self.expr, self.line = expr, line

class IRExprStmt(IRNode):
    def __init__(self, expr, line=0):
        self.expr, self.line = expr, line

class IRReturn(IRNode):
    def __init__(self, expr, line=0):
        self.expr, self.line = expr, line

class IRBranch(IRNode):
    """Lowered form of `if`."""
    def __init__(self, cond, then_body, else_body, line=0):
        self.cond, self.then_body, self.else_body, self.line = cond, then_body, else_body, line

class IRLoop(IRNode):
    """Lowered form of `for`."""
    def __init__(self, var, iterable, body, line=0):
        self.var, self.iterable, self.body, self.line = var, iterable, body, line

class IRTryCatch(IRNode):
    def __init__(self, body, err_name, handler, line=0):
        self.body, self.err_name, self.handler, self.line = body, err_name, handler, line

class IRPattern:
    """Lowered match pattern — a plain data carrier, not an IRNode (patterns
    aren't executed as instructions, they're matched against a value)."""
    def __init__(self, kind, value=None, subnames=None):
        self.kind, self.value, self.subnames = kind, value, subnames  # kind: "lit"|"wildcard"|"bind"|"variant"

class IRMatchArm:
    def __init__(self, pattern, body):
        self.pattern, self.body = pattern, body

class IRMatch(IRNode):
    def __init__(self, expr, arms, line=0):
        self.expr, self.arms, self.line = expr, arms, line

class IRUseCapability(IRNode):
    def __init__(self, name, line=0):
        self.name, self.line = name, line

class IRUseModule(IRNode):
    def __init__(self, path, alias, line=0):
        self.path, self.alias, self.line = path, alias, line

# ---- IR top-level declarations ----
class IRFunctionDef(IRNode):
    def __init__(self, name, params, ret_type, type_params, body, line=0):
        self.name, self.params, self.ret_type, self.type_params, self.body, self.line = \
            name, params, ret_type, type_params, body, line  # params: [(name, type_or_None)]

class IRStructDef(IRNode):
    def __init__(self, name, fields, line=0):
        self.name, self.fields, self.line = name, fields, line  # fields: [name, ...]

class IREnumDef(IRNode):
    def __init__(self, name, variants, line=0):
        self.name, self.variants, self.line = name, variants, line  # variants: [(name, [field, ...])]

class IRTestDef(IRNode):
    def __init__(self, name, body, line=0):
        self.name, self.body, self.line = name, body, line

class IRProgram:
    """The complete lowered unit: top-level statements plus every declaration,
    in source order (declarations also appear in `top_level` at their
    original position, so execution order for the interpreter-equivalent
    backend is preserved; the split-out lists are for backends, like the
    bytecode compiler, that want direct access without re-scanning)."""
    def __init__(self, top_level, functions, structs, enums, tests=None):
        self.top_level = top_level
        self.functions = functions
        self.structs = structs
        self.enums = enums
        self.tests = tests or []


class IRBuilder:
    """Lowers a parsed, Resolver-checked, TypeChecker-checked AST into IR.
    Reuses a TypeChecker instance's own `infer()` so IR types are guaranteed
    consistent with what type-checking already verified — this class does
    not re-implement or duplicate inference logic."""

    def __init__(self, module_globals):
        self.tc = TypeChecker()
        self.module_globals = module_globals  # set[str] from Resolver — for IRLoad kind
        self.enum_variant_info = {}   # variant_name -> (enum_name, [field_names]) — flat, whole-program
        self.unit_variant_names = set()  # variant names with zero fields

    def build_program(self, stmts):
        self.tc.func_sigs = {
            s.name: ([t for _, t in s.params], s.ret_type, s.type_params)
            for s in stmts if isinstance(s, FnDef)
        }
        self._prescan_enums(stmts)
        top_level, functions, structs, enums, tests = [], [], [], [], []
        env = {}
        for s in stmts:
            ir = self._lower_stmt(s, env, in_function=False)
            top_level.append(ir)
            if isinstance(ir, IRFunctionDef):
                functions.append(ir)
            elif isinstance(ir, IRStructDef):
                structs.append(ir)
            elif isinstance(ir, IREnumDef):
                enums.append(ir)
            elif isinstance(ir, IRTestDef):
                tests.append(ir)
        return IRProgram(top_level, functions, structs, enums, tests)

    def _prescan_enums(self, stmts):
        """Flat, whole-program scan for enum variant names — mirrors Resolver's
        own Tier-2 prescan exactly, since variant registration in the reference
        interpreter is (like functions) a flat, non-lexical table."""
        for s in stmts:
            if isinstance(s, EnumDef):
                for vname, fields in s.variants:
                    self.enum_variant_info[vname] = (s.name, fields)
                    if not fields:
                        self.unit_variant_names.add(vname)
            elif isinstance(s, If):
                self._prescan_enums(s.body); self._prescan_enums(s.orelse)
            elif isinstance(s, For):
                self._prescan_enums(s.body)
            elif isinstance(s, Try):
                self._prescan_enums(s.body); self._prescan_enums(s.handler)
            elif isinstance(s, FnDef):
                self._prescan_enums(s.body)
            elif isinstance(s, Match):
                for _pattern, body in s.cases:
                    self._prescan_enums(body)

    def _kind_for(self, name, env, in_function):
        if name in env:
            return "local"
        return "global"  # module-level let, or (inside a function) Rule C global

    def _lower_stmt(self, node, env, in_function):
        if isinstance(node, Let):
            t = self.tc.infer(node.expr, env)
            expr_ir = self._lower_expr(node.expr, env, in_function)
            env[node.name] = node.type_ann or t
            return IRLet(node.name, node.type_ann or t, expr_ir, node.line)
        if isinstance(node, Print):
            return IRPrint(self._lower_expr(node.expr, env, in_function), 0)
        if isinstance(node, If):
            then_env, else_env = dict(env), dict(env)
            then_body = [self._lower_stmt(s, then_env, in_function) for s in node.body]
            else_body = [self._lower_stmt(s, else_env, in_function) for s in node.orelse]
            return IRBranch(self._lower_expr(node.cond, env, in_function), then_body, else_body)
        if isinstance(node, For):
            inner = dict(env); inner[node.var] = "Any"
            body = [self._lower_stmt(s, inner, in_function) for s in node.body]
            return IRLoop(node.var, self._lower_expr(node.iterable, env, in_function), body)
        if isinstance(node, Try):
            body_env = dict(env)
            body = [self._lower_stmt(s, body_env, in_function) for s in node.body]
            handler_env = dict(env); handler_env[node.err_name] = "Any"
            handler = [self._lower_stmt(s, handler_env, in_function) for s in node.handler]
            return IRTryCatch(body, node.err_name, handler)
        if isinstance(node, FnDef):
            local = {p: ("Any" if pt in node.type_params else (pt or "Any")) for p, pt in node.params}
            body = [self._lower_stmt(s, local, in_function=True) for s in node.body]
            return IRFunctionDef(node.name, node.params, node.ret_type, node.type_params, body, node.line)
        if isinstance(node, TestDef):
            local = {}
            body = [self._lower_stmt(s, local, in_function=True) for s in node.body]
            return IRTestDef(node.name, body, node.line)
        if isinstance(node, StructDef):
            return IRStructDef(node.name, node.fields)
        if isinstance(node, EnumDef):
            return IREnumDef(node.name, node.variants)
        if isinstance(node, Match):
            arms = []
            for pattern, body in node.cases:
                ir_pattern, bound = self._lower_pattern(pattern)
                case_env = dict(env)
                case_env.update({b: "Any" for b in bound})
                ir_body = [self._lower_stmt(s, case_env, in_function) for s in body]
                arms.append(IRMatchArm(ir_pattern, ir_body))
            return IRMatch(self._lower_expr(node.expr, env, in_function), arms, node.line)
        if isinstance(node, Return):
            return IRReturn(self._lower_expr(node.expr, env, in_function), node.line)
        if isinstance(node, FieldSet):
            return IRFieldSet(self._lower_expr(node.obj, env, in_function), node.field,
                               self._lower_expr(node.expr, env, in_function))
        if isinstance(node, IndexSet):
            return IRIndexSet(self._lower_expr(node.obj, env, in_function),
                               self._lower_expr(node.key, env, in_function),
                               self._lower_expr(node.expr, env, in_function))
        if isinstance(node, UseModule):
            env[node.alias] = "Any"
            return IRUseModule(node.path, node.alias, node.line)
        if isinstance(node, Use):
            return IRUseCapability(node.name, node.line)
        if isinstance(node, ExprStmt):
            return IRExprStmt(self._lower_expr(node.expr, env, in_function))
        raise LumenError(f"IRBuilder: no lowering defined for statement {type(node).__name__}")

    def _lower_pattern(self, pattern):
        if isinstance(pattern, PLit):
            return IRPattern("lit", value=pattern.value), set()
        if isinstance(pattern, PWildcard):
            return IRPattern("wildcard"), set()
        if isinstance(pattern, PVariant):
            return IRPattern("variant", value=pattern.name, subnames=pattern.subnames), set(pattern.subnames)
        if isinstance(pattern, PBind):
            if pattern.name in self.unit_variant_names:
                # errors.md §4.5: a bare name matching a known UNIT variant is a
                # variant-equality test, not a catch-all bind (unless shadowed by
                # a local binding — but a match pattern never introduces one, so
                # this check is unconditional here, matching the interpreter).
                return IRPattern("unit_variant", value=pattern.name), set()
            return IRPattern("bind", value=pattern.name), {pattern.name}
        raise LumenError(f"IRBuilder: no lowering defined for pattern {type(pattern).__name__}")

    def _lower_expr(self, node, env, in_function):
        t = self.tc.infer(node, env)
        if isinstance(node, (Num, Str, Bool)):
            return IRConst(node.v, t)
        if isinstance(node, Var):
            if node.name not in env and node.name in self.enum_variant_info:
                enum_name, fields = self.enum_variant_info[node.name]
                if not fields:
                    return IRUnitVariant(node.name, enum_name, node.name)
            return IRLoad(node.name, t, self._kind_for(node.name, env, in_function))
        if isinstance(node, BinOp):
            return IRBinOp(node.op, self._lower_expr(node.l, env, in_function),
                           self._lower_expr(node.r, env, in_function), t)
        if isinstance(node, BoolOp):
            return IRBoolOp(node.op, self._lower_expr(node.l, env, in_function),
                             self._lower_expr(node.r, env, in_function), t)
        if isinstance(node, Not):
            return IRNot(self._lower_expr(node.e, env, in_function), t)
        if isinstance(node, ListLit):
            return IRListLit([self._lower_expr(i, env, in_function) for i in node.items], t)
        if isinstance(node, DictLit):
            return IRDictLit([(self._lower_expr(k, env, in_function), self._lower_expr(v, env, in_function))
                               for k, v in node.pairs], t)
        if isinstance(node, StructLit):
            return IRStructNew(node.name, {k: self._lower_expr(v, env, in_function)
                                            for k, v in node.values.items()}, node.name)
        if isinstance(node, Index):
            return IRIndex(self._lower_expr(node.obj, env, in_function),
                            self._lower_expr(node.key, env, in_function), t)
        if isinstance(node, FieldGet):
            return IRFieldGet(self._lower_expr(node.obj, env, in_function), node.field, t)
        if isinstance(node, MethodCall):
            return IRMethodCall(self._lower_expr(node.obj, env, in_function), node.method,
                                 [self._lower_expr(a, env, in_function) for a in node.args], t)
        if isinstance(node, SpawnExpr):
            inner = self._lower_expr(node.call, env, in_function)
            return IRSpawn(inner, "Any", node.line)
        if isinstance(node, Call):
            args = [self._lower_expr(a, env, in_function) for a in node.args]
            return IRCall(node.name, args, t, self._call_kind(node.name), node.line)
        raise LumenError(f"IRBuilder: no lowering defined for expression {type(node).__name__}")

    def _call_kind(self, name):
        if name in BUILTIN_FUNCTION_NAMES:
            return "builtin"
        if name in self.tc.func_sigs:
            return "function"
        return "variant"  # Resolver already proved it's one of these three kinds


class IRValidator:
    """Structural sanity pass over an already-built IRProgram. This exists
    independently of IRBuilder so a hand-constructed or future-optimizer-
    mutated IR tree can also be checked before being handed to codegen —
    catching *compiler* bugs (a malformed IR tree), not *user program* bugs
    (those were already rejected by Resolver/TypeChecker before IR existed)."""

    def validate(self, program: IRProgram):
        errors = []
        for stmt in program.top_level:
            self._check_stmt(stmt, errors, in_function=False)
        return errors

    def _check_stmt(self, node, errors, in_function):
        if isinstance(node, IRReturn) and not in_function:
            errors.append("IRReturn appears outside any IRFunctionDef")
        if isinstance(node, IRLet):
            self._check_expr(node.expr, errors)
        elif isinstance(node, IRPrint):
            self._check_expr(node.expr, errors)
        elif isinstance(node, IRExprStmt):
            self._check_expr(node.expr, errors)
        elif isinstance(node, IRReturn):
            self._check_expr(node.expr, errors)
        elif isinstance(node, IRBranch):
            self._check_expr(node.cond, errors)
            for s in node.then_body + node.else_body:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRLoop):
            self._check_expr(node.iterable, errors)
            for s in node.body:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRTryCatch):
            for s in node.body + node.handler:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRFunctionDef):
            for s in node.body:
                self._check_stmt(s, errors, in_function=True)
        elif isinstance(node, IRTestDef):
            for s in node.body:
                self._check_stmt(s, errors, in_function=True)
        elif isinstance(node, IRMatch):
            self._check_expr(node.expr, errors)
            for arm in node.arms:
                for s in arm.body:
                    self._check_stmt(s, errors, in_function)
        elif isinstance(node, (IRFieldSet, IRIndexSet)):
            self._check_expr(node.expr, errors)

    def _check_expr(self, node, errors):
        if not isinstance(node, IRNode):
            errors.append(f"non-IR value {node!r} found where an IR expression was expected")
            return
        if not hasattr(node, "type") or node.type is None:
            errors.append(f"{type(node).__name__} is missing a `.type` annotation (typed IR invariant)")


# ---------- Bytecode VM (experimental second backend) ----------
# Demonstrates the architecture can evolve past tree-walking without
# touching the lexer/parser. Covers the core subset: let, print, if/else,
# for-in over lists, arithmetic/boolean expressions. Structs, enums, match,
# try/catch, modules, and capabilities are not yet compiled by this backend;
# the compiler raises a clear error naming the unsupported construct.

class Instr:
    __slots__ = ("op", "arg")
    def __init__(self, op, arg=None): self.op, self.arg = op, arg
    def __repr__(self): return f"{self.op} {self.arg if self.arg is not None else ''}"

class BytecodeProgram:
    """What Compiler.compile_program returns: the flat instruction array plus
    the per-function metadata CALL/RETURN need. Kept as an explicit container
    (not just the bare list) so it's clear the VM needs more than raw
    bytecode to run function calls — this is the seed of a real "code
    object" concept, deliberately not over-built until it's needed."""
    def __init__(self, code, func_entry, func_params, test_entry=None):
        self.code, self.func_entry, self.func_params = code, func_entry, func_params
        self.test_entry = test_entry or {}  # test_name (str) -> entry pc; zero params always

class Compiler:
    """Phase 2: compiles typed IR to bytecode. Deliberately never imports or
    matches on an AST node type — see IRBuilder/IRProgram above. Covers:
    variables, arithmetic/boolean/comparison, lists, indexing (read+write),
    functions/calls/recursion, structs, enums, pattern matching, try/catch.
    Modules/capabilities/concurrency still raise a clear, named "not yet
    supported" error rather than silently mis-compiling — same honesty
    policy as the rest of this project.

    Function calls use an explicit call-stack (`VM.call_stack`), not a nested
    Python call for each Lumen call, specifically so deep Lumen recursion
    does not consume Python's own call stack — the VM's iterative bytecode
    loop stays iterative regardless of Lumen-level recursion depth.

    Only TOP-LEVEL function definitions are supported (a `fn` nested inside
    another `fn`'s body is rejected with a named error). This is a
    deliberate scope boundary, not an oversight: the interpreter's function
    table is imperatively populated in general (Phase 2 Milestone 1's
    Resolver docstring covers this precisely) — for *top-level* functions
    specifically, this compiler replicates that exactly via a runtime
    REGISTER_FN marker at each function's declaration site (so calling a
    function before its own top-level `fn` statement has executed is still
    correctly a runtime NameError, matching the interpreter — see
    test_equivalence.py). Replicating the imperative-registration semantics
    for a *nested* function as well would require a fundamentally different
    (non-flat) compilation strategy; deferred rather than done unsoundly.

    try/catch is implemented with a real handler stack (`VM.try_stack`),
    not Python-level exception scoping tricks: TRY_PUSH records where to
    jump plus a scope/call-stack snapshot to restore on error, so an error
    raised arbitrarily deep inside a function called from within the try
    body still unwinds correctly (see VM.run's per-instruction try/except)."""

    def __init__(self):
        self.code = []

    def emit(self, op, arg=None):
        self.code.append(Instr(op, arg))
        return len(self.code) - 1

    def compile_program(self, ir_program: "IRProgram"):
        # Pass 0: flat, whole-program metadata (mirrors IRBuilder's own
        # flat, non-lexical tables for functions/variants — see class docstring).
        self.variant_info = {}  # variant_name -> (enum_name, [field_names])
        for enum_def in ir_program.enums:
            for vname, fields in enum_def.variants:
                self.variant_info[vname] = (enum_def.name, fields)
        self.struct_fields = {s.name: s.fields for s in ir_program.structs}  # struct_name -> [declared field names]

        # Pass 1: compile every top-level function's body into a dedicated
        # region, recording (entry_pc, [param names]) per name. This must
        # happen before Pass 2 so forward/recursive/mutual calls can resolve
        # their jump target regardless of textual order (the RUNTIME
        # callability check — REGISTER_FN / registered_fns — is what still
        # enforces the interpreter's order-dependent NameError semantics;
        # see the class docstring).
        skip = self.emit("JUMP", None)
        self.func_entry = {}
        self.func_params = {}
        self.test_entry = {}
        for fn in ir_program.functions:
            self.func_entry[fn.name] = len(self.code)
            self.func_params[fn.name] = [p for p, _t in fn.params]
            for s in fn.body:
                self.compile_stmt(s)
            self.emit("LOAD_CONST", None)
            self.emit("RETURN")  # implicit fall-off-the-end return (matches Interpreter.call_function)
        for t in ir_program.tests:
            # Test bodies compile into the SAME region, using the exact same
            # zero-arg call convention — see VM.run_one_test, which reuses
            # the shared _call_at machinery rather than inventing a second one.
            self.test_entry[t.name] = len(self.code)
            for s in t.body:
                self.compile_stmt(s)
            self.emit("LOAD_CONST", None)
            self.emit("RETURN")
        self.code[skip].arg = len(self.code)

        # Pass 2: top-level statements, in source order. An IRFunctionDef here
        # emits REGISTER_FN (its body was already compiled in Pass 1) instead
        # of being re-compiled — this is the runtime "now callable" marker.
        # IRStructDef/IREnumDef carry no executable behavior of their own
        # (construction is compiled at each use-site via the variant_info /
        # struct-literal field list already on the IR node) so they emit nothing.
        # IRTestDef is likewise inert here — its body was compiled above, but a
        # test only runs when `lumen test` explicitly asks for it by name
        # (VM.run_one_test / Interpreter.run_tests), never as part of ordinary
        # program execution — matching real test-framework convention.
        for s in ir_program.top_level:
            if isinstance(s, IRFunctionDef):
                self.emit("REGISTER_FN", s.name)
            elif isinstance(s, (IRStructDef, IREnumDef, IRTestDef)):
                pass
            else:
                self.compile_stmt(s)
        self.emit("HALT")
        return BytecodeProgram(self.code, self.func_entry, self.func_params, self.test_entry)

    def compile_stmt(self, node):
        if isinstance(node, IRLet):
            self.compile_expr(node.expr)
            self.emit("STORE", node.name)
        elif isinstance(node, IRPrint):
            self.compile_expr(node.expr)
            self.emit("PRINT")
        elif isinstance(node, IRReturn):
            self.compile_expr(node.expr)
            self.emit("RETURN")
        elif isinstance(node, IRFieldSet):
            self.compile_expr(node.obj)
            self.compile_expr(node.expr)
            self.emit("FIELD_SET", node.field)
        elif isinstance(node, IRIndexSet):
            self.compile_expr(node.obj)
            self.compile_expr(node.key)
            self.compile_expr(node.expr)
            self.emit("INDEX_SET")
        elif isinstance(node, IRBranch):
            self.compile_expr(node.cond)
            jf = self.emit("JUMP_IF_FALSE", None)
            self.emit("PUSH_SCOPE")
            for s in node.then_body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            jmp = self.emit("JUMP", None)
            self.code[jf].arg = len(self.code)
            self.emit("PUSH_SCOPE")
            for s in node.else_body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.code[jmp].arg = len(self.code)
        elif isinstance(node, IRLoop):
            self.compile_expr(node.iterable)
            self.emit("GET_ITER")
            loop_start = len(self.code)
            jend = self.emit("FOR_ITER", None)
            self.emit("PUSH_SCOPE")   # fresh scope EACH iteration — matches Interpreter's per-iteration Env
            self.emit("STORE", node.var)
            for s in node.body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.emit("JUMP", loop_start)
            self.code[jend].arg = len(self.code)
            self.emit("POP_ITER")
        elif isinstance(node, IRTryCatch):
            try_push_idx = self.emit("TRY_PUSH", None)
            self.emit("PUSH_SCOPE")
            for s in node.body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.emit("TRY_POP")   # try body completed cleanly: this handler no longer applies
            jmp_over_catch = self.emit("JUMP", None)
            catch_entry_pc = len(self.code)
            # NOTE: on the error path, VM.run's exception handler itself pushes
            # exactly one scope containing `err_name` before jumping here — the
            # catch body must NOT push its own extra scope for that binding.
            for s in node.handler: self.compile_stmt(s)
            self.emit("POP_SCOPE")  # pop the scope the exception handler pushed
            self.code[jmp_over_catch].arg = len(self.code)
            self.code[try_push_idx].arg = (catch_entry_pc, node.err_name)
        elif isinstance(node, IRMatch):
            self.compile_expr(node.expr)   # stack: [scrutinee]
            end_jumps = []
            for arm in node.arms:
                p = arm.pattern
                if p.kind == "wildcard":
                    self.emit("POP")
                    self.emit("PUSH_SCOPE")
                    for s in arm.body: self.compile_stmt(s)
                    self.emit("POP_SCOPE")
                    end_jumps.append(self.emit("JUMP", None))
                    break  # wildcard always matches; nothing after it is reachable
                elif p.kind == "bind":
                    self.emit("PUSH_SCOPE")
                    self.emit("STORE", p.value)
                    for s in arm.body: self.compile_stmt(s)
                    self.emit("POP_SCOPE")
                    end_jumps.append(self.emit("JUMP", None))
                    break  # a plain bind, like wildcard, always matches
                elif p.kind == "lit":
                    self.emit("DUP")
                    self.emit("LOAD_CONST", p.value)
                    self.emit("BINOP", "==")
                    jf = self.emit("JUMP_IF_FALSE", None)
                    self.emit("POP")
                    self.emit("PUSH_SCOPE")
                    for s in arm.body: self.compile_stmt(s)
                    self.emit("POP_SCOPE")
                    end_jumps.append(self.emit("JUMP", None))
                    self.code[jf].arg = len(self.code)
                elif p.kind == "unit_variant":
                    self.emit("DUP")
                    self.emit("IS_VARIANT", p.value)
                    jf = self.emit("JUMP_IF_FALSE", None)
                    self.emit("POP")
                    self.emit("PUSH_SCOPE")
                    for s in arm.body: self.compile_stmt(s)
                    self.emit("POP_SCOPE")
                    end_jumps.append(self.emit("JUMP", None))
                    self.code[jf].arg = len(self.code)
                elif p.kind == "variant":
                    self.emit("DUP")
                    self.emit("IS_VARIANT", p.value)
                    jf = self.emit("JUMP_IF_FALSE", None)
                    self.emit("PUSH_SCOPE")
                    self.emit("DESTRUCTURE_VARIANT", tuple(p.subnames))
                    for s in arm.body: self.compile_stmt(s)
                    self.emit("POP_SCOPE")
                    end_jumps.append(self.emit("JUMP", None))
                    self.code[jf].arg = len(self.code)
                else:
                    raise LumenError(f"VM: match pattern kind '{p.kind}' is not supported by the bytecode backend yet")
            else:
                # every arm's JUMP_IF_FALSE fell through (no wildcard/bind terminated the chain early)
                self.emit("MATCH_FAIL", node.line)
            for j in end_jumps:
                self.code[j].arg = len(self.code)
        elif isinstance(node, IRExprStmt):
            self.compile_expr(node.expr)
            self.emit("POP")
        elif isinstance(node, IRUseCapability):
            self.emit("GRANT_CAPABILITY", (node.name, node.line))
        elif isinstance(node, IRUseModule):
            self.emit("USE_MODULE", (node.path, node.alias, node.line))
        else:
            raise LumenError(f"VM: IR node '{type(node).__name__}' is not supported by the bytecode backend yet — use the default interpreter")

    def compile_expr(self, node):
        if isinstance(node, IRConst):
            self.emit("LOAD_CONST", node.value)
        elif isinstance(node, IRLoad):
            self.emit("LOAD", node.name)
        elif isinstance(node, IRUnitVariant):
            self.emit("MAKE_VARIANT", (node.enum_name, node.name, (), 0))
        elif isinstance(node, IRListLit):
            for it in node.items: self.compile_expr(it)
            self.emit("MAKE_LIST", len(node.items))
        elif isinstance(node, IRDictLit):
            for k, v in node.pairs:
                self.compile_expr(k)
                self.compile_expr(v)
            self.emit("MAKE_DICT", len(node.pairs))
        elif isinstance(node, IRStructNew):
            field_names = list(node.fields.keys())
            for fname in field_names:
                self.compile_expr(node.fields[fname])
            declared = frozenset(self.struct_fields.get(node.struct_name, ()))
            self.emit("MAKE_STRUCT", (node.struct_name, tuple(field_names), declared))
        elif isinstance(node, IRFieldGet):
            self.compile_expr(node.obj)
            self.emit("FIELD_GET", node.field)
        elif isinstance(node, IRBinOp):
            self.compile_expr(node.left)
            self.compile_expr(node.right)
            self.emit("BINOP", node.op)
        elif isinstance(node, IRBoolOp):
            self.compile_expr(node.left)
            self.emit("DUP")
            j = self.emit("JUMP_IF_TRUE" if node.op == "or" else "JUMP_IF_FALSE", None)
            self.emit("POP")             # left didn't short-circuit: discard it, evaluate right
            self.compile_expr(node.right)
            self.code[j].arg = len(self.code)  # short-circuit case lands here, with `left` still on the stack
        elif isinstance(node, IRNot):
            self.compile_expr(node.expr)
            self.emit("NOT")
        elif isinstance(node, IRIndex):
            self.compile_expr(node.obj)
            self.compile_expr(node.key)
            self.emit("INDEX_GET")
        elif isinstance(node, IRCall) and node.kind == "function":
            for a in node.args:
                self.compile_expr(a)
            self.emit("CALL", (node.name, len(node.args)))
        elif isinstance(node, IRCall) and node.kind == "variant":
            enum_name, field_names = self.variant_info[node.name]
            for a in node.args:
                self.compile_expr(a)
            self.emit("MAKE_VARIANT", (enum_name, node.name, tuple(field_names), len(node.args)))
        elif isinstance(node, IRCall) and node.kind == "builtin":
            if node.name not in VM_SUPPORTED_BUILTINS:
                raise LumenError(
                    f"VM: builtin '{node.name}()' is not supported by the bytecode backend yet "
                    f"(concurrency primitives require thread support not yet implemented in the VM) "
                    f"— use the default interpreter"
                )
            for a in node.args:
                self.compile_expr(a)
            self.emit("CALL_BUILTIN", (node.name, len(node.args)))
        elif isinstance(node, IRMethodCall):
            self.compile_expr(node.obj)
            for a in node.args:
                self.compile_expr(a)
            self.emit("CALL_METHOD", (node.method, len(node.args)))
        else:
            raise LumenError(f"VM: IR expression '{type(node).__name__}' is not supported by the bytecode backend yet — use the default interpreter")

class VMModule:
    """A loaded Lumen module as seen from the VM: the sub-VM instance that ran
    its top-level code (so `.scopes[0]` holds its globals and
    `.registered_fns`/`.func_entry`/`.func_params` on its own compiled
    `program` are ready for cross-module calls) plus the program itself,
    which `call_exported_function` needs alongside the sub-VM's state."""
    def __init__(self, alias, vm_instance, program):
        self.alias, self.vm, self.program = alias, vm_instance, program
    def __repr__(self): return f"<module {self.alias}>"


class VM:
    def __init__(self):
        self.stack = []
        self.scopes = [{}]  # scope stack — [-1] is innermost; matches Env-chain semantics exactly
        self.iter_stack = []
        self.call_stack = []       # [(return_pc, saved_scopes), ...] — explicit, so Lumen recursion
                                    # depth never consumes Python's own call stack (see Compiler docstring)
        self.registered_fns = set()  # runtime "has this fn statement executed yet" — mirrors Interpreter.functions
        self.try_stack = []        # [(catch_pc, err_name, saved_scopes, saved_call_stack_depth), ...]
        self.capabilities = set()  # granted by GRANT_CAPABILITY — mirrors Interpreter.capabilities exactly
        self.base_dir = "."        # for resolving `use "path" as alias` — set by run_file_vm / USE_MODULE
        self.loading = []          # shared cycle-detection chain (ordered!) across nested module VMs — modules.md §5.6
        self.pc = 0
        self._interp = Interpreter()  # reuse apply_op / stringify for consistent semantics; persistent,
                                       # not re-created per instruction, so cross-module calls share it too

    def load(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise LumenError(f"NameError: '{name}' is not defined")

    def store(self, name, value):
        self.scopes[-1][name] = value  # always innermost — matches language.md §1.11 Rule B

    def run(self, program: "BytecodeProgram"):
        self.pc = 0
        while self._exec_one(program):
            pass

    def call_exported_function(self, program, name, args, alias="module"):
        """Calls a function that belongs to THIS (sub-module) VM instance's
        own compiled program, from outside — used by CALL_METHOD when the
        importing module's code does `module.fn(args)`. Reuses `_call_at`
        exactly (not a separate/duplicated call mechanism), so a cross-module
        call has identical semantics — recursion, nested try/catch, further
        nested calls into yet other modules — to an ordinary in-module call."""
        if name not in self.registered_fns:
            raise LumenError(f"NameError: module '{alias}' has no function '{name}'")
        params = program.func_params[name]
        if len(args) != len(params):
            raise LumenError(f"TypeError: {name}() expects {len(params)} arguments, got {len(args)}")
        return self._call_at(program, program.func_entry[name], params, args)

    def run_one_test(self, program, test_name):
        """Runs a single `test "name": ...` block to completion or first
        failure, via the exact same nested-call machinery as an ordinary or
        cross-module function call. Returns (passed: bool, message: str|None)
        — never raises, since a failing assertion is the *expected*, handled
        outcome for a test runner, not a program-terminating error."""
        entry_pc = program.test_entry[test_name]
        try:
            self._call_at(program, entry_pc, [], [])
            return True, None
        except (LumenError, LumenRaise) as e:
            return False, (e.message if isinstance(e, LumenRaise) else str(e))

    def _call_at(self, program, entry_pc, params, args):
        """Shared machinery underlying every 'call this and get the return
        value from outside the normal bytecode stream' need: cross-module
        calls (call_exported_function) and running a named test
        (run_one_test). Binds args into a fresh Rule-C scope and runs
        _exec_one in a nested loop until the matching RETURN unwinds the
        call stack back to where we started."""
        saved_pc = self.pc
        target_depth = len(self.call_stack)
        self.call_stack.append((-1, self.scopes))  # -1: never actually jumped to — see loop-exit note below
        self.scopes = [self.scopes[0], dict(zip(params, args))]
        self.pc = entry_pc
        while len(self.call_stack) > target_depth:
            if not self._exec_one(program):
                raise LumenError("VM: internal error — HALT reached during a nested call")
        # the matching RETURN already popped call_stack back to target_depth and
        # pushed the return value; its bogus `self.pc = -1` is never executed
        # because the while-condition above stops calling _exec_one first.
        result = self.stack.pop()
        self.pc = saved_pc
        return result

    def _exec_one(self, program: "BytecodeProgram"):
        """Executes exactly one instruction (or one try/catch error-recovery
        jump), mutating self.pc in place. Returns False on HALT, True
        otherwise. Shared by run() and call_exported_function() so every
        execution path — top-level, in-module calls, cross-module calls —
        has exactly one implementation of what each opcode does."""
        code = program.code
        interp = self._interp
        try:
            instr = code[self.pc]
            op, arg = instr.op, instr.arg
            if op == "HALT": return False
            elif op == "LOAD_CONST": self.stack.append(arg)
            elif op == "LOAD": self.stack.append(self.load(arg))
            elif op == "STORE": self.store(arg, self.stack.pop())
            elif op == "POP": self.stack.pop()
            elif op == "DUP": self.stack.append(self.stack[-1])
            elif op == "PUSH_SCOPE": self.scopes.append({})
            elif op == "POP_SCOPE": self.scopes.pop()
            elif op == "REGISTER_FN": self.registered_fns.add(arg)
            elif op == "GRANT_CAPABILITY":
                name, line = arg
                if name not in ("fs", "net"):
                    raise LumenError(f"ModuleError: unknown module '{name}' at line {line}")
                self.capabilities.add(name)
            elif op == "USE_MODULE":
                self._use_module(arg)
            elif op == "TRY_PUSH":
                catch_pc, err_name = arg
                self.try_stack.append((catch_pc, err_name, list(self.scopes), len(self.call_stack)))
            elif op == "TRY_POP":
                self.try_stack.pop()
            elif op == "CALL":
                name, argc = arg
                if name not in self.registered_fns:
                    # matches Interpreter's imperative registration exactly — see
                    # test_equivalence.py's forward-reference-fails-at-top-level case
                    raise LumenError(f"NameError: function '{name}' is not defined")
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                params = program.func_params[name]
                if len(call_args) != len(params):
                    raise LumenError(f"TypeError: {name}() expects {len(params)} arguments, got {len(call_args)}")
                self.call_stack.append((self.pc + 1, self.scopes))
                self.scopes = [self.scopes[0], dict(zip(params, call_args))]  # Rule C: global + own params only
                self.pc = program.func_entry[name]
                return True
            elif op == "RETURN":
                retval = self.stack.pop()
                self.pc, self.scopes = self.call_stack.pop()
                self.stack.append(retval)
                return True
            elif op == "CALL_METHOD":
                method, argc = arg
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                mod = self.stack.pop()
                if not isinstance(mod, VMModule):
                    raise LumenError(f"TypeError: '{method}' is not callable on this value")
                self.stack.append(mod.vm.call_exported_function(mod.program, method, call_args, mod.alias))
            elif op == "CALL_BUILTIN":
                name, argc = arg
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                if name == "len":
                    if not isinstance(call_args[0], (list, dict, str)):
                        raise LumenError(f"TypeError: len() expects a List, Dict, or Str, got {type(call_args[0]).__name__}")
                    self.stack.append(len(call_args[0]))
                elif name == "push":
                    if not isinstance(call_args[0], list):
                        raise LumenError(f"TypeError: push() expects a List as its first argument, got {type(call_args[0]).__name__}")
                    call_args[0].append(call_args[1])
                    self.stack.append(None)
                elif name == "keys":
                    if not isinstance(call_args[0], dict):
                        raise LumenError(f"TypeError: keys() expects a Dict, got {type(call_args[0]).__name__}")
                    self.stack.append(list(call_args[0].keys()))
                elif name == "values":
                    if not isinstance(call_args[0], dict):
                        raise LumenError(f"TypeError: values() expects a Dict, got {type(call_args[0]).__name__}")
                    self.stack.append(list(call_args[0].values()))
                elif name == "fail":
                    raise LumenRaise(call_args[0] if call_args else "error")
                elif name == "assert":
                    if not interp.truthy(call_args[0]):
                        raise LumenRaise("assertion failed")
                    self.stack.append(None)
                elif name == "assert_eq":
                    if call_args[0] != call_args[1]:
                        raise LumenRaise(f"assertion failed: {interp.stringify(call_args[0])} != {interp.stringify(call_args[1])}")
                    self.stack.append(None)
                elif name == "assert_ne":
                    if call_args[0] == call_args[1]:
                        raise LumenRaise(f"assertion failed: {interp.stringify(call_args[0])} == {interp.stringify(call_args[1])}")
                    self.stack.append(None)
                elif name == "now":
                    import time as _time
                    self.stack.append(_time.time())
                elif name == "read_line":
                    try:
                        self.stack.append(input())
                    except EOFError:
                        raise LumenRaise("end of input")
                elif name in ("read_file", "write_file", "http_get"):
                    self.stack.append(self._call_capability(name, call_args))
            elif op == "PRINT": print(interp.stringify(self.stack.pop()))
            elif op == "MAKE_LIST":
                items = [self.stack.pop() for _ in range(arg)][::-1]
                self.stack.append(items)
            elif op == "MAKE_DICT":
                flat = [self.stack.pop() for _ in range(2 * arg)][::-1]
                d = {}
                for i in range(0, len(flat), 2):
                    d[flat[i]] = flat[i + 1]
                self.stack.append(d)
            elif op == "MAKE_STRUCT":
                struct_name, field_names, declared_fields = arg
                given = set(field_names)
                if given != declared_fields:
                    missing = sorted(declared_fields - given)
                    extra = sorted(given - declared_fields)
                    detail = []
                    if missing: detail.append(f"missing {missing}")
                    if extra: detail.append(f"unexpected {extra}")
                    # pop the values that WERE pushed, to leave the stack balanced, before raising
                    for _ in field_names: self.stack.pop()
                    raise LumenError(f"TypeError: {struct_name} {{ }} " + ", ".join(detail))
                values = [self.stack.pop() for _ in field_names][::-1]
                self.stack.append(LumenInstance(struct_name, dict(zip(field_names, values))))
            elif op == "FIELD_GET":
                obj = self.stack.pop()
                if isinstance(obj, VMModule):
                    if arg in obj.vm.scopes[0]:
                        self.stack.append(obj.vm.scopes[0][arg])
                    else:
                        raise LumenError(f"NameError: module '{obj.alias}' has no value '{arg}'")
                elif isinstance(obj, LumenInstance):
                    if arg not in obj.fields:
                        raise LumenError(f"NameError: {obj.struct_name} has no field '{arg}'")
                    self.stack.append(obj.fields[arg])
                else:
                    raise LumenError(f"TypeError: cannot read field '{arg}' on non-struct value")
            elif op == "FIELD_SET":
                value = self.stack.pop(); obj = self.stack.pop()
                if not isinstance(obj, LumenInstance):
                    raise LumenError(f"TypeError: cannot set field '{arg}' on non-struct value")
                obj.fields[arg] = value
            elif op == "MAKE_VARIANT":
                enum_name, variant_name, field_names, argc = arg
                if argc != len(field_names):
                    for _ in range(argc): self.stack.pop()  # keep the stack balanced before raising
                    raise LumenError(f"TypeError: {variant_name}() expects {len(field_names)} value(s), got {argc}")
                values = [self.stack.pop() for _ in field_names][::-1]
                self.stack.append(EnumInstance(enum_name, variant_name, dict(zip(field_names, values))))
            elif op == "IS_VARIANT":
                v = self.stack.pop()
                self.stack.append(isinstance(v, EnumInstance) and v.variant == arg)
            elif op == "DESTRUCTURE_VARIANT":
                v = self.stack.pop()
                for sname, val in zip(arg, v.fields.values()):
                    self.store(sname, val)
            elif op == "MATCH_FAIL":
                v = self.stack.pop()
                raise LumenError(f"MatchError at line {arg}: no pattern matched {interp.stringify(v)}")
            elif op == "INDEX_GET":
                key = self.stack.pop(); obj = self.stack.pop()
                try:
                    self.stack.append(obj[int(key)] if isinstance(obj, list) else obj[key])
                except IndexError:
                    raise LumenRaise(f"index {key} is out of range (length {len(obj)})")
                except KeyError:
                    raise LumenRaise(f"key {key!r} not found")
                except TypeError:
                    raise LumenError(f"TypeError: cannot index into this value with {key!r}")
            elif op == "INDEX_SET":
                value = self.stack.pop(); key = self.stack.pop(); obj = self.stack.pop()
                try:
                    obj[key] = value
                except (TypeError, IndexError) as e:
                    raise LumenError(f"TypeError: cannot assign index on this value ({e})")
            elif op == "BINOP":
                r = self.stack.pop(); l = self.stack.pop()
                self.stack.append(interp.apply_op(arg, l, r))
            elif op == "NOT":
                self.stack.append(not self.stack.pop())
            elif op == "JUMP": self.pc = arg; return True
            elif op == "JUMP_IF_FALSE":
                if not self.stack.pop(): self.pc = arg; return True
            elif op == "JUMP_IF_TRUE":
                if self.stack.pop(): self.pc = arg; return True
            elif op == "GET_ITER":
                self.iter_stack.append(iter(self.stack.pop()))
            elif op == "FOR_ITER":
                try: self.stack.append(next(self.iter_stack[-1]))
                except StopIteration: self.pc = arg; return True
            elif op == "POP_ITER": self.iter_stack.pop()
            else:
                raise LumenError(f"VM: unknown opcode {op}")
            self.pc += 1
            return True
        except (LumenError, LumenRaise) as e:
            if not self.try_stack:
                raise
            catch_pc, err_name, saved_scopes, saved_call_depth = self.try_stack.pop()
            self.scopes = saved_scopes
            self.call_stack = self.call_stack[:saved_call_depth]
            msg = e.message if isinstance(e, LumenRaise) else str(e)
            self.scopes.append({err_name: LumenInstance("Error", {"message": msg})})
            self.pc = catch_pc
            return True

    def _use_module(self, arg):
        import os
        path, alias, line = arg
        full = os.path.join(self.base_dir, path)
        if not os.path.isfile(full):
            pkg_path = os.path.join(self.base_dir, "lumen_packages", path)
            if os.path.isfile(pkg_path):
                full = pkg_path
            else:
                raise LumenError(
                    f"ModuleError: cannot find module '{path}' at line {line} "
                    f"(looked next to this file and in lumen_packages/)"
                )
        abs_full = os.path.abspath(full)
        if abs_full in self.loading:
            cycle = " -> ".join(os.path.basename(p) for p in self.loading) + f" -> {os.path.basename(abs_full)}"
            raise LumenError(
                f"ModuleError: circular import detected at line {line}: {cycle} "
                f"(module '{path}' is already being loaded higher up this import chain)"
            )
        with open(full) as f:
            src = f.read()
        self.loading.append(abs_full)
        try:
            sub_ast = Parser(lex(src)).parse_program()
            sub_resolver = Resolver()
            sub_resolver.resolve_program(sub_ast)
            TypeChecker().check_program(sub_ast)
            sub_ir = IRBuilder(sub_resolver.module_globals).build_program(sub_ast)
            ir_errors = IRValidator().validate(sub_ir)
            if ir_errors:
                raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
            sub_program = Compiler().compile_program(sub_ir)
            sub_vm = VM()
            sub_vm.base_dir = os.path.dirname(full) or "."
            sub_vm.loading = self.loading  # shared cycle-detection set, matching interpreter exactly
            sub_vm.run(sub_program)
        except (SyntaxError, LumenError) as e:
            raise LumenError(f"ModuleError: error loading '{path}': {e}")
        finally:
            self.loading.remove(abs_full) if abs_full in self.loading else None
        self.store(alias, VMModule(alias, sub_vm, sub_program))

    def _call_capability(self, name, call_args):
        needed = "fs" if name in ("read_file", "write_file") else "net"
        if needed not in self.capabilities:
            raise LumenError(
                f"PermissionError: {name}() needs the '{needed}' capability — "
                f"add 'use {needed}' at the top of your program to allow it"
            )
        if name == "read_file":
            try:
                with open(call_args[0]) as f:
                    return f.read()
            except OSError as e:
                raise LumenRaise(f"could not read '{call_args[0]}': {e.strerror}")
        if name == "write_file":
            try:
                with open(call_args[0], "w") as f:
                    f.write(call_args[1])
                return None
            except OSError as e:
                raise LumenRaise(f"could not write '{call_args[0]}': {e.strerror}")
        if name == "http_get":
            import urllib.request
            try:
                with urllib.request.urlopen(call_args[0], timeout=5) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                raise LumenRaise(f"http_get failed: {e}")

def run_file_vm(path):
    import os
    with open(path) as f:
        src = f.read()
    ast = Parser(lex(src)).parse_program()
    resolver = Resolver()
    resolver.resolve_program(ast)
    TypeChecker().check_program(ast)
    ir_program = IRBuilder(resolver.module_globals).build_program(ast)
    ir_errors = IRValidator().validate(ir_program)
    if ir_errors:
        raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
    code = Compiler().compile_program(ir_program)
    vm = VM()
    vm.base_dir = os.path.dirname(os.path.abspath(path))
    vm.run(code)

class ReturnSignal(Exception):
    def __init__(self, value): self.value = value

class Env:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent
    def get(self, name):
        if name in self.vars: return self.vars[name]
        if self.parent: return self.parent.get(name)
        raise LumenError(f"NameError: '{name}' is not defined")
    def has(self, name):
        if name in self.vars: return True
        if self.parent: return self.parent.has(name)
        return False
    def set(self, name, value):
        self.vars[name] = value

class LumenRaise(Exception):
    """A catchable runtime error, carries a Lumen-visible error object."""
    def __init__(self, message):
        self.message = message

class Interpreter:
    CAPABILITY_MODULES = {"fs", "net"}

    def __init__(self, base_dir=".", loading=None):
        self.globals = Env()
        self.functions = {}
        self.structs = {}
        self.capabilities = set()
        self.base_dir = base_dir
        self.enum_variants = {}   # variant_name -> (enum_name, [field_names])
        self.loading = loading if loading is not None else []  # ordered: abs paths currently being imported (cycle guard)
        self.tests = []           # [(name, body)] — registered, not executed inline; see run_tests()

    def run(self, stmts):
        for s in stmts:
            self.exec_stmt(s, self.globals)

    def exec_block(self, stmts, env):
        for s in stmts:
            self.exec_stmt(s, env)

    def exec_stmt(self, node, env):
        if isinstance(node, Let):
            env.set(node.name, self.eval(node.expr, env))
        elif isinstance(node, Print):
            print(self.stringify(self.eval(node.expr, env)))
        elif isinstance(node, If):
            if self.truthy(self.eval(node.cond, env)):
                self.exec_block(node.body, Env(env))
            else:
                self.exec_block(node.orelse, Env(env))
        elif isinstance(node, For):
            items = self.eval(node.iterable, env)
            for item in items:
                loop_env = Env(env)
                loop_env.set(node.var, item)
                self.exec_block(node.body, loop_env)
        elif isinstance(node, FnDef):
            self.functions[node.name] = node
        elif isinstance(node, TestDef):
            self.tests.append((node.name, node.body))
        elif isinstance(node, StructDef):
            self.structs[node.name] = node.fields
        elif isinstance(node, EnumDef):
            for vname, fields in node.variants:
                self.enum_variants[vname] = (node.name, fields)
        elif isinstance(node, Match):
            value = self.eval(node.expr, env)
            for pattern, body in node.cases:
                case_env = Env(env)
                if self.match_pattern(pattern, value, case_env):
                    self.exec_block(body, case_env)
                    break
            else:
                raise LumenError(f"MatchError at line {node.line}: no pattern matched {self.stringify(value)}")
        elif isinstance(node, Use):
            if node.name not in self.CAPABILITY_MODULES:
                raise LumenError(f"ModuleError: unknown module '{node.name}' at line {node.line}")
            self.capabilities.add(node.name)
        elif isinstance(node, UseModule):
            import os
            full = os.path.join(self.base_dir, node.path)
            if not os.path.isfile(full):
                pkg_path = os.path.join(self.base_dir, "lumen_packages", node.path)
                if os.path.isfile(pkg_path):
                    full = pkg_path
                else:
                    raise LumenError(
                        f"ModuleError: cannot find module '{node.path}' at line {node.line} "
                        f"(looked next to this file and in lumen_packages/)"
                    )
            abs_full = os.path.abspath(full)
            if abs_full in self.loading:
                cycle = " -> ".join(os.path.basename(p) for p in self.loading) + f" -> {os.path.basename(abs_full)}"
                raise LumenError(
                    f"ModuleError: circular import detected at line {node.line}: {cycle} "
                    f"(module '{node.path}' is already being loaded higher up this import chain)"
                )
            with open(full) as f:
                src = f.read()
            self.loading.append(abs_full)
            try:
                sub = Interpreter(base_dir=os.path.dirname(full) or ".", loading=self.loading)
                mod_ast = Parser(lex(src)).parse_program()
                Resolver().resolve_program(mod_ast)
                TypeChecker().check_program(mod_ast)
                sub.run(mod_ast)
            except (SyntaxError, LumenError) as e:
                raise LumenError(f"ModuleError: error loading '{node.path}': {e}")
            finally:
                self.loading.remove(abs_full) if abs_full in self.loading else None
            env.set(node.alias, LumenModule(node.alias, sub))
        elif isinstance(node, Return):
            raise ReturnSignal(self.eval(node.expr, env))
        elif isinstance(node, FieldSet):
            obj = self.eval(node.obj, env)
            if not isinstance(obj, LumenInstance):
                raise LumenError(f"TypeError: cannot set field '{node.field}' on non-struct value")
            obj.fields[node.field] = self.eval(node.expr, env)
        elif isinstance(node, Try):
            try:
                self.exec_block(node.body, Env(env))
            except (LumenRaise, LumenError, ZeroDivisionError) as e:
                handler_env = Env(env)
                msg = e.message if isinstance(e, LumenRaise) else str(e)
                handler_env.set(node.err_name, LumenInstance("Error", {"message": msg}))
                self.exec_block(node.handler, handler_env)
        elif isinstance(node, IndexSet):
            obj = self.eval(node.obj, env)
            key = self.eval(node.key, env)
            val = self.eval(node.expr, env)
            try:
                obj[key] = val
            except (TypeError, IndexError) as e:
                raise LumenError(f"TypeError: cannot assign index on this value ({e})")
        elif isinstance(node, ExprStmt):
            self.eval(node.expr, env)
        else:
            raise LumenError(f"Unknown statement: {node}")

    def eval(self, node, env):
        if isinstance(node, Num): return node.v
        if isinstance(node, Str): return node.v
        if isinstance(node, Bool): return node.v
        if isinstance(node, ListLit): return [self.eval(i, env) for i in node.items]
        if isinstance(node, DictLit):
            return {self.eval(k, env): self.eval(v, env) for k, v in node.pairs}
        if isinstance(node, Index):
            obj = self.eval(node.obj, env)
            key = self.eval(node.key, env)
            try:
                return obj[int(key)] if isinstance(obj, list) else obj[key]
            except IndexError:
                raise LumenRaise(f"index {key} is out of range (length {len(obj)})")
            except KeyError:
                raise LumenRaise(f"key {key!r} not found")
            except TypeError:
                raise LumenError(f"TypeError: cannot index into this value with {key!r}")
        if isinstance(node, Var):
            if node.name in self.enum_variants and not env.has(node.name):
                enum_name, fields = self.enum_variants[node.name]
                if not fields:
                    return EnumInstance(enum_name, node.name, {})
            return env.get(node.name)
        if isinstance(node, BinOp):
            l = self.eval(node.l, env)
            r = self.eval(node.r, env)
            return self.apply_op(node.op, l, r)
        if isinstance(node, BoolOp):
            l = self.truthy(self.eval(node.l, env))
            if node.op == "and":
                return l and self.truthy(self.eval(node.r, env))
            return l or self.truthy(self.eval(node.r, env))
        if isinstance(node, Not):
            return not self.truthy(self.eval(node.e, env))
        if isinstance(node, StructLit):
            if node.name not in self.structs:
                raise LumenError(f"TypeError: unknown struct '{node.name}'")
            expected = set(self.structs[node.name])
            given = set(node.values.keys())
            if expected != given:
                missing = expected - given
                extra = given - expected
                detail = []
                if missing: detail.append(f"missing {sorted(missing)}")
                if extra: detail.append(f"unexpected {sorted(extra)}")
                raise LumenError(f"TypeError: {node.name} {{ }} " + ", ".join(detail))
            values = {k: self.eval(v, env) for k, v in node.values.items()}
            return LumenInstance(node.name, values)
        if isinstance(node, FieldGet):
            obj = self.eval(node.obj, env)
            if isinstance(obj, LumenModule):
                if node.field in obj.interp.globals.vars:
                    return obj.interp.globals.vars[node.field]
                raise LumenError(f"NameError: module '{obj.name}' has no value '{node.field}'")
            if not isinstance(obj, LumenInstance):
                raise LumenError(f"TypeError: cannot read field '{node.field}' on non-struct value")
            if node.field not in obj.fields:
                raise LumenError(f"NameError: {obj.struct_name} has no field '{node.field}'")
            return obj.fields[node.field]
        if isinstance(node, MethodCall):
            obj = self.eval(node.obj, env)
            if not isinstance(obj, LumenModule):
                raise LumenError(f"TypeError: '{node.method}' is not callable on this value")
            if node.method not in obj.interp.functions:
                raise LumenError(f"NameError: module '{obj.name}' has no function '{node.method}'")
            args = [self.eval(a, env) for a in node.args]
            return obj.interp.call_function(node.method, args)
        if isinstance(node, SpawnExpr):
            call = node.call
            if call.name not in self.functions:
                raise LumenError(f"NameError: function '{call.name}' is not defined (line {node.line})")
            args = [self.eval(a, env) for a in call.args]
            task = TaskHandle()
            def worker():
                try:
                    task.result = self.call_function(call.name, args)
                except (LumenError, LumenRaise) as e:
                    task.error = e
                task.done.set()
            task.thread = threading.Thread(target=worker, daemon=True)
            task.thread.start()
            return task
        if isinstance(node, Call):
            args = [self.eval(a, env) for a in node.args]
            if node.name == "len":
                if not isinstance(args[0], (list, dict, str)):
                    raise LumenError(f"TypeError: len() expects a List, Dict, or Str, got {type(args[0]).__name__}")
                return len(args[0])
            if node.name == "wait":
                task = args[0]
                if not isinstance(task, TaskHandle):
                    raise LumenError("TypeError: wait() expects a task returned by spawn")
                task.done.wait()
                if task.error: raise task.error
                return task.result
            if node.name == "chan": return LumenChannel()
            if node.name == "send":
                if not isinstance(args[0], LumenChannel):
                    raise LumenError("TypeError: send() expects a channel returned by chan()")
                args[0].q.put(args[1]); return None
            if node.name == "receive":
                if not isinstance(args[0], LumenChannel):
                    raise LumenError("TypeError: receive() expects a channel returned by chan()")
                return args[0].q.get()
            if node.name == "push":
                if not isinstance(args[0], list):
                    raise LumenError(f"TypeError: push() expects a List as its first argument, got {type(args[0]).__name__}")
                args[0].append(args[1]); return None
            if node.name == "keys":
                if not isinstance(args[0], dict):
                    raise LumenError(f"TypeError: keys() expects a Dict, got {type(args[0]).__name__}")
                return list(args[0].keys())
            if node.name == "values":
                if not isinstance(args[0], dict):
                    raise LumenError(f"TypeError: values() expects a Dict, got {type(args[0]).__name__}")
                return list(args[0].values())
            if node.name == "fail":
                raise LumenRaise(args[0] if args else "error")
            if node.name == "assert":
                if not self.truthy(args[0]):
                    raise LumenRaise("assertion failed")
                return None
            if node.name == "assert_eq":
                if args[0] != args[1]:
                    raise LumenRaise(f"assertion failed: {self.stringify(args[0])} != {self.stringify(args[1])}")
                return None
            if node.name == "assert_ne":
                if args[0] == args[1]:
                    raise LumenRaise(f"assertion failed: {self.stringify(args[0])} == {self.stringify(args[1])}")
                return None
            if node.name == "now":
                import time as _time
                return _time.time()
            if node.name == "read_line":
                try:
                    return input()
                except EOFError:
                    raise LumenRaise("end of input")
            if node.name in ("read_file", "write_file", "http_get"):
                return self.call_capability(node.name, args)
            if node.name in self.enum_variants:
                enum_name, fields = self.enum_variants[node.name]
                if len(args) != len(fields):
                    raise LumenError(f"TypeError: {node.name}() expects {len(fields)} value(s), got {len(args)}")
                return EnumInstance(enum_name, node.name, dict(zip(fields, args)))
            if node.name not in self.functions:
                raise LumenError(f"NameError: function '{node.name}' is not defined")
            return self.call_function(node.name, args)
        raise LumenError(f"Unknown expression: {node}")

    def call_function(self, name, args):
        fn = self.functions[name]
        if len(args) != len(fn.params):
            raise LumenError(f"TypeError: {name}() expects {len(fn.params)} arguments, got {len(args)}")
        call_env = Env(self.globals)
        for (p, _ptype), a in zip(fn.params, args):
            call_env.set(p, a)
        try:
            self.exec_block(fn.body, call_env)
        except ReturnSignal as r:
            return r.value
        except RecursionError:
            # The reference implementation's tree-walking call_function recurses
            # in lockstep with Lumen-level recursion, so unbounded Lumen
            # recursion becomes unbounded Python recursion. Without this,
            # a raw Python RecursionError traceback would leak straight to
            # the user — exactly the class of bug this project treats as
            # unconditionally unacceptable elsewhere (see docs/errors.md
            # §4.2's uniform-catchability guarantee). Converting it to the
            # ordinary runtime-failure protocol makes it catchable by an
            # enclosing try/catch like any other failure, and gives an
            # uncaught one a clean message instead of a Python stack dump.
            raise LumenRaise("stack overflow: maximum call depth exceeded")
        return None

    def run_tests(self):
        """Runs every registered `test "name": ...` block (only meaningful
        after `run()` has already executed the file's top-level code, so all
        tests were registered — and any top-level setup `let`s exist). Each
        test runs in its own Env(self.globals), matching a function call's
        Rule-C isolation. Returns [(name, passed: bool, message: str|None)]."""
        results = []
        for name, body in self.tests:
            test_env = Env(self.globals)
            try:
                self.exec_block(body, test_env)
                results.append((name, True, None))
            except (LumenError, LumenRaise) as e:
                msg = e.message if isinstance(e, LumenRaise) else str(e)
                results.append((name, False, msg))
            except RecursionError:
                results.append((name, False, "stack overflow: maximum call depth exceeded"))
        return results

    def match_pattern(self, pattern, value, env):
        if isinstance(pattern, PLit):
            return value == pattern.value
        if isinstance(pattern, PWildcard):
            return True
        if isinstance(pattern, PVariant):
            if not isinstance(value, EnumInstance) or value.variant != pattern.name:
                return False
            for sname, fval in zip(pattern.subnames, value.fields.values()):
                env.set(sname, fval)
            return True
        if isinstance(pattern, PBind):
            if pattern.name in self.enum_variants:
                enum_name, fields = self.enum_variants[pattern.name]
                if not fields:
                    return isinstance(value, EnumInstance) and value.variant == pattern.name
            env.set(pattern.name, value)
            return True
        return False

    def call_capability(self, name, args):
        needed = "fs" if name in ("read_file", "write_file") else "net"
        if needed not in self.capabilities:
            raise LumenError(
                f"PermissionError: {name}() needs the '{needed}' capability — "
                f"add 'use {needed}' at the top of your program to allow it"
            )
        if name == "read_file":
            try:
                with open(args[0]) as f:
                    return f.read()
            except OSError as e:
                raise LumenRaise(f"could not read '{args[0]}': {e.strerror}")
        if name == "write_file":
            try:
                with open(args[0], "w") as f:
                    f.write(args[1])
                return None
            except OSError as e:
                raise LumenRaise(f"could not write '{args[0]}': {e.strerror}")
        if name == "http_get":
            import urllib.request
            try:
                with urllib.request.urlopen(args[0], timeout=5) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                raise LumenRaise(f"http_get failed: {e}")

    def apply_op(self, op, l, r):
        try:
            if op == "+":
                if isinstance(l, str) or isinstance(r, str):
                    return self.stringify(l) + self.stringify(r)
                return l + r
            if op == "-": return l - r
            if op == "*": return l * r
            if op == "/":
                if r == 0:
                    raise LumenRaise("division by zero")
                return l / r
            if op == "%": return l % r
            if op == "==": return l == r
            if op == "!=": return l != r
            if op == "<": return l < r
            if op == ">": return l > r
            if op == "<=": return l <= r
            if op == ">=": return l >= r
        except TypeError:
            raise LumenError(f"TypeError: cannot apply '{op}' to {type(l).__name__} and {type(r).__name__}")
        raise LumenError(f"Unknown operator: {op}")

    def truthy(self, v):
        return bool(v)

    def stringify(self, v):
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{self.stringify(k)}: {self.stringify(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list): return "[" + ", ".join(self.stringify(x) for x in v) + "]"
        if isinstance(v, LumenInstance): return repr(v)
        if isinstance(v, EnumInstance): return repr(v)
        if v is None: return "nothing"
        return str(v)

def run_file(path):
    import os
    with open(path) as f:
        src = f.read()
    tokens = lex(src)
    ast = Parser(tokens).parse_program()
    Resolver().resolve_program(ast)
    TypeChecker().check_program(ast)
    Interpreter(base_dir=os.path.dirname(os.path.abspath(path))).run(ast)

def check_file(path):
    with open(path) as f:
        src = f.read()
    ast = Parser(lex(src)).parse_program()
    Resolver().resolve_program(ast)
    TypeChecker().check_program(ast)

def repl():
    print(f"Lumen {LUMEN_VERSION} REPL — type Lumen statements, Ctrl-D to exit")
    interp = Interpreter(base_dir=".")
    resolver = Resolver()  # persists across lines — see Resolver.resolve_incremental
    while True:
        try:
            line = input("lumen> ")
        except EOFError:
            print(); break
        if not line.strip():
            continue
        try:
            ast = Parser(lex(line + "\n")).parse_program()
            resolver.resolve_incremental(ast)
            TypeChecker().check_program(ast)
            interp.run(ast)
        except (SyntaxError, LumenError) as e:
            print(str(e))
        except LumenRaise as e:
            print(f"Uncaught error: {e.message}")

def parse_manifest(path):
    """Minimal lumen.toml reader: sections [x] and key = "value" pairs. No external deps."""
    sections = {}
    current = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                sections[current] = {}
                continue
            if "=" in line and current:
                k, v = line.split("=", 1)
                sections[current][k.strip()] = v.strip().strip('"')
    return sections

def cmd_install():
    import os
    if not os.path.isfile("lumen.toml"):
        print("Error: no lumen.toml in this directory (run 'lumen new <name>' first)")
        sys.exit(1)
    manifest = parse_manifest("lumen.toml")
    deps = manifest.get("dependencies", {})
    if not deps:
        print("No dependencies listed in lumen.toml — nothing to install.")
        return
    os.makedirs("lumen_packages", exist_ok=True)
    for name, version in deps.items():
        local = os.path.join("lumen_packages", name)
        if os.path.isdir(local):
            print(f"  {name} {version}: already present in lumen_packages/{name}")
        else:
            print(f"  {name} {version}: NOT FOUND. Lumen has no public package registry yet — "
                  f"place the package's source in lumen_packages/{name}/ manually.")

def cmd_test(args):
    """`lumen test [path] [--vm] [--filter SUBSTR]` — Phase 3's end-user test
    command. Discovers `.lum` files (default: everything under ./tests/, or
    a specific file/dir if given), runs each file's top-level code (which
    registers any `test "name": ...` blocks — they do NOT execute inline,
    matching real test-framework convention), then runs every registered
    test and reports pass/fail with assertion messages for failures.
    --vm runs the discovered tests through the bytecode VM backend instead
    of the reference interpreter (falls back to the interpreter's own
    "not yet supported" boundary if a test file uses a VM-unsupported
    construct — reported per-file, not a silent skip).
    --filter SUBSTR only runs tests whose name contains SUBSTR.

    `lumen test --self-test [--vm]` is the separate, developer-facing command
    that runs *this Lumen implementation's own* conformance/regression/
    equivalence suite (tests/run_all.py or tests/test_equivalence.py) — kept
    under its own explicit flag specifically so it can never be confused
    with "run my project's tests," which is what a Lumen end user means by
    `lumen test`.
    """
    import os
    if "--self-test" in args:
        _cmd_self_test(args)
        return

    use_vm = "--vm" in args
    filter_substr = None
    if "--filter" in args:
        i = args.index("--filter")
        if i + 1 >= len(args):
            print("usage: lumen test [path] [--vm] [--filter SUBSTR]"); sys.exit(1)
        filter_substr = args[i + 1]
    positional = [a for a in args if a not in ("--vm",) and a != filter_substr and a != "--filter"]

    if positional:
        target = positional[0]
    elif os.path.isdir("tests"):
        target = "tests"
    elif os.path.isfile("lumen.toml"):
        manifest = parse_manifest("lumen.toml")
        target = manifest.get("project", {}).get("entry", "main.lum")
    else:
        print("usage: lumen test [path] [--vm] [--filter SUBSTR]")
        print("(no ./tests/ directory and no ./lumen.toml found — nothing to discover)")
        sys.exit(1)

    if os.path.isdir(target):
        files = sorted(
            os.path.join(dirpath, f)
            for dirpath, _dirs, filenames in os.walk(target)
            for f in filenames if f.endswith(".lum")
        )
    elif os.path.isfile(target):
        files = [target]
    else:
        print(f"Error: '{target}' not found"); sys.exit(1)

    if not files:
        print(f"No .lum files found under '{target}'"); sys.exit(0)

    total_pass = total_fail = 0
    any_file_error = False
    for path in files:
        try:
            names, run_one = _prepare_tests(path, use_vm)
        except (SyntaxError, LumenError) as e:
            print(f"{path}: {e}")
            any_file_error = True
            continue
        if filter_substr is not None:
            names = [n for n in names if filter_substr in n]
        if not names:
            continue
        print(f"\n{path}: running {len(names)} test(s)")
        for name in names:
            passed, msg = run_one(name)
            if passed:
                total_pass += 1
                print(f'  test "{name}" ... ok')
            else:
                total_fail += 1
                print(f'  test "{name}" ... FAILED')
                print(f"    {msg}")

    print(f"\ntest result: {'FAILED' if (total_fail or any_file_error) else 'ok'}. "
          f"{total_pass} passed; {total_fail} failed")
    sys.exit(1 if (total_fail or any_file_error) else 0)


def _prepare_tests(path, use_vm):
    """Parses/resolves/type-checks/runs one file's top-level code (registering
    its tests), and returns (test_names, run_one(name) -> (passed, message)),
    dispatching to whichever backend was requested."""
    import os
    with open(path) as f:
        src = f.read()
    ast = Parser(lex(src)).parse_program()
    resolver = Resolver()
    resolver.resolve_program(ast)
    TypeChecker().check_program(ast)
    base_dir = os.path.dirname(os.path.abspath(path))
    if use_vm:
        ir_program = IRBuilder(resolver.module_globals).build_program(ast)
        ir_errors = IRValidator().validate(ir_program)
        if ir_errors:
            raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
        program = Compiler().compile_program(ir_program)
        vm = VM()
        vm.base_dir = base_dir
        vm.run(program)
        names = list(program.test_entry.keys())
        return names, lambda name: vm.run_one_test(program, name)
    else:
        interp = Interpreter(base_dir=base_dir)
        interp.run(ast)
        results = {name: (passed, msg) for name, passed, msg in interp.run_tests()}
        return list(results.keys()), lambda name: results[name]


def _cmd_self_test(args):
    import subprocess
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if "--vm" in args:
        script = os.path.join(here, "tests", "test_equivalence.py")
        label = "interpreter <-> VM equivalence suite"
    else:
        script = os.path.join(here, "tests", "run_all.py")
        label = "full regression + conformance suite (reference interpreter)"
    print(f"Running: {label}\n")
    r = subprocess.run([sys.executable, script])
    sys.exit(r.returncode)

def new_project(name):
    import os
    if os.path.exists(name):
        print(f"Error: '{name}' already exists"); sys.exit(1)
    os.makedirs(os.path.join(name, "src"))
    os.makedirs(os.path.join(name, "tests"))
    with open(os.path.join(name, "lumen.toml"), "w") as f:
        f.write(f'[project]\nname = "{name}"\nversion = "0.1.0"\nentry = "src/main.lum"\n'
                 f'# lumen_version: the Lumen Language Specification this project targets\n'
                 f'lumen_version = "{SPEC_VERSION}"\n'
                 f'\n[dependencies]\n# name = "version"\n')
    with open(os.path.join(name, "src", "main.lum"), "w") as f:
        f.write('print "Hello from ' + name + '"\n')
    with open(os.path.join(name, "tests", "main_test.lum"), "w") as f:
        f.write('use "../src/main.lum" as main\n\n'
                 'test "placeholder":\n'
                 '    assert(true)\n')
    with open(os.path.join(name, "README.md"), "w") as f:
        f.write(f"# {name}\n\n"
                 f"A Lumen {SPEC_VERSION} project.\n\n"
                 f"## Run\n```\nlumen run\n```\n\n"
                 f"## Test\n```\nlumen test\n```\n")
    print(f"Created project '{name}/':")
    print(f"  {name}/lumen.toml")
    print(f"  {name}/src/main.lum")
    print(f"  {name}/tests/main_test.lum")
    print(f"  {name}/README.md")
    print(f"\ncd {name} && lumen run")

def main():
    args = sys.argv[1:]
    if not args:
        print("usage: lumen <run|check|repl|new|install|--version> [file.lum]")
        sys.exit(1)
    cmd = args[0]
    if cmd in ("--version", "-v", "version"):
        print(f"Lumen {LUMEN_VERSION}")
        print(f"Language specification: {SPEC_VERSION}")
        print(f"Runtime: {RUNTIME_KIND}")
        return
    if cmd == "repl":
        repl(); return
    if cmd == "new":
        if len(args) != 2:
            print("usage: lumen new <project_name>"); sys.exit(1)
        new_project(args[1]); return
    if cmd == "install":
        cmd_install(); return
    if cmd == "test":
        cmd_test(args[1:]); return
    if cmd == "check":
        rest = [a for a in args[1:] if a != "--vm"]
        path = rest[0] if rest else _project_entry_or_die("check")
        try:
            check_file(path)
            print(f"No errors found in {path}")
        except (SyntaxError, LumenError) as e:
            print(str(e)); sys.exit(1)
        return
    if cmd == "run":
        use_vm = "--vm" in args
        rest = [a for a in args[1:] if a != "--vm"]
        path = rest[0] if rest else _project_entry_or_die("run")
    else:
        use_vm = False
        path = cmd  # backward-compatible: `lumen file.lum`
    try:
        (run_file_vm if use_vm else run_file)(path)
    except (SyntaxError, LumenError) as e:
        print(str(e))
        sys.exit(1)
    except LumenRaise as e:
        print(f"Uncaught error: {e.message}")
        sys.exit(1)

def _project_entry_or_die(cmd_name):
    """No file given: look for ./lumen.toml and use [project].entry — this is
    what makes `cd my_project && lumen run` work with zero arguments, the
    Phase 3 scaffolding pass condition."""
    import os
    if not os.path.isfile("lumen.toml"):
        print(f"usage: lumen {cmd_name} [--vm] <file.lum>")
        print("(no file given and no ./lumen.toml found)")
        sys.exit(1)
    manifest = parse_manifest("lumen.toml")
    entry = manifest.get("project", {}).get("entry")
    if not entry:
        print(f"Error: lumen.toml has no [project] entry field")
        sys.exit(1)
    return entry

if __name__ == "__main__":
    main()
