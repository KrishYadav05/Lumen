#!/usr/bin/env python3
"""Lumen 1.0.0 — reference implementation (interpreter + bytecode VM backend).
Implements Lumen Language Specification 1.0 — see docs/ for the normative spec.
"""
import sys, os, re, threading, queue, errno, stat, contextlib, json
from datetime import datetime, timezone

_VM_IDS = iter(range(1, 1 << 62))    # unique ids for VMs and threads (who contributed which frames to a trace); next() on it is atomic

# Single source of truth for every version string the toolchain prints.
# If you bump one of these, this is the only place that should need editing.
LUMEN_VERSION = "3.1.0-p4"      # this implementation's own release version (semver)
SPEC_VERSION = "3.1"         # the Lumen Language Specification version it conforms to (docs/)
RUNTIME_KIND = "interpreter"  # default execution backend selected by `lumen run` (vs `--vm`)

# ---------- Lexer ----------
TOKEN_SPEC = [
    ("NUMBER",   r"\d+(\.\d+)?"),
    ("STRING",   r'"[^"]*"'),
    ("NAME",     r"[A-Za-z_][A-Za-z0-9_]*"),
    ("OP",       r"==|!=|<=|>=|->|[+\-*/%()\[\]{},:<>=.?]"),
    ("NEWLINE",  r"\n"),
    ("SKIP",     r"[ \t]+"),
    ("COMMENT",  r"#.*"),
]
MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC))
KEYWORDS = {"let","print","if","else","for","in","fn","return","true","false",
            "try","catch","struct","and","or","not","use","as","enum","match","spawn","test",
            "returns","is","while","finally"}
# "returns" and "is" are new in Lumen 2.1 (low-punctuation syntax — see
# language.md §1.13): "returns" is an alternative spelling for a function's
# '->' return-type arrow, and "is" is an alternative spelling for the ':'
# that associates a key/field with its value in a dict or struct literal.
# Reserving these as keywords is the same kind of narrow, disclosed exception
# "test" was in 1.1 (breaks only a program that used "returns"/"is" as an
# identifier) — not a new major-version break.

# Single source of truth for every builtin function name — referenced by
# Resolver.BUILTIN_NAMES, IRBuilder._call_kind, and Compiler's allow-list.
# This list used to be duplicated in three places; a real bug (assert_eq
# misrouted to "variant" kind, crashing the compiler) came directly from
# that duplication getting out of sync when assert/assert_eq/assert_ne were
# added. Add a new builtin here once, not three times.
BUILTIN_FUNCTION_NAMES = {"len", "push", "keys", "values", "read_file", "write_file",
                           "http_get", "fail", "wait", "chan", "send", "receive",
                           "assert", "assert_eq", "assert_ne", "now", "read_line", "range",
                           "chr", "ord", "type_of",
                           "file_exists", "append_file", "list_dir", "delete_file", "is_dir",
                           # Lumen 3.1: explicit conversions between Result / Option / exceptions
                           "ok_or", "unwrap", "unwrap_or", "is_ok", "is_err", "is_some", "is_none",
                           "fail_with"}
# The subset of BUILTIN_FUNCTION_NAMES the VM backend actually compiles today.
# Kept separate from the full set so the VM's "not yet supported" error stays
# accurate as VM coverage grows, without touching Resolver/IRBuilder.
# wait/chan/send/receive: VM concurrency support (SPAWN opcode + TaskHandle/
# LumenChannel reuse), mirroring the Interpreter's thread-based implementation.
# range: added in 2.0 alongside the corrected assignment semantics (language.md
# §1.11.1) specifically so a counted loop (`for i in range(n)`) doesn't need
# Lumen-level recursion just to manufacture [0..n-1] — see range's own note
# below for why that recursion was a real, hittable recursion-depth landmine.
# chr/ord: added in Phase 3 specifically because there was NO way at all,
# anywhere in the language, to construct a string containing a double-quote
# character — the only string literal syntax is `"[^"]*"` (no escapes, no
# second quote style), so `"\""` can't be written and there's no other
# character-by-code-point builtin. That silently blocked anything that needs
# to emit quoted output (JSON among them) from pure Lumen code. chr(34) is
# now the only way to get that character; see std/json.lum.
# type_of: added alongside chr/ord for the same reason — writing a generic
# encoder (or any other value-shape-dependent code) needs SOME way to ask
# "what kind of value is this at runtime", and there was none: no is_list/
# is_dict/is_int-style predicate existed, and equality-based duck-typing is a
# real landmine in Lumen specifically because Bool is an Int subtype at the
# Python level (`0 == false` and `1 == true` are both true), so "compare
# against true/false to detect Bool" silently misclassifies 0 and 1. Returns
# one of the exact type-name strings already used throughout this codebase
# and types.md ("Int", "Float", "Bool", "Str", "List", "Dict"), plus "Nothing"
# for the value produced by a function that falls off its end without
# `return` (standard-library.md §8.3), a struct/enum value's own declared
# type name, or "Task"/"Channel"/"Module" for the three other first-class
# runtime values the language has.
# file_exists/append_file/list_dir/delete_file/is_dir: added in Language
# Specification 2.3, filling in the rest of the `fs` capability surface —
# only read_file/write_file existed before, so a real std/fs.lum module
# could do little beyond wrap those two directly (PHASE3_STATUS.md). `is_dir`
# specifically was found missing one level deeper: a `std/fs.lum` helper
# that filters `list_dir`'s entries down to just the regular files needs
# SOME way to tell a file from a subdirectory, and there was none (even
# `file_exists` only answers "is something there", not "what kind"). All
# five are gated by the same `fs` capability as read_file/write_file (no
# new, finer-grained capability was added — write_file already has the
# same destructive potential as delete_file, so splitting them apart would
# be a false precision). See standard-library.md §8.4 for full semantics.
VM_SUPPORTED_BUILTINS = {"len", "push", "keys", "values", "fail",
                          "read_file", "write_file", "http_get",
                          "assert", "assert_eq", "assert_ne", "now", "read_line",
                          "wait", "chan", "send", "receive", "range", "chr", "ord",
                          "type_of",
                          "file_exists", "append_file", "list_dir", "delete_file", "is_dir",
                          "ok_or", "unwrap", "unwrap_or", "is_ok", "is_err", "is_some", "is_none",
                          "fail_with"}

class Token:
    """`col`/`end_col` are 1-based source columns (end exclusive) — what lets a diagnostic
    point at the exact characters it is about, not just a line. Structural tokens
    (INDENT/DEDENT/NEWLINE/EOF) carry col 0."""
    def __init__(self, kind, value, line, col=0, end_col=0):
        self.kind, self.value, self.line = kind, value, line
        self.col, self.end_col = col, end_col
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
            tokens.append(Token(kind, val, line, m.start() + 1, m.end() + 1))
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
class Node:
    # Source position, set by the parser: `line`/`col` of the first character, `end_col`
    # just past the last character of the node's first line (1-based, 0 = unknown).
    # Diagnostics use these to point at exactly the code a message is about.
    line = 0
    col = 0
    end_col = 0
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
    type_span = None      # (col, end_col) of the written type annotation, when there is one
    def __init__(self, name, expr, type_ann=None, line=0):
        self.name, self.expr, self.type_ann, self.line = name, expr, type_ann, line
class Assign(Node):
    """Bare `name = expr` (no `let`) — reassigns a name that must already be
    declared somewhere in the enclosing scope chain, mutating it IN PLACE at
    the scope where it lives. Distinct from `Let`, which always declares a
    fresh binding in the current scope (shadowing any outer one of the same
    name). Before this node existed, the parser produced a `Let` for both
    forms, which meant every bare reassignment silently created a throwaway
    shadow in whatever block scope it was textually inside (an `if`/`for`/
    `try`/`match` body) instead of updating the outer variable — found via
    the simplest possible test case, `total = total + n` inside a `for`
    loop, which silently computed nothing. See language.md §1.11 (post-1.1
    revision) for the corrected, normative scoping rule this restores."""
    def __init__(self, name, expr, line=0):
        self.name, self.expr, self.line = name, expr, line
class Print(Node):
    def __init__(self, expr): self.expr = expr
class If(Node):
    def __init__(self, cond, body, orelse): self.cond, self.body, self.orelse = cond, body, orelse
class For(Node):
    def __init__(self, var, iterable, body): self.var, self.iterable, self.body = var, iterable, body
class While(Node):
    """`while cond: body` — condition-based looping (Language Specification
    2.2). Added specifically because `for` only iterates a known collection/
    range and there is still no `break`/`continue` (PERFORMANCE_HARDENING_STATUS.md), so
    there was no way at all to loop "until a condition changes" without
    unbounded recursion — a real gap for any cursor-walking code (a hand-
    written parser, reading a stream to a sentinel, etc.). No loop variable:
    the condition is a plain expression, re-evaluated before every
    iteration, exactly like Python/JS/C's `while`."""
    def __init__(self, cond, body): self.cond, self.body = cond, body
class FnDef(Node):
    def __init__(self, name, params, body, ret_type=None, line=0, type_params=None):
        self.name, self.params, self.body, self.ret_type, self.line = name, params, body, ret_type, line
        self.type_params = type_params or []
class Return(Node):
    def __init__(self, expr, line=0): self.expr, self.line = expr, line
class ExprStmt(Node):
    def __init__(self, expr): self.expr = expr
class StructDef(Node):
    """`fields` is the list of field NAMES (as ever). Lumen 3.1 adds optional field types and
    type parameters: `field_types` maps a field name to its type string (a field written without
    a type is absent from it, i.e. unchecked), `type_params` is `<A, B>` of `struct Pair<A, B>`."""
    def __init__(self, name, fields, type_params=None, field_types=None, line=0):
        self.name, self.fields = name, fields
        self.type_params = type_params or []
        self.field_types = field_types or {}
        if line: self.line = line
class StructLit(Node):
    def __init__(self, name, values): self.name, self.values = name, values
class FieldGet(Node):
    def __init__(self, obj, field): self.obj, self.field = obj, field
class FieldSet(Node):
    def __init__(self, obj, field, expr): self.obj, self.field, self.expr = obj, field, expr
class Try(Node):
    def __init__(self, body, err_name, handler): self.body, self.err_name, self.handler = body, err_name, handler
class TryFinally(Node):
    """`try: ... [catch e: ...] finally: ...` (Lumen 3.1). `body` is the protected block: the
    `Try` node when a `catch` is present (so the catch handler is protected too), else the bare
    try body. `finalizer` runs exactly once however `body` is left — normal completion, a
    `return`, a `?` propagation, or an error — and may not itself `return` or use `?`."""
    def __init__(self, body, finalizer, line=0):
        self.body, self.finalizer = body, finalizer
        if line: self.line = line
class Propagate(Node):
    """`expr?` (Lumen 3.1): unwrap a `Result`/`Option`, or return the failure from the enclosing
    function at once. See docs/errors.md §4.2.3."""
    def __init__(self, expr, line=0):
        self.expr = expr
        if line: self.line = line
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
    """`variants` is [(variant_name, [payload_key, ...])] as ever. A payload key is the label the
    program gave the field, or its position ("0", "1", ...) for a field written as a bare type.
    Lumen 3.1: `type_params` is `<T, E>` of `enum Result<T, E>`; `field_types` maps a variant
    name to one entry per payload field — the type string, or None for a field written as a bare
    label (no type, unchecked, exactly the pre-3.1 meaning)."""
    def __init__(self, name, variants, type_params=None, field_types=None, line=0):
        self.name, self.variants = name, variants
        self.type_params = type_params or []
        self.field_types = field_types or {}
        if line: self.line = line
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
        self.observed = False       # `wait` has been called on it: whoever waited has the failure
        self.done = threading.Event()
        with _TASK_LOCK:
            _OPEN_TASKS[id(self)] = self
    def __repr__(self): return "<task>"

# Tasks that are running, or that failed and nobody has waited for yet (Lumen 3.1). A task that finishes
# without failing leaves the table at once, so it does not grow with the number of tasks a program spawns.
# What is still in it when the program ends is what `report_lost_task_failures` looks at.
_TASK_LOCK = threading.Lock()
_OPEN_TASKS = {}


def _task_finished(task):
    if task.error is None:
        with _TASK_LOCK:
            _OPEN_TASKS.pop(id(task), None)


def _task_observed(task):
    task.observed = True
    with _TASK_LOCK:
        _OPEN_TASKS.pop(id(task), None)

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
    # Two enum values are equal when they are the same variant of the same enum with equal payloads
    # (`Ok(1) == Ok(1)`) — what `assert_eq(parse("5"), Ok(5))` needs. Before 3.1 this was identity.
    def __eq__(self, other):
        return (isinstance(other, EnumInstance) and self.enum_name == other.enum_name
                and self.variant == other.variant and self.fields == other.fields)
    def __hash__(self):
        return hash((self.enum_name, self.variant))

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
    # Structural equality, like enum values (see EnumInstance).
    def __eq__(self, other):
        return (isinstance(other, LumenInstance) and self.struct_name == other.struct_name
                and self.fields == other.fields)
    def __hash__(self):
        return hash(self.struct_name)

# The library enums every program has in scope (Lumen 3.1). Defined in Lumen itself, parsed by the
# ordinary parser, and handed to the ordinary pipeline — they are enums like any other, not a
# special case in the checker or either engine. See inject_prelude().
PRELUDE_SOURCE = """\
enum Option<T>:
    Some(T)
    None

enum Result<T, E>:
    Ok(T)
    Err(E)
"""
PRELUDE_TYPE_NAMES = ("Option", "Result")

