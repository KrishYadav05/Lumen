# docs/language.md

# Lumen Language Specification — Chapter 1: Language Overview

Status: **Normative.** This chapter is binding for Lumen 1.0 conforming implementations.

## 1.1 Design Goals

Lumen is a dynamically-executed, gradually-typed, indentation-delimited language. Its
design goals, in priority order, are:

1. **Readability over brevity.** Block structure is expressed through indentation, not
   braces; statements are not terminated by a statement separator token.
2. **Gradual verification.** Type annotations are optional. Where present, they are
   checked in a static pass that runs to completion *before* any code executes. Absence
   of annotation is not absence of safety — it is an explicit `Any` type that is
   compatible with everything.
3. **Errors as values, uniformly.** There is exactly one runtime failure protocol.
   Every runtime failure — whether an "expected" condition (division by zero, an
   out-of-range index) or a programmer mistake (calling an undefined function, a wrong
   argument count) — surfaces the same way and is interceptable by the same construct
   (`try`/`catch`). See Chapter 4 (`errors.md`) for the full model.
4. **Explicit capability grants for I/O.** No program has file or network access by
   default. See Chapter 6 (`security.md`).
5. **Small orthogonal core, no redundant forms.** One declaration keyword (`let`), one
   loop keyword (`for`), one function keyword (`fn`). There is no `while`, no `const`,
   no ternary operator, no `elif` (use nested `if`/`else`), no multiple return values.

## 1.2 Source Text

- Source files are UTF-8 text with the extension `.lum`.
- A source file is a sequence of lines. Line terminator is `\n`; `\r\n` is not
  normalized by the reference lexer and MUST NOT appear in conforming source files.
- There is no file-level or block comment form; only line comments (§1.4).

## 1.3 Lexical Grammar (token classes)

| Token class | Definition |
|---|---|
| `NUMBER` | `\d+(\.\d+)?` — an integer literal has no `.`; a float literal has exactly one `.` with at least one digit on each side. There is no exponent notation (`1e10` is not a valid `NUMBER`), no hex/octal/binary literal forms, and no digit-group separators. |
| `STRING` | `"[^"]*"` — a double-quoted run of any characters except `"`. **There is no escape-sequence mechanism.** A backslash inside a string literal is an ordinary character, not an escape introducer: the source `"a\nb"` denotes the four-character-plus payload literal backslash, `n`, not a newline. A string literal **cannot contain a `"` character**, embedded or otherwise — there is no way to escape a quote inside a Lumen string literal in v1.0. This is a known, deliberate v1.0 limitation (see §1.8). |
| `NAME` | `[A-Za-z_][A-Za-z0-9_]*` — identifiers are case-sensitive. There is no length limit. |
| `OP` | One of: `== != <= >= -> + - * / % ( ) [ ] { } , : < > = .` |
| `NEWLINE` | A literal `\n`, significant as a statement separator (§1.5). |
| `COMMENT` | `#` through end of line. Discarded by the lexer; does not produce a token. |

Whitespace (space, tab) between tokens on the same line is insignificant and discarded,
**except** for leading whitespace at the start of a line, which is significant (§1.5).

## 1.4 Comments

```lumen
# this is a comment — runs to end of line
let x = 5   # trailing comments are permitted
```

There is no multi-line comment syntax.

## 1.5 Indentation and Block Structure

Lumen has no `{ }` block delimiters. A block is introduced by a statement header ending
in `:` followed by one or more lines indented strictly deeper than the header. The
reference lexer computes indentation as the count of **leading space characters**
(`raw.lstrip(" ")`); **leading tabs are not counted as indentation** and their use in
leading whitespace is undefined behavior in v1.0 — conforming source files MUST use
spaces exclusively for indentation. Blank lines and comment-only lines do not affect
the indentation stack and may appear at any indentation level.

A block ends at the first subsequent line whose indentation is less than or equal to
the block's opening indentation (a `DEDENT`). Indentation levels between the levels
already on the indent stack are illegal and MUST be rejected by the parser as a syntax
error (this is enforced structurally by the lexer's indent-stack discipline; the
reference implementation raises a syntax error on any token that cannot be reduced by
the stack, rather than silently accepting inconsistent indentation).

### 1.5.1 Bracket-aware suppression (added in Language Specification 1.1)

While an unclosed `(`, `[`, or `{` is open, indentation is not significant and a
line break does not produce a `NEWLINE` token — exactly like Python:

```lumen
let point = {
    "x": 1,
    "y": 2,
}
```
is one logical statement. Purely additive relative to 1.0 (every such literal was
a syntax error before; this only makes more programs valid). Ordinary line-by-line
indentation resumes once bracket depth returns to zero.