# ---------- Parser ----------
class Parser:
    # Type names that never count as an implicit generic placeholder (Lumen
    # 2.1's auto-generics — see FN parsing below and language.md §1.13.3).
    _CONCRETE_TYPE_NAMES = {"Int", "Float", "Str", "Bool", "List", "Dict", "Any"}

    def __init__(self, tokens):
        self.toks = [t for t in tokens]
        self.i = 0
        # Lumen 2.1 paren-free calls (language.md §1.13.2): `name arg1 arg2`
        # is a call with no parens at all. This flag is temporarily forced
        # False while parsing a dict/struct-literal VALUE (the one place a
        # bare multi-argument call would be ambiguous with "that value ended,
        # the next key/field just started" since both those literals make
        # their own ',' optional). An explicit parenthesized group always
        # resets it back to True for its own contents, since parens remove
        # the ambiguity outright.
        self.allow_bare_call = True
        # A quick token-level pre-scan (before real parsing) collecting every
        # name declared with `fn`, plus every builtin. Paren-free application
        # is only ever attempted for a name in this set, so an ordinary bare
        # variable reference can never be misread as a call no matter what
        # token happens to follow it on the same line — see _starts_atom.
        self._callable_names = set(BUILTIN_FUNCTION_NAMES)
        # Names this file declares with `struct`/`enum`, plus the library enums (Option, Result).
        # A bare name in a type position that is one of these is a real type, not an implicit
        # generic placeholder, and a bare name in an enum payload that is one of these is the
        # payload's TYPE rather than a label (see parse_variant_field).
        self._declared_types = set(PRELUDE_TYPE_NAMES)
        self._in_bare_args = False  # True while reading the atoms of a paren-free call: `f x?` is `f (x?)`
                                    # nowhere — see parse_trailers.
        for idx, tok in enumerate(self.toks):
            if tok.kind == "FN" and idx + 1 < len(self.toks) and self.toks[idx + 1].kind == "NAME":
                self._callable_names.add(self.toks[idx + 1].value)
            elif tok.kind in ("STRUCT", "ENUM") and idx + 1 < len(self.toks) and self.toks[idx + 1].kind == "NAME":
                self._declared_types.add(self.toks[idx + 1].value)

    def peek(self): return self.toks[self.i]
    def advance(self):
        t = self.toks[self.i]; self.i += 1; return t

    def _end_col_since(self, start_idx):
        """End column of the last real token consumed since `start_idx`, on the line `start_idx`
        began on (a node that spans several lines is underlined to the end of its first line)."""
        first = self.toks[start_idx]
        for j in range(self.i - 1, start_idx - 1, -1):
            t = self.toks[j]
            if t.kind in ("NEWLINE", "INDENT", "DEDENT", "EOF"):
                continue
            if t.line == first.line:
                return t.end_col
        return first.end_col

    def _mark(self, node, start_idx):
        """Records where `node` is in the source (line, col, end_col) unless it already knows.
        Never overwrites: a node a sub-rule already marked keeps its own, tighter position."""
        if isinstance(node, Node) and node.col == 0:
            t = self.toks[start_idx]
            if t.col:
                node.col = t.col
                node.end_col = self._end_col_since(start_idx)
                if not node.line:
                    node.line = t.line
        return node

    def parse_type(self):
        """A type: `Name` or `Name<Type, Type, ...>`, returned in canonical text form
        ("Result<Int, Str>"). Types stay plain strings everywhere downstream; the checker's
        helpers (split_type, ...) take them apart."""
        name = self.expect("NAME").value
        if self.peek().kind == "OP" and self.peek().value == "<":
            self.advance()
            args = [self.parse_type()]
            while self.peek().value == ",":
                self.advance()
                args.append(self.parse_type())
            t = self.peek()
            if t.kind == "OP" and t.value == ">":
                self.advance()
            elif t.kind == "OP" and t.value == ">=":
                # `let x: Option<Int>= ...` lexes `>=` as one token: close the type with its '>'
                # and leave the '=' behind for the statement
                t.value, t.col = "=", t.col + 1
            else:
                raise SyntaxError(f"Lumen: expected '>' to close the type arguments of {name} but found "
                                  f"{t.kind} ({t.value!r}) at line {t.line}")
            return f"{name}<{', '.join(args)}>"
        return name

    def _is_placeholder(self, atom):
        return atom not in self._CONCRETE_TYPE_NAMES and atom not in self._declared_types

    def check(self, kind): return self.peek().kind == kind
    def expect(self, kind):
        if not self.check(kind):
            t = self.peek()
            raise SyntaxError(f"Lumen: expected {kind} but found {t.kind} ({t.value!r}) at line {t.line}")
        return self.advance()
    def skip_newlines(self):
        while self.check("NEWLINE"):
            self.advance()

    def opt_colon(self):
        """Lumen 2.1 (language.md §1.13.1): the ':' before a block body used
        to be mandatory after if/else/for/fn/match/case/try/catch/struct/
        enum/test headers. It's now optional — indentation alone is enough
        to open the block — but both spellings keep working identically."""
        if self.peek().value == ":":
            self.advance()

    def parse_sep_list(self, closing, parse_item):
        """Lumen 2.1 (language.md §1.13.1): a '<item> (',' <item>)*'-style
        list where the comma is now OPTIONAL — items may be separated by ','
        or by nothing at all (just whitespace/newline), interchangeably.
        Stops without consuming the token whose value equals `closing`."""
        items = []
        while self.peek().value != closing:
            items.append(parse_item())
            if self.peek().value == ",":
                self.advance()
        return items

    def _starts_atom(self, tok):
        """Lumen 2.1: whether `tok` can begin one argument of a paren-free
        call. Deliberately excludes every operator (including unary '-') and
        every keyword — see parse_primary's Var branch for why: this is what
        makes `f x + y` mean `(f x) + y` rather than `f(x + y)` or `f(x, +y)`,
        exactly like ML/Haskell application, and why a negative-number
        argument needs parens (`f x (-1)`), not a special-cased lexer rule."""
        if tok.kind in ("NUMBER", "STRING", "NAME", "TRUE", "FALSE"):
            return True
        return tok.value in ("(", "[", "{")

    def parse_program(self):
        # Found via Phase 4 fuzzing: pathologically deep nesting (e.g. a few
        # thousand nested parens) drives the recursive-descent parser past
        # Python's own recursion limit, which previously surfaced as a raw,
        # uncaught `RecursionError` — the exact class of bug this project's
        # established discipline (see Interpreter.call_function's matching
        # fix for deep Lumen-level call recursion) treats as a real bug, not
        # a backend quirk: no Python-internal exception may ever reach the
        # user. `parse_program` is the single entry point every caller uses
        # (run_file*, check_file, the REPL, module loading), so catching
        # here converts it once, cleanly, for all of them.
        try:
            stmts = []
            self.skip_newlines()
            while not self.check("EOF"):
                stmts.append(self.parse_stmt())
                self.skip_newlines()
            return stmts
        except RecursionError:
            raise SyntaxError("Lumen: expression or statement nested too deeply to parse")

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
        start = self.i
        node = self._parse_stmt_inner()
        return self._mark(node, start)

    def _parse_stmt_inner(self):
        if self.check("LET"):
            t = self.advance()
            name = self.expect("NAME").value
            type_ann, type_span = None, None
            if self.peek().value == ":" or self.check("AS"):   # `as`: Lumen 2.1 alt spelling, let x as Int = 5
                self.advance()
                tstart = self.i
                type_ann = self.parse_type()
                if self.toks[tstart].col:
                    type_span = (self.toks[tstart].col, self._end_col_since(tstart))   # where the type is written
            self.expect("OP")  # '='
            expr = self.parse_expr()
            node = Let(name, expr, type_ann, t.line)
            node.type_span = type_span
            return node
        if self.check("PRINT"):
            self.advance()
            expr = self.parse_expr()
            # Lumen 2.1 (language.md §1.13.4): `print a b c` prints multiple
            # space-separated values, exactly as `print a + " " + b + " " + c`
            # would (same display-conversion rule as `+`'s string coercion,
            # standard-library.md §8.3) -- pure sugar folded into the existing
            # single-expression Print node, so nothing downstream of the
            # parser needs to know this syntax exists. Unambiguous because
            # parse_expr() is already maximally greedy (it swallows every
            # binary operator it can, including a trailing '- 5' as
            # subtraction of the PREVIOUS expression), so whatever token is
            # left over literally cannot bind to it via any operator in the
            # grammar -- it can only be the start of a new item, or garbage
            # the existing end-of-statement check below will reject anyway.
            while self._starts_atom(self.peek()):
                nxt = self.parse_expr()
                expr = BinOp("+", BinOp("+", expr, Str(" ")), nxt)
            return Print(expr)
        if self.check("IF"):
            return self.parse_if()
        if self.check("FOR"):
            self.advance()
            var = self.expect("NAME").value
            self.expect("IN")
            it = self.parse_expr()
            self.opt_colon()
            body = self.parse_block()
            return For(var, it, body)
        if self.check("WHILE"):
            self.advance()
            cond = self.parse_expr()
            self.opt_colon()
            body = self.parse_block()
            return While(cond, body)
        if self.check("TEST"):
            t = self.advance()
            name_tok = self.expect("STRING")
            self.opt_colon()
            body = self.parse_block()
            return TestDef(name_tok.value[1:-1], body, t.line)
        if self.check("FN"):
            t = self.advance()
            name = self.expect("NAME").value
            type_params = []
            if self.peek().value == "<":
                self.advance()
                type_params = self.parse_sep_list(">", lambda: self.expect("NAME").value)
                self.expect("OP")  # >
            def parse_param():
                pname = self.expect("NAME").value
                ptype = None
                if self.peek().value == ":":
                    self.advance()
                    ptype = self.parse_type()
                elif self.check("AS"):  # Lumen 2.1 alt spelling: x as Int
                    self.advance()
                    ptype = self.parse_type()
                return (pname, ptype)
            params = []
            if self.peek().value == "(":
                self.advance()
                params = self.parse_sep_list(")", parse_param)
                self.expect("OP")  # )
            else:
                # Lumen 2.1: parens around the parameter list are optional
                # too — params are read as bare names (each optionally typed
                # via 'name as Type' or 'name: Type') until a token that
                # can't start another one: 'returns', '->', ':', or the end
                # of the line. No lookahead needed: those are all reserved
                # keywords/operators, never NAME tokens.
                while self.peek().kind == "NAME":
                    params.append(parse_param())
            ret_type = None
            if self.peek().value == "->":
                self.advance()
                ret_type = self.parse_type()
            elif self.check("RETURNS"):  # Lumen 2.1 alt spelling for '->'
                self.advance()
                ret_type = self.parse_type()
            # Lumen 2.1 auto-generics (language.md §1.13.3): if <...> wasn't
            # given explicitly, any param/return type name that isn't one of
            # the built-in concrete types is treated as an implicit generic
            # placeholder (checked as "Any", same as a declared one) instead
            # of a literal type name that would almost certainly mismatch.
            # This can only make checking MORE permissive than before, never
            # reject a program the old, explicit-<T>-only rule accepted —
            # the project's own soundness rule for the type checker.
            if not type_params:
                # Lumen 3.1: a name this file (or the library) DECLARES as a struct/enum is a real
                # type, not a placeholder, and the rule looks inside `Name<...>` too, so
                # `fn first(xs: List) -> Option<T>` makes T (and only T) an implicit parameter.
                auto = []
                for ty in [pt for _, pt in params] + [ret_type]:
                    for atom in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ty or ""):
                        if self._is_placeholder(atom) and atom not in auto:
                            auto.append(atom)
                type_params = auto
            self.opt_colon()
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
            while self.peek().kind == "OP" and self.peek().value == ".":   # `use fs.read`
                self.advance()
                seg = self.advance()
                # any word, keywords included: `process.spawn` must reach the "reserved
                # capability" error rather than die as a syntax error on the `spawn` keyword
                if not (isinstance(seg.value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", seg.value)):
                    raise SyntaxError(f"Lumen: expected a capability name after '.' but found {seg.kind} at line {seg.line}")
                name += "." + seg.value
            return Use(name, t.line)
        if self.check("ENUM"):
            et = self.advance()
            name = self.expect("NAME").value
            type_params = self._parse_type_params()
            self.opt_colon()
            self.skip_newlines(); self.expect("INDENT")
            variants = []
            field_types = {}
            self.skip_newlines()
            while not self.check("DEDENT"):
                vt = self.expect("NAME")
                vname = vt.value
                fields = []   # [(label_or_None, type_or_None)]
                if self.peek().value == "(":
                    self.advance()
                    fields = self.parse_sep_list(")", lambda: self.parse_variant_field(type_params))
                    self.expect("OP")  # )
                else:
                    # Lumen 2.1 paren-free variant fields (language.md
                    # §1.13.2): `Some value` instead of `Some(value)`. Each
                    # variant is already on its own line, so reading bare
                    # NAME tokens up to end-of-line is unambiguous — mirrors
                    # the paren-free pattern side in parse_pattern.
                    while self.peek().kind == "NAME":
                        fields.append(self.parse_variant_field(type_params))
                labels = [lab for lab, _t in fields if lab is not None]
                if len(set(labels)) != len(labels):
                    raise SyntaxError(f"Lumen: variant {vname} of enum {name} repeats a field label at line {vt.line}")
                keys = [lab if lab is not None else str(i) for i, (lab, _t) in enumerate(fields)]
                variants.append((vname, keys))
                field_types[vname] = [t for _l, t in fields]
                self.skip_newlines()
            self.expect("DEDENT")
            return EnumDef(name, variants, type_params, field_types, et.line)
        if self.check("MATCH"):
            t = self.advance()
            expr = self.parse_expr()
            self.opt_colon()
            self.skip_newlines(); self.expect("INDENT")
            cases = []
            self.skip_newlines()
            while not self.check("DEDENT"):
                pattern = self.parse_pattern()
                self.opt_colon()
                body = self.parse_block()
                cases.append((pattern, body))
                self.skip_newlines()
            self.expect("DEDENT")
            return Match(expr, cases, t.line)
        if self.check("STRUCT"):
            st = self.advance()
            name = self.expect("NAME").value
            type_params = self._parse_type_params()
            self.opt_colon()
            self.skip_newlines(); self.expect("INDENT")
            fields = []
            field_types = {}
            self.skip_newlines()
            while not self.check("DEDENT"):
                ft = self.expect("NAME")
                if self.peek().kind == "OP" and self.peek().value == ":":   # Lumen 3.1: `x: Int`
                    self.advance()
                    field_types[ft.value] = self.parse_type()
                elif self.check("AS"):
                    self.advance()
                    field_types[ft.value] = self.parse_type()
                if ft.value in fields:
                    raise SyntaxError(f"Lumen: struct {name} repeats the field '{ft.value}' at line {ft.line}")
                fields.append(ft.value)
                self.skip_newlines()
            self.expect("DEDENT")
            return StructDef(name, fields, type_params, field_types, st.line)
        if self.check("TRY"):
            tt = self.advance()
            self.opt_colon()
            body = self.parse_block()
            self.skip_newlines_peekahead_kw("CATCH")
            caught = None
            if self.check("CATCH"):
                self.advance()
                err_name = self.expect("NAME").value
                self.opt_colon()
                handler = self.parse_block()
                caught = Try(body, err_name, handler)
                caught.line, caught.col = tt.line, tt.col
            self.skip_newlines_peekahead_kw("FINALLY")
            if self.check("FINALLY"):
                self.advance()
                self.opt_colon()
                finalizer = self.parse_block()
                return TryFinally([caught] if caught is not None else body, finalizer, tt.line)
            if caught is None:
                t = self.peek()
                raise SyntaxError(f"Lumen: 'try' needs a 'catch' or a 'finally' block, but found {t.kind} ({t.value!r}) after the try body at line {t.line}")
            return caught
        # assignment or expression
        expr = self.parse_expr()
        if self.peek().value == "=" and self.peek().kind == "OP":
            self.advance()
            value = self.parse_expr()
            if isinstance(expr, Var):
                return Assign(expr.name, value)
            if isinstance(expr, FieldGet):
                return FieldSet(expr.obj, expr.field, value)
            if isinstance(expr, Index):
                return IndexSet(expr.obj, expr.key, value)
            raise SyntaxError("Lumen: invalid assignment target")
        return ExprStmt(expr)

    def _parse_type_params(self):
        """`<T, E>` after the name of an enum or struct declaration (Lumen 3.1), or []."""
        if not (self.peek().kind == "OP" and self.peek().value == "<"):
            return []
        self.advance()
        params = self.parse_sep_list(">", lambda: self.expect("NAME").value)
        self.expect("OP")  # >
        if len(set(params)) != len(params):
            raise SyntaxError(f"Lumen: repeated type parameter in <{', '.join(params)}> at line {self.peek().line}")
        return params

    def parse_variant_field(self, tparams):
        """One payload field of an enum variant: `label`, `label: Type`, or a bare `Type`.
        A bare name is read as a TYPE when it is a built-in type, one of this enum's type
        parameters, a struct/enum the file declares, or is followed by `<...>`; any other bare
        name is a LABEL — the pre-3.1 meaning, kept so that `Circle(radius)` still declares an
        unchecked payload called `radius`. Returns (label_or_None, type_or_None)."""
        tok = self.expect("NAME")
        name, nxt = tok.value, self.peek()
        if nxt.kind == "OP" and nxt.value == ":":
            self.advance()
            return (name, self.parse_type())
        if nxt.kind == "OP" and nxt.value == "<":
            self.i -= 1
            return (None, self.parse_type())
        if name in self._CONCRETE_TYPE_NAMES or name in tparams or name in self._declared_types:
            return (None, name)
        return (name, None)

    def parse_if(self):
        self.expect("IF")
        cond = self.parse_expr()
        self.opt_colon()
        body = self.parse_block()
        orelse = []
        self.skip_newlines_peekahead()
        if self.check("ELSE"):
            self.advance()
            self.opt_colon()
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
        start = self.i
        node = self.parse_and()
        while self.check("OR"):
            self.advance()
            node = self._mark(BoolOp("or", node, self.parse_and()), start)
        return node

    def parse_and(self):
        start = self.i
        node = self.parse_not()
        while self.check("AND"):
            self.advance()
            node = self._mark(BoolOp("and", node, self.parse_not()), start)
        return node

    def parse_not(self):
        if self.check("NOT"):
            start = self.i
            self.advance()
            return self._mark(Not(self.parse_not()), start)
        return self.parse_comparison()

    def parse_comparison(self):
        start = self.i
        node = self.parse_add()
        while self.peek().value in ("==", "!=", "<", ">", "<=", ">="):
            op = self.advance().value
            right = self.parse_add()
            node = self._mark(BinOp(op, node, right), start)
        return node

    def parse_add(self):
        start = self.i
        node = self.parse_mul()
        while self.peek().value in ("+", "-"):
            op = self.advance().value
            right = self.parse_mul()
            node = self._mark(BinOp(op, node, right), start)
        return node

    def parse_mul(self):
        start = self.i
        node = self.parse_unary()
        while self.peek().value in ("*", "/", "%"):
            op = self.advance().value
            right = self.parse_unary()
            node = self._mark(BinOp(op, node, right), start)
        return node

    def parse_unary(self):
        if self.peek().value == "-":
            start = self.i
            self.advance()
            return self._mark(BinOp("-", Num(0), self.parse_unary()), start)
        return self.parse_primary()

    def parse_pattern(self):
        start = self.i
        return self._mark(self._parse_pattern_inner(), start)

    def _parse_pattern_inner(self):
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
                subs = self.parse_sep_list(")", lambda: self.expect("NAME").value)
                self.expect("OP")  # )
                return PVariant(name, subs)
            if self.peek().kind == "NAME":
                # Lumen 2.1: a paren-free variant pattern — `Some v` instead
                # of `Some(v)` — safe with no ambiguity at all, since each
                # match case is already parsed on its own line/position
                # (language.md §1.13.2).
                subs = []
                while self.peek().kind == "NAME":
                    subs.append(self.advance().value)
                return PVariant(name, subs)
            return PBind(name)
        raise SyntaxError(f"Lumen: invalid pattern at line {t.line}")

    def parse_trailers(self, node):
        """`.field`, `[index]`, `.method(args)` and — Lumen 3.1 — the postfix `?`, applied left to
        right to `node` for as long as one follows (so `m.load(p)?.name` is read the way it
        looks). Every node built here starts where `node` starts."""
        while True:
            t = self.peek()
            if t.kind == "OP" and t.value == ".":
                self.advance()
                new = FieldGet(node, self.expect("NAME").value)
            elif t.kind == "OP" and t.value == "[":
                self.advance()
                saved = self._reset_bare_call()
                try:
                    key = self.parse_expr()
                finally:
                    self.allow_bare_call = saved
                self.expect("OP")  # ]
                new = Index(node, key)
            elif t.kind == "OP" and t.value == "(" and isinstance(node, FieldGet):
                self.advance()
                args = []
                self.skip_newlines()
                while self.peek().value != ")":
                    args.append(self._parse_no_bare_call())
                    if self.peek().value == ",":
                        self.advance()
                    self.skip_newlines()
                self.expect("OP")  # )
                new = MethodCall(node.obj, node.field, args)
            elif t.kind == "OP" and t.value == "?":
                self.advance()
                new = Propagate(node, t.line)
                new.q_col = t.col       # where the `?` itself is, for diagnostics about the propagation
            else:
                break
            new.line, new.col = node.line, node.col
            last = self.toks[self.i - 1]
            new.end_col = last.end_col if last.line == node.line else node.end_col
            node = new
        return node

    def _reset_bare_call(self):
        """Returns the previous allow_bare_call value and sets it True.
        Called right after consuming an explicit opening bracket/paren
        ('(', '[', the args of an old-style call, ...): an explicit
        delimiter always resolves the dict/struct-literal-value ambiguity
        for its own contents, regardless of what the ambient context was."""
        saved = self.allow_bare_call
        self.allow_bare_call = True
        return saved

    def parse_primary(self):
        start = self.i
        node = self._parse_primary_inner(start)
        return self._mark(node, start)

    def _parse_primary_inner(self, start):
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
            self.skip_newlines()
            while self.peek().value != "]":
                items.append(self._parse_no_bare_call())
                if self.peek().value == ",":
                    self.advance()
                self.skip_newlines()
            self.expect("OP")  # ]
            return self.parse_trailers(ListLit(items))
        if t.value == "{":
            self.advance()
            pairs = []
            self.skip_newlines()
            while self.peek().value != "}":
                key = self._parse_no_bare_call()
                self.expect_colon_or_is()
                val = self._parse_no_bare_call()
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
                self.skip_newlines()
                while self.peek().value != ")":
                    args.append(self._parse_no_bare_call())
                    if self.peek().value == ",":
                        self.advance()
                    self.skip_newlines()
                self.expect("OP")  # )
                node = self._mark(Call(t.value, args, t.line), start)
            elif self.peek().value == "{":
                self.advance()
                values = {}
                self.skip_newlines()
                while self.peek().value != "}":
                    fname = self.expect("NAME").value
                    self.expect_colon_or_is()
                    values[fname] = self._parse_no_bare_call()
                    if self.peek().value == ",":
                        self.advance()
                    self.skip_newlines()
                self.expect("OP")  # }
                node = self._mark(StructLit(t.value, values), start)
            elif self.allow_bare_call and t.value in self._callable_names and self._starts_atom(self.peek()):
                # Lumen 2.1 paren-free call (language.md §1.13.2): `name arg1
                # arg2 ...` with no parens at all. Each argument is parsed at
                # ATOM precedence only (self.parse_primary(), not parse_expr)
                # — exactly like ML/Haskell application — so `add 2 3 + 1` is
                # `(add 2 3) + 1`, and a nested call or a compound expression
                # used as one argument needs its own parens: `add 2 (3 + 1)`.
                args = []
                saved = self.allow_bare_call
                self.allow_bare_call = False
                try:
                    while self._starts_atom(self.peek()):
                        arg = self.parse_primary()
                        if isinstance(arg, Propagate):
                            raise SyntaxError(
                                f"Lumen: a '?' right after an argument of the paren-free call '{t.value}' is ambiguous "
                                f"(does it apply to the argument or to the call?) — write ({t.value} ...)? to propagate "
                                f"the call's result, or {t.value}((...)?) to unwrap just the argument (line {t.line})")
                        args.append(arg)
                finally:
                    self.allow_bare_call = saved
                return self._mark(Call(t.value, args, t.line), start)  # no trailers on a bare-call result in v1
            else:
                node = self._mark(Var(t.value), start)
            return self.parse_trailers(node)
        if t.value == "(":
            self.advance()
            saved = self._reset_bare_call()
            try:
                e = self.parse_expr()
            finally:
                self.allow_bare_call = saved
            self.expect("OP")  # )
            return self.parse_trailers(e)
        raise SyntaxError(f"Lumen: unexpected token {t.kind} ({t.value!r}) at line {t.line}")

    def expect_colon_or_is(self):
        """':' or 'is' between a dict-literal key (or struct-literal field
        name) and its value — both spellings accepted (Lumen 2.1)."""
        if self.check("IS"):
            self.advance()
        else:
            self.expect("OP")  # ':'

    def _parse_no_bare_call(self):
        """Parses one item of a comma-optional list with paren-free calls
        (language.md §1.13.2) disabled: a list-literal item, a call or
        method-call argument, or a dict/struct-literal key or value. All of
        these make their own ',' optional, so without this, a bare
        multi-argument call used as one item (`[add 2 3]`, `{"a" is greet
        name}`) could keep consuming tokens straight through the next
        sibling item, with nothing to mark where this one ends. A
        parenthesized call is unaffected — parens always re-enable
        paren-free calls for their own contents (see the '(' branch above)
        — so `[(add 2 3) 10]` / `{"a" is (greet name)}` still work; only the
        un-parenthesized spelling needs the parens here.

        With bare calls disabled, a callable name with no '(' just parses
        as a plain `Var` (see parse_primary) instead of a `Call` — so
        without the check below, `[add 2 3]` would silently misparse as the
        well-formed-looking 3-item list `[add, 2, 3]` rather than failing.
        Catch that here with a clear message instead."""
        saved = self.allow_bare_call
        self.allow_bare_call = False
        try:
            result = self.parse_expr()
        finally:
            self.allow_bare_call = saved
        if (isinstance(result, Var) and result.name in self._callable_names
                and self._starts_atom(self.peek())):
            raise SyntaxError(
                f"Lumen: '{result.name}' looks like a multi-argument call here, but "
                f"that needs parens when it's one of several items in a list, call, "
                f"or dict/struct literal — write ({result.name} ...) instead "
                f"(line {self.peek().line})"
            )
        return result

# ---------- The library prelude and the shared front end ----------
_PRELUDE_DECLS = None


def _prelude_decls():
    global _PRELUDE_DECLS
    if _PRELUDE_DECLS is None:
        decls = Parser(lex(PRELUDE_SOURCE)).parse_program()
        for d in decls:
            d.line = d.col = d.end_col = 0   # no source position: a diagnostic never points into the prelude
            d.prelude = True
        _PRELUDE_DECLS = decls
    return _PRELUDE_DECLS


def _walk_decls(stmts, enums, variants, structs, fns):
    """Collects every enum / variant / struct / fn name declared anywhere in `stmts` (declaration
    tables are flat, see types.md 3.10 — a name declared in a nested block counts)."""
    for s in stmts:
        if isinstance(s, EnumDef):
            enums.add(s.name)
            variants.update(v for v, _f in s.variants)
        elif isinstance(s, StructDef):
            structs.add(s.name)
        elif isinstance(s, FnDef):
            fns.add(s.name)
            _walk_decls(s.body, enums, variants, structs, fns)
        elif isinstance(s, If):
            _walk_decls(s.body, enums, variants, structs, fns); _walk_decls(s.orelse, enums, variants, structs, fns)
        elif isinstance(s, (For, While, TestDef)):
            _walk_decls(s.body, enums, variants, structs, fns)
        elif isinstance(s, Try):
            _walk_decls(s.body, enums, variants, structs, fns); _walk_decls(s.handler, enums, variants, structs, fns)
        elif isinstance(s, TryFinally):
            _walk_decls(s.body, enums, variants, structs, fns); _walk_decls(s.finalizer, enums, variants, structs, fns)
        elif isinstance(s, Match):
            for _p, body in s.cases:
                _walk_decls(body, enums, variants, structs, fns)


def inject_prelude(stmts):
    """Puts the library enums (`Option<T>`, `Result<T, E>`) in front of a parsed program, so
    every later stage sees them as ordinary enum declarations. A program that already uses one
    of the library's names for something else keeps its meaning: the library enum is simply not
    loaded when the program declares an enum or struct of that name, or an enum variant or
    function named like one of its variants (so a program that wrote its own `enum Option` before
    3.1 behaves exactly as before)."""
    enums, variants, structs, fns = set(), set(), set(), set()
    _walk_decls(stmts, enums, variants, structs, fns)
    taken = variants | fns
    keep = [d for d in _prelude_decls()
            if d.name not in enums and d.name not in structs
            and not any(v in taken for v, _f in d.variants)]
    return keep + list(stmts)


def _attach_source(e, src, label):
    """Remembers which file and text an error came from, so the CLI can show the offending line
    (never overwrites: the innermost — first — source wins)."""
    if getattr(e, "lumen_src", None) is None:
        e.lumen_src, e.lumen_file = src, label
    return e


def analyze(src, label=None, base_dir=None, warnings=None):
    """THE front end, shared by every way of running or checking code: source text -> (ast,
    resolver) after lexing, parsing, loading the library prelude, name resolution and type
    checking. `run`, `check`, `test`, module loading and the VM all come through here, so they
    cannot disagree about what a program means or whether it is accepted. `base_dir`, when given,
    lets the checker read the function signatures of the modules the program imports (so a call
    `m.f(x)` is checked like a local call); `warnings`, a list, collects what the checker would
    warn about without rejecting the program."""
    try:
        ast = inject_prelude(Parser(lex(src)).parse_program())
        resolver = Resolver()
        resolver.resolve_program(ast)
        checker = TypeChecker(base_dir=base_dir)
        try:
            checker.check_program(ast)
        finally:
            if warnings is not None:
                for w in checker.warnings:
                    _attach_source(w, src, label)
                warnings.extend(checker.warnings)
    except (SyntaxError, LumenError) as e:
        _attach_source(e, src, label)
        raise
    return ast, resolver


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
    resolver limitation): errors.md §4.2.5 froze, with a worked example, the
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
            elif isinstance(s, While):
                self._prescan(s.body)
            elif isinstance(s, Try):
                self._prescan(s.body); self._prescan(s.handler)
            elif isinstance(s, TryFinally):
                self._prescan(s.body); self._prescan(s.finalizer)
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
        elif isinstance(node, Assign):
            self._resolve_expr(node.expr, scope_stack, in_function)
            self._resolve_assign_target(node.name, scope_stack, node)
        elif isinstance(node, Print):
            self._resolve_expr(node.expr, scope_stack, in_function)
        elif isinstance(node, If):
            self._resolve_expr(node.cond, scope_stack, in_function)
            self._resolve_block(node.body, scope_stack + [set()], in_function)
            self._resolve_block(node.orelse, scope_stack + [set()], in_function)
        elif isinstance(node, For):
            self._resolve_expr(node.iterable, scope_stack, in_function)
            self._resolve_block(node.body, scope_stack + [{node.var}], in_function)
        elif isinstance(node, While):
            self._resolve_expr(node.cond, scope_stack, in_function)
            self._resolve_block(node.body, scope_stack + [set()], in_function)
        elif isinstance(node, Try):
            self._try_depth += 1
            try:
                self._resolve_block(node.body, scope_stack + [set()], in_function)
            finally:
                self._try_depth -= 1
            self._resolve_block(node.handler, scope_stack + [{node.err_name}], in_function)
        elif isinstance(node, TryFinally):
            # The finalizer runs in the scope of the `try` statement itself: it sees what was
            # declared before the `try`, never the body's own locals or the catch variable.
            self._resolve_block(node.body, scope_stack + [set()], in_function)
            self._resolve_block(node.finalizer, scope_stack + [set()], in_function)
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
            # A capability declaration is not a name and not an action: it is a
            # static statement about the file. So it is validated now (typos and
            # reserved capabilities fail at `lumen check`), and it must sit at
            # the top level so a reader — or the audit tooling — sees every
            # declaration of a file by reading its header (security.md §7.3).
            expand_capability_decl(node.name, node.line)
            if in_function or len(scope_stack) != 1:
                raise LumenError(
                    f"ModuleError: 'use {node.name}' at line {node.line} must be at the top level of "
                    f"the file, not inside a function or block — capability declarations are static",
                    code="E0002", line=node.line, col=node.col or None, end_col=node.end_col or None,
                    text=f"'use {node.name}' must be at the top level of the file")
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
                return  # Tier 3: inside a try-body, defer to the runtime check (preserves errors.md §4.2.5)
            raise self._name_error(
                f"'{node.name}' is not defined (unresolved by static name resolution)", node,
                "E0101", f"'{node.name}' is not defined")
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
                raise self._name_error(
                f"unknown struct '{node.name}' (unresolved by static name resolution)", node,
                "E0102", f"unknown struct '{node.name}'")
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
            self._resolve_call_target(node.call.name, node.line, node.call)
            for a in node.call.args:
                self._resolve_expr(a, scope_stack, in_function)
        elif isinstance(node, Call):
            self._resolve_call_target(node.name, node.line, node)
            for a in node.args:
                self._resolve_expr(a, scope_stack, in_function)
        elif isinstance(node, Propagate):
            self._resolve_expr(node.expr, scope_stack, in_function)
        # Num, Str, Bool: nothing to resolve

    def _name_error(self, text, node, code, short):
        """A static NameError that knows where it is. `str()` keeps the long-standing wording
        (plus " at line N" when the position is known)."""
        line = getattr(node, "line", 0) or 0
        suffix = f" at line {line}" if line else ""
        return LumenError(f"NameError: {text}{suffix}", code=code, line=line or None,
                          col=getattr(node, "col", 0) or None, end_col=getattr(node, "end_col", 0) or None,
                          text=short)

    def _resolve_assign_target(self, name, scope_stack, node=None):
        """Same three-tier model `_resolve_expr`'s `Var` case uses for a
        *read*, applied to an assignment *target*: a bare `name = expr` must
        refer to a name already declared with `let` somewhere visible (Tier 1
        lexical scope, or Tier 2 module-level globals already covered by
        being lexically visible). Assigning to a name that doesn't exist
        anywhere is rejected at compile time, same as reading one — except,
        as with reads, inside a `try` body (Tier 3) it's deferred to the
        existing runtime check in `Env.assign`, so it stays uniformly
        catchable."""
        if self._known(name, scope_stack):
            return
        if self._try_depth > 0:
            return  # Tier 3: inside a try-body, defer to the runtime check
        raise self._name_error(
            f"cannot assign to '{name}' — it was never declared with 'let' "
            f"(unresolved by static name resolution)", node, "E0101",
            f"cannot assign to '{name}' — it was never declared with 'let'")

    def _resolve_call_target(self, name, line, node=None):
        if (name in self.function_names or name in self.enum_variant_names
                or name in self.BUILTIN_NAMES):
            return
        if self._try_depth > 0:
            return  # Tier 3: inside a try-body, defer to the runtime check (preserves errors.md §4.2.5)
        raise LumenError(
            f"NameError: function '{name}' is not defined (unresolved by static name "
            f"resolution — no 'fn {name}' or matching enum variant exists anywhere in "
            f"this module) at line {line}",
            code="E0101", line=line or None, col=getattr(node, "col", 0) or None,
            end_col=getattr(node, "end_col", 0) or None, text=f"function '{name}' is not defined")


# ---------- Static types: text helpers ----------
# Types are plain strings everywhere ("Int", "Result<Int, Str>"). These helpers are the only
# code that takes one apart, so the rules below are stated once.
_TYPE_PRIMITIVES = frozenset({"Int", "Float", "Str", "Bool"})
_TYPE_SPLIT_CACHE = {}


def split_type(t):
    """'Result<Int, List>' -> ('Result', ('Int', 'List')); 'Int' -> ('Int', ())."""
    hit = _TYPE_SPLIT_CACHE.get(t)
    if hit is not None:
        return hit
    i = t.find("<")
    if i < 0 or not t.endswith(">"):
        res = (t.strip(), ())
    else:
        inner, args, depth, cur = t[i + 1:-1], [], 0, []
        for ch in inner:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            if ch == "," and depth == 0:
                args.append("".join(cur).strip()); cur = []
            else:
                cur.append(ch)
        args.append("".join(cur).strip())
        res = (t[:i].strip(), tuple(args))
    _TYPE_SPLIT_CACHE[t] = res
    return res


def make_type(name, args):
    return f"{name}<{', '.join(args)}>" if args else name


def subst_type(t, bindings):
    """Replaces type parameters by their bindings, wherever they occur inside `t`."""
    name, args = split_type(t)
    if not args:
        return bindings.get(name, name)
    return make_type(name, [subst_type(a, bindings) for a in args])


def show_type(t):
    """A type as a diagnostic prints it: an unknown part inside type arguments is `_`."""
    name, args = split_type(t)
    if not args:
        return name
    return make_type(name, ["_" if a == "Any" else show_type(a) for a in args])


def _unknowns(t):
    return len(re.findall(r"\bAny\b", t))


def assignable(expected, actual):
    """Whether a value of static type `actual` may be used where `expected` is wanted. `Any`
    (an unannotated or unknowable type) is compatible with everything, in either direction —
    that is what makes the checker gradual — and it may appear inside type arguments too, so
    `Ok(5)`, which is a `Result<Int, _>`, fits a `Result<Int, Str>`."""
    if expected == actual or expected == "Any" or actual == "Any":
        return True
    en, ea = split_type(expected)
    an, aa = split_type(actual)
    if en != an:
        return False
    if not ea or not aa:
        return True            # a bare generic name carries no arguments to disagree about
    return len(ea) == len(aa) and all(assignable(e, a) for e, a in zip(ea, aa))


class _Mismatch(Exception):
    def __init__(self, expected, actual, param=None):
        super().__init__(f"{expected} vs {actual}")
        self.expected, self.actual, self.param = expected, actual, param


def unify(pattern, actual, params, bindings):
    """Matches a declared type (which may mention the type parameters `params`) against an
    actual type, recording what each parameter stood for in `bindings`. Raises _Mismatch."""
    if actual == "Any" or pattern == "Any":
        return
    pn, pa = split_type(pattern)
    if not pa and pn in params:
        old = bindings.get(pn)
        if old is None or old == "Any":
            bindings[pn] = actual
        elif not (assignable(old, actual) or assignable(actual, old)):
            raise _Mismatch(old, actual, param=pn)
        elif _unknowns(actual) < _unknowns(old):
            bindings[pn] = actual          # the more informative of two compatible types
        return
    an, aa = split_type(actual)
    if pn != an:
        raise _Mismatch(pattern, actual)
    if pa and aa:
        if len(pa) != len(aa):
            raise _Mismatch(pattern, actual)
        for p, a in zip(pa, aa):
            unify(p, a, params, bindings)


class TypeChecker:
    """Gradual static type checker. Only what the program annotates is enforced; everything
    else is `Any` and always compatible. Types are strings; generic types are written
    `Name<Arg, ...>` (docs/types.md §3.8). What it checks, with the diagnostic code of each
    (docs/types.md §3.6, docs/errors.md §4.11): `let`/return/argument/payload/assignment types (E0201), type-argument
    arity (E0202), generic parameter consistency (E0204), exhaustive `match` (E0301), variant
    and pattern problems (E0302, E0303), the `?` operator (E0401-E0404), a discarded `Result`
    (E0405) and a missing `return` (E0406), `return`/`?` inside `finally` (E0407).

    `lenient=True` is for the IR builder, which re-uses `infer()` only to learn a type: it
    never raises, it just answers `Any` where the checker would have rejected the program."""
    PRIMITIVES = {"Int", "Float", "Str", "Bool"}

    def __init__(self, lenient=False, base_dir=None):
        self.func_sigs = {}     # name -> (param_types, ret_type, type_params)
        self.enums = {}         # name -> {"params": [...], "variants": {vname: [type|None]}, "order": [vname]}
        self.variants = {}      # variant name -> enum name           (flat, like the run-time table)
        self.structs = {}       # name -> {"params": [...], "types": {field: type}, "fields": [...]}
        self.module_sigs = {}   # module alias -> {function name: (param_types, ret_type, type_params)}
        self.warnings = []
        self.lenient = lenient
        self.base_dir = base_dir
        self._fn = []           # the functions being checked, innermost last
        self._finally = 0       # > 0 while checking a `finally` block
        self._in_test = False

    # ------------------------------------------------------------------ diagnostics
    def _diag(self, code, kind, text, node=None, help=None, notes=None, node_span=None):
        line = getattr(node, "line", 0) or 0
        msg = f"{kind} at line {line}: {text}" if line else f"{kind}: {text}"
        col = getattr(node, "col", 0) or None
        end_col = getattr(node, "end_col", 0) or None
        if node_span:
            col, end_col = node_span
        return LumenError(msg, code=code, line=line or None, col=col, end_col=end_col,
                          help=help, notes=notes, text=text)

    def _fail(self, err):
        if not self.lenient:
            raise err

    def _warn(self, err):
        err.severity = "warning"
        if not self.lenient:
            self.warnings.append(err)

    # ------------------------------------------------------------------ declarations
    def register_declarations(self, stmts):
        """Learns every function signature, enum and struct a program declares — before any
        statement is checked, so a use may come before its declaration."""
        for s in stmts:
            if isinstance(s, FnDef):
                self.func_sigs[s.name] = ([t for _, t in s.params], s.ret_type, s.type_params)
        self._collect_types(stmts)
        for s in stmts:
            if isinstance(s, UseModule):
                self._load_module(s)

    def _collect_types(self, stmts):
        for s in stmts:
            if isinstance(s, EnumDef):
                variants = {}
                for vname, keys in s.variants:
                    declared = s.field_types.get(vname)
                    variants[vname] = list(declared) if declared is not None else [None] * len(keys)
                    self.variants[vname] = s.name
                self.enums[s.name] = {"params": list(s.type_params), "variants": variants,
                                      "order": [v for v, _k in s.variants]}
            elif isinstance(s, StructDef):
                self.structs[s.name] = {"params": list(s.type_params), "types": dict(s.field_types),
                                        "fields": list(s.fields)}
            elif isinstance(s, FnDef):
                self._collect_types(s.body)
            elif isinstance(s, If):
                self._collect_types(s.body); self._collect_types(s.orelse)
            elif isinstance(s, (For, While, TestDef)):
                self._collect_types(s.body)
            elif isinstance(s, Try):
                self._collect_types(s.body); self._collect_types(s.handler)
            elif isinstance(s, TryFinally):
                self._collect_types(s.body); self._collect_types(s.finalizer)
            elif isinstance(s, Match):
                for _p, body in s.cases:
                    self._collect_types(body)

    def _load_module(self, use):
        """`use "x.lum" as m`: reads just the function signatures out of the module, so a call
        `m.f(...)` is checked (and typed) like a local call. Anything that goes wrong here — no
        base directory, a missing file, a syntax error in it — leaves the module untyped; the
        run-time import reports the real problem."""
        if self.base_dir is None:
            return
        try:
            full = _locate_module(self.base_dir, use.path, use.line)
            with open(full) as f:
                mod = Parser(lex(f.read())).parse_program()
        except (SyntaxError, LumenError, OSError, UnicodeDecodeError):
            return
        self.module_sigs[use.alias] = {
            s.name: ([t for _, t in s.params], s.ret_type, s.type_params)
            for s in mod if isinstance(s, FnDef)}

    def validate_type(self, t, tparams, node, span=None):
        """Rejects a malformed type annotation: wrong number of type arguments, arguments on
        a type that takes none. Unknown bare names pass (they may be declared in an imported
        module) — only a *generic* use of an unknown name is an error."""
        name, args = split_type(t)
        if name in tparams:
            if args:
                self._fail(self._diag("E0202", "TypeError", f"type parameter '{name}' takes no type arguments", node, node_span=span))
        elif name in _TYPE_PRIMITIVES or name == "Any":
            if args:
                self._fail(self._diag("E0202", "TypeError", f"{name} takes no type arguments", node, node_span=span))
        elif name in ("List", "Dict"):
            if args:
                self._fail(self._diag(
                    "E0202", "TypeError",
                    f"{name} takes no type arguments in this version of Lumen (element types are not tracked) — write {name}",
                    node, node_span=span))
        elif name in self.enums or name in self.structs:
            want = len((self.enums.get(name) or self.structs.get(name))["params"])
            if len(args) != want:
                example = make_type(name, ["Int"] * want) if want else name
                self._fail(self._diag(
                    "E0202", "TypeError",
                    f"{name} takes {want} type argument(s) but {len(args)} were given"
                    + (f" — write it like {example}" if want else ""), node, node_span=span))
        elif args:
            self._fail(self._diag("E0203", "TypeError", f"unknown generic type '{name}'", node, node_span=span))
        for a in args:
            self.validate_type(a, tparams, node, span)

    # ------------------------------------------------------------------ statements
    def check_program(self, stmts):
        self.register_declarations(stmts)
        env = {}
        self._check_block(stmts, env)

    def _check_block(self, stmts, env):
        for s in stmts:
            self.check_stmt(s, env)

    def _tp_any(self):
        """The type parameters in scope (of the function being checked) all stand for `Any`
        inside its body — the body is checked once, not once per instantiation."""
        return {tp: "Any" for fn in self._fn for tp in fn.type_params}

    def check_stmt(self, node, env):
        if isinstance(node, Let):
            t = self.infer(node.expr, env)
            if node.type_ann:
                ann = subst_type(node.type_ann, self._tp_any())
                self.validate_type(node.type_ann, [tp for fn in self._fn for tp in fn.type_params], node, node.type_span)
                if not assignable(ann, t):
                    raise_ = self._diag(
                        "E0201", "TypeError",
                        f"'{node.name}' is declared as {show_type(ann)} but assigned a {show_type(t)} value",
                        node.expr if node.expr.col else node,
                        notes=[f"expected: {show_type(ann)}", f"found: {show_type(t)}"])
                    self._fail(raise_)
                env["@ann:" + node.name] = ann
            env[node.name] = (subst_type(node.type_ann, self._tp_any()) if node.type_ann else t)
        elif isinstance(node, Assign):
            t = self.infer(node.expr, env)
            ann = env.get("@ann:" + node.name)
            if ann is not None and split_type(ann)[0] not in _TYPE_PRIMITIVES | {"Any"}:
                # A name declared with a non-primitive type keeps it: assigning something else
                # is an error. (Reassignment of a primitive-annotated name is still only
                # re-inferred, as before 3.1 — see docs/types.md 3.6.)
                if not assignable(ann, t):
                    self._fail(self._diag(
                        "E0201", "TypeError",
                        f"cannot assign a {show_type(t)} value to '{node.name}', which is declared as {show_type(ann)}",
                        node.expr if node.expr.col else node,
                        notes=[f"expected: {show_type(ann)}", f"found: {show_type(t)}"]))
                env[node.name] = ann
            else:
                env[node.name] = t
        elif isinstance(node, FnDef):
            self._check_fn(node)
        elif isinstance(node, TestDef):
            saved = (self._fn, self._finally, self._in_test)
            self._fn, self._finally, self._in_test = [], 0, True
            try:
                self._check_block(node.body, {})
            finally:
                self._fn, self._finally, self._in_test = saved
        elif isinstance(node, If):
            self.infer(node.cond, env)
            self._check_block(node.body, dict(env))
            self._check_block(node.orelse, dict(env))
        elif isinstance(node, For):
            self.infer(node.iterable, env)
            inner = dict(env); inner[node.var] = "Any"
            self._check_block(node.body, inner)
        elif isinstance(node, While):
            self.infer(node.cond, env)
            self._check_block(node.body, dict(env))
        elif isinstance(node, Try):
            self._check_block(node.body, dict(env))
            inner = dict(env); inner[node.err_name] = "Any"
            self._check_block(node.handler, inner)
        elif isinstance(node, TryFinally):
            self._check_block(node.body, dict(env))
            self._finally += 1
            try:
                self._check_block(node.finalizer, dict(env))
            finally:
                self._finally -= 1
        elif isinstance(node, Match):
            self._check_match(node, env)
        elif isinstance(node, Return):
            self._check_return(node, env)
        elif isinstance(node, ExprStmt):
            t = self.infer(node.expr, env)
            if split_type(t)[0] == "Result" and not isinstance(node.expr, Propagate):
                self._fail(self._diag(
                    "E0405", "TypeError",
                    f"the {show_type(t)} value is discarded, so a failure would be ignored without a trace",
                    node.expr if node.expr.col else node,
                    help="handle it with `match`, propagate it with `?`, or discard it on purpose with `let _ = ...`"))
        elif isinstance(node, Print):
            self.infer(node.expr, env)
        elif isinstance(node, FieldSet):
            ot = self.infer(node.obj, env)
            vt = self.infer(node.expr, env)
            sname, sargs = split_type(ot)
            info = self.structs.get(sname)
            ft = info["types"].get(node.field) if info else None
            if ft is not None:
                # `p.x = value` on a typed field: the value must fit the declared type (a generic
                # struct's parameters are what the object's own type says they are, else unknown)
                want = subst_type(ft, {p: (sargs[i] if i < len(sargs) else "Any") for i, p in enumerate(info["params"])})
                if not assignable(want, vt):
                    self._fail(self._diag(
                        "E0201", "TypeError",
                        f"field '{node.field}' of struct {sname} expects {show_type(want)} but got {show_type(vt)}",
                        node.expr if node.expr.col else node,
                        notes=[f"expected: {show_type(want)}", f"found: {show_type(vt)}"]))
        elif isinstance(node, IndexSet):
            self.infer(node.obj, env); self.infer(node.key, env); self.infer(node.expr, env)
        elif isinstance(node, UseModule):
            env[node.alias] = "Module"
        elif isinstance(node, EnumDef):
            tps = list(node.type_params)
            for vname, types in node.field_types.items():
                for t in types:
                    if t is not None:
                        self.validate_type(t, tps, node)
        elif isinstance(node, StructDef):
            for t in node.field_types.values():
                self.validate_type(t, list(node.type_params), node)

    def _check_fn(self, node):
        tps = list(node.type_params)
        for _p, pt in node.params:
            if pt:
                self.validate_type(pt, tps, node)
        if node.ret_type:
            self.validate_type(node.ret_type, tps, node)
        anyp = {tp: "Any" for tp in tps}
        local = {p: subst_type(pt or "Any", anyp) for p, pt in node.params}
        saved = (self._fn, self._finally, self._in_test)
        self._fn, self._finally, self._in_test = self._fn + [node], 0, False
        try:
            self._check_block(node.body, local)
        finally:
            self._fn, self._finally, self._in_test = saved
        if node.ret_type:
            rn = split_type(node.ret_type)[0]
            if rn in ("Result", "Option") and not self._always_exits(node.body):
                self._fail(self._diag(
                    "E0406", "TypeError",
                    f"function '{node.name}' is declared -> {show_type(node.ret_type)} but can reach its end "
                    f"without returning a value", node,
                    help="end every path with `return Ok(...)`, `return Err(...)`, `return Some(...)` or `return None`"))

    def _always_exits(self, stmts):
        """True when every path through `stmts` returns or raises — i.e. cannot fall off the end."""
        for s in stmts:
            if isinstance(s, Return):
                return True
            if isinstance(s, ExprStmt) and isinstance(s.expr, Call) and s.expr.name in ("fail", "fail_with"):
                return True
            if isinstance(s, If) and s.orelse and self._always_exits(s.body) and self._always_exits(s.orelse):
                return True
            if isinstance(s, Match) and s.cases and all(self._always_exits(b) for _p, b in s.cases):
                return True
            if isinstance(s, Try) and self._always_exits(s.body) and self._always_exits(s.handler):
                return True
            if isinstance(s, TryFinally) and self._always_exits(s.body):
                return True
        return False

    def _check_return(self, node, env):
        t = self.infer(node.expr, env)
        if self._finally:
            self._fail(self._diag(
                "E0407", "TypeError", "'return' is not allowed in a 'finally' block — cleanup must not change how the function exits",
                node))
        if not self._fn:
            return
        fn = self._fn[-1]
        if not fn.ret_type:
            return
        expected = subst_type(fn.ret_type, {tp: "Any" for tp in fn.type_params})
        if not assignable(expected, t):
            self._fail(self._diag(
                "E0201", "TypeError",
                f"function '{fn.name}' declares -> {show_type(expected)} but returns a {show_type(t)} value",
                node.expr if node.expr.col else node,
                notes=[f"expected: {show_type(expected)}", f"found: {show_type(t)}"]))

    # ------------------------------------------------------------------ match
    def _unit_variant(self, name):
        en = self.variants.get(name)
        return en is not None and not self.enums[en]["variants"][name]

    def _check_match(self, node, env):
        st = self.infer(node.expr, env)
        sname, sargs = split_type(st)
        enum = self.enums.get(sname) if st != "Any" else None
        tmap = {}
        if enum is not None:
            tmap = {p: (sargs[i] if i < len(sargs) else "Any") for i, p in enumerate(enum["params"])}
        covered, catch_all, arm_variants = set(), False, []
        for pattern, body in node.cases:
            arm_env = dict(env)
            vname = None
            if isinstance(pattern, PVariant):
                vname = pattern.name
                subs = pattern.subnames
                payload = None
                if enum is not None:
                    if vname not in enum["variants"]:
                        self._fail(self._diag(
                            "E0302", "TypeError",
                            f"'{vname}' is not a variant of {show_type(st)}", pattern,
                            help="its variants are: " + ", ".join(enum["order"])))
                    else:
                        payload = enum["variants"][vname]
                        if len(subs) != len(payload):
                            self._fail(self._diag(
                                "E0303", "TypeError",
                                f"the pattern {vname}(...) names {len(subs)} field(s) but the variant has {len(payload)}",
                                pattern))
                        covered.add(vname)
                for i, sub in enumerate(subs):
                    pt = payload[i] if payload is not None and i < len(payload) else None
                    arm_env[sub] = subst_type(pt, tmap) if pt else "Any"
                arm_variants.append(vname)
            elif isinstance(pattern, PBind):
                if self._unit_variant(pattern.name):
                    vname = pattern.name
                    if enum is not None:
                        if vname not in enum["variants"]:
                            self._fail(self._diag(
                                "E0302", "TypeError",
                                f"'{vname}' is not a variant of {show_type(st)}", pattern,
                                help="its variants are: " + ", ".join(enum["order"])))
                        else:
                            covered.add(vname)
                    arm_variants.append(vname)
                else:
                    catch_all = True
                    arm_env[pattern.name] = st
            elif isinstance(pattern, PWildcard):
                catch_all = True
            self._check_block(body, arm_env)
        if catch_all:
            return
        if enum is not None:
            missing = [v for v in enum["order"] if v not in covered]
            if missing:
                self._fail(self._diag(
                    "E0301", "TypeError",
                    f"this match on {show_type(st)} does not cover: {', '.join(missing)}", node,
                    help="add an arm for each missing variant, or a final `_` arm"))
        elif arm_variants:
            owners = {self.variants.get(v) for v in arm_variants}
            if len(owners) == 1 and None not in owners:
                en = self.enums[owners.pop()]
                missing = [v for v in en["order"] if v not in arm_variants]
                if missing:
                    self._warn(self._diag(
                        "W0301", "Warning",
                        f"this match has no catch-all arm and does not cover: {', '.join(missing)}; the value's "
                        f"type is not known here, so if one of those variants arrives the program fails at run "
                        f"time with a MatchError", node,
                        help="annotate the function's return type so the compiler can check this, or add a `_` arm"))

    # ------------------------------------------------------------------ expressions
    def infer(self, node, env):
        if isinstance(node, Num): return "Float" if isinstance(node.v, float) else "Int"
        if isinstance(node, Str): return "Str"
        if isinstance(node, Bool): return "Bool"
        if isinstance(node, ListLit):
            for i in node.items: self.infer(i, env)
            return "List"
        if isinstance(node, DictLit):
            for k, v in node.pairs:
                self.infer(k, env); self.infer(v, env)
            return "Dict"
        if isinstance(node, Var):
            if node.name in env:
                return env[node.name]
            if node.name in self.variants and self._unit_variant(node.name):
                return self._variant_type(node.name, {})
            return "Any"
        if isinstance(node, Not):
            self.infer(node.e, env)
            return "Bool"
        if isinstance(node, BoolOp):
            self.infer(node.l, env); self.infer(node.r, env)
            return "Bool"
        if isinstance(node, BinOp):
            lt, rt = self.infer(node.l, env), self.infer(node.r, env)
            return self._infer_binop(node, lt, rt)
        if isinstance(node, Call):
            return self._infer_call(node, env)
        if isinstance(node, StructLit):
            return self._infer_struct_lit(node, env)
        if isinstance(node, FieldGet):
            ot = self.infer(node.obj, env)
            name, args = split_type(ot)
            info = self.structs.get(name)
            if info is not None and node.field in info["types"]:
                tmap = {p: (args[i] if i < len(args) else "Any") for i, p in enumerate(info["params"])}
                return subst_type(info["types"][node.field], tmap)
            return "Any"
        if isinstance(node, Index):
            self.infer(node.obj, env); self.infer(node.key, env)
            return "Any"
        if isinstance(node, MethodCall):
            self.infer(node.obj, env)
            sig = None
            if isinstance(node.obj, Var) and env.get(node.obj.name, "Module") == "Module":
                sig = self.module_sigs.get(node.obj.name, {}).get(node.method)
            if sig is not None:
                return self._call_sig(node, node.args, sig, f"{node.obj.name}.{node.method}", env)
            for a in node.args: self.infer(a, env)
            return "Any"
        if isinstance(node, SpawnExpr):
            self.infer(node.call, env)
            return "Any"
        if isinstance(node, Propagate):
            return self._infer_propagate(node, env)
        return "Any"

    def _infer_binop(self, node, lt, rt):
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

    # ---- calls
    def _infer_call(self, node, env):
        name = node.name
        if name in BUILTIN_FUNCTION_NAMES:
            return self._infer_builtin(node, env)
        if name in self.variants:
            return self._infer_variant(node, env)
        if name in self.func_sigs:
            return self._call_sig(node, node.args, self.func_sigs[name], name, env)
        for a in node.args:
            self.infer(a, env)
        return "Any"

    def _call_sig(self, node, arg_nodes, sig, name, env):
        param_types, ret, type_params = sig
        bindings = {}
        for i, a in enumerate(arg_nodes):
            at = self.infer(a, env)
            if i >= len(param_types):
                continue
            pt = param_types[i]
            if not pt or at == "Any":
                continue
            try:
                unify(pt, at, type_params, bindings)
            except _Mismatch as m:
                if m.param:
                    self._fail(self._diag(
                        "E0204", "TypeError",
                        f"type parameter '{m.param}' inferred as both {show_type(m.expected)} and "
                        f"{show_type(m.actual)} in call to '{name}'", node))
                else:
                    self._fail(self._diag(
                        "E0201", "TypeError",
                        f"'{name}' argument {i+1} expects {show_type(pt)} but got {show_type(at)}",
                        a if a.col else node,
                        notes=[f"expected: {show_type(pt)}", f"found: {show_type(at)}"]))
        if not ret:
            return "Any"
        return subst_type(ret, {tp: bindings.get(tp, "Any") for tp in type_params})

    def _variant_type(self, vname, bindings):
        enum_name = self.variants[vname]
        enum = self.enums[enum_name]
        return make_type(enum_name, [bindings.get(p, "Any") for p in enum["params"]])

    def _infer_variant(self, node, env):
        vname = node.name
        enum = self.enums[self.variants[vname]]
        payload = enum["variants"][vname]
        arg_types = [self.infer(a, env) for a in node.args]
        typed = any(p is not None for p in payload)
        if typed and len(node.args) != len(payload):
            self._fail(self._diag(
                "E0304", "TypeError",
                f"{vname}() expects {len(payload)} value(s), got {len(node.args)}", node))
        bindings = {}
        for i, (pt, at) in enumerate(zip(payload, arg_types)):
            if pt is None:
                continue
            try:
                unify(pt, at, enum["params"], bindings)
            except _Mismatch as m:
                self._fail(self._diag(
                    "E0201", "TypeError",
                    f"{vname}() argument {i+1} expects {show_type(m.expected if not m.param else pt)} but got "
                    f"{show_type(m.actual)}", node.args[i] if node.args[i].col else node,
                    notes=[f"expected: {show_type(pt)}", f"found: {show_type(at)}"]))
        return self._variant_type(vname, bindings)

    def _infer_struct_lit(self, node, env):
        value_types = {k: self.infer(v, env) for k, v in node.values.items()}
        info = self.structs.get(node.name)
        if info is None:
            return "Any"
        bindings = {}
        for k, vt in value_types.items():
            ft = info["types"].get(k)
            if ft is None:
                continue
            try:
                unify(ft, vt, info["params"], bindings)
            except _Mismatch as m:
                self._fail(self._diag(
                    "E0201", "TypeError",
                    f"field '{k}' of struct {node.name} expects {show_type(ft)} but got {show_type(vt)}",
                    node.values[k] if node.values[k].col else node,
                    notes=[f"expected: {show_type(ft)}", f"found: {show_type(vt)}"]))
        return make_type(node.name, [bindings.get(p, "Any") for p in info["params"]]) if info["params"] else node.name

    def _infer_builtin(self, node, env):
        ats = [self.infer(a, env) for a in node.args]
        name = node.name
        def kind(i):
            return split_type(ats[i])[0] if i < len(ats) else "Any"
        def args_of(i):
            return split_type(ats[i])[1]
        if name == "ok_or" and len(ats) == 2:
            if kind(0) not in ("Option", "Any"):
                self._fail(self._diag("E0201", "TypeError", f"ok_or() expects an Option as its first argument, got {show_type(ats[0])}", node))
                return "Any"
            payload = args_of(0)[0] if args_of(0) else "Any"
            return make_type("Result", [payload, ats[1]])
        if name in ("unwrap", "unwrap_or") and ats:
            if kind(0) not in ("Option", "Result", "Any"):
                self._fail(self._diag("E0201", "TypeError", f"{name}() expects a Result or an Option, got {show_type(ats[0])}", node))
                return "Any"
            payload = args_of(0)[0] if args_of(0) else "Any"
            if name == "unwrap_or" and len(ats) == 2 and not assignable(payload, ats[1]):
                self._fail(self._diag("E0201", "TypeError",
                                      f"unwrap_or() default is a {show_type(ats[1])} but the value would be a {show_type(payload)}", node))
            return payload
        if name in ("is_ok", "is_err") and ats:
            if kind(0) not in ("Result", "Any"):
                self._fail(self._diag("E0201", "TypeError", f"{name}() expects a Result, got {show_type(ats[0])}", node))
            return "Bool"
        if name in ("is_some", "is_none") and ats:
            if kind(0) not in ("Option", "Any"):
                self._fail(self._diag("E0201", "TypeError", f"{name}() expects an Option, got {show_type(ats[0])}", node))
            return "Bool"
        return "Any"

    # ---- the ? operator
    def _infer_propagate(self, node, env):
        t = self.infer(node.expr, env)
        span = None
        if node.col and node.end_col:
            span = (node.col, node.end_col)
        if self.lenient:
            name, args = split_type(t)
            return args[0] if name in ("Result", "Option") and args else "Any"
        if self._finally:
            self._fail(self._diag(
                "E0407", "TypeError", "'?' is not allowed in a 'finally' block — cleanup must not change how the function exits",
                node, node_span=span))
        if not self._fn:
            where = "a test block" if self._in_test else "top-level code"
            self._fail(self._diag(
                "E0402", "TypeError", f"'?' can only be used inside a function (it returns from the function), not in {where}",
                node, node_span=span,
                help="match on the value instead, or move this code into a function"))
            return "Any"
        fn = self._fn[-1]
        name, args = split_type(t)
        if name == "Result":
            kind, payload = "Result", (args[0] if args else "Any")
        elif name == "Option":
            kind, payload = "Option", (args[0] if args else "Any")
        elif name == "Any":
            kind, payload = None, "Any"
        else:
            self._fail(self._diag(
                "E0401", "TypeError", f"the '?' operator applies to a Result or an Option, but this is a {show_type(t)}",
                node, node_span=span,
                help="'?' unwraps `Ok(v)`/`Some(v)` and returns `Err(e)`/`None` from the function"))
            return "Any"
        if not fn.ret_type:
            return payload
        rt = subst_type(fn.ret_type, {tp: "Any" for tp in fn.type_params})
        rn, ra = split_type(rt)
        if rn == "Any":
            return payload
        if kind is None:
            if rn not in ("Result", "Option"):
                self._fail(self._diag(
                    "E0403", "TypeError",
                    f"'?' may return an Err or None from '{fn.name}', which is declared -> {show_type(rt)}",
                    node, node_span=span,
                    help="declare the function as returning a Result or an Option"))
            return payload
        if rn != kind:
            self._fail(self._diag(
                "E0403", "TypeError",
                f"'?' on a {kind} returns {'an Err' if kind == 'Result' else 'None'} from '{fn.name}', but the function is declared -> {show_type(rt)}",
                node, node_span=span,
                notes=[f"this expression is a {show_type(t)}", f"the function returns {show_type(rt)}"],
                help=("convert explicitly with `ok_or(option, error)` (Option -> Result), or `match` and return the failure you mean"
                      if kind == "Option" and rn == "Result" else
                      "convert explicitly: `match` on the value and return the failure you mean")))
            return payload
        if kind == "Result":
            found = args[1] if len(args) > 1 else "Any"
            want = ra[1] if len(ra) > 1 else "Any"
            if not assignable(want, found):
                self._fail(self._diag(
                    "E0404", "TypeError",
                    f"incompatible error type: '?' would return a {show_type(found)} error, but '{fn.name}' returns errors of type {show_type(want)}",
                    node, node_span=span,
                    notes=[f"expected error type: {show_type(want)}", f"found error type: {show_type(found)}"],
                    help=f"'?' never converts errors; `match` the result and build the {show_type(want)} error yourself: Err(<{show_type(want)}>)"))
        return payload

class LumenError(Exception):
    """A Lumen compile-time or run-time failure. `str(e)` is the message, in the form the language
    has always printed ("TypeError at line 3: ..."). The optional keyword fields carry the same
    failure as structure — a stable `code`, the `line`/`col`/`end_col` of the code at fault, a
    `help` line, extra `notes`, and `text` (the message without its "Kind at line N:" prefix) — so
    a tool can render it better than the plain message without anyone having to parse that."""
    severity = "error"
    def __init__(self, message="", *, code=None, line=None, col=None, end_col=None,
                 help=None, notes=None, text=None):
        super().__init__(message)
        self.code, self.line, self.col, self.end_col = code, line, col, end_col
        self.help, self.notes = help, list(notes or [])
        self.text = text if text is not None else message


# =====================================================================
# Phase 5: the capability runtime (Language Specification 3.0)
# =====================================================================
# Capability security in Lumen is two separate things, and keeping them
# separate is the whole point of this section:
#
#   DECLARING  — a `use fs.read` line in a .lum file says "this file needs
#                to read files". It is documentation the audit tooling and
#                the host can read. It grants nothing.
#   GRANTING   — the HOST (the `lumen run --allow-read=...` command line, or
#                the embedding Python program that builds a `Policy`) decides
#                what a run may actually do. Nothing inside a .lum file can
#                create, widen, or even inspect a grant.
#
# An operation is permitted only if ALL of these hold, checked at the moment
# it is attempted, in one function (`capability_call`) that both the
# interpreter and the VM call — there is deliberately no second copy of this
# logic for the two backends to disagree about:
#   1. the module that is executing the call declared the capability;
#   2. every module on its import chain declared it too (a library cannot
#      hold authority its importer lacks);
#   3. the host's Policy grants the capability at all;
#   4. the specific resource (a path, a host:port) is inside the granted scope.
# Anything else is a structured `PermissionDenied` error and the operation
# is NOT performed. A program starts with the empty policy: default-deny.

CAPABILITIES = {
    "fs.read":  "read file contents, and ask whether a path exists or is a directory",
    "fs.write": "create, overwrite, append to, or delete files",
    "fs.list":  "list the entries of a directory",
    "net.http": "make HTTP and HTTPS GET requests",
}
# Shorthands for declarations only. `use fs` declares all three fs.*
# capabilities, `use net` declares net.http. They exist so 2.x source keeps
# parsing; because declaring never grants, over-declaring costs nothing but
# a longer list in the audit output.
CAPABILITY_ALIASES = {"fs": ("fs.read", "fs.write", "fs.list"), "net": ("net.http",)}
# Named in the capability taxonomy but with no operation behind them in this
# version. Declaring one is an error (unsupported operations fail closed)
# rather than a silent no-op that would look like protection.
RESERVED_CAPABILITIES = ("net.connect", "env.read", "process.spawn")

# builtin -> (capability it needs, number of arguments). The ONE table every
# gated operation is routed through; BUILTIN_FUNCTION_NAMES, the VM and the
# interpreter all read it, so a gated builtin cannot be added without a
# capability decision.
OPERATIONS = {
    "read_file":   ("fs.read", 1),
    "file_exists": ("fs.read", 1),
    "is_dir":      ("fs.read", 1),
    "write_file":  ("fs.write", 2),
    "append_file": ("fs.write", 2),
    "delete_file": ("fs.write", 1),
    "list_dir":    ("fs.list", 1),
    "http_get":    ("net.http", 1),
}
CAPABILITY_BUILTIN_NAMES = frozenset(OPERATIONS)
assert CAPABILITY_BUILTIN_NAMES <= BUILTIN_FUNCTION_NAMES

_GRANT_FLAG_HINT = {
    "fs.read": "--allow-read[=PATH,...]", "fs.write": "--allow-write[=PATH,...]",
    "fs.list": "--allow-list[=PATH,...]", "net.http": "--allow-net[=HOST[:PORT],...]",
}


def expand_capability_decl(name, line):
    """The canonical capability names a `use <name>` declaration declares.
    Raises ModuleError for anything not recognised — at check time, so a typo
    in a declaration is caught by `lumen check`, not discovered mid-run."""
    if name in CAPABILITIES:
        return (name,)
    if name in CAPABILITY_ALIASES:
        return CAPABILITY_ALIASES[name]
    if name in RESERVED_CAPABILITIES:
        raise LumenError(
            f"ModuleError: capability '{name}' at line {line} is reserved but not implemented "
            f"in this version, so it cannot be declared (unsupported capabilities fail closed)")
    if "." in name:
        raise LumenError(
            f"ModuleError: unknown capability '{name}' at line {line} "
            f"(known: {', '.join(sorted(CAPABILITIES))})")
    raise LumenError(f"ModuleError: unknown module '{name}' at line {line}")


class PermissionDenied(LumenError):
    """A capability check failed. A LumenError (so `try`/`catch` sees it like
    any other runtime error, and an uncaught one prints exactly like one),
    carrying the structured fields a handler can branch on — see
    `make_error_value`. `resource` is what the program asked for, with any
    URL reduced to scheme://host:port so a denial never echoes a credential
    or a query string."""
    def __init__(self, message, capability, operation, resource, reason):
        super().__init__(message)
        self.capability, self.operation = capability, operation
        self.resource, self.reason = resource, reason


def _is_within(path, scope):
    p, s = os.path.normcase(path), os.path.normcase(scope)
    if p == s:
        return True
    return p.startswith(s if s.endswith(os.sep) else s + os.sep)


def _parse_host_port(text):
    """'host', 'host:port' or '[v6]:port' -> (lowercase host, port|None)."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit("//" + text)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError(f"invalid network scope {text!r}")
    if (not host or parts.path or parts.query or parts.username is not None
            or not re.fullmatch(r"[A-Za-z0-9._-]+|[0-9A-Fa-f:.]+", host)):
        raise ValueError(f"invalid network scope {text!r} (expected HOST or HOST:PORT)")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError(f"invalid port in network scope {text!r}")
    return host.lower(), port


class AuditError(LumenError):
    """The audit log could not be written. Raised INSTEAD of performing the operation: when the host asked for a
    record, an access that cannot be recorded does not happen (it fails closed)."""


_AUDIT_FIELD_MAX = 300


def _audit_clean(value):
    """What may go into a record: strings are length-capped (a program can ask for an absurdly long path) and
    containers are cleaned recursively. JSON encoding does the rest — every control character, quote and newline in
    a string is escaped, so a hostile file name can never start a new line or forge a record."""
    if isinstance(value, str):
        return value if len(value) <= _AUDIT_FIELD_MAX else value[:_AUDIT_FIELD_MAX] + "...[truncated]"
    if isinstance(value, (list, tuple)):
        return [_audit_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _audit_clean(v) for k, v in value.items()}
    return value


class AuditLog:
    """Optional, host-controlled record of permission decisions (SEC-010): one JSON object per line, appended.

    What is recorded: that the run started and with which grants, what each module declared (the program's
    *requests*), the consent answers given at a prompt, and every gated operation with its decision (`allow` /
    `deny`), capability, operation, resource and — for a denial — the reason. What is never recorded: file
    contents, data written, URL userinfo / path / query (a URL is reduced to scheme://host:port, exactly as in a
    denial), environment, or anything the program computed.

    The log is opened by the HOST, before the program runs, and the program has no handle on it. A program could
    still reach it through a granted file scope, so the log refuses to coexist with a write grant that includes it
    (`Policy` calls `_check_unreachable`); reading it is not prevented — it holds only paths and decisions.

    If a record cannot be written the operation is not performed (AuditError)."""
    LEVELS = ("all", "denied")

    def __init__(self, path, level="all"):
        if level not in self.LEVELS:
            raise ValueError(f"audit level must be one of {', '.join(self.LEVELS)} (got {level!r})")
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("the audit log path must be a non-empty string")
        base = os.path.basename(path)
        if base in ("", ".", ".."):
            raise ValueError(f"the audit log path '{path}' must name a file")
        self.level = level
        # the file as the OS will reach it: real directory + the given name (the name itself must not be a link)
        self.path = os.path.join(os.path.realpath(os.path.dirname(path) or "."), base)
        flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise ValueError(f"the audit log '{path}' is a symbolic link; name the real file")
            raise ValueError(f"cannot open the audit log '{path}': {e.strerror}")
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            os.close(fd)
            raise ValueError(f"the audit log '{path}' must be a regular file with a single name (no hard links)")
        self._fd = fd
        self._lock = threading.Lock()
        self._seq = 0

    def _check_unreachable(self, built_grants):
        """Called by Policy: the program must not be able to write (or delete) its own audit trail."""
        scope = built_grants.get("fs.write", ())
        if "fs.write" in built_grants and scope is None:
            raise ValueError("an audit log cannot be combined with an unrestricted fs.write grant: a program allowed to "
                             "write anywhere could erase its own audit trail (grant --allow-write=PATH,... instead)")
        for entry in scope or ():
            if _is_within(self.path, entry):
                raise ValueError(f"the audit log '{self.path}' lies inside the fs.write scope '{entry}': the program "
                                 f"could overwrite or delete it (keep the log outside every writable path)")

    def record(self, event, **fields):
        with self._lock:
            self._seq += 1
            entry = {"seq": self._seq,
                     "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                     "event": event}
            entry.update({k: _audit_clean(v) for k, v in fields.items() if v is not None})
            data = (json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
            try:
                while data:
                    data = data[os.write(self._fd, data):]
            except OSError as e:
                raise AuditError(f"AuditError: the audit log could not be written ({e.strerror}); "
                                 f"the operation was not performed")

    def access(self, decision, capability, op, resource, module, reason=None, redirect=False):
        if decision == "allow" and self.level != "all":
            return
        self.record("access", decision=decision, capability=capability, operation=op, resource=resource,
                    module=module, reason=reason, redirect=True if redirect else None)

    def close(self):
        fd, self._fd = getattr(self, "_fd", None), None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def __del__(self):
        self.close()


# Descriptor-relative file access (openat-style) is what makes scoped file access immune to a directory being
# swapped for a symlink between the check and the use. Every POSIX platform has it; Windows does not, and there a
# scoped grant falls back to checking the resolved path (docs/security-model.md 5.2 says so).
_HAVE_DIRFD = bool(
    hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd and os.unlink in os.supports_dir_fd
    and os.listdir in os.supports_fd)
_O_DIR = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))


class Policy:
    """The host's decision about what a run may do. Immutable once built.

    `grants` maps a capability name to its scope: `None` means unrestricted,
    otherwise a list of filesystem paths (fs.*) or HOST / HOST:PORT strings
    (net.http). A capability absent from the mapping is denied. The empty
    Policy — `Policy()` / `DENY_ALL` — grants nothing and is what every run
    gets unless the host says otherwise (SEC-001, default-deny).

    Filesystem scopes are resolved to real paths (symlinks and `..` removed)
    when the Policy is built, relative to the current directory — the same
    directory the fs builtins themselves resolve against.
    """
    def __init__(self, grants=None, audit=None):
        built = {}
        for cap, scope in (grants or {}).items():
            if cap not in CAPABILITIES:
                raise ValueError(f"unknown capability {cap!r}")
            if scope is None:
                built[cap] = None
            elif cap == "net.http":
                built[cap] = tuple(sorted(set(_parse_host_port(s) for s in scope), key=lambda t: (t[0], t[1] or 0)))
            else:
                built[cap] = tuple(sorted(set(os.path.realpath(s) for s in scope)))
            if built[cap] == ():
                raise ValueError(f"capability {cap!r} was given an empty scope list")
        self._grants = built
        self.audit = audit          # an AuditLog or None: the host's optional record of decisions
        if audit is not None:
            audit._check_unreachable(built)
        # Granted directories are PINNED: opened once, without following a symlink, and every scoped operation is
        # then performed relative to that descriptor. What the grant names is therefore the directory that existed
        # when the policy was built, not whatever a path string means later. (A scope that does not exist yet, or is
        # a single file, is pinned through its parent directory at use time — see _anchor.)
        self._pinned = {}
        self._pin_lock = threading.Lock()
        if _HAVE_DIRFD:
            for cap, scope in built.items():
                if scope is not None and cap != "net.http":
                    for entry in scope:
                        self._pinned_fd(entry)

    def extended(self, extra):
        """A NEW Policy: this one plus the capabilities in `extra` ({capability: None | [scopes]}) that this one does
        not already grant. An existing grant is never widened or replaced — the consent prompt uses this, so a prompt
        can only add what was not granted. Keeps the audit log (and re-checks it against the new grants)."""
        merged = {}
        for cap, scope in self._grants.items():
            if scope is None:
                merged[cap] = None
            elif cap == "net.http":
                merged[cap] = [(f"[{h}]" if ":" in h else h) + (f":{p}" if p else "") for h, p in scope]
            else:
                merged[cap] = list(scope)
        for cap, scope in extra.items():
            if cap not in merged:
                merged[cap] = scope
        return Policy(merged, audit=self.audit)

    def _pinned_fd(self, entry):
        """The descriptor of scope directory `entry`, opened (once) with O_NOFOLLOW; None if it is not a directory
        right now. A failed attempt is not cached, so a scope created after the policy was built is pinned on first use."""
        with self._pin_lock:
            fd = self._pinned.get(entry)
            if fd is None:
                try:
                    fd = os.open(entry, _O_DIR | os.O_NOFOLLOW)
                except OSError:
                    return None
                self._pinned[entry] = fd
            return fd

    def _anchor(self, capability, target):
        """For a resolved `target` inside this capability's scope: (dir_fd, parts, owned_fd) where `parts` are the
        remaining path components to walk beneath dir_fd, and owned_fd (or None) is a descriptor the caller must close."""
        for entry in self._grants[capability]:
            if _is_within(target, entry):
                break
        else:
            return None
        rel = os.path.relpath(target, entry)
        parts = () if rel == os.curdir else tuple(rel.split(os.sep))
        fd = self._pinned_fd(entry)
        if fd is not None:
            return fd, parts, None
        # the scope is a file (or does not exist yet): anchor on its parent directory, and walk from the entry's name
        parent = os.path.dirname(entry)
        pfd = os.open(parent, _O_DIR)
        if sys.platform.startswith("linux") and os.path.realpath(f"/proc/self/fd/{pfd}") != parent:
            os.close(pfd)
            return None
        return pfd, (os.path.basename(entry),) + parts, pfd

    def __del__(self):
        for fd in list(getattr(self, "_pinned", {}).values()):
            try:
                os.close(fd)
            except OSError:
                pass

    @classmethod
    def deny_all(cls):
        return cls()

    @classmethod
    def unrestricted(cls, capabilities=None):
        """Every named capability (default: all of them), everywhere. For
        embedders and tests; the command line never builds this implicitly."""
        return cls({c: None for c in (capabilities or CAPABILITIES)})

    def granted(self, capability):
        return capability in self._grants

    def scope(self, capability):
        """None if unrestricted, else the tuple of scope entries. Only
        meaningful when `granted(capability)`."""
        return self._grants.get(capability)

    def fs_allows(self, capability, resolved_path):
        scope = self._grants.get(capability)
        return scope is None or any(_is_within(resolved_path, s) for s in scope)

    def net_allows(self, host, port):
        scope = self._grants.get("net.http")
        return scope is None or any(h == host and (p is None or p == port) for h, p in scope)

    def scope_text(self, cap):
        """'everywhere', or the scope entries as text — what the CLI shows and the audit log records."""
        scope = self._grants[cap]
        if scope is None:
            return "everywhere"
        if cap == "net.http":
            return ", ".join((f"[{h}]" if ":" in h else h) + (f":{p}" if p else "") for h, p in scope)
        return ", ".join(scope)

    def grants_summary(self):
        """{capability: 'everywhere' | [scope entries]} for every granted capability (the audit log's `run` record)."""
        return {cap: ("everywhere" if sc is None else [x.strip() for x in self.scope_text(cap).split(", ")])
                for cap, sc in self._grants.items()}

    def describe(self):
        """One human-readable line per capability, for the CLI and audit log."""
        lines = []
        for cap in CAPABILITIES:
            if cap not in self._grants:
                lines.append(f"{cap}: denied")
            elif self._grants[cap] is None:
                lines.append(f"{cap}: allowed everywhere")
            else:
                lines.append(f"{cap}: allowed for {self.scope_text(cap)}")
        return lines


DENY_ALL = Policy()


class CapabilityContext:
    """One module's view of the capability system: what it declared, who
    imported it, and the (shared) host policy. The interpreter and the VM
    each hold one per module instance; imported modules get a child whose
    `parent` is the importer's context."""
    def __init__(self, policy=None, label=None, parent=None):
        self.policy = policy if policy is not None else (parent.policy if parent else DENY_ALL)
        self.label = label          # file name for messages; None = the anonymous top-level program
        self.parent = parent
        self.own = set()            # canonical names this module declared with `use`

    def declare(self, canonical_names):
        new = set(canonical_names) - self.own
        self.own.update(canonical_names)
        audit = self.policy.audit
        if new and audit is not None:       # a declaration is the program's REQUEST; the host's audit log records it
            audit.record("declare", module=self.label, capabilities=sorted(new))

    def _name(self):
        return f"'{self.label}'" if self.label else "this program"

    def _first_non_declarer(self, capability):
        node = self
        while node is not None:
            if capability not in node.own:
                return node
            node = node.parent
        return None


# ---------------------------------------------------------------------------------------------
# Errors as values: stable codes, causes, traces (Language Specification 3.1, docs/errors.md §4.8–§4.12)
# ---------------------------------------------------------------------------------------------
# Every run-time failure carries a stable CODE — what a program or a log search should rely on;
# the wording of the message may be improved at any time, the code never changes meaning.
RUNTIME_CODES = {
    "R0001": "error raised by the program (fail)",
    "R0002": "TypeError — a value of the wrong type, or a call with the wrong arguments",
    "R0003": "NameError — an undefined name, function or field",
    "R0004": "MatchError — no pattern of a match matched",
    "R0005": "division or modulo by zero",
    "R0006": "index out of range",
    "R0007": "key not found",
    "R0008": "PermissionError — a capability was not declared or not granted",
    "R0009": "stack overflow — maximum call depth exceeded",
    "R0010": "ModuleError — a module could not be found or loaded",
    "R0011": "unwrap of an Err or None",
    "R0012": "assertion failed",
    "R0013": "end of input",
    "R0014": "file or directory operation failed",
    "R0015": "network request failed",
}
_KIND_CODES = {"TypeError": "R0002", "NameError": "R0003", "MatchError": "R0004",
               "PermissionError": "R0008", "ModuleError": "R0010"}
_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{0,63}")
_TRACE_KEEP = 20   # a trace longer than 2 * _TRACE_KEEP frames shows its first and last _TRACE_KEEP


def _error_kind(exc):
    if isinstance(exc, LumenRaise):
        return "Error"
    m = re.match(r"([A-Z][A-Za-z]*Error)\b", str(exc))
    return m.group(1) if m else "Error"


def runtime_code(exc):
    """The stable code of a failure: the one the raise site gave it, else the one for its kind."""
    code = getattr(exc, "code", None)
    if code:
        return code
    return _KIND_CODES.get(_error_kind(exc), "R0001")


def format_frame(entry):
    name, file, line = entry[0], entry[1], entry[2]
    return f"{name} ({file or '<program>'}:{line})" if line else f"{name} ({file or '<program>'})"


def trace_lines(entries):
    """The call stack of a failure as text, outermost call first, innermost last. `entries` is
    innermost-first (the order they are collected in); a very deep trace (an infinite
    recursion) is shortened in the middle."""
    lines = [format_frame(e) for e in reversed(entries)]
    if len(lines) > 2 * _TRACE_KEEP:
        omitted = len(lines) - 2 * _TRACE_KEEP
        lines = lines[:_TRACE_KEEP] + [f"... {omitted} more frames ..."] + lines[-_TRACE_KEEP:]
    return lines


def _frame_entries(exc):
    return list(getattr(exc, "lumen_frames", ()))


class _TraceState(threading.local):
    """The interpreter's live call stack for ONE thread: [[function name, file, current line], ...],
    outermost first. A `spawn`ed task runs on its own thread, so it has its own."""
    def __init__(self):
        self.frames = []
        self.token = next(_VM_IDS)    # tells this thread's contribution to a failure's trace from any other's


_TLS = _TraceState()


def note_failure_here(e):
    """Interpreter: record the calls that led to failure `e` — once per thread, the first time the
    failure is seen on it, while the stack is still exactly as it was when the failure happened.
    (A task's failure re-raised by `wait` in another thread adds that thread's calls after the task's.)"""
    seen = e.__dict__.setdefault("lumen_vms", set())
    if _TLS.token in seen:
        return
    seen.add(_TLS.token)
    frames = e.__dict__.setdefault("lumen_frames", [])
    for rec in reversed(_TLS.frames):
        frames.append((rec[0], rec[1], rec[2]))


def make_error_value(exc, entries=None):
    """The Lumen value bound by `catch e`. Every error carries `message`; `kind` (the leading
    `XError` of its message, else "Error"); `code` (a stable identifier, see RUNTIME_CODES);
    `cause` (`Some(error)` when this failure was raised on account of another, else `None`);
    and `trace` (the calls that led here, outermost first, as "function (file:line)" strings).
    A PermissionDenied also carries `capability`, `operation`, `resource` and `reason`, so a
    handler can tell a denial from a failure without parsing text. `entries` is the call stack
    as the engine saw it; without it, whatever the failure collected on its way up."""
    msg = exc.message if isinstance(exc, LumenRaise) else str(exc)
    cause = getattr(exc, "cause", None)
    fields = {"message": msg, "kind": _error_kind(exc), "code": runtime_code(exc),
              "cause": EnumInstance("Option", "Some", {"0": cause}) if cause is not None
                       else EnumInstance("Option", "None", {}),
              "trace": trace_lines(entries if entries is not None else _frame_entries(exc))}
    if isinstance(exc, PermissionDenied):
        fields.update(capability=exc.capability, operation=exc.operation,
                      resource=exc.resource, reason=exc.reason)
    return LumenInstance("Error", fields)


def error_report(exc, entries=None):
    """The text printed for a failure nobody caught: the message exactly as before 3.1 on the
    first line (so nothing that reads it breaks), then its code, its trace, and its causes."""
    value = make_error_value(exc, entries)
    first = f"Uncaught error: {value.fields['message']}" if isinstance(exc, LumenRaise) else str(exc)
    lines = [first, f"  code: {value.fields['code']}"]
    def add(err, indent):
        pad = "  " * indent
        tr = err.fields["trace"]
        if tr:
            lines.append(f"{pad}trace (most recent call last):")
            lines.extend(f"{pad}  {t}" for t in tr)
    add(value, 1)
    cur, depth = value, 1
    while depth < 8:
        cause = cur.fields["cause"]
        if cause.variant != "Some":
            break
        cur = cause.fields["0"]
        lines.append(f"{'  ' * depth}caused by: {cur.fields['message']}")
        lines.append(f"{'  ' * (depth + 1)}code: {cur.fields['code']}")
        add(cur, depth + 1)
        depth += 1
    for p in getattr(exc, "lumen_pending", ()):
        lines.append(f"  while cleaning up after this earlier failure: {p.fields['message']}")
        lines.append(f"    code: {p.fields['code']}")
    return "\n".join(lines)


def render_diagnostic(e):
    """A compile-time diagnostic as text: the message exactly as `str(e)` has always printed it (so
    nothing that reads the first line breaks), then where it is — file, line, column — the source
    line with the offending code underlined, its stable code, and any notes (expected / found) and
    help the checker attached."""
    out = [str(e)]
    line = getattr(e, "line", None)
    if line:
        file = getattr(e, "lumen_file", None) or "<program>"
        col = getattr(e, "col", None)
        out.append(f"  --> {file}:{line}" + (f":{col}" if col else ""))
        src = getattr(e, "lumen_src", None)
        rows = src.split("\n") if isinstance(src, str) else []
        if 1 <= line <= len(rows):
            text = rows[line - 1].rstrip("\r")
            gutter = str(line)
            pad = " " * len(gutter)
            if len(text) > 200:
                text, col = text[:200] + " ...", (col if col and col <= 200 else None)
            out.append(f"  {pad} |")
            out.append(f"  {gutter} | {text}")
            if col:
                end = getattr(e, "end_col", None)
                width = (end - col) if end and end > col else 1
                lead = "".join("\t" if ch == "\t" else " " for ch in text[:col - 1])
                out.append(f"  {pad} | {lead}{'^' * width}")
    code = getattr(e, "code", None)
    if code:
        out.append(f"  = code: {code}")
    for note in getattr(e, "notes", None) or ():
        out.append(f"  = {note}")
    if getattr(e, "help", None):
        out.append(f"  = help: {e.help}")
    return "\n".join(out)


def failure_text(e):
    """What is printed for a failure nobody caught. A failure of the running program (it was seen by an
    engine, so it has its calls) gets its code, trace and causes; a compile-time error is rendered
    with its location and, where the checker knows them, expected / found and a help line."""
    if isinstance(e, LumenRaise) or getattr(e, "lumen_frames", None) is not None:
        return error_report(e)
    if isinstance(e, LumenError):
        return render_diagnostic(e)
    return str(e)


def _is_error_value(v):
    return isinstance(v, LumenInstance) and v.struct_name == "Error"


RESULT_BUILTIN_NAMES = frozenset({"ok_or", "unwrap", "unwrap_or", "is_ok", "is_err", "is_some", "is_none"})


def attach_pending(e, pending):
    """A `finally` block failed while it was running on behalf of the failure `pending`. `e`, the
    failure of the cleanup, is the one that continues; `pending` is not lost — it becomes `e`'s
    cause. If `e` already names a cause of its own (`fail(msg, cause)` in the finalizer) that one
    stays the cause, and `pending` is kept beside it (`lumen_pending`), which an uncaught-error
    report prints but a `catch` cannot read as a field."""
    if e is pending:
        return
    value = make_error_value(pending)
    if getattr(e, "cause", None) is None:
        e.cause = value
    else:
        e.__dict__.setdefault("lumen_pending", []).append(value)


def _check_code(code):
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        raise LumenError(f"TypeError: an error code must be a Str of letters, digits, '_', '-' or '.' "
                         f"starting with a letter (up to 64 characters), got {code!r}")
    return code


def make_fail(name, args):
    """`fail(message [, cause])` and `fail_with(code, message [, cause])` — ONE function both engines
    call, so they cannot differ. Returns the exception to raise."""
    if name == "fail":
        if len(args) > 2:
            return LumenError(f"TypeError: fail() expects a message and an optional cause, got {len(args)} arguments")
        message = args[0] if args else "error"
        cause = args[1] if len(args) == 2 else None
        code = None
    else:
        if len(args) not in (2, 3):
            return LumenError(f"TypeError: fail_with() expects a code, a message and an optional cause, got {len(args)} arguments")
        try:
            code = _check_code(args[0])
        except LumenError as e:
            return e
        message = args[1]
        cause = args[2] if len(args) == 3 else None
    if cause is not None and not _is_error_value(cause):
        return LumenError(f"TypeError: the cause of an error must be the error a `catch` bound, got {type_name_of(cause)}")
    return LumenRaise(message, code=code, cause=cause)


def type_name_of(v):
    """The name `type_of` reports, for use in messages from code that has no engine at hand."""
    if v is None: return "Nothing"
    if isinstance(v, bool): return "Bool"
    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "Str"
    if isinstance(v, list): return "List"
    if isinstance(v, dict): return "Dict"
    if isinstance(v, LumenInstance): return v.struct_name
    if isinstance(v, EnumInstance): return v.enum_name
    return "Any"


def _enum_payload(v):
    vals = list(v.fields.values())
    return vals[0] if len(vals) == 1 else None


def propagate_step(v):
    """The one definition of what `?` does to a value, shared by both engines. Returns
    (True, payload) when execution continues with `payload`, or (False, v) when the function must
    return `v` (the `Err(...)` or `None` it was applied to, unchanged) at once. Anything that is
    not a Result or an Option is a TypeError — `?` never guesses."""
    if isinstance(v, EnumInstance):
        if v.enum_name == "Result" and v.variant in ("Ok", "Err") and len(v.fields) == 1:
            return (True, _enum_payload(v)) if v.variant == "Ok" else (False, v)
        if v.enum_name == "Option":
            if v.variant == "Some" and len(v.fields) == 1:
                return True, _enum_payload(v)
            if v.variant == "None" and not v.fields:
                return False, v
    raise LumenError(f"TypeError: the '?' operator expects a Result or an Option, got {type_name_of(v)}")


def _ok(payload):
    return EnumInstance("Result", "Ok", {"0": payload})


def _is_variant(v, enum_name, *variants):
    return isinstance(v, EnumInstance) and v.enum_name == enum_name and v.variant in variants


def result_builtin(name, args, stringify):
    """`ok_or`, `unwrap`, `unwrap_or`, `is_ok`, `is_err`, `is_some`, `is_none` — the explicit,
    named ways between Option, Result and an exception. Shared by both engines."""
    want = {"ok_or": 2, "unwrap": 1, "unwrap_or": 2, "is_ok": 1, "is_err": 1, "is_some": 1, "is_none": 1}[name]
    if len(args) != want:
        raise LumenError(f"TypeError: {name}() expects {want} argument{'s' if want != 1 else ''}, got {len(args)}")
    v = args[0]
    if name == "ok_or":
        if not _is_variant(v, "Option", "Some", "None"):
            raise LumenError(f"TypeError: ok_or() expects an Option as its first argument, got {type_name_of(v)}")
        return _ok(_enum_payload(v)) if v.variant == "Some" else EnumInstance("Result", "Err", {"0": args[1]})
    if name in ("is_ok", "is_err"):
        if not _is_variant(v, "Result", "Ok", "Err"):
            raise LumenError(f"TypeError: {name}() expects a Result, got {type_name_of(v)}")
        return (v.variant == "Ok") == (name == "is_ok")
    if name in ("is_some", "is_none"):
        if not _is_variant(v, "Option", "Some", "None"):
            raise LumenError(f"TypeError: {name}() expects an Option, got {type_name_of(v)}")
        return (v.variant == "Some") == (name == "is_some")
    # unwrap / unwrap_or
    if _is_variant(v, "Result", "Ok") or _is_variant(v, "Option", "Some"):
        return _enum_payload(v)
    if name == "unwrap_or" and (_is_variant(v, "Result", "Err") or _is_variant(v, "Option", "None")):
        return args[1]
    if _is_variant(v, "Result", "Err"):
        payload = _enum_payload(v)
        raise LumenRaise(f"called unwrap on an Err value: {stringify(payload)}", code="R0011",
                         cause=payload if _is_error_value(payload) else None)
    if _is_variant(v, "Option", "None"):
        raise LumenRaise("called unwrap on a None value", code="R0011")
    raise LumenError(f"TypeError: {name}() expects a Result or an Option, got {type_name_of(v)}")


def _module_load_error(path, e):
    """`ModuleError: error loading 'x': <cause>` — keeping a PermissionDenied's
    structured fields, so a denial that happens while a module's top level runs
    is still recognisably a denial to a `catch`."""
    msg = f"ModuleError: error loading '{path}': {e}"
    if isinstance(e, PermissionDenied):
        return PermissionDenied(msg, e.capability, e.operation, e.resource, e.reason)
    return LumenError(msg)


def _locate_module(base_dir, path, line):
    """Where `use "<path>" as x` finds its file — ONE function for both backends.
    Only relative paths to files named `*.lum` can be imported, and any other name gets
    exactly the same "cannot find module" error as a missing file. Importing is loading *code*,
    and it is not gated by `fs.read`; without this rule `use "secrets.txt" as s`
    would be a file-reading primitive for a program with no capabilities, because the
    parse error for the file's contents (for instance `NameError: 'TOP' is not defined`)
    echoes part of what it holds, and "cannot find" vs "error loading" would reveal
    which paths exist. Searched: next to the importing file, then in lumen_packages/."""
    import os
    def missing():
        return LumenError(f"ModuleError: cannot find module '{path}' at line {line} "
                          f"(looked next to this file and in lumen_packages/)")
    if not path.endswith(".lum") or "\x00" in path or os.path.isabs(path):
        raise missing()   # (docs/modules.md 5.4: there is no notion of an absolute import path)
    full = os.path.join(base_dir, path)
    if os.path.isfile(full):
        return full
    pkg_path = os.path.join(base_dir, "lumen_packages", path)
    if os.path.isfile(pkg_path):
        return pkg_path
    raise missing()


def _redact_url(url):
    """scheme://host[:port] only — never userinfo, path, or query."""
    from urllib.parse import urlsplit
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return f"{p.scheme}://{host}" + (f":{p.port}" if p.port else "")
    except ValueError:
        return "<invalid url>"


def _deny(capability, op, resource, reason, message):
    raise PermissionDenied(f"PermissionError: {message}", capability, op, resource, reason)


def _authorize_declared_and_granted(ctx, capability, op, resource):
    """Checks 1-3 of the module docstring above. Returns nothing; raises."""
    blocker = ctx._first_non_declarer(capability)
    if blocker is ctx:
        _deny(capability, op, resource, "not-declared",
              f"{op}() needs the '{capability}' capability, which {ctx._name()} has not declared "
              f"— add 'use {capability}' at the top of {ctx.label or 'the file'}")
    if blocker is not None:
        _deny(capability, op, resource, "not-declared-by-importer",
              f"{op}() needs the '{capability}' capability: {ctx._name()} declares it, but "
              f"{blocker._name()}, which imports it (directly or indirectly), does not — a library "
              f"cannot hold authority its importer lacks")
    if not ctx.policy.granted(capability):
        _deny(capability, op, resource, "not-granted",
              f"{op}() requires the '{capability}' capability, which was not granted for this run "
              f"— grant it with {_GRANT_FLAG_HINT[capability]} (declaring it with 'use' does not grant it)")


def _check_arguments(op, args):
    cap, arity = OPERATIONS[op]
    if len(args) != arity:
        raise LumenError(f"TypeError: {op}() expects {arity} argument{'s' if arity != 1 else ''}, got {len(args)}")
    for i, a in enumerate(args):
        if not isinstance(a, str):
            what = "a Str path" if (i == 0 and cap.startswith("fs.")) else \
                   "a Str URL" if i == 0 else "a Str"
            raise LumenError(f"TypeError: {op}() expects {what} as argument {i + 1}, got {_lumen_type_name(a)}")
        if i == 0 and "\x00" in a:   # only the path / URL: file *contents* may legitimately hold NUL
            raise LumenError(f"TypeError: {op}() argument 1 must not contain a NUL character")


def _lumen_type_name(v):
    if v is None: return "Nothing"
    if isinstance(v, bool): return "Bool"
    if isinstance(v, int): return "Int"
    if isinstance(v, float): return "Float"
    if isinstance(v, str): return "Str"
    if isinstance(v, list): return "List"
    if isinstance(v, dict): return "Dict"
    return type(v).__name__


def _fs_target(ctx, capability, op, path, follow_final=True):
    """The concrete path an fs operation must act on. Unrestricted grant: the
    path exactly as the program wrote it. Scoped grant: the real path (every
    symlink and `..` resolved) — which is then checked against the scope AND
    is the very string the operation uses, so what was authorised is exactly
    what is touched. `follow_final=False` (delete) resolves the containing
    directory but keeps the final name, because removing a symlink removes the
    link, not its target."""
    policy = ctx.policy
    if policy.scope(capability) is None:
        return path
    def outside():
        _deny(capability, op, path, "out-of-scope",
              f"{op}('{path}') is outside the paths granted for '{capability}'")
    try:
        if follow_final:
            target = os.path.realpath(path)
        else:
            base = os.path.basename(path)
            if base in ("", ".", ".."):
                target = os.path.realpath(path)
            else:
                target = os.path.join(os.path.realpath(os.path.dirname(path) or "."), base)
    except (OSError, ValueError):
        outside()
    if not policy.fs_allows(capability, target):
        outside()
    return target


def _changed_denial(capability, op, path):
    _deny(capability, op, path, "out-of-scope",
          f"{op}('{path}') is outside the paths granted for '{capability}' (the path changed while it was being opened)")


def _scoped(ctx, capability):
    """True when this fs capability is restricted to a scope AND the platform can enforce it descriptor-relatively."""
    return _HAVE_DIRFD and ctx.policy.scope(capability) is not None


def _is_symlink_at(dir_fd, name):
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


@contextlib.contextmanager
def _beneath(ctx, capability, op, path, target, whole=False):
    """Descriptor-relative access to `target`, which `_fs_target` already authorised. Yields (dir_fd, leaf):
    the directory containing the final component, and its name (leaf is None when the target IS that directory,
    or, with whole=True, the directory is the target and dir_fd is it).

    The walk starts at the PINNED scope directory and opens every component with O_NOFOLLOW|O_DIRECTORY, so it
    can only ever descend, and never through a symlink. `target` came from realpath, so no link should appear on
    the way; if a directory has been swapped for a link (or anything else changed so the walk cannot proceed as
    resolved) in the window since the check, the walk fails and the operation is refused. Nothing is looked up by
    path string after the check, which is what closes the check-then-use race."""
    opened = []
    try:
        anchor = ctx.policy._anchor(capability, target)
        if anchor is None:
            _deny(capability, op, path, "out-of-scope",
                  f"{op}('{path}') is outside the paths granted for '{capability}'")
        fd, parts, owned = anchor
        if owned is not None:
            opened.append(owned)
        for name in (parts if whole else parts[:-1]):
            try:
                fd = os.open(name, _O_DIR | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as e:
                # Refusing to follow a link is ELOOP on some systems and ENOTDIR on others (Linux reports ENOTDIR for
                # O_NOFOLLOW|O_DIRECTORY on a symlink), so ENOTDIR is told apart from a genuine "not a directory"
                # by looking at what the name is now.
                if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and _is_symlink_at(fd, name)):
                    _changed_denial(capability, op, path)
                raise
            opened.append(fd)
        yield fd, (None if whole or not parts else parts[-1])
    finally:
        for f in reversed(opened):
            try:
                os.close(f)
            except OSError:
                pass


def _open_checked(ctx, capability, op, path, target, mode):
    """open() for a scoped grant. On POSIX the file is opened relative to the pinned scope directory without
    following any symlink (`_beneath`), so what is opened is what was authorised even if another process is
    rearranging directories; writes truncate only after the opened file has been verified. On Linux the opened
    descriptor's real path is re-checked as well (defence in depth)."""
    if ctx.policy.scope(capability) is None:
        return open(target, mode)
    flags = {"r": os.O_RDONLY, "w": os.O_WRONLY | os.O_CREAT, "a": os.O_WRONLY | os.O_CREAT | os.O_APPEND}[mode]
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    if _HAVE_DIRFD:
        with _beneath(ctx, capability, op, path, target) as (pfd, leaf):
            if leaf is None:
                raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))
            try:
                fd = os.open(leaf, flags, 0o666, dir_fd=pfd)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    _changed_denial(capability, op, path)
                raise
    else:
        fd = os.open(target, flags, 0o666)
    try:
        if sys.platform.startswith("linux"):
            actual = os.path.realpath(f"/proc/self/fd/{fd}")
            if not ctx.policy.fs_allows(capability, actual):
                _deny(capability, op, path, "out-of-scope",
                      f"{op}('{path}') is outside the paths granted for '{capability}'")
        if mode == "w":
            os.ftruncate(fd, 0)
        return os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise


def _fs_mode(ctx, capability, op, path, target):
    """st_mode of the final component (not following a final symlink), or None if it cannot be examined.
    Unscoped grants stat the path as written; scoped ones stat relative to the pinned directory."""
    if not _scoped(ctx, capability):
        try:
            return os.stat(target).st_mode
        except (OSError, ValueError):
            return None
    try:
        with _beneath(ctx, capability, op, path, target) as (pfd, leaf):
            if leaf is None:
                return os.fstat(pfd).st_mode
            return os.stat(leaf, dir_fd=pfd, follow_symlinks=False).st_mode
    except OSError:        # no such directory on the way, or the name is gone: it does not exist (a PermissionError passes through)
        return None


def _fs_listdir(ctx, capability, op, path, target):
    if not _scoped(ctx, capability):
        return sorted(os.listdir(target))
    with _beneath(ctx, capability, op, path, target, whole=True) as (dfd, _):
        try:
            lfd = os.open(".", _O_DIR, dir_fd=dfd)      # a private descriptor, so no shared offset is touched
        except OSError as e:
            if e.errno == errno.ELOOP:
                _changed_denial(capability, op, path)
            raise
        try:
            return sorted(os.listdir(lfd))
        finally:
            os.close(lfd)


def _fs_unlink(ctx, capability, op, path, target):
    if not _scoped(ctx, capability):
        os.remove(target)
        return
    with _beneath(ctx, capability, op, path, target) as (pfd, leaf):
        if leaf is None:
            raise OSError(errno.EISDIR, os.strerror(errno.EISDIR))
        try:
            os.unlink(leaf, dir_fd=pfd)
        except OSError as e:
            if e.errno == errno.ELOOP:
                _changed_denial(capability, op, path)
            raise