## 1.6 Statement Separation

Statements are separated by `NEWLINE` tokens; there is no `;` statement terminator and
`;` is not a legal character anywhere in Lumen source. Multiple statements MUST NOT
appear on one physical line. Blank lines between statements are permitted and ignored.

## 1.7 Keyword Index

The following identifiers are reserved and cannot be used as a variable, function,
struct, enum, or module-alias name. Every keyword's semantics are defined normatively
in the chapter listed.

| Keyword | Meaning | Defined in |
|---|---|---|
| `let` | Bind or rebind a name in the current block scope (§1.9, §1.10) | language.md |
| `print` | Evaluate an expression, convert to display form (§types.md §2.7), write it plus a trailing newline to standard output | language.md |
| `if` / `else` | Conditional branch | grammar.md |
| `for` / `in` | Iterate a `List`'s elements, a `Dict`'s **keys** (not its values — `for k in some_dict` binds `k` to each key in insertion order; to also access the value, index with `some_dict[k]` inside the loop body), or a `Str`'s individual characters (each yielded as a length-1 `Str`), in order | grammar.md |
| `fn` | Declare a named function, optionally generic (`fn f<T>(...)`) | types.md |
| `return` | Exit the enclosing function immediately with a value; `return` with no expression is not valid grammar — a bare `return` is not permitted, every `return` must carry an expression | grammar.md |
| `true` / `false` | The two `Bool` literals | types.md |
| `try` / `catch` | Runtime-failure interception (§errors.md) | errors.md |
| `struct` | Declare a nominal record type with an exact, unordered field set | types.md |
| `enum` | Declare a nominal tagged union with unit or payload-carrying variants | types.md |
| `and` / `or` / `not` | Short-circuiting boolean operators (§1.10 precedence table) | language.md |
| `use` | Either grant an I/O capability (`use fs`, `use net`) or import a module (`use "path" as name`) | security.md / modules.md |
| `as` | Introduces the local binding name in a module import | modules.md |
| `enum` variant construction is via ordinary call syntax; there is no separate keyword | — | types.md |
| `match` | Pattern-match an expression against an ordered list of variant/literal/wildcard/bind patterns | grammar.md |
| `spawn` | Start a function call on a new OS thread, returning a task handle | concurrency.md |
| `test` | *(Language Specification 1.1)* Declare a named test; the body does not execute as part of ordinary program flow, only under `lumen test` | testing.md |

Additionally, the following names are **not reserved keywords** but are bound in the
global namespace by the runtime and SHOULD NOT be shadowed by user declarations (doing
so is legal — Lumen has no keyword-vs-builtin protection — but shadows the built-in for
the remainder of the enclosing scope): `len, push, keys, values, read_file, write_file,
http_get, fail, wait, chan, send, receive`. See `standard-library.md` for their exact
signatures.

## 1.8 Known v1.0 Lexical Limitations (frozen, not a defect)

These are intentional simplicity trade-offs for the 1.0 freeze, not omissions:

- No string escape sequences and no way to embed `"` in a string literal.
- No multi-line string literal form.
- No numeric literal forms beyond decimal integer and decimal float (no hex/octal/
  binary/exponent/underscore-grouped literals).
- Indentation must use spaces; tab-indentation is undefined behavior.

A post-1.0 proposal MAY introduce escape sequences and additional numeric literal
forms; both are additive changes to the lexer's `STRING`/`NUMBER` token patterns and do
not require grammar changes elsewhere, so they are compatible with the evolution
process in `modules.md` §5.6.

## 1.9 Evaluation Order

Evaluation is strict (eager) and left-to-right, uniformly:

- In a binary expression `a OP b`, `a` is fully evaluated, then `b` is fully evaluated,
  then `OP` is applied. There is no short-circuit evaluation for any operator **except**
  `and` and `or`, which short-circuit as specified in §1.10.
- In a call `f(a, b, c)`, arguments are evaluated left to right before the call target
  is invoked.
- In a list literal `[a, b, c]` and a dict literal `{k1: v1, k2: v2}`, entries are
  evaluated left to right, keys before their corresponding values.
- Function calls that use `spawn` are the sole exception: `spawn f(a, b)` evaluates `a`
  and `b` in the calling thread (left to right, as normal), then hands the call off to
  a new thread; the *body* of `f` executes concurrently with the caller (see
  `concurrency.md`).

## 1.10 Operator Precedence and Associativity