def capability_call(ctx, op, args):
    """THE resource-access layer: every gated builtin — in the interpreter and
    in the VM — comes through here. Argument validation, the four checks of
    the section header, then the OS operation. Nothing else in this file
    touches the file system or the network on a Lumen program's behalf.

    When the host supplied an audit log (`ctx.policy.audit`), the decision is
    recorded here and nowhere else: `allow` once the four checks have passed
    (before the operation, so an access that cannot be recorded does not
    happen), `deny` for every PermissionDenied however late it arises."""
    _check_arguments(op, args)
    capability = OPERATIONS[op][0]
    resource = _redact_url(args[0]) if op == "http_get" else args[0]
    audit = ctx.policy.audit
    try:
        _authorize_declared_and_granted(ctx, capability, op, resource)
        if op == "http_get":
            return _http_get(ctx, args[0])
        target = _fs_target(ctx, capability, op, args[0], follow_final=(op != "delete_file"))
        if audit is not None:
            audit.access("allow", capability, op, resource, ctx.label)
        return _fs_perform(ctx, capability, op, args, target)
    except PermissionDenied as e:
        if audit is not None:
            audit.access("deny", e.capability, e.operation, e.resource, ctx.label, reason=e.reason)
        raise


def _fs_perform(ctx, capability, op, args, target):
    path = args[0]
    if op == "read_file":
        try:
            with _open_checked(ctx, capability, op, path, target, "r") as f:
                return f.read()
        except OSError as e:
            raise LumenRaise(f"could not read '{path}': {e.strerror}", code="R0014")
    if op == "write_file":
        try:
            with _open_checked(ctx, capability, op, path, target, "w") as f:
                f.write(args[1])
            return None
        except OSError as e:
            raise LumenRaise(f"could not write '{path}': {e.strerror}", code="R0014")
    if op == "append_file":
        try:
            with _open_checked(ctx, capability, op, path, target, "a") as f:
                f.write(args[1])
            return None
        except OSError as e:
            raise LumenRaise(f"could not append to '{path}': {e.strerror}", code="R0014")
    if op == "file_exists":
        return _fs_mode(ctx, capability, op, path, target) is not None
    if op == "is_dir":
        mode = _fs_mode(ctx, capability, op, path, target)
        return mode is not None and stat.S_ISDIR(mode)
    if op == "list_dir":
        try:
            return _fs_listdir(ctx, capability, op, path, target)
        except OSError as e:
            raise LumenRaise(f"could not list directory '{path}': {e.strerror}", code="R0014")
    if op == "delete_file":
        try:
            _fs_unlink(ctx, capability, op, path, target)
            return None
        except OSError as e:
            raise LumenRaise(f"could not delete '{path}': {e.strerror}", code="R0014")
    raise LumenError(f"internal error: gated operation '{op}' has no implementation")  # fail closed