The following table is exhaustive: every operator in Lumen 1.0 appears in it. Rows are
ordered from **lowest to highest** binding precedence. All binary operators are
left-associative unless noted.

| Level | Operators | Associativity | Notes |
|---|---|---|---|
| 1 (lowest) | `or` | left | short-circuit: right operand not evaluated if left is truthy |
| 2 | `and` | left | short-circuit: right operand not evaluated if left is falsy |
| 3 | `not` | right (prefix, unary) | `not not x` is legal and evaluates `x`'s negation twice |
| 4 | `== != < > <= >=` | left, **non-chaining** | `a < b < c` parses as `(a < b) < c` — the boolean result of `a < b` is compared against `c`. Lumen does NOT support Python-style chained comparisons. This is frozen v1.0 behavior. |
| 5 | `+ -` (binary) | left | `+` is overloaded: numeric addition, or string concatenation if either operand is a `Str` (§types.md §2.6) |
| 6 | `* / %` | left | `/` always produces a `Float` (true division), even for two `Int` operands. `%` follows the host modulo convention: result takes the sign of the divisor. `*` is also defined for `List`/`Str` repetition (`[1,2] * 3`, `"ab" * 3`) — see `types.md` §3.6.1 for the complete operator/coercion table. |
| 7 | `-` (unary prefix) | right | Parsed as `0 - x`; there is no unary `+`. |
| 8 (highest) | `.` `[...]` `(...)` | left | Field access, indexing, and call, chained left to right: `a.b[0].c(1)` |

There are no bitwise operators, no `**`/exponent operator, and no assignment operators
other than `=` (there is no `+=`, `-=`, etc. — see §1.10.1).

### 1.10.1 Assignment is a statement, not an expression

`name = expr` and `let name = expr` are both parsed as the identical statement form
(`Let`); Lumen has no separate "declare" vs. "reassign" grammar production, and no
compound assignment operators. Assignment cannot appear inside an expression (`x = (y
= 5)` is a syntax error).

## 1.11 Scoping Rules

Lumen scoping is **lexical and block-structured**, with two rules that MUST be
understood together, as they interact non-obviously:

**Rule A — every block introduces a new child scope.** The body of `if`, `else`, `for`,
`try`, and `catch` each execute in a fresh scope chained to the scope that contains
the header. Reads walk outward through parent scopes until a binding is found or the
global scope is exhausted (raising `NameError`, see `errors.md`).

**Rule B — `let` (and bare reassignment, which is the same statement — §1.10.1) always
writes into the *current* (innermost) scope, never an outer one, even if a
same-named binding already exists in an outer scope.** Consequently:

```lumen
let x = 1
if true:
    x = 2        # creates a NEW binding for x in the if-block's scope
    print x      # -> 2
print x          # -> 1  (outer x was never touched)
```

This means Lumen has no in-place mutation of an outer-scope variable from within a
nested block in v1.0 — every block-local `let`/assignment shadows. This is frozen,
deliberate v1.0 behavior (it removes an entire class of "did this accidentally mutate
the outer variable" bugs at the cost of no way to intentionally do so from a nested
block). Mutating a data structure's *contents* (a `List` or `Dict` element, or a
`struct` field) from a nested block DOES persist outward, because the container
value itself — not the variable binding — is what's mutated:

```lumen
let box = [0]
if true:
    box[0] = 2   # mutates the List's contents, not the `box` binding itself
print box[0]      # -> 2
```

**Rule C — function bodies do not close over the caller's local scope.** A function's
body executes in a scope chained *directly to the global (module top-level) scope* plus
its own parameters — never to the lexical scope of whatever block the `fn` statement
textually appears in. Consequently, a function defined inside another function cannot
see the outer function's local `let` bindings:

```lumen
fn outer():
    let y = 5
    fn inner():
        return y      # NameError: 'y' is not defined — inner() cannot see outer()'s y
    return inner()
```

Top-level (global, module-scope) `let` bindings ARE visible from every function body,
because every function's scope chains to the global scope directly. This is the only
form of "closure" Lumen 1.0 functions have: closure over globals, not over arbitrary
enclosing locals.

## 1.12 Mutability

Lumen 1.0 has a single binding form. There is no `const`, no `var`/`let` distinction,
and no compiler-enforced immutability of any kind. All bindings are reassignable
within their own scope (subject to the shadowing rule in §1.11). This is a deliberate
v1.0 simplification, not an oversight: introducing a second, immutable binding form is
an additive, backward-compatible extension reserved for a future minor version (see
`modules.md` §5.6 for the evolution process) and is explicitly out of scope for the 1.0
freeze.

---