def _authorize_url(ctx, op, url, redirect=False):
    """http/https only, and the host:port inside the granted scope. Used for
    the initial URL and again for every redirect hop — a redirect must not
    widen the destinations a program can reach."""
    from urllib.parse import urlsplit
    shown = _redact_url(url)
    try:
        parts = urlsplit(url)
        scheme, host, port = parts.scheme.lower(), parts.hostname, parts.port
    except ValueError:
        raise LumenRaise("http_get failed: invalid URL", code="R0015")
    if scheme not in ("http", "https"):
        # file://, ftp://, data:, ... — urllib would happily read local files
        # for these, which is filesystem access that no net grant covers.
        raise LumenRaise(f"http_get failed: only http:// and https:// URLs are supported (got '{scheme or 'no'}' scheme)", code="R0015")
    if not host:
        raise LumenRaise("http_get failed: invalid URL (no host)", code="R0015")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not ctx.policy.net_allows(host.lower(), port):
        where = "redirected to" if redirect else "requested"
        _deny("net.http", op, shown, "redirect-out-of-scope" if redirect else "out-of-scope",
              f"{op}() {where} '{shown}', which is not among the hosts granted for 'net.http'")


def _http_get(ctx, url):
    import urllib.request
    _authorize_url(ctx, "http_get", url)
    audit = ctx.policy.audit
    if audit is not None:
        audit.access("allow", "net.http", "http_get", _redact_url(url), ctx.label)

    class _Scoped(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _authorize_url(ctx, "http_get", newurl, redirect=True)
            if audit is not None:
                audit.access("allow", "net.http", "http_get", _redact_url(newurl), ctx.label, redirect=True)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_Scoped)
    try:
        with opener.open(url, timeout=5) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (PermissionDenied, LumenRaise):
        raise
    except Exception as e:
        raise LumenRaise(f"http_get failed: {e}", code="R0015")


GRANT_FLAGS = {
    "--allow-read": ("fs.read",), "--allow-write": ("fs.write",), "--allow-list": ("fs.list",),
    "--allow-fs": ("fs.read", "fs.write", "fs.list"), "--allow-net": ("net.http",),
}


def parse_grant_flags(args):
    """Splits the permission flags out of a command line. Returns
    (Policy, remaining_args). A bare flag (`--allow-read`) grants the
    capability everywhere; `--allow-read=a,b` grants it for those paths only;
    repeated flags accumulate, and an unrestricted grant absorbs scoped ones.
    There is deliberately no `--allow-all`, and an unrecognised `--allow-*`
    flag is an error, not a silently ignored typo."""
    grants, rest = {}, []
    audit_path = audit_level = None
    for a in args:
        flag, eq, value = a.partition("=")
        if flag in ("--audit-log", "--audit-level"):
            if not eq or not value:
                raise ValueError(f"{flag} needs a value: {flag}=" + ("FILE" if flag == "--audit-log" else "all|denied"))
            if flag == "--audit-log":
                if audit_path is not None:
                    raise ValueError("--audit-log given more than once")
                audit_path = value
            else:
                if audit_level is not None:
                    raise ValueError("--audit-level given more than once")
                audit_level = value
        elif flag in GRANT_FLAGS:
            if eq and not value:
                raise ValueError(f"{flag}= needs at least one value (or drop the '=' to grant everywhere)")
            scopes = value.split(",") if eq else None
            if scopes is not None and any(s == "" for s in scopes):
                raise ValueError(f"{flag}: empty entry in '{value}'")
            for cap in GRANT_FLAGS[flag]:
                if scopes is None:
                    grants[cap] = None
                elif grants.get(cap, []) is not None:
                    grants.setdefault(cap, []).extend(scopes)
        elif a.startswith("--allow") or a.startswith("--audit"):
            raise ValueError(f"unknown permission flag '{flag}' — grant capabilities individually: "
                             f"{', '.join(sorted(GRANT_FLAGS))}; audit options: --audit-log=FILE, --audit-level=all|denied")
        else:
            rest.append(a)
    if audit_level is not None and audit_path is None:
        raise ValueError("--audit-level needs --audit-log=FILE")
    audit = AuditLog(audit_path, audit_level or "all") if audit_path is not None else None
    try:
        return Policy(grants, audit=audit), rest
    except ValueError:
        if audit is not None:
            audit.close()
        raise


# ---------------------------------------------------------------------
# Consent prompt (SEC-005) — a USER INTERFACE over the same Policy, not a security boundary.
# `lumen run --ask FILE` shows what the program declares with `use`, lets the person decide, and then runs with
# the Policy that was shown. It can only ADD capabilities the program itself declared and that no flag granted; it
# never widens a flag's grant; it never runs without a terminal on both input and output; and every default
# answer is a denial. Enforcement is unchanged: the runtime checks the Policy, whatever was typed here.
# ---------------------------------------------------------------------
_CAP_GROUPS = (("Filesystem", ("fs.read", "fs.write", "fs.list")), ("Network", ("net.http",)))
_MAX_BAD_ANSWERS = 3


def _printable(text):
    """Text for a terminal: control characters (which could carry escape sequences) are shown, not sent."""
    return "".join(c if c.isprintable() else (f"\\x{ord(c):02x}" if ord(c) < 256 else f"\\u{ord(c):04x}") for c in text)


def declared_capabilities(path):
    """The canonical capability names the entry program declares: its top-level `use` lines. Every module a
    program imports needs its own declaration AND all of its importers' (the entry program is the root of every
    importer chain), so this set is the ceiling for the whole run."""
    with open(path) as f:
        src = f.read()
    names = set()
    for stmt in Parser(lex(src)).parse_program():
        if isinstance(stmt, Use):
            names.update(expand_capability_decl(stmt.name, stmt.line))
    return names


def permission_lines(policy, declared):
    """The EFFECTIVE permissions of a run, one line per capability: granted by the policy AND declared by the
    program. A grant for something the program does not declare is shown as what it is — without effect."""
    lines = []
    for cap in CAPABILITIES:
        if cap in declared and policy.granted(cap):
            lines.append(f"  {cap:<9} allowed: {_printable(policy.scope_text(cap))}")
        elif policy.granted(cap):
            lines.append(f"  {cap:<9} denied (granted by a flag, but the program does not declare it)")
        else:
            lines.append(f"  {cap:<9} denied")
    return lines


def request_consent(policy, declared, program, instream, outstream):
    """Shows the permissions `program` declares, asks about each one no flag has already granted, shows the
    effective permissions and asks for a final yes. Returns the Policy to run with, or None if the person
    declined or cancelled (nothing is then run). Returns `policy` untouched, silently, when there is nothing new
    to ask about. Every default (Enter, end of input, anything unrecognised) is a denial."""
    undecided = [c for c in CAPABILITIES if c in declared and not policy.granted(c)]
    if not undecided:
        return policy
    audit = policy.audit
    out = lambda text="": print(text, file=outstream, flush=True)

    def ask(prompt):
        outstream.write(prompt)
        outstream.flush()
        line = instream.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n").strip()

    if audit is not None:
        audit.record("request", program=program, declared=sorted(declared),
                     granted_by_flags=[c for c in CAPABILITIES if c in declared and policy.granted(c)],
                     undecided=undecided)
    out(f"{_printable(program)} requests these permissions (declared with `use`; a declaration grants nothing):")
    for group, caps in _CAP_GROUPS:
        shown = [c for c in caps if c in declared]
        if shown:
            out(f"\n  {group}")
            for c in shown:
                state = f"granted by flag: {_printable(policy.scope_text(c))}" if policy.granted(c) else "NOT granted"
                out(f"    {c:<9} {CAPABILITIES[c]}  [{state}]")
    out("\nNothing is allowed unless you allow it here or with --allow-* flags. Enter denies.")
    try:
        for cap in undecided:
            what = "HOST or HOST:PORT" if cap == "net.http" else "paths"
            bad = 0
            while True:
                answer = ask(f"\nAllow {cap}? Enter {what} (comma-separated), 'everywhere', or press Enter to deny: ")
                if answer == "" or answer.lower() in ("n", "no"):
                    decision, scope = "denied", None
                    break
                if answer.lower() in ("y", "yes"):
                    problem = f"'{answer}' does not say what to allow: enter {what}, or 'everywhere'"
                else:
                    entries = None if answer == "everywhere" else [x.strip() for x in answer.split(",")]
                    try:
                        if entries is not None and any(e == "" for e in entries):
                            raise ValueError("empty entry in the list")
                        candidate = policy.extended({cap: entries})
                        decision, scope = "granted", candidate.scope_text(cap)
                        break
                    except ValueError as e:      # a bad scope, or one the audit log refuses: ask again, never crash
                        problem = str(e)
                bad += 1
                out(f"  {_printable(problem)}")
                if bad >= _MAX_BAD_ANSWERS:
                    out(f"  too many invalid answers: {cap} stays denied")
                    decision, scope = "denied", None
                    break
            if decision == "granted":
                policy = candidate
            if audit is not None:
                audit.record("consent", capability=cap, decision=decision, scope=scope)
        out("\nEffective permissions for this run:")
        for line in permission_lines(policy, declared):
            out(line)
        answer = ask("\nRun with these permissions? [y/N] ")
        confirmed = answer.lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt, UnicodeDecodeError):   # a closed or garbled terminal cannot grant
        out()
        confirmed = False
    if audit is not None:
        audit.record("confirm", decision="run" if confirmed else "cancelled")
    return policy if confirmed else None


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

class IRAssign(IRNode):
    """Lowered form of a bare `name = expr` (no `let`) — see `Assign`'s
    docstring. Compiles to a distinct `ASSIGN` opcode that searches the VM's
    scope stack for the nearest scope already containing `name` and mutates
    it there, instead of `STORE` (from `IRLet`), which always writes the
    innermost scope."""
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

class IRWhile(IRNode):
    """Lowered form of `while`."""
    def __init__(self, cond, body, line=0):
        self.cond, self.body, self.line = cond, body, line

class IRTryCatch(IRNode):
    def __init__(self, body, err_name, handler, line=0):
        self.body, self.err_name, self.handler, self.line = body, err_name, handler, line

class IRTryFinally(IRNode):
    """Lowered form of `try ... finally ...` (a `catch`, when there is one, is an IRTryCatch inside `body`)."""
    def __init__(self, body, finalizer, line=0):
        self.body, self.finalizer, self.line = body, finalizer, line

class IRPropagate(IRNode):
    """Lowered form of `expr?`. `.type` is the type of the unwrapped value."""
    def __init__(self, expr, type_, line=0):
        self.expr, self.type, self.line = expr, type_, line

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
        self.tc = TypeChecker(lenient=True)
        self.module_globals = module_globals  # set[str] from Resolver — for IRLoad kind
        self.enum_variant_info = {}   # variant_name -> (enum_name, [field_names]) — flat, whole-program
        self.unit_variant_names = set()  # variant names with zero fields

    def build_program(self, stmts):
        self.tc.register_declarations(stmts)
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
            elif isinstance(s, While):
                self._prescan_enums(s.body)
            elif isinstance(s, Try):
                self._prescan_enums(s.body); self._prescan_enums(s.handler)
            elif isinstance(s, TryFinally):
                self._prescan_enums(s.body); self._prescan_enums(s.finalizer)
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
        ir = self._lower_stmt_inner(node, env, in_function)
        if isinstance(ir, IRNode) and not ir.line:
            ir.line = getattr(node, "line", 0) or 0     # every statement knows its source line (stack traces)
        return ir

    def _lower_stmt_inner(self, node, env, in_function):
        if isinstance(node, Let):
            t = self.tc.infer(node.expr, env)
            expr_ir = self._lower_expr(node.expr, env, in_function)
            env[node.name] = node.type_ann or t
            return IRLet(node.name, node.type_ann or t, expr_ir, node.line)
        if isinstance(node, Assign):
            expr_ir = self._lower_expr(node.expr, env, in_function)
            # `env` already holds this name's type from its original `let`
            # (Resolver already proved the name exists, except the Tier-3
            # try-body-deferred case, where it's simply not in `env` here —
            # "Any" is the honest answer for a name this pass can't see).
            return IRAssign(node.name, env.get(node.name, "Any"), expr_ir, node.line)
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
        if isinstance(node, While):
            body = [self._lower_stmt(s, dict(env), in_function) for s in node.body]
            return IRWhile(self._lower_expr(node.cond, env, in_function), body)
        if isinstance(node, Try):
            body_env = dict(env)
            body = [self._lower_stmt(s, body_env, in_function) for s in node.body]
            handler_env = dict(env); handler_env[node.err_name] = "Any"
            handler = [self._lower_stmt(s, handler_env, in_function) for s in node.handler]
            return IRTryCatch(body, node.err_name, handler)
        if isinstance(node, TryFinally):
            body = [self._lower_stmt(s, dict(env), in_function) for s in node.body]
            finalizer = [self._lower_stmt(s, dict(env), in_function) for s in node.finalizer]
            return IRTryFinally(body, finalizer)
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
        if isinstance(node, Propagate):
            return IRPropagate(self._lower_expr(node.expr, env, in_function), t, node.line)
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
            self._check_expr(node.expr, errors, in_function)
        elif isinstance(node, IRAssign):
            self._check_expr(node.expr, errors, in_function)
        elif isinstance(node, IRPrint):
            self._check_expr(node.expr, errors, in_function)
        elif isinstance(node, IRExprStmt):
            self._check_expr(node.expr, errors, in_function)
        elif isinstance(node, IRReturn):
            self._check_expr(node.expr, errors, in_function)
        elif isinstance(node, IRBranch):
            self._check_expr(node.cond, errors, in_function)
            for s in node.then_body + node.else_body:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRLoop):
            self._check_expr(node.iterable, errors, in_function)
            for s in node.body:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRWhile):
            self._check_expr(node.cond, errors, in_function)
            for s in node.body:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRTryCatch):
            for s in node.body + node.handler:
                self._check_stmt(s, errors, in_function)
        elif isinstance(node, IRTryFinally):
            for s in node.body:
                self._check_stmt(s, errors, in_function)
            for s in node.finalizer:
                self._check_stmt(s, errors, in_function)
                if isinstance(s, IRReturn):
                    errors.append("IRReturn appears inside an IRTryFinally finalizer")
        elif isinstance(node, IRFunctionDef):
            for s in node.body:
                self._check_stmt(s, errors, in_function=True)
        elif isinstance(node, IRTestDef):
            for s in node.body:
                self._check_stmt(s, errors, in_function=True)
        elif isinstance(node, IRMatch):
            self._check_expr(node.expr, errors, in_function)
            for arm in node.arms:
                for s in arm.body:
                    self._check_stmt(s, errors, in_function)
        elif isinstance(node, (IRFieldSet, IRIndexSet)):
            self._check_expr(node.expr, errors, in_function)

    def _check_expr(self, node, errors, in_function=True):
        if not isinstance(node, IRNode):
            errors.append(f"non-IR value {node!r} found where an IR expression was expected")
            return
        if not hasattr(node, "type") or node.type is None:
            errors.append(f"{type(node).__name__} is missing a `.type` annotation (typed IR invariant)")
        if isinstance(node, IRPropagate) and not in_function:
            errors.append("IRPropagate appears outside any IRFunctionDef")
        for child in self._children(node):
            self._check_expr(child, errors, in_function)

    @staticmethod
    def _children(node):
        if isinstance(node, (IRBinOp, IRBoolOp)): return [node.left, node.right]
        if isinstance(node, (IRNot, IRPropagate)): return [node.expr]
        if isinstance(node, IRListLit): return list(node.items)
        if isinstance(node, IRDictLit): return [x for kv in node.pairs for x in kv]
        if isinstance(node, IRStructNew): return list(node.fields.values())
        if isinstance(node, IRIndex): return [node.obj, node.key]
        if isinstance(node, IRFieldGet): return [node.obj]
        if isinstance(node, IRCall): return list(node.args)
        if isinstance(node, IRMethodCall): return [node.obj] + list(node.args)
        if isinstance(node, IRSpawn): return [node.call]
        return []


class IROptimizer:
    """Optional, opt-in IR-to-IR optimization passes (Phase 4: `-O0`/`-O1`/
    `-O2`). Runs after IRValidator (so it only ever transforms IR already
    proven structurally sound) and before Compiler — today only the bytecode
    VM backend consumes IR at all (the Interpreter walks the original AST
    directly), so `-O1`/`-O2` only affect `--vm` runs; `-O0` is a pure
    identity pass and is the default, so none of the suite's pre-existing
    behavior changes unless a flag asks for it.

    The central safety rule for every fold in this class: a fold is only
    applied when evaluating it via the *exact same* `Interpreter.apply_op`/
    `.truthy` used at runtime does NOT raise. If it would raise, the original
    expression is left completely untouched, so the error still surfaces at
    its original runtime moment — folding a would-be error forward to compile
    time would change *when* (or whether, inside a branch that never
    executes) a program fails, which is an observable behavior change, not a
    safe optimization. This is why the optimizer reuses the interpreter's own
    operator semantics instead of re-implementing arithmetic: two independent
    implementations of "what does 1/0 do" is exactly the kind of duplication
    that has caused real bugs elsewhere in this codebase (see
    BUILTIN_FUNCTION_NAMES's docstring).

    -O1: constant folding of `IRBinOp`/`IRNot` over literal operands, plus
         dead-code elimination of statements that can never run (anything
         after an unconditional `return` in the same straight-line body).
    -O2: everything in -O1, plus:
         - short-circuit folding of `and`/`or` when the left operand is a
           literal — drops the right operand (and any of its side effects)
           entirely, exactly matching what the VM's own short-circuit
           bytecode (DUP/JUMP_IF_TRUE/JUMP_IF_FALSE) already does at
           runtime, just decided at compile time instead.
         - dead-branch elimination: an `if` whose condition folds to a
           literal is replaced by just its live branch's statements,
           spliced directly into the surrounding statement list.

    Correctness net: tests/test_optimizer.py asserts that running the same
    program through the VM at -O0, -O1, and -O2 produces byte-identical
    stdout and exit code — the same discipline test_equivalence.py applies
    to interpreter<->VM agreement, applied here to optimized<->unoptimized
    agreement.
    """
    def __init__(self, level):
        self.level = level
        self._interp = Interpreter()  # reused ONLY for apply_op/truthy — never .run()

    def optimize(self, program: "IRProgram") -> "IRProgram":
        if self.level <= 0:
            return program
        # IRFunctionDef/IRTestDef objects in `functions`/`tests` are the SAME
        # instances appended into `top_level` by IRBuilder, so mutating a
        # node's `.body` in place here (never replacing the node itself)
        # keeps every one of IRProgram's lists consistent automatically.
        program.top_level = self._opt_stmts(program.top_level)
        return program

    def _opt_stmts(self, stmts):
        out = []
        for s in stmts:
            piece = self._opt_stmt(s)
            if piece is None:
                continue
            pieces = piece if isinstance(piece, list) else [piece]
            out.extend(pieces)
            if pieces and isinstance(pieces[-1], IRReturn):
                break  # dead-code elimination: nothing after this can ever run
        return out

    def _opt_stmt(self, node):
        if isinstance(node, (IRLet, IRAssign, IRPrint, IRExprStmt, IRReturn)):
            node.expr = self._opt_expr(node.expr)
            return node
        if isinstance(node, IRFieldSet):
            node.obj = self._opt_expr(node.obj)
            node.expr = self._opt_expr(node.expr)
            return node
        if isinstance(node, IRIndexSet):
            node.obj = self._opt_expr(node.obj)
            node.key = self._opt_expr(node.key)
            node.expr = self._opt_expr(node.expr)
            return node
        if isinstance(node, IRBranch):
            node.cond = self._opt_expr(node.cond)
            node.then_body = self._opt_stmts(node.then_body)
            node.else_body = self._opt_stmts(node.else_body)
            if self.level >= 2 and isinstance(node.cond, IRConst):
                return node.then_body if self._interp.truthy(node.cond.value) else node.else_body
            return node
        if isinstance(node, IRLoop):
            node.iterable = self._opt_expr(node.iterable)
            node.body = self._opt_stmts(node.body)
            return node
        if isinstance(node, IRWhile):
            node.cond = self._opt_expr(node.cond)
            node.body = self._opt_stmts(node.body)
            # Safe direction only: a condition that folds to a constant
            # False means the loop body never runs at all, so the whole
            # node is dead code, exactly like IRBranch's dead-branch rule
            # above. A condition folding to constant True is deliberately
            # NOT specially handled here — unlike `if`, a `while true:`
            # loop has no `else` to discard, and this language has no
            # `break`, so folding it to "runs forever" would need a
            # different representation than "a list of statements" to be
            # correct; leaving the node as an ordinary (unfolded) IRWhile
            # is always correct, just not maximally optimized.
            if self.level >= 2 and isinstance(node.cond, IRConst) and not self._interp.truthy(node.cond.value):
                return []
            return node
        if isinstance(node, IRTryCatch):
            node.body = self._opt_stmts(node.body)
            node.handler = self._opt_stmts(node.handler)
            return node
        if isinstance(node, IRTryFinally):
            node.body = self._opt_stmts(node.body)
            node.finalizer = self._opt_stmts(node.finalizer)
            return node
        if isinstance(node, IRMatch):
            node.expr = self._opt_expr(node.expr)
            for arm in node.arms:
                arm.body = self._opt_stmts(arm.body)
            return node
        if isinstance(node, (IRFunctionDef, IRTestDef)):
            node.body = self._opt_stmts(node.body)
            return node
        return node  # IRStructDef/IREnumDef/IRUseCapability/IRUseModule: nothing to fold

    def _opt_expr(self, node):
        if isinstance(node, IRBinOp):
            node.left = self._opt_expr(node.left)
            node.right = self._opt_expr(node.right)
            if isinstance(node.left, IRConst) and isinstance(node.right, IRConst):
                try:
                    value = self._interp.apply_op(node.op, node.left.value, node.right.value)
                except (LumenError, LumenRaise):
                    return node  # would raise at runtime too — preserve original timing
                return IRConst(value, node.type, node.line)
            return node
        if isinstance(node, IRNot):
            node.expr = self._opt_expr(node.expr)
            if isinstance(node.expr, IRConst):
                return IRConst(not self._interp.truthy(node.expr.value), node.type, node.line)
            return node
        if isinstance(node, IRBoolOp):
            node.left = self._opt_expr(node.left)
            node.right = self._opt_expr(node.right)
            if self.level >= 2 and isinstance(node.left, IRConst):
                left_truthy = self._interp.truthy(node.left.value)
                if (node.op == "or" and left_truthy) or (node.op == "and" and not left_truthy):
                    return node.left  # short-circuits; right is never evaluated at runtime either
            return node
        if isinstance(node, IRListLit):
            node.items = [self._opt_expr(i) for i in node.items]
            return node
        if isinstance(node, IRDictLit):
            node.pairs = [(self._opt_expr(k), self._opt_expr(v)) for k, v in node.pairs]
            return node
        if isinstance(node, IRStructNew):
            node.fields = {k: self._opt_expr(v) for k, v in node.fields.items()}
            return node
        if isinstance(node, IRIndex):
            node.obj = self._opt_expr(node.obj)
            node.key = self._opt_expr(node.key)
            return node
        if isinstance(node, IRFieldGet):
            node.obj = self._opt_expr(node.obj)
            return node
        if isinstance(node, IRCall):
            node.args = [self._opt_expr(a) for a in node.args]
            return node
        if isinstance(node, IRMethodCall):
            node.obj = self._opt_expr(node.obj)
            node.args = [self._opt_expr(a) for a in node.args]
            return node
        if isinstance(node, IRSpawn):
            node.call = self._opt_expr(node.call)
            return node
        if isinstance(node, IRPropagate):
            node.expr = self._opt_expr(node.expr)
            return node
        return node  # IRConst/IRLoad/IRUnitVariant: nothing to fold


# ---------- Bytecode VM (experimental second backend) ----------
# Demonstrates the architecture can evolve past tree-walking without
# touching the lexer/parser. Covers the core subset: let, print, if/else,
# for-in over lists, arithmetic/boolean expressions. Structs, enums, match,
# try/catch, modules, and capabilities are not yet compiled by this backend;
# the compiler raises a clear error naming the unsupported construct.

class Instr:
    """One bytecode instruction. `line` is the source line of the statement it was compiled
    from — what a stack trace reports for the frame that is executing it."""
    __slots__ = ("op", "arg", "line")
    def __init__(self, op, arg=None, line=0): self.op, self.arg, self.line = op, arg, line
    def __repr__(self): return f"{self.op} {self.arg if self.arg is not None else ''}"

class BytecodeProgram:
    """What Compiler.compile_program returns: the flat instruction array plus
    the per-function metadata CALL/RETURN need. Kept as an explicit container
    (not just the bare list) so it's clear the VM needs more than raw
    bytecode to run function calls — this is the seed of a real "code
    object" concept, deliberately not over-built until it's needed."""
    def __init__(self, code, func_entry, func_params, test_entry=None, declared_capabilities=None):
        self.code, self.func_entry, self.func_params = code, func_entry, func_params
        self.test_entry = test_entry or {}  # test_name (str) -> entry pc; zero params always
        # Canonical capability names this file declared with `use` (Phase 5). Static, known
        # before the first instruction runs — so import-chain checks never depend on
        # statement order. Declaring grants nothing; the VM's Policy decides that.
        self.declared_capabilities = frozenset(declared_capabilities or ())

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
        self.cur_line = 0       # source line of the statement being compiled
        self.handlers = []      # the try/catch/finally regions enclosing the code being compiled
                                # (innermost last), for the function being compiled: [("catch"|"finally", node)]
        self.in_finalizer = 0

    def emit(self, op, arg=None):
        self.code.append(Instr(op, arg, self.cur_line))
        return len(self.code) - 1

    def _emit_finalizer(self, node):
        """One inlined copy of a `finally` block, in its own scope."""
        self.in_finalizer += 1
        try:
            self.emit("PUSH_SCOPE")
            for s in node.finalizer:
                self.compile_stmt(s)
            self.emit("POP_SCOPE")
        finally:
            self.in_finalizer -= 1

    def _emit_unwind(self):
        """Code that leaves every enclosing try/catch/finally region of the current function,
        innermost first, before a `return` (or a `?` that found a failure) leaves the function —
        so each `finally` block runs, exactly once, in the order the interpreter runs them. A
        region outside the outermost `finally` needs nothing: RETURN discards the handlers of the
        frame it leaves."""
        if self.in_finalizer:
            raise LumenError("VM: 'return' or '?' inside a finally block is not supported")
        recs = self.handlers
        finals = [i for i, r in enumerate(recs) if r[0] == "finally"]
        if not finals:
            return
        for i in range(len(recs) - 1, finals[0] - 1, -1):
            kind, node = recs[i]
            if kind == "catch":
                self.emit("TRY_POP")
            else:
                self.emit("FIN_UNWIND")
                self.handlers = recs[:i]     # the finalizer runs inside the regions OUTSIDE this try
                try:
                    self._emit_finalizer(node)
                finally:
                    self.handlers = recs

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
            self.handlers = []
            self.cur_line = fn.line
            for s in fn.body:
                self.compile_stmt(s)
            self.emit("LOAD_CONST", None)
            self.emit("RETURN")  # implicit fall-off-the-end return (matches Interpreter.call_function)
        for t in ir_program.tests:
            # Test bodies compile into the SAME region, using the exact same
            # zero-arg call convention — see VM.run_one_test, which reuses
            # the shared _call_at machinery rather than inventing a second one.
            self.test_entry[t.name] = len(self.code)
            self.handlers = []
            self.cur_line = t.line
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
        self.handlers = []
        for s in ir_program.top_level:
            if isinstance(s, IRFunctionDef):
                self.cur_line = s.line
                self.emit("REGISTER_FN", s.name)
            elif isinstance(s, (IRStructDef, IREnumDef, IRTestDef)):
                pass
            else:
                self.compile_stmt(s)
        self.emit("HALT")
        declared = set()
        for top in ir_program.top_level:
            if isinstance(top, IRUseCapability):
                declared.update(expand_capability_decl(top.name, top.line))
        return BytecodeProgram(self.code, self.func_entry, self.func_params, self.test_entry, declared)

    def compile_stmt(self, node):
        if node.line:
            self.cur_line = node.line
        if isinstance(node, IRLet):
            self.compile_expr(node.expr)
            self.emit("STORE", node.name)
        elif isinstance(node, IRAssign):
            self.compile_expr(node.expr)
            self.emit("ASSIGN", node.name)
        elif isinstance(node, IRPrint):
            self.compile_expr(node.expr)
            self.emit("PRINT")
        elif isinstance(node, IRReturn):
            self.compile_expr(node.expr)
            self._emit_unwind()
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
        elif isinstance(node, IRWhile):
            loop_start = len(self.code)
            self.compile_expr(node.cond)
            jend = self.emit("JUMP_IF_FALSE", None)
            self.emit("PUSH_SCOPE")   # fresh scope EACH iteration, matching IRLoop/IRBranch
            for s in node.body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.emit("JUMP", loop_start)
            self.code[jend].arg = len(self.code)
        elif isinstance(node, IRTryCatch):
            try_push_idx = self.emit("TRY_PUSH", None)
            self.handlers.append(("catch", node))
            self.emit("PUSH_SCOPE")
            for s in node.body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.handlers.pop()
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
        elif isinstance(node, IRTryFinally):
            # FIN_PUSH registers the exceptional exit; the finalizer is inlined once per way out of
            # the body: falling off its end, each `return`/`?` inside it (see _emit_unwind), and an
            # error (entered at fin_pc by the VM's handler, which parks the pending error until
            # RERAISE puts it back in flight).
            fin_push = self.emit("FIN_PUSH", None)
            self.handlers.append(("finally", node))
            self.emit("PUSH_SCOPE")
            for s in node.body: self.compile_stmt(s)
            self.emit("POP_SCOPE")
            self.handlers.pop()
            self.emit("TRY_POP")
            self._emit_finalizer(node)
            jmp_end = self.emit("JUMP", None)
            fin_pc = len(self.code)
            self._emit_finalizer(node)
            self.emit("RERAISE")
            self.code[jmp_end].arg = len(self.code)
            self.code[fin_push].arg = fin_pc
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
                self.cur_line = node.line        # the failure belongs to the `match`, not to the last arm's body
                self.emit("MATCH_FAIL", node.line)
            for j in end_jumps:
                self.code[j].arg = len(self.code)
        elif isinstance(node, IRExprStmt):
            self.compile_expr(node.expr)
            self.emit("POP")
        elif isinstance(node, IRUseCapability):
            pass  # a declaration is static: collected into BytecodeProgram.declared_capabilities, no runtime effect
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
        elif isinstance(node, IRCall) and node.kind == "variant" and node.name not in self.variant_info:
            # A call to a name that is no function, builtin or enum variant. Name Resolution rejects
            # that statically EXCEPT inside a `try` body, where it defers to a catchable runtime
            # NameError (errors.md §4.9) — IRBuilder labels such a call "variant" by elimination. Emit
            # an ordinary CALL, whose runtime check raises exactly the interpreter's NameError
            # (arguments are evaluated first on both backends). Without this the compiler crashed
            # with a raw Python KeyError.
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
                    f"— use the default interpreter"
                )
            for a in node.args:
                self.compile_expr(a)
            self.emit("CALL_BUILTIN", (node.name, len(node.args)))
        elif isinstance(node, IRPropagate):
            # operand first; then PROPAGATE either pushes the unwrapped value and jumps over the
            # stub below, or falls into it: leave the enclosing try/finally regions, then RETURN
            # the failure unchanged — the same steps a written `return` takes.
            self.compile_expr(node.expr)
            site = self.emit("PROPAGATE", None)
            self._emit_unwind()
            self.emit("RETURN")
            self.code[site].arg = len(self.code)
        elif isinstance(node, IRSpawn):
            call = node.call
            for a in call.args:
                self.compile_expr(a)
            self.emit("SPAWN", (call.name, len(call.args)))
        elif isinstance(node, IRMethodCall):
            self.compile_expr(node.obj)
            for a in node.args:
                self.compile_expr(a)
            self.emit("CALL_METHOD", (node.method, len(node.args)))
        else:
            raise LumenError(f"VM: IR expression '{type(node).__name__}' is not supported by the bytecode backend yet — use the default interpreter")

# The bytecode VM keeps Lumen's call stack on the heap, so runaway recursion would otherwise grow
# until the machine runs out of memory. Past this many nested calls it fails like the interpreter
# does — a catchable "stack overflow" (R0009). (The depth at which each engine gives up differs:
# the interpreter's is whatever the host's Python stack allows; see docs/errors.md §4.14.)
VM_MAX_CALL_DEPTH = 10000


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
    def __init__(self, policy=None, cap_ctx=None):
        self.stack = []
        self.scopes = [{}]  # scope stack — [-1] is innermost; matches Env-chain semantics exactly
        self.iter_stack = []
        self.call_stack = []       # [(return_pc, saved_scopes, stack_base, iter_base, fn_name), ...] — explicit, so Lumen
                                    # recursion depth never consumes Python's own call stack (see Compiler
                                    # docstring). stack_base/iter_base are the operand-stack and iterator-stack
                                    # heights at the moment of the call: a RETURN truncates both back to them, so
                                    # an early `return` out of a `for` loop (or out of the middle of an
                                    # expression) can never leave a stale iterator or operand behind.
        self.registered_fns = set()  # runtime "has this fn statement executed yet" — mirrors Interpreter.functions
        self.try_stack = []        # [(handler_pc, err_name, saved_scopes, call_depth, stack_len, iter_len, kind, extra), ...]
                                    # kind "catch": a `try ... catch`; "cleanup": a `try ... finally` body that is
                                    # running; "fin_run": the finalizer of a failed try is running — `extra` is the
                                    # failure it will put back in flight (RERAISE) when it completes
        # Phase 5: what this module declared, who imported it, and the host's Policy —
        # the same CapabilityContext the Interpreter holds, consulted through the same
        # `capability_call`, so the two backends cannot differ on what is permitted.
        self.cap_ctx = cap_ctx if cap_ctx is not None else CapabilityContext(policy)
        self.base_dir = "."        # for resolving `use "path" as alias` — set by run_file_vm / USE_MODULE
        self.loading = []          # shared cycle-detection chain (ordered!) across nested module VMs — modules.md §5.6
        self.pc = 0
        self._threads = threading.local()   # per-thread forks used for calls INTO this module (see _fork_for_thread)
        self.vm_id = next(_VM_IDS)  # which VM contributed which frames to a failure's trace
        self.file = None            # source file name shown in traces (None: "<program>")
        self.base_name = "<main>"   # what the bottom frame is called in a trace
        self._interp = Interpreter()  # reuse apply_op / stringify for consistent semantics; persistent,
                                       # not re-created per instruction, so cross-module calls share it too

    def load(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        raise LumenError(f"NameError: '{name}' is not defined")

    def store(self, name, value):
        self.scopes[-1][name] = value  # always innermost — `let`'s declare semantics

    def assign(self, name, value):
        """Bare reassignment (compiled from `IRAssign`): mirrors `Env.assign`
        exactly — finds the nearest enclosing scope that already has `name`
        and mutates it THERE, rather than `store`'s always-innermost write.
        This is what makes `total = total + n` inside a for/if/try/match body
        update the outer `total` instead of shadowing it for that block."""
        for scope in reversed(self.scopes):
            if name in scope:
                scope[name] = value
                return
        raise LumenError(f"NameError: cannot assign to '{name}' — it was never declared with 'let'")

    def run(self, program: "BytecodeProgram"):
        self.cap_ctx.declare(program.declared_capabilities)
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
        return self._fork_for_thread()._call_at(program, program.func_entry[name], params, args, name)

    def _fork_for_thread(self):
        """The VM that runs calls into this module on the CURRENT thread. A VM keeps its control flow
        (operand stack, call stack, pc, handlers) as instance state, so two threads calling into the
        same module on one instance would corrupt each other (before 3.1, two `spawn`ed tasks calling the
        same imported function failed with "HALT reached during a nested call"). Each thread therefore gets
        its own VM that shares, by reference, what must stay shared: the module's global scope, its function
        table, its capability context, its base directory and the import-cycle chain."""
        vm = getattr(self._threads, "vm", None)
        if vm is None:
            vm = VM()
            vm.scopes = [self.scopes[0]]
            vm.registered_fns = self.registered_fns
            vm.cap_ctx = self.cap_ctx
            vm.base_dir = self.base_dir
            vm.loading = self.loading
            vm.file = self.file
            self._threads.vm = vm
        return vm

    def spawn(self, program, name, args):
        """VM analogue of Interpreter's SpawnExpr handling: runs `name(*args)`
        on a background thread and returns a TaskHandle immediately. Unlike
        the Interpreter (whose Python call stack naturally isolates each
        thread's own control flow while `self.functions`/`self.globals` stay
        shared), the VM keeps its control flow — stack, call_stack, pc,
        try_stack — as *instance* state, so running the spawned call via
        `self._call_at` directly would have two threads mutate the same
        `self.pc` concurrently. Instead, this gives the task its own VM
        instance for that per-thread state, while sharing by reference
        exactly the state that must stay consistent across threads: the
        global scope (scopes[0]), registered_fns, and the capability context (a
        spawned task has exactly its spawner's declarations and the same
        host Policy — it cannot hold more authority than the code that
        started it)."""
        if name not in self.registered_fns:
            raise LumenError(f"NameError: function '{name}' is not defined")
        task = TaskHandle()
        sub_vm = VM()
        sub_vm.scopes = [self.scopes[0]]
        sub_vm.registered_fns = self.registered_fns
        sub_vm.cap_ctx = self.cap_ctx
        sub_vm.base_dir = self.base_dir
        sub_vm.loading = self.loading
        sub_vm.file = self.file
        params = program.func_params[name]
        def worker():
            try:
                if len(args) != len(params):
                    raise LumenError(f"TypeError: {name}() expects {len(params)} arguments, got {len(args)}")
                task.result = sub_vm._call_at(program, program.func_entry[name], params, args, name)
            except (LumenError, LumenRaise) as e:
                task.error = e
            except Exception as e:      # an implementation fault must not leave `wait` blocked forever
                task.error = LumenError(f"InternalError: the task failed inside the VM ({type(e).__name__}: {e})")
            finally:
                task.done.set()
                _task_finished(task)
        task.thread = threading.Thread(target=worker, daemon=True)
        task.thread.start()
        return task

    def run_one_test(self, program, test_name):
        """Runs a single `test "name": ...` block to completion or first
        failure, via the exact same nested-call machinery as an ordinary or
        cross-module function call. Returns (passed: bool, message: str|None)
        — never raises, since a failing assertion is the *expected*, handled
        outcome for a test runner, not a program-terminating error."""
        entry_pc = program.test_entry[test_name]
        try:
            self._call_at(program, entry_pc, [], [], f'test "{test_name}"')
            return True, None
        except (LumenError, LumenRaise) as e:
            return False, (e.message if isinstance(e, LumenRaise) else str(e))

    def _call_at(self, program, entry_pc, params, args, name="<call>"):
        """Shared machinery underlying every 'call this and get the return
        value from outside the normal bytecode stream' need: cross-module
        calls (call_exported_function) and running a named test
        (run_one_test). Binds args into a fresh Rule-C scope and runs
        _exec_one in a nested loop until the matching RETURN unwinds the
        call stack back to where we started."""
        saved_pc = self.pc
        saved_scopes = self.scopes
        target_depth = len(self.call_stack)
        stack_base, iter_base, try_base = len(self.stack), len(self.iter_stack), len(self.try_stack)
        if len(self.call_stack) >= VM_MAX_CALL_DEPTH:
            raise LumenRaise("stack overflow: maximum call depth exceeded", code="R0009")
        self.call_stack.append((-1, self.scopes, stack_base, iter_base, name))  # -1: never actually jumped to — see loop-exit note below
        self.scopes = [self.scopes[0], dict(zip(params, args))]
        self.pc = entry_pc
        try:
            while len(self.call_stack) > target_depth:
                if not self._exec_one(program):
                    raise LumenError("VM: internal error — HALT reached during a nested call")
        except BaseException:
            # An error left this nested call. Whatever it was in the middle of — frames, scopes,
            # half-evaluated operands, open loops, try handlers it had pushed — is abandoned
            # exactly, so the VM is back in the state it was in before the call and the caller
            # (a `catch`, or the next call into this module) sees nothing of the failed one.
            del self.call_stack[target_depth:]
            del self.stack[stack_base:]
            del self.iter_stack[iter_base:]
            del self.try_stack[try_base:]
            self.scopes = saved_scopes
            self.pc = saved_pc
            raise
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
            # Ordered by actual measured hotness (cProfile on fib(24) and
            # stdlib-loop workloads), not by category — this if/elif chain is
            # a linear scan, so whichever opcode is checked first is
            # effectively free and whichever is checked last pays for every
            # check before it. Before this reordering, BINOP and
            # JUMP_IF_FALSE (needed by literally every arithmetic op and
            # every `if`/loop condition) sat at positions 28-30 of a 30-way
            # chain — the worst possible spot for the hottest opcodes. Purely
            # a reorder: every branch's code is byte-identical to before,
            # only the sequence changed, so this cannot change any program's
            # behavior (verified by the interpreter<->VM equivalence suite,
            # which is byte-for-byte output comparison, not just "it parses").
            if op == "HALT": return False
            elif op == "LOAD": self.stack.append(self.load(arg))
            elif op == "LOAD_CONST": self.stack.append(arg)
            elif op == "BINOP":
                r = self.stack.pop(); l = self.stack.pop()
                self.stack.append(interp.apply_op(arg, l, r))
            elif op == "JUMP_IF_FALSE":
                if not self.stack.pop(): self.pc = arg; return True
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
                if len(self.call_stack) >= VM_MAX_CALL_DEPTH:
                    raise LumenRaise("stack overflow: maximum call depth exceeded", code="R0009")
                self.call_stack.append((self.pc + 1, self.scopes, len(self.stack), len(self.iter_stack), name))
                self.scopes = [self.scopes[0], dict(zip(params, call_args))]  # Rule C: global + own params only
                self.pc = program.func_entry[name]
                return True
            elif op == "RETURN":
                retval = self.stack.pop()
                self.pc, self.scopes, stack_base, iter_base, _fn = self.call_stack.pop()
                # Leave the frame completely: operands left over from a half-evaluated expression
                # and iterators of loops the `return` jumped out of belong to this frame only.
                del self.stack[stack_base:]
                del self.iter_stack[iter_base:]
                # A `return` inside a `try` body jumps straight out of the
                # function, bypassing the TRY_POP that IRTryCatch's codegen
                # only emits right after the try body completes by falling
                # through (see compile_stmt's IRTryCatch case) — so without
                # this, the matching TRY_PUSH's entry would be left on
                # try_stack forever: a stale handler, still bound to a call
                # frame that no longer exists, that could wrongly catch some
                # later, completely unrelated exception (discovered via
                # std/json.lum's `try: return HEX_VALUE[c] catch e: ...`
                # idiom, called repeatedly from inside a module — see
                # CHANGELOG.md's 2.2 entry). Any try_stack entry whose
                # recorded call depth is deeper than where we've just
                # returned to belongs to the frame(s) just unwound past, so
                # it's discarded here, exactly as if its TRY_POP had run.
                new_depth = len(self.call_stack)
                while self.try_stack and self.try_stack[-1][3] > new_depth:
                    self.try_stack.pop()
                self.stack.append(retval)
                return True
            elif op == "STORE": self.store(arg, self.stack.pop())
            elif op == "ASSIGN": self.assign(arg, self.stack.pop())
            elif op == "JUMP": self.pc = arg; return True
            elif op == "JUMP_IF_TRUE":
                if self.stack.pop(): self.pc = arg; return True
            elif op == "POP": self.stack.pop()
            elif op == "GET_ITER":
                self.iter_stack.append(iter(self.stack.pop()))
            elif op == "FOR_ITER":
                try: self.stack.append(next(self.iter_stack[-1]))
                except StopIteration: self.pc = arg; return True
            elif op == "POP_ITER": self.iter_stack.pop()
            elif op == "PUSH_SCOPE": self.scopes.append({})
            elif op == "POP_SCOPE": self.scopes.pop()
            elif op == "INDEX_GET":
                key = self.stack.pop(); obj = self.stack.pop()
                try:
                    self.stack.append(obj[int(key)] if isinstance(obj, list) else obj[key])
                except IndexError:
                    raise LumenRaise(f"index {key} is out of range (length {len(obj)})", code="R0006")
                except KeyError:
                    raise LumenRaise(f"key {key!r} not found", code="R0007")
                except TypeError:
                    raise LumenError(f"TypeError: cannot index into this value with {key!r}")
            elif op == "INDEX_SET":
                value = self.stack.pop(); key = self.stack.pop(); obj = self.stack.pop()
                try:
                    obj[key] = value
                except (TypeError, IndexError) as e:
                    raise LumenError(f"TypeError: cannot assign index on this value ({e})")
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
            elif op == "DUP": self.stack.append(self.stack[-1])
            elif op == "NOT": self.stack.append(not self.stack.pop())
            elif op == "PRINT": print(interp.stringify(self.stack.pop()))
            elif op == "REGISTER_FN": self.registered_fns.add(arg)
            elif op == "USE_MODULE":
                self._use_module(arg)
            elif op == "TRY_PUSH":
                catch_pc, err_name = arg
                self.try_stack.append((catch_pc, err_name, list(self.scopes), len(self.call_stack),
                                       len(self.stack), len(self.iter_stack), "catch", None))
            elif op == "TRY_POP":
                self.try_stack.pop()
            elif op == "FIN_PUSH":
                # a `try ... finally`: on failure the VM jumps to `arg` (the finalizer's exceptional copy)
                self.try_stack.append((arg, None, list(self.scopes), len(self.call_stack),
                                       len(self.stack), len(self.iter_stack), "cleanup", None))
            elif op == "FIN_UNWIND":
                # a `return` (or `?`) is leaving the try body: the finalizer runs now, as if the body
                # had ended, in the scope the `try` statement itself was in
                entry = self.try_stack.pop()
                self.scopes = entry[2]
            elif op == "RERAISE":
                # the exceptional copy of the finalizer finished: the failure it interrupted continues
                entry = self.try_stack.pop()
                raise entry[7]
            elif op == "PROPAGATE":
                proceed, value = propagate_step(self.stack.pop())
                self.stack.append(value)
                if proceed:
                    self.pc = arg      # skip the stub that returns the failure
                    return True
            elif op == "CALL_METHOD":
                method, argc = arg
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                mod = self.stack.pop()
                if not isinstance(mod, VMModule):
                    raise LumenError(f"TypeError: '{method}' is not callable on this value")
                self.stack.append(mod.vm.call_exported_function(mod.program, method, call_args, mod.alias))
            elif op == "SPAWN":
                name, argc = arg
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                if name not in self.registered_fns:
                    raise LumenError(f"NameError: function '{name}' is not defined")
                self.stack.append(self.spawn(program, name, call_args))
            elif op == "CALL_BUILTIN":
                name, argc = arg
                call_args = [self.stack.pop() for _ in range(argc)][::-1]
                if name == "len":
                    if not isinstance(call_args[0], (list, dict, str)):
                        raise LumenError(f"TypeError: len() expects a List, Dict, or Str, got {type(call_args[0]).__name__}")
                    self.stack.append(len(call_args[0]))
                elif name == "chr":
                    if not isinstance(call_args[0], int) or isinstance(call_args[0], bool):
                        raise LumenError(f"TypeError: chr() expects an Int, got {type(call_args[0]).__name__}")
                    if not (0 <= call_args[0] <= 0x10FFFF):
                        raise LumenError(f"TypeError: chr() argument {call_args[0]} is out of the valid code point range (0-1114111)")
                    self.stack.append(chr(call_args[0]))
                elif name == "ord":
                    if not isinstance(call_args[0], str) or len(call_args[0]) != 1:
                        raise LumenError(f"TypeError: ord() expects a single-character Str, got {call_args[0]!r}")
                    self.stack.append(ord(call_args[0]))
                elif name == "type_of":
                    self.stack.append(self._type_of(call_args[0]))
                elif name == "range":
                    self.stack.append(interp._builtin_range(call_args))
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
                elif name == "fail" or name == "fail_with":
                    raise make_fail(name, call_args)
                elif name in RESULT_BUILTIN_NAMES:
                    self.stack.append(result_builtin(name, call_args, interp.stringify))
                elif name == "assert":
                    if not interp.truthy(call_args[0]):
                        raise LumenRaise("assertion failed", code="R0012")
                    self.stack.append(None)
                elif name == "assert_eq":
                    if call_args[0] != call_args[1]:
                        raise LumenRaise(f"assertion failed: {interp.stringify(call_args[0])} != {interp.stringify(call_args[1])}", code="R0012")
                    self.stack.append(None)
                elif name == "assert_ne":
                    if call_args[0] == call_args[1]:
                        raise LumenRaise(f"assertion failed: {interp.stringify(call_args[0])} == {interp.stringify(call_args[1])}", code="R0012")
                    self.stack.append(None)
                elif name == "now":
                    import time as _time
                    self.stack.append(_time.time())
                elif name == "read_line":
                    try:
                        self.stack.append(input())
                    except EOFError:
                        raise LumenRaise("end of input", code="R0013")
                elif name in CAPABILITY_BUILTIN_NAMES:
                    self.stack.append(capability_call(self.cap_ctx, name, call_args))
                elif name == "wait":
                    task = call_args[0]
                    if not isinstance(task, TaskHandle):
                        raise LumenError("TypeError: wait() expects a task returned by spawn")
                    _task_observed(task)
                    task.done.wait()
                    if task.error: raise task.error
                    self.stack.append(task.result)
                elif name == "chan":
                    self.stack.append(LumenChannel())
                elif name == "send":
                    if not isinstance(call_args[0], LumenChannel):
                        raise LumenError("TypeError: send() expects a channel returned by chan()")
                    call_args[0].q.put(call_args[1])
                    self.stack.append(None)
                elif name == "receive":
                    if not isinstance(call_args[0], LumenChannel):
                        raise LumenError("TypeError: receive() expects a channel returned by chan()")
                    self.stack.append(call_args[0].q.get())
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
            else:
                raise LumenError(f"VM: unknown opcode {op}")
            self.pc += 1
            return True
        except (LumenError, LumenRaise) as e:
            self._note_failure(e, program)
            return self._recover(e)
        except RecursionError:
            # modules calling modules calling modules ... nest Python calls (one `_call_at` each)
            e = LumenRaise("stack overflow: maximum call depth exceeded", code="R0009")
            self._note_failure(e, program)
            return self._recover(e)

    # ---- failure handling: traces, catch, finally ---------------------------------------------------

    def _line_at(self, program, pc):
        code = program.code
        return code[pc].line if 0 <= pc < len(code) else 0

    def _note_failure(self, e, program):
        """Record the calls that led to this failure — once per VM, the first time the failure is seen
        here, while the stack is still exactly as it was when the failure happened. A failure that
        crosses into another module's VM (or comes back through `wait`) collects each VM's frames in
        turn, innermost first."""
        seen = e.__dict__.setdefault("lumen_vms", set())
        if self.vm_id in seen:
            return
        seen.add(self.vm_id)
        frames = e.__dict__.setdefault("lumen_frames", [])
        file = self.file
        pc = self.pc
        calls = self.call_stack
        for i in range(len(calls) - 1, -1, -1):
            ret_pc, _sc, _sb, _ib, name = calls[i]
            frames.append((name, file, self._line_at(program, pc)))
            if ret_pc < 0:        # called from outside this VM (a module call, a test): its caller is elsewhere
                return
            pc = ret_pc - 1       # the CALL instruction in the caller
        frames.append((self.base_name, file, self._line_at(program, pc)))

    def _recover(self, e):
        """Find the innermost handler for failure `e`: a `catch` takes it (binding the error value), a
        `finally` runs its cleanup with the failure parked, and a failure that escapes a running
        finalizer replaces the one the finalizer was running for — which becomes its cause."""
        while self.try_stack:
            handler_pc, err_name, saved_scopes, call_depth, stack_len, iter_len, kind, extra = self.try_stack.pop()
            if kind == "fin_run":
                attach_pending(e, extra)
                continue
            self.scopes = saved_scopes
            self.call_stack = self.call_stack[:call_depth]
            # the failed statement may have been mid-expression and mid-loop: drop its operands and
            # the iterators of every loop the failure jumped out of (the handler's own loop, if it is
            # inside one, is below these marks and untouched)
            del self.stack[stack_len:]
            del self.iter_stack[iter_len:]
            if kind == "catch":
                self.scopes.append({err_name: make_error_value(e)})
                self.pc = handler_pc
                return True
            self.try_stack.append((handler_pc, None, saved_scopes, call_depth, stack_len, iter_len, "fin_run", e))
            self.pc = handler_pc
            return True
        raise e

    def _type_of(self, v):
        """VM-side counterpart of Interpreter._type_of — see its docstring
        for why Bool must be checked before Int. The only difference from
        the Interpreter's version is the module wrapper type: the VM wraps
        an imported module as a VMModule, never a LumenModule."""
        if v is None: return "Nothing"
        if isinstance(v, bool): return "Bool"
        if isinstance(v, int): return "Int"
        if isinstance(v, float): return "Float"
        if isinstance(v, str): return "Str"
        if isinstance(v, list): return "List"
        if isinstance(v, dict): return "Dict"
        if isinstance(v, LumenInstance): return v.struct_name
        if isinstance(v, EnumInstance): return v.enum_name
        if isinstance(v, TaskHandle): return "Task"
        if isinstance(v, LumenChannel): return "Channel"
        if isinstance(v, VMModule): return "Module"
        return "Any"

    def _use_module(self, arg):
        import os
        path, alias, line = arg
        full = _locate_module(self.base_dir, path, line)
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
            sub_ast, sub_resolver = analyze(src, os.path.basename(full), os.path.dirname(full) or ".")
            sub_ir = IRBuilder(sub_resolver.module_globals).build_program(sub_ast)
            ir_errors = IRValidator().validate(sub_ir)
            if ir_errors:
                raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
            sub_program = Compiler().compile_program(sub_ir)
            sub_vm = VM(cap_ctx=CapabilityContext(self.cap_ctx.policy, label=os.path.basename(full),
                                                  parent=self.cap_ctx))
            sub_vm.base_dir = os.path.dirname(full) or "."
            sub_vm.file = os.path.basename(full)
            sub_vm.loading = self.loading  # shared cycle-detection set, matching interpreter exactly
            sub_vm.run(sub_program)
        except (SyntaxError, LumenError) as e:
            raise _module_load_error(path, e)
        finally:
            self.loading.remove(abs_full) if abs_full in self.loading else None
        self.store(alias, VMModule(alias, sub_vm, sub_program))



def run_file_vm(path, opt_level=0, policy=None):
    import os
    with open(path) as f:
        src = f.read()
    ast, resolver = analyze(src, os.path.basename(path), os.path.dirname(os.path.abspath(path)))
    ir_program = IRBuilder(resolver.module_globals).build_program(ast)
    ir_errors = IRValidator().validate(ir_program)
    if ir_errors:
        raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
    ir_program = IROptimizer(opt_level).optimize(ir_program)
    code = Compiler().compile_program(ir_program)
    vm = VM(policy=policy, cap_ctx=CapabilityContext(policy, label=os.path.basename(path)))
    vm.base_dir = os.path.dirname(os.path.abspath(path))
    vm.file = os.path.basename(path)
    vm.run(code)

class ReturnSignal(Exception):
    def __init__(self, value): self.value = value

class Env:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent
        # The call-stack record ([function, file, current line]) of the function this scope belongs to,
        # shared with every scope nested in it, so that noting "which line is running" for a stack trace
        # is one attribute read — not a thread-local lookup — per statement.
        self.frame = parent.frame if parent is not None else None
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
    def assign(self, name, value):
        """Reassignment (bare `name = expr`, no `let`): finds the NEAREST
        enclosing scope that already has `name` bound and mutates it THERE,
        instead of `set`'s always-innermost declare semantics. This is what
        makes `total = total + n` inside a `for`/`if`/`try`/`match` body
        actually update the outer `total` rather than shadowing it for the
        duration of that block. Raises if `name` was never declared with
        `let` anywhere visible — assigning to an undeclared name is a clear
        error, not a silent implicit declaration."""
        env = self
        while env is not None:
            if name in env.vars:
                env.vars[name] = value
                return
            env = env.parent
        raise LumenError(f"NameError: cannot assign to '{name}' — it was never declared with 'let'")

class LumenRaise(Exception):
    """A catchable runtime error, carries a Lumen-visible error object. `code` is its stable
    identifier (None: the generic R0001 unless the message says otherwise); `cause` is the Error
    value of the failure this one was raised on account of (`fail(message, cause)`)."""
    def __init__(self, message, code=None, cause=None):
        super().__init__(message)
        self.message = message
        self.code, self.cause = code, cause

class Interpreter:
    def __init__(self, base_dir=".", loading=None, policy=None, cap_ctx=None, label=None):
        self.globals = Env()
        self.functions = {}
        self.structs = {}
        # Phase 5: this module's declarations, its importer chain, and the host's Policy.
        # The default Policy grants nothing (default-deny); only the host can supply another.
        self.cap_ctx = cap_ctx if cap_ctx is not None else CapabilityContext(policy, label=label)
        self.base_dir = base_dir
        self.file = label         # source file name shown in stack traces (None: "<program>")
        self.enum_variants = {}   # variant_name -> (enum_name, [field_names])
        self.loading = loading if loading is not None else []  # ordered: abs paths currently being imported (cycle guard)
        self.tests = []           # [(name, body)] — registered, not executed inline; see run_tests()

    def run(self, stmts):
        # Declarations are static: collect every top-level `use <capability>` BEFORE executing
        # anything, so what this module declares (and therefore what any module it imports
        # may exercise) never depends on statement order.
        declared = set()
        for s in stmts:
            if isinstance(s, Use):
                declared.update(expand_capability_decl(s.name, s.line))
        self.cap_ctx.declare(declared)
        frames = _TLS.frames
        rec = ["<main>", self.file, 0]
        self.globals.frame = rec
        frames.append(rec)
        try:
            for s in stmts:
                self.exec_stmt(s, self.globals)
        except (LumenError, LumenRaise) as e:
            note_failure_here(e)
            raise
        finally:
            frames.pop()

    def exec_block(self, stmts, env):
        for s in stmts:
            self.exec_stmt(s, env)

    def exec_stmt(self, node, env):
        """Same type(node)-keyed dispatch as eval() — see its docstring. Also records which source
        line this thread's current function is executing, for a stack trace."""
        fn = self._EXEC_DISPATCH.get(type(node))
        if fn is None:
            raise LumenError(f"Unknown statement: {node}")
        fr = env.frame
        if fr is not None:
            fr[2] = node.line
        fn(self, node, env)

    def _exec_Let(self, node, env):
        env.set(node.name, self.eval(node.expr, env))

    def _exec_Assign(self, node, env):
        env.assign(node.name, self.eval(node.expr, env))

    def _exec_Print(self, node, env):
        print(self.stringify(self.eval(node.expr, env)))

    def _exec_If(self, node, env):
        if self.truthy(self.eval(node.cond, env)):
            self.exec_block(node.body, Env(env))
        else:
            self.exec_block(node.orelse, Env(env))

    def _exec_For(self, node, env):
        items = self.eval(node.iterable, env)
        for item in items:
            loop_env = Env(env)
            loop_env.set(node.var, item)
            self.exec_block(node.body, loop_env)

    def _exec_While(self, node, env):
        fr = env.frame
        while True:
            if fr is not None:
                fr[2] = node.line     # the condition is part of the `while` line, every time round
            if not self.truthy(self.eval(node.cond, env)):
                break
            self.exec_block(node.body, Env(env))

    def _exec_FnDef(self, node, env):
        self.functions[node.name] = node

    def _exec_TestDef(self, node, env):
        self.tests.append((node.name, node.body))

    def _exec_StructDef(self, node, env):
        self.structs[node.name] = node.fields

    def _exec_EnumDef(self, node, env):
        for vname, fields in node.variants:
            self.enum_variants[vname] = (node.name, fields)

    def _exec_Match(self, node, env):
        value = self.eval(node.expr, env)
        for pattern, body in node.cases:
            case_env = Env(env)
            if self.match_pattern(pattern, value, case_env):
                self.exec_block(body, case_env)
                break
        else:
            raise LumenError(f"MatchError at line {node.line}: no pattern matched {self.stringify(value)}")

    def _exec_Use(self, node, env):
        # Declaring a capability is not an action: `run` already recorded it, and nothing
        # here grants anything. (Re-validated so a tree that skipped the Resolver still
        # fails closed on an unknown name.)
        self.cap_ctx.declare(expand_capability_decl(node.name, node.line))

    def _exec_UseModule(self, node, env):
        import os
        full = _locate_module(self.base_dir, node.path, node.line)
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
            sub = Interpreter(base_dir=os.path.dirname(full) or ".", loading=self.loading,
                              label=os.path.basename(full),
                              cap_ctx=CapabilityContext(self.cap_ctx.policy, label=os.path.basename(full),
                                                        parent=self.cap_ctx))
            mod_ast, _resolver = analyze(src, os.path.basename(full), os.path.dirname(full) or ".")
            sub.run(mod_ast)
        except (SyntaxError, LumenError) as e:
            raise _module_load_error(node.path, e)
        finally:
            self.loading.remove(abs_full) if abs_full in self.loading else None
        env.set(node.alias, LumenModule(node.alias, sub))

    def _exec_Return(self, node, env):
        raise ReturnSignal(self.eval(node.expr, env))

    def _exec_FieldSet(self, node, env):
        obj = self.eval(node.obj, env)
        if not isinstance(obj, LumenInstance):
            raise LumenError(f"TypeError: cannot set field '{node.field}' on non-struct value")
        obj.fields[node.field] = self.eval(node.expr, env)

    def _exec_Try(self, node, env):
        try:
            self.exec_block(node.body, Env(env))
        except (LumenRaise, LumenError, ZeroDivisionError) as e:
            note_failure_here(e)
            handler_env = Env(env)
            handler_env.set(node.err_name, make_error_value(e))
            self.exec_block(node.handler, handler_env)

    def _exec_TryFinally(self, node, env):
        """`try: ... finally: ...` — the finalizer runs exactly once however the body ends: by
        falling off its end, by a `return` / `?` (a ReturnSignal passing through), or by a failure.
        A failure continues after the finalizer (unchanged, trace and all); one that escapes the
        finalizer itself replaces it, and the failure it replaced becomes its cause."""
        try:
            self.exec_block(node.body, Env(env))
        except (LumenRaise, LumenError, ZeroDivisionError) as e:
            note_failure_here(e)
            try:
                self.exec_block(node.finalizer, Env(env))
            except (LumenRaise, LumenError) as e2:
                note_failure_here(e2)
                attach_pending(e2, e)
                raise
            raise
        except ReturnSignal:
            self.exec_block(node.finalizer, Env(env))
            raise
        self.exec_block(node.finalizer, Env(env))

    def _exec_IndexSet(self, node, env):
        obj = self.eval(node.obj, env)
        key = self.eval(node.key, env)
        val = self.eval(node.expr, env)
        try:
            obj[key] = val
        except (TypeError, IndexError) as e:
            raise LumenError(f"TypeError: cannot assign index on this value ({e})")

    def _exec_ExprStmt(self, node, env):
        self.eval(node.expr, env)



    def eval(self, node, env):
        """Dispatches by the node's exact runtime type (every AST Node subclass
        is a flat leaf with no further subclassing, so type(node) is X is
        provably equivalent to the isinstance(node, X) checks this replaced) —
        an O(1) dict lookup instead of a linear isinstance chain."""
        fn = self._EVAL_DISPATCH.get(type(node))
        if fn is None:
            raise LumenError(f"Unknown expression: {node}")
        return fn(self, node, env)

    def _eval_Num(self, node, env):
        return node.v

    def _eval_Str(self, node, env):
        return node.v

    def _eval_Bool(self, node, env):
        return node.v

    def _eval_ListLit(self, node, env):
        return [self.eval(i, env) for i in node.items]

    def _eval_DictLit(self, node, env):
        return {self.eval(k, env): self.eval(v, env) for k, v in node.pairs}

    def _eval_Index(self, node, env):
        obj = self.eval(node.obj, env)
        key = self.eval(node.key, env)
        try:
            return obj[int(key)] if isinstance(obj, list) else obj[key]
        except IndexError:
            raise LumenRaise(f"index {key} is out of range (length {len(obj)})", code="R0006")
        except KeyError:
            raise LumenRaise(f"key {key!r} not found", code="R0007")
        except TypeError:
            raise LumenError(f"TypeError: cannot index into this value with {key!r}")

    def _eval_Var(self, node, env):
        if node.name in self.enum_variants and not env.has(node.name):
            enum_name, fields = self.enum_variants[node.name]
            if not fields:
                return EnumInstance(enum_name, node.name, {})
        return env.get(node.name)

    def _eval_BinOp(self, node, env):
        l = self.eval(node.l, env)
        r = self.eval(node.r, env)
        return self.apply_op(node.op, l, r)

    def _eval_BoolOp(self, node, env):
        l = self.truthy(self.eval(node.l, env))
        if node.op == "and":
            return l and self.truthy(self.eval(node.r, env))
        return l or self.truthy(self.eval(node.r, env))

    def _eval_Not(self, node, env):
        return not self.truthy(self.eval(node.e, env))

    def _eval_StructLit(self, node, env):
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

    def _eval_FieldGet(self, node, env):
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

    def _eval_Propagate(self, node, env):
        """`expr?` — the operand is evaluated first; on Ok / Some its payload is the value of the
        whole expression, on Err / None the enclosing function returns that same value at once."""
        proceed, value = propagate_step(self.eval(node.expr, env))
        if proceed:
            return value
        raise ReturnSignal(value)

    def _eval_MethodCall(self, node, env):
        obj = self.eval(node.obj, env)
        if not isinstance(obj, LumenModule):
            raise LumenError(f"TypeError: '{node.method}' is not callable on this value")
        if node.method not in obj.interp.functions:
            raise LumenError(f"NameError: module '{obj.name}' has no function '{node.method}'")
        args = [self.eval(a, env) for a in node.args]
        return obj.interp.call_function(node.method, args)

    def _eval_SpawnExpr(self, node, env):
        call = node.call
        if call.name not in self.functions:
            raise LumenError(f"NameError: function '{call.name}' is not defined (line {node.line})")
        args = [self.eval(a, env) for a in call.args]
        task = TaskHandle()
        def worker():
            try:
                task.result = self.call_function(call.name, args)
            except (LumenError, LumenRaise) as e:
                note_failure_here(e)
                task.error = e
            except Exception as e:      # an implementation fault must not leave `wait` blocked forever
                task.error = LumenError(f"InternalError: the task failed inside the interpreter ({type(e).__name__}: {e})")
            finally:
                task.done.set()
                _task_finished(task)
        task.thread = threading.Thread(target=worker, daemon=True)
        task.thread.start()
        return task

    def _eval_Call(self, node, env):
        args = [self.eval(a, env) for a in node.args]
        name = node.name
        # Fast path: a plain user-function call. A name that is no built-in (every name tested below is
        # in BUILTIN_FUNCTION_NAMES — tests/test_phase4.py pins that) and no enum variant can only be a
        # user function, so skip the chain of built-in comparisons. Anything else takes the chain, in the
        # same order as before, and so do the NameError and the built-ins' own failures.
        if name not in BUILTIN_FUNCTION_NAMES and name not in self.enum_variants and name in self.functions:
            return self.call_function(name, args)
        if node.name == "len":
            if not isinstance(args[0], (list, dict, str)):
                raise LumenError(f"TypeError: len() expects a List, Dict, or Str, got {type(args[0]).__name__}")
            return len(args[0])
        if node.name == "chr":
            if not isinstance(args[0], int) or isinstance(args[0], bool):
                raise LumenError(f"TypeError: chr() expects an Int, got {type(args[0]).__name__}")
            if not (0 <= args[0] <= 0x10FFFF):
                raise LumenError(f"TypeError: chr() argument {args[0]} is out of the valid code point range (0-1114111)")
            return chr(args[0])
        if node.name == "ord":
            if not isinstance(args[0], str) or len(args[0]) != 1:
                raise LumenError(f"TypeError: ord() expects a single-character Str, got {args[0]!r}")
            return ord(args[0])
        if node.name == "type_of":
            return self._type_of(args[0])
        if node.name == "range":
            return self._builtin_range(args)
        if node.name == "wait":
            task = args[0]
            if not isinstance(task, TaskHandle):
                raise LumenError("TypeError: wait() expects a task returned by spawn")
            _task_observed(task)
            task.done.wait()
            if task.error:
                note_failure_here(task.error)
                raise task.error
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
        if node.name == "fail" or node.name == "fail_with":
            raise make_fail(node.name, args)
        if node.name in RESULT_BUILTIN_NAMES:
            return result_builtin(node.name, args, self.stringify)
        if node.name == "assert":
            if not self.truthy(args[0]):
                raise LumenRaise("assertion failed", code="R0012")
            return None
        if node.name == "assert_eq":
            if args[0] != args[1]:
                raise LumenRaise(f"assertion failed: {self.stringify(args[0])} != {self.stringify(args[1])}", code="R0012")
            return None
        if node.name == "assert_ne":
            if args[0] == args[1]:
                raise LumenRaise(f"assertion failed: {self.stringify(args[0])} == {self.stringify(args[1])}", code="R0012")
            return None
        if node.name == "now":
            import time as _time
            return _time.time()
        if node.name == "read_line":
            try:
                return input()
            except EOFError:
                raise LumenRaise("end of input", code="R0013")
        if node.name in CAPABILITY_BUILTIN_NAMES:
            return self.call_capability(node.name, args)
        if node.name in self.enum_variants:
            enum_name, fields = self.enum_variants[node.name]
            if len(args) != len(fields):
                raise LumenError(f"TypeError: {node.name}() expects {len(fields)} value(s), got {len(args)}")
            return EnumInstance(enum_name, node.name, dict(zip(fields, args)))
        if node.name not in self.functions:
            raise LumenError(f"NameError: function '{node.name}' is not defined")
        return self.call_function(node.name, args)

    _EVAL_DISPATCH = {
        Num: _eval_Num,
        Str: _eval_Str,
        Bool: _eval_Bool,
        ListLit: _eval_ListLit,
        DictLit: _eval_DictLit,
        Index: _eval_Index,
        Var: _eval_Var,
        BinOp: _eval_BinOp,
        BoolOp: _eval_BoolOp,
        Not: _eval_Not,
        StructLit: _eval_StructLit,
        FieldGet: _eval_FieldGet,
        MethodCall: _eval_MethodCall,
        SpawnExpr: _eval_SpawnExpr,
        Call: _eval_Call,
        Propagate: _eval_Propagate,
    }

    _EXEC_DISPATCH = {
        Let: _exec_Let,
        Assign: _exec_Assign,
        Print: _exec_Print,
        If: _exec_If,
        For: _exec_For,
        While: _exec_While,
        FnDef: _exec_FnDef,
        TestDef: _exec_TestDef,
        StructDef: _exec_StructDef,
        EnumDef: _exec_EnumDef,
        Match: _exec_Match,
        Use: _exec_Use,
        UseModule: _exec_UseModule,
        Return: _exec_Return,
        FieldSet: _exec_FieldSet,
        Try: _exec_Try,
        TryFinally: _exec_TryFinally,
        IndexSet: _exec_IndexSet,
        ExprStmt: _exec_ExprStmt,
    }



    def call_function(self, name, args):
        fn = self.functions[name]
        if len(args) != len(fn.params):
            raise LumenError(f"TypeError: {name}() expects {len(fn.params)} arguments, got {len(args)}")
        call_env = Env(self.globals)
        for (p, _ptype), a in zip(fn.params, args):
            call_env.set(p, a)
        frames = _TLS.frames
        rec = [name, self.file, fn.line]
        call_env.frame = rec
        frames.append(rec)
        try:
            self.exec_block(fn.body, call_env)
        except ReturnSignal as r:
            return r.value
        except (LumenError, LumenRaise) as e:
            note_failure_here(e)
            raise
        except RecursionError:
            # The reference implementation's tree-walking call_function recurses
            # in lockstep with Lumen-level recursion, so unbounded Lumen
            # recursion becomes unbounded Python recursion. Without this,
            # a raw Python RecursionError traceback would leak straight to
            # the user — exactly the class of bug this project treats as
            # unconditionally unacceptable elsewhere (see docs/errors.md
            # §4.2.5's uniform-catchability guarantee). Converting it to the
            # ordinary runtime-failure protocol makes it catchable by an
            # enclosing try/catch like any other failure, and gives an
            # uncaught one a clean message instead of a Python stack dump.
            e = LumenRaise("stack overflow: maximum call depth exceeded", code="R0009")
            note_failure_here(e)
            raise e
        finally:
            frames.pop()
        return None

    def run_tests(self):
        """Runs every registered `test "name": ...` block (only meaningful
        after `run()` has already executed the file's top-level code, so all
        tests were registered — and any top-level setup `let`s exist). Each
        test runs in its own Env(self.globals), matching a function call's
        Rule-C isolation. Returns [(name, passed: bool, message: str|None)]."""
        results = []
        frames = _TLS.frames
        for name, body in self.tests:
            test_env = Env(self.globals)
            rec = [f'test "{name}"', self.file, 0]
            test_env.frame = rec
            frames.append(rec)
            try:
                self.exec_block(body, test_env)
                results.append((name, True, None))
            except (LumenError, LumenRaise) as e:
                msg = e.message if isinstance(e, LumenRaise) else str(e)
                results.append((name, False, msg))
            except RecursionError:
                results.append((name, False, "stack overflow: maximum call depth exceeded"))
            finally:
                frames.pop()
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
        return capability_call(self.cap_ctx, name, args)

    def _builtin_range(self, args):
        """`range(n)` -> [0, 1, ..., n-1] (empty for n <= 0); `range(start,
        end)` -> [start, ..., end-1] (empty for end <= start). Implemented
        natively (Python's own range(), not a Lumen-level loop or recursion)
        specifically so a counted loop — `for i in range(n)` — never risks
        the interpreter's recursion-depth limit the way a hand-written
        Lumen recursive counter (std/collections.lum's old private
        `range_acc` helper, before 2.0) did for any n in the high hundreds.
        Shared, exact logic for both backends — see VM._exec_one's `range`
        CALL_BUILTIN case, which calls this same method."""
        if len(args) == 1:
            n = args[0]
            if not isinstance(n, int) or isinstance(n, bool):
                raise LumenError(f"TypeError: range() expects an Int, got {type(n).__name__}")
            return list(range(n))
        if len(args) == 2:
            start, end = args
            for v in (start, end):
                if not isinstance(v, int) or isinstance(v, bool):
                    raise LumenError(f"TypeError: range() expects Int arguments, got {type(v).__name__}")
            return list(range(start, end))
        raise LumenError(f"TypeError: range() expects 1 or 2 arguments, got {len(args)}")

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
                    raise LumenRaise("division by zero", code="R0005")
                return l / r
            if op == "%":
                if r == 0 and isinstance(r, (int, float)):
                    raise LumenRaise("modulo by zero", code="R0005")
                return l % r
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

    def _type_of(self, v):
        """The runtime type-name string for `type_of()` (standard-library.md
        §8.5.1). `bool` is checked before `int` because Python's `bool` is an
        `int` subclass — `isinstance(True, int)` is true, so Bool must be
        matched first or every Bool would be misreported as Int."""
        if v is None: return "Nothing"
        if isinstance(v, bool): return "Bool"
        if isinstance(v, int): return "Int"
        if isinstance(v, float): return "Float"
        if isinstance(v, str): return "Str"
        if isinstance(v, list): return "List"
        if isinstance(v, dict): return "Dict"
        if isinstance(v, LumenInstance): return v.struct_name
        if isinstance(v, EnumInstance): return v.enum_name
        if isinstance(v, TaskHandle): return "Task"
        if isinstance(v, LumenChannel): return "Channel"
        if isinstance(v, LumenModule): return "Module"
        return "Any"

    def stringify(self, v):
        if isinstance(v, bool): return "true" if v else "false"
        if isinstance(v, dict):
            return "{" + ", ".join(f"{self.stringify(k)}: {self.stringify(x)}" for k, x in v.items()) + "}"
        if isinstance(v, list): return "[" + ", ".join(self.stringify(x) for x in v) + "]"
        if isinstance(v, LumenInstance): return repr(v)
        if isinstance(v, EnumInstance): return repr(v)
        if v is None: return "nothing"
        return str(v)

def run_file(path, policy=None):
    """Runs a program on the reference interpreter. `policy` is the HOST's
    grant decision (see `Policy`); None means the empty policy — the program
    can compute and print, and nothing else."""
    import os
    with open(path) as f:
        src = f.read()
    ast, _resolver = analyze(src, os.path.basename(path), os.path.dirname(os.path.abspath(path)))
    Interpreter(base_dir=os.path.dirname(os.path.abspath(path)), policy=policy,
                label=os.path.basename(path)).run(ast)

def check_file(path):
    """Parses, resolves and type-checks `path` without running it. Returns the warnings (a program
    with warnings is still accepted); raises on the first error."""
    with open(path) as f:
        src = f.read()
    warnings = []
    analyze(src, os.path.basename(path), os.path.dirname(os.path.abspath(path)), warnings)
    return warnings

def repl(policy=None):
    print(f"Lumen {LUMEN_VERSION} REPL — type Lumen statements, Ctrl-D to exit")
    interp = Interpreter(base_dir=".", policy=policy)
    resolver = Resolver()  # persists across lines — see Resolver.resolve_incremental
    prelude = list(_prelude_decls())      # the library enums, loaded once for the whole session
    resolver.resolve_incremental(prelude)
    interp.run(prelude)
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
            TypeChecker().check_program(prelude + ast)
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

    try:
        policy, args = parse_grant_flags(args)
    except ValueError as e:
        print(f"Error: {e}"); sys.exit(2)
    _audit_run(policy, "test", backend="vm" if "--vm" in args else "interpreter")
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
            names, run_one = _prepare_tests(path, use_vm, policy)
        except (SyntaxError, LumenError) as e:
            print(f"{path}: {failure_text(e)}")
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


def _prepare_tests(path, use_vm, policy=None):
    """Parses/resolves/type-checks/runs one file's top-level code (registering
    its tests), and returns (test_names, run_one(name) -> (passed, message)),
    dispatching to whichever backend was requested."""
    import os
    with open(path) as f:
        src = f.read()
    base_dir = os.path.dirname(os.path.abspath(path))
    ast, resolver = analyze(src, os.path.basename(path), base_dir)
    if use_vm:
        ir_program = IRBuilder(resolver.module_globals).build_program(ast)
        ir_errors = IRValidator().validate(ir_program)
        if ir_errors:
            raise LumenError("VM: internal compiler error — IR validation failed: " + "; ".join(ir_errors))
        program = Compiler().compile_program(ir_program)
        vm = VM(cap_ctx=CapabilityContext(policy, label=os.path.basename(path)))
        vm.base_dir = base_dir
        vm.file = os.path.basename(path)
        vm.run(program)
        names = list(program.test_entry.keys())
        return names, lambda name: vm.run_one_test(program, name)
    else:
        interp = Interpreter(base_dir=base_dir, policy=policy, label=os.path.basename(path))
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

USAGE = """usage: lumen <command> [options] [file.lum]

commands:
  run [--vm] [-O0|-O1|-O2] [FILE]   run a program (FILE defaults to the project entry in ./lumen.toml)
  check [FILE]                      parse, resolve and type-check without running (no permissions needed)
  test [--vm] [PATH]                run `test` blocks
  repl                              interactive prompt
  new NAME                          create a project
  install                           install dependencies from ./lumen.toml
  --version, --help

permissions (run, test, repl) — a program can do NOTHING outside itself unless you grant it.
A `use fs.read` line in a .lum file only DECLARES a need; these flags GRANT (docs/capabilities.md):
  --allow-read[=PATH,...]     read files / test whether a path exists        (fs.read)
  --allow-write[=PATH,...]    create, overwrite, append to, delete files     (fs.write)
  --allow-list[=PATH,...]     list directory contents                        (fs.list)
  --allow-fs[=PATH,...]       all three of the above
  --allow-net[=HOST[:PORT],...]   HTTP/HTTPS GET requests                    (net.http)
A flag with no '=' grants the capability everywhere; with '=' only for those paths/hosts.
There is deliberately no --allow-all.

  --ask                       (run only) show what the program declares and ask before running; needs a
                              terminal on stdin and stderr, adds only what you type, denies by default
  --audit-log=FILE            append one JSON line per permission decision to FILE (docs/security-model.md 6)
  --audit-level=all|denied    with --audit-log: record everything (default) or only denials and consent
Without --ask nothing is ever prompted for: a denied operation is an error."""


def _require_file(path):
    import os
    # exists-and-not-a-directory (not isfile: `lumen run /dev/stdin` reads a program from a pipe)
    if os.path.isdir(path) or not os.path.exists(path):
        print(f"Error: cannot read '{path}': {'is a directory' if os.path.isdir(path) else 'no such file'}")
        sys.exit(1)


LOST_TASK_GRACE = 0.1     # seconds the end of a program waits for tasks that are still running, to see them fail


def lost_task_failures(grace=LOST_TASK_GRACE):
    """The spawned tasks whose failure nobody observed: failed, and never `wait`ed on. A task still running
    when the program ends is given `grace` seconds (in all, not each) so that one that fails at once is seen
    deterministically; one that is still running after that is abandoned, as it always was, and not reported."""
    with _TASK_LOCK:
        pending = list(_OPEN_TASKS.values())
    deadline = datetime.now(timezone.utc).timestamp() + grace
    for t in pending:
        if not t.done.is_set():
            left = deadline - datetime.now(timezone.utc).timestamp()
            if left > 0:
                t.done.wait(left)
    return [t for t in pending if t.done.is_set() and t.error is not None and not t.observed]


def report_lost_task_failures(stream=None):
    """Prints, to standard error, every failure nobody `wait`ed for (errors.md §4.7). The exit status is
    not changed by it. Returns how many were reported."""
    lost = lost_task_failures()
    out = stream if stream is not None else sys.stderr
    for t in lost:
        print("Lumen: warning: a spawned task failed and nothing waited for it, so its failure would otherwise be lost:", file=out)
        for line in failure_text(t.error).split("\n"):
            print("  " + line, file=out)
        with _TASK_LOCK:
            _OPEN_TASKS.pop(id(t), None)
    return len(lost)


def main():
    args = sys.argv[1:]
    if not args:
        print(USAGE)
        sys.exit(1)
    cmd = args[0]
    if cmd in ("--help", "-h", "help"):
        print(USAGE)
        return
    if cmd in ("--version", "-v", "version"):
        print(f"Lumen {LUMEN_VERSION}")
        print(f"Language specification: {SPEC_VERSION}")
        print(f"Runtime: {RUNTIME_KIND}")
        return
    if "--ask" in args and cmd != "run":
        print("Error: --ask is only supported by 'run' (it shows a program's declared permissions before running it)")
        sys.exit(2)
    if cmd == "repl":
        try:
            policy, _ = parse_grant_flags(args[1:])
        except ValueError as e:
            print(f"Error: {e}"); sys.exit(2)
        _audit_run(policy, "repl")
        repl(policy); return
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
        _require_file(path)
        try:
            warnings = check_file(path)
        except (SyntaxError, LumenError) as e:
            print(failure_text(e)); sys.exit(1)
        for w in warnings:
            print(render_diagnostic(w))
        print(f"No errors found in {path}" + (f" ({len(warnings)} warning{'s' if len(warnings) != 1 else ''})" if warnings else ""))
        return
    # Permission flags (--allow-read=..., see parse_grant_flags) are the ONLY way a run
    # is granted anything; with none, the program gets the empty policy.
    try:
        policy, args = parse_grant_flags(args)
    except ValueError as e:
        print(f"Error: {e}"); sys.exit(2)
    ask = cmd == "run" and "--ask" in args
    if cmd == "run":
        use_vm = "--vm" in args
        opt_level = _parse_opt_level(args)
        rest = [a for a in args[1:] if a != "--vm" and a != "--ask" and not _is_opt_flag(a)]
        path = rest[0] if rest else _project_entry_or_die("run")
    else:
        use_vm = False
        opt_level = 0
        path = cmd  # backward-compatible: `lumen file.lum`
    _require_file(path)
    if ask:
        policy = _ask_for_permissions(policy, path)
    _audit_run(policy, "run", backend="vm" if use_vm else "interpreter", file=path)
    try:
        if use_vm:
            run_file_vm(path, opt_level=opt_level, policy=policy)
        else:
            run_file(path, policy=policy)
    except (SyntaxError, LumenError, LumenRaise) as e:
        print(failure_text(e))
        sys.stdout.flush()
        report_lost_task_failures()
        sys.exit(1)
    report_lost_task_failures()

def _audit_run(policy, command, **fields):
    if policy.audit is not None:
        policy.audit.record("run", command=command, grants=policy.grants_summary(), **fields)


def _is_tty(stream):
    try:
        return stream is not None and stream.isatty()
    except (ValueError, OSError):       # a closed stream is not a terminal
        return False


def _ask_for_permissions(policy, path):
    """`lumen run --ask`: the consent prompt (see request_consent). Needs a real terminal on BOTH stdin and stderr —
    a pipe, a file or a script cannot answer for a person — and otherwise refuses to run rather than guessing."""
    if not (_is_tty(sys.stdin) and _is_tty(sys.stderr)):
        print("Error: --ask needs an interactive terminal (stdin and stderr must both be terminals). "
              "A non-interactive run grants only what --allow-* flags say; it never prompts.")
        sys.exit(2)
    try:
        check_file(path)        # syntax / declaration errors are reported before anyone is asked anything
        declared = declared_capabilities(path)
    except (SyntaxError, LumenError) as e:
        print(str(e)); sys.exit(1)
    result = request_consent(policy, declared, os.path.basename(path), sys.stdin, sys.stderr)
    if result is None:
        print("lumen: cancelled — nothing was run", file=sys.stderr)
        sys.exit(1)
    return result


def _is_opt_flag(a):
    return a in ("-O0", "-O1", "-O2")

def _parse_opt_level(args):
    """`-O0` (default: no optimization — identity, every pre-Phase-4 test's
    behavior is unchanged unless a flag opts in), `-O1` (constant folding +
    dead-code elimination), `-O2` (+ short-circuit and dead-branch folding).
    Only affects `--vm` runs — see IROptimizer's docstring for why. Last flag
    wins if more than one is given; unrecognized -O values are a clean error
    rather than silently falling back to -O0."""
    levels = [a for a in args if a.startswith("-O")]
    if not levels:
        return 0
    last = levels[-1]
    if last not in ("-O0", "-O1", "-O2"):
        print(f"Error: unknown optimization flag '{last}' (expected -O0, -O1, or -O2)")
        sys.exit(1)
    return int(last[2])

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
