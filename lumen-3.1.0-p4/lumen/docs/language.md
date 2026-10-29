# docs/language.md

# Lumen Language Specification — Chapter 1: Language Overview

Status: **Normative.** This chapter is binding for Lumen 1.0 conforming implementations. Revised for
Language Specification **3.1** (Phase 4: Error Handling & Reliability); changes are marked *(3.1)*.
Publisher: Krish Kumar.

Every example in this chapter marked `lumen run` or `lumen error` is executed by `tests/test_docs.py` on the
reference interpreter, on the bytecode VM and on the VM at `-O2`; the output shown is the output produced.

## 1.1 Design Goals

Lumen is a dynamically-executed, gradually-typed, indentation-delimited language. Its
design goals, in priority order, are:

1. **Readability over brevity.** Block structure is expressed through indentation, not
   braces; statements are not terminated by a statement separator token.
2. **Gradual verification.** Type annotations are optional. Where present, they are
   checked in a static pass that runs to completion *before* any code executes. Absence
   of annotation is not absence of safety — it is an explicit `Any` type that is
   compatible with everything.
3. **Failures are explicit, and expected failures are values.** *(3.1; 1.0–3.0 said "exactly one
   runtime failure protocol".)* A failure that the caller is expected to handle — a string that is not a
   number, a record that is not there — is a **value**, `Result<T, E>` or `Option<T>`, that the static
   checker can follow and that `?` propagates. A failure that means something is broken — an index past the
   end, a wrong argument count, a failed `assert` — is an **exception**, interceptable by `try`/`catch`
   and cleaned up after by `finally`. Neither kind is ever lost silently: a discarded `Result` is a compile
   error, and an uncaught exception ends the program with a code and a stack trace. See Chapter 4
   (`errors.md`) for the full model.

```lumen run
fn parse_port(text) -> Result<Int, Str>:
    if text == "":
        return Err("empty")
    return Ok(len(text))

let items = [1, 2, 3]
print parse_port("")
try:
    print items[9]
catch e:
    print e.code + ": " + e.message
```
```output
Err(empty)
R0006: index 9 is out of range (length 3)
```

4. **Secure by default: explicit capabilities for I/O.** No program has file or network
   access by default. A file *declares* what it needs (`use fs.read`); the *host* grants it
   (`--allow-read=DIR`). Declaring never grants. See Chapter 7 (`security.md`) and
   `capabilities.md`.
5. **Small orthogonal core, no redundant forms.** One declaration keyword (`let`), one
   loop keyword (`for`; `while` was added in 2.2, §1.14), one function keyword (`fn`). There is no `const`,
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

Lumen has no `{ }` block delimiters. A block is introduced by a statement header
(optionally ending in `:` — see §1.13.1, *2.1*: the colon is accepted but no longer
required) followed by one or more lines indented strictly deeper than the header. The
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
| `while` | *(2.2)* Loop while a condition expression is truthy, re-evaluating the condition before every iteration, including the first (§1.14) | grammar.md |
| `fn` | Declare a named function, optionally generic (`fn f<T>(...)`) | types.md |
| `return` | Exit the enclosing function immediately with a value; `return` with no expression is not valid grammar — a bare `return` is not permitted, every `return` must carry an expression | grammar.md |
| `true` / `false` | The two `Bool` literals | types.md |
| `try` / `catch` | Runtime-failure interception (§errors.md) | errors.md |
| `finally` | *(3.1)* A block that runs exactly once however the `try` ends — normal end, `return`, `?`, or a failure (`errors.md` §4.6). `return` and `?` are not allowed inside it | errors.md |
| `struct` | Declare a nominal record type with an exact, unordered field set | types.md |
| `enum` | Declare a nominal tagged union with unit or payload-carrying variants | types.md |
| `and` / `or` / `not` | Short-circuiting boolean operators (§1.10 precedence table) | language.md |
| `use` | Either *declare* a needed I/O capability (`use fs.read`, `use net.http`; declaring grants nothing — the host grants) or import a module (`use "path" as name`) | capabilities.md / modules.md |
| `as` | Introduces the local binding name in a module import (`use "path" as name`); also, *(2.1)* an alternative spelling for the `:` type-annotation separator in a `let`/`fn` param (`x as Int`, §1.13.1) | modules.md / §1.13.1 |
| `enum` variant construction is via ordinary call syntax; there is no separate keyword | — | types.md |
| `match` | Pattern-match an expression against an ordered list of variant/literal/wildcard/bind patterns | grammar.md |
| `spawn` | Start a function call on a new OS thread, returning a task handle | concurrency.md |
| `test` | *(Language Specification 1.1)* Declare a named test; the body does not execute as part of ordinary program flow, only under `lumen test` | testing.md |
| `returns` | *(2.1)* Alternative spelling for the `->` return-type arrow in a `fn` header (§1.13.1) | §1.13.1 |
| `is` | *(2.1)* Alternative spelling for the `:` that associates a key/field with its value in a dict or struct literal (§1.13.1) | §1.13.1 |

Additionally, the following names are **not reserved keywords** but are bound in the
global namespace by the runtime and SHOULD NOT be shadowed by user declarations (doing
so is legal — Lumen has no keyword-vs-builtin protection — but shadows the built-in for
the remainder of the enclosing scope): `len, push, keys, values, read_file, write_file,
http_get, fail, wait, chan, send, receive, assert, assert_eq, assert_ne, now, read_line,
range, chr, ord, type_of, append_file, file_exists, is_dir, list_dir, delete_file`, and *(3.1)* `fail_with, ok_or, unwrap, unwrap_or, is_ok, is_err, is_some, is_none` together with the variants `Some`, `None`, `Ok`, `Err` (a program that declares one of those names keeps its own meaning — `errors.md` §4.13). See
`standard-library.md` for their exact signatures.

## 1.8 Known v1.0 Lexical Limitations (frozen, not a defect)

These are intentional simplicity trade-offs for the 1.0 freeze, not omissions:

- No string escape sequences and no way to embed `"` **directly in a source
  literal**. This remains true at the lexical level — the grammar is
  unchanged — but as of Phase 3's `chr`/`ord` builtins (`standard-library.md`)
  a program can build a `"` character at runtime (`chr(34)`) and compose it
  into a string value however it needs to; `std/json.lum`'s encoder does
  exactly this. So the limitation is real but no longer a dead end.
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
- *(3.1)* In `expr?`, `expr` is fully evaluated first. If it is `Ok(v)` or `Some(v)` the whole expression is
  `v`, and evaluation continues. If it is `Err(e)` or `None`, evaluation of the **enclosing expression stops
  there** — nothing to the right of the `?`, and no later argument or operand, is evaluated — and the
  enclosing function returns that value unchanged, after running any `finally` block between the `?`
  and the function's end, innermost first. This is the same on both engines (`errors.md` §4.2.3, §4.6).

```lumen run
fn trace(label, value):
    print label
    return value

fn run() -> Result<Int, Str>:
    let total = trace("first", Ok(1))? + trace("second", Ok(2))? + trace("third", Err("stop"))? + trace("never", Ok(4))?
    return Ok(total)

print run()
```
```output
first
second
third
Err(stop)
```

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
| 8 (highest) | `.` `[...]` `(...)` `?` | left | Field access, indexing, call, and *(3.1)* the postfix `?`, chained left to right: `a.b[0].c(1)`, `wrap(a)?.value` |

There are no bitwise operators, no `**`/exponent operator, and no assignment operators
other than `=` (there is no `+=`, `-=`, etc. — see §1.10.1).

*(3.1)* **`?`** is a postfix operator at the highest level: it applies to the call, name, field access or
index immediately before it, so `-half(n)?` is `-(half(n)?)` and `wrap(a)?.value` is `(wrap(a)?).value`.
In paren-free application (§1.13.2) a `?` after an argument — `f x?` — would be ambiguous (does it apply to `x`
or to the call?) and is a syntax error; write `f(x)?` or `(f x)?`.

```lumen run
fn half(n) -> Result<Int, Str>:
    if n % 2 != 0:
        return Err("odd")
    return Ok(n / 2)

struct Box:
    value

fn wrap(n) -> Result<Box, Str>:
    return Ok(Box { value: n })

fn go(n) -> Result<Int, Str>:
    let a = -half(n)?
    let b = wrap(a)?.value
    return Ok(b)

print go(8)
print go(7)
```
```output
Ok(-4.0)
Err(odd)
```

*(2.1)* **Paren-free function application** (`f x y`, §1.13.2) is not in this table
because it has no operator symbol, but it binds tighter than every row above: an
application's arguments are read at atom precedence only (a literal, a name, or an
explicitly bracketed/parenthesized group — never a binary-operator expression), so
`f x + y` parses as `(f x) + y`, not `f(x + y)`, exactly as in ML/Haskell. A compound
expression used as one argument needs its own parens: `f x (y + 1)`.

### 1.10.1 Assignment is a statement, not an expression

`let name = expr` (declare) and bare `name = expr` (reassign) are two different
statement forms — `Let` and `Assign` respectively — with different scoping
behavior; see §1.11.1 for the distinction (corrected, breaking, in 2.0 — prior
to that both spellings were parsed identically, which was the bug §1.11.1
documents). Neither form is an expression: assignment cannot appear inside one
(`x = (y = 5)` is a syntax error), and there are no compound assignment
operators (no `+=`, `-=`, etc.).

## 1.11 Scoping Rules

> **Corrected in Language Specification 2.0 (breaking change).** Specs 1.0 and 1.1
> documented a "Rule B" under which bare reassignment (`x = 2`, no `let`) silently
> shadowed an outer-scope variable instead of mutating it — meaning the single most
> common pattern in programming, accumulating into a variable from inside a loop
> (`total = total + n`), silently computed nothing, with no error. That was a genuine
> design mistake, not a deliberate tradeoff, found by writing exactly that program.
> 2.0 fixes it: `let` and bare reassignment are now two different statements with
> different, conventional semantics (§1.11.1). A 1.x program that relied on the old
> shadowing behavior — using `x = 2` inside a nested block and expecting the outer
> `x` to stay untouched — is the one thing this breaks; every other 1.x program keeps
> its exact observable behavior, and programs that hit the old bug now simply work.

Lumen scoping is **lexical and block-structured**, with three rules that MUST be
understood together, as they interact non-obviously:

**Rule A — every block introduces a new child scope.** The body of `if`, `else`, `for`,
`while`, `try`, and `catch` each execute in a fresh scope chained to the scope that
contains the header — for `while`, a new scope is created on **every** iteration (see
§1.14), the same as `for`. Reads walk outward through parent scopes until a binding is
found or the global scope is exhausted (raising `NameError`, see `errors.md`).

### 1.11.1 Rule B — `let` declares, bare reassignment assigns (these are different statements)

**`let name = expr` always writes into the *current* (innermost) scope, creating a
NEW binding there — shadowing any same-named binding in an outer scope, for the
remaining duration of that block.** This half of the old rule is unchanged:

```lumen
let x = 1
if true:
    let x = 2    # a NEW, shadowing binding, local to the if-block
    print x      # -> 2
print x          # -> 1  (outer x untouched — a different `x` was declared, not assigned)
```

**Bare `name = expr` (no `let`) is a different statement: it requires `name` to
already be declared somewhere in the enclosing scope chain, and mutates it AT THE
SCOPE WHERE IT'S ALREADY BOUND** — walking outward from the innermost scope exactly
the way a read does, and writing to the first scope that already has the name,
rather than always declaring fresh in the innermost scope:

```lumen
let x = 1
if true:
    x = 2        # finds and mutates the EXISTING outer x — no new binding created
    print x      # -> 2
print x          # -> 2  (the same x was mutated)
```

```lumen
let total = 0
for n in [1, 2, 3, 4, 5]:
    total = total + n   # mutates the outer `total` on every iteration
print total              # -> 15
```

Assigning to a name that was never declared with `let` anywhere visible is a
compile-time `NameError` (same static check `let`/ordinary reads already get —
see `errors.md` §4.9), not an implicit declaration: `nope = 5` with no prior
`let nope` fails to resolve, rather than silently creating `nope`.

Mutating a data structure's *contents* (a `List` or `Dict` element, or a `struct`
field) was never affected by any of this — the container value itself, not a
variable binding, is what's mutated, in both v1.x and 2.0:

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

Lumen has a single binding form. There is no `const`, no `var`/`let` distinction,
and no compiler-enforced immutability of any kind. Every binding is reassignable —
from the scope that declared it, or from any nested block, per §1.11.1's assignment
rule. This is a deliberate simplification, not an oversight: introducing a second,
immutable binding form is an additive, backward-compatible extension reserved for a
future minor version (see `modules.md` §5.6 for the evolution process) and remains
out of scope for now.

## 1.13 Low-Punctuation Syntax (Language Specification 2.1)

> **Added in 2.1 — additive, not breaking.** Every form described in §1.1–§1.12 above
> still parses and means exactly what it always did. Nothing described in this section
> removes or changes a 1.0–2.0 program's behavior; it only makes additional spellings
> of the same programs legal. The motivation was direct user feedback that writing
> Lumen involved too much small, low-value punctuation ceremony (a colon here, a comma
> there) relative to what it was actually saying — a complaint distinct from, but
> related to, the ergonomics work in 2.0 (`CHANGELOG.md`). A conforming 2.1
> implementation MUST accept both the old and new spelling of every form below,
> freely mixed, including within the same file or even the same line.

### 1.13.1 Optional colons, optional commas

**The `:` before a block body is now optional.** Every block-opening header —
`if`/`else`, `for ... in ...`, `fn ...`, `match ...`/each `case` pattern, `try`/
`catch ...`, `struct ...`, `enum ...`, `test "..."` — may end with `:` or with
nothing at all; indentation alone is sufficient to open the block either way:

```lumen
if x > 0:        # both spellings are the same AST node — this is NOT sugar
    print "a"     # for something subtly different, it's literally identical
if x > 0
    print "a"
```

A `let`/`fn`-parameter type annotation may use `:` or the keyword `as`,
interchangeably: `let x: Int = 5` and `let x as Int = 5` are identical; likewise
a `fn` parameter's type (`fn f(x: Int)` / `fn f(x as Int)`). A `fn`'s return type
may use `->` or the keyword `returns`, interchangeably: `fn f() -> Int` and
`fn f() returns Int` are identical.

**The `,` between items of a list literal, a call's arguments, a `fn` parameter
list, and a dict/struct literal's entries is now optional.** Items may be
separated by `,`, by nothing at all (just whitespace or a newline), or a mix of
both in the same literal:

```lumen
let nums = [1 2 3 4 5]        # identical to [1, 2, 3, 4, 5]
fn add a b                    # identical to fn add(a, b) — parens optional too,
    return a + b               # see §1.13.2
print add 2 3                 # or: print add(2, 3)
```

A **trailing comma is now accepted** everywhere a comma is accepted, for the same
reason: once the comma is purely optional, treating one last comma right before
the closing bracket as an error would be an arbitrary exception to that, not a
useful check. `[1, 2,]` and `f(1, 2,)` both parse exactly like the version with
no trailing comma. (This was a documented 1.0–2.0 rule — a trailing comma was a
syntax error — that 2.1 relaxes; it tightens nothing that used to be valid.)

A dict-literal or struct-literal entry's `key: value` / `field: value` separator
may use `:` or the keyword `is`, interchangeably: `{"a": 1}` and `{"a" is 1}` are
identical, as are `Point { x: 1, y: 2 }` and `Point { x is 1 y is 2 }`.

**`<T>` around a `fn`'s explicit generic type-parameter list is still accepted**
but is no longer the only way to declare one — see §1.13.3.

### 1.13.2 Paren-free function calls (application)

A call to a declared `fn` or a built-in function no longer needs parentheses at
all: `name arg1 arg2 ...` is a call, read exactly like ML/Haskell function
application — each argument is parsed at **atom precedence only** (a number,
string, `true`/`false`, a name, or an explicitly bracketed/parenthesized group),
never a binary-operator expression, and the call keeps consuming arguments for as
long as the next token could start one. This is why `f x + y` means `(f x) + y`:
`+` cannot start an atom, so application stops there and the `+` is read at its
normal (much lower) precedence, outside the call. A compound expression used as
one argument needs its own parens, which simultaneously is the *only* remaining
job parentheses have in a call: grouping an argument into a single atom.

```lumen
fn add a b
    return a + b

print add 2 3          # add(2, 3)
print add 2 3 + 1       # (add(2, 3)) + 1 -- NOT add(2, 3 + 1)
print add 2 (3 + 1)     # add(2, 4) -- the compound argument needs parens
```

This applies equally to constructing an enum variant with a compound-expression
payload (`return Some (n / 2)`, not `return Some n / 2`, which would parse as
`(Some n) / 2`) and to a `match` arm's variant pattern, which may bind its
payload fields bare instead of parenthesized: `Some v` instead of `Some(v)`.
Likewise, an `enum` **declaration**'s variant payload fields may be written bare:

```lumen
enum Opt
    Some value   # identical to Some(value)
    None

match half 10
    Some v        # identical to Some(v):
        print "got" v
    None
        print "nothing"
```

**Exception — a bare multi-argument call needs parens when it's one of several
sibling items** in a comma-optional list (a list literal's items, a call's own
arguments, a dict/struct literal's keys or values): `[add 2 3]` is unambiguous (a
1-item list holding one call) but `[add 2 3 10]` is not — it cannot tell whether
you mean a 1-item list (`add` called with three arguments) or a 2- or 3-item list
(`add` called with fewer, plus more sibling numbers). Rather than guess, this is
a clean, immediate `SyntaxError` naming the fix:

```lumen
let xs = [add 2 3]        # fine -- the call is the list's only item
let ys = [(add 2 3) 10]   # fine -- parens mark exactly where the call ends
let zs = [add 2 3 10]     # SyntaxError: 'add' looks like a multi-argument call
                            # here, but that needs parens when it's one of
                            # several items -- write (add 2 3 10) or similar
```

A call with **zero** arguments still requires explicit `()` — `now()`,
`read_line()` — paren-free application only ever adds arguments to a call that
already has at least one; a bare name alone (no parens, no following atoms) is
always a plain variable reference (or, for a name with no `let` binding, a
`NameError` — functions are still not first-class values, §3.10 of `types.md`,
unchanged by any of this).

### 1.13.3 Implicit (auto) generics

A `fn`'s `<T, U, ...>` explicit generic-parameter list is now optional. If it's
omitted, every parameter/return type name that isn't one of the built-in concrete
types (`Int Float Str Bool List Dict Any`) is automatically treated as an
implicit generic placeholder — checked exactly as permissively as an explicitly
declared one (`types.md` §3.8: generics are not checked inside function bodies
either way). This can only make a program that used to be a `TypeError` (a
"concrete" type name that was really meant generically) into one that type-checks
instead — it can never reject a program the old, explicit-`<T>`-only rule
accepted.

*(3.1, one narrow exception.)* A name that the program **declares** as a `struct` or `enum` — and `Option`
and `Result`, which every program has — is that type, not a placeholder. `fn area(s: Circle)` now requires a
`Circle` (E0201 for anything else); before 3.1 `Circle` there was a placeholder that accepted every value.
A name that is none of those is still an implicit generic:

```lumen run
fn identity x as T returns T
    return x

print identity 5
print identity "five"
```
```output
5
five
```

```lumen error E0201
struct Circle:
    r: Int

struct Square:
    side: Int

fn area(s: Circle) -> Int:
    return 3 * s.r * s.r

print area(Square { side: 2 })
```
```diagnostic
TypeError at line 10: 'area' argument 1 expects Circle but got Square
  --> example.lum:10:12
     |
  10 | print area(Square { side: 2 })
     |            ^^^^^^^^^^^^^^^^^^
  = code: E0201
  = expected: Circle
  = found: Square
```


```lumen
fn identity<T>(x: T) -> T:     # 1.0–2.0 spelling, still works
    return x

fn identity x as T returns T   # 2.1: T is auto-generic -- no <T> needed
    return x
```

### 1.13.4 `print` with multiple values

`print` accepts more than one space-separated value on the same line, displaying
them joined by a single space — exactly as if you had written `a + " " + b + " "
+ c` yourself, reusing `+`'s existing string-coercion rule (`standard-library.md`
§8.3) rather than introducing a new display mechanism:

```lumen
print "total:" total        # prints "total: 15", same as print "total: " + total
print "got" v "from" n       # prints e.g. "got 5 from 10"
```

This is unambiguous because an ordinary expression (`print a + b`) is already
parsed fully greedily — every binary operator it can absorb, it does — so
whatever token is left over on the line literally cannot bind to the first
expression via any operator in the grammar; it can only be the start of a new
value to print.

## 1.14 The `while` Statement (Language Specification 2.2)

> **Added in 2.2 — additive, not breaking.** Nothing in §1.1–§1.13 changes;
> `while` is a new statement form alongside the existing `for`, not a
> replacement for it. `for` remains the right choice whenever a loop is
> driven by an existing collection, range, or string; `while` is for the
> remaining case `for` cannot express — looping on an arbitrary boolean
> condition that isn't naturally "iterate over a sequence" (e.g. a
> cursor-based parser advancing through a string one character at a time
> until it reaches a stopping condition, or any loop whose length isn't
> known up front).

```lumen
while condition:
    body
```

`condition` is evaluated, and if `truthy` (`errors.md`'s truthiness rule — the
same one `if` uses), `body` executes once, in a **fresh scope** (per §1.11
Rule A), and then `condition` is evaluated again. This repeats for as long as
`condition` keeps evaluating truthy. `condition` is evaluated **before every
iteration, including the first** — a `while` loop whose condition is false the
very first time it is checked runs zero iterations, exactly like `for x in []`:

```lumen
let i = 10
while i < 5:
    print "never prints"
print "done"      # the only output: "done"
```

A new, per-iteration scope (not one shared scope for the whole loop) means a
`let` inside the body is freshly (re)declared on every pass, exactly as for a
`for` loop's body — see §1.11 Rule A. Reassigning an outer-scope variable
(Rule B, bare `name = expr`, no `let`) is the normal way to carry state from
one iteration to the next, since a fresh `let` would simply re-initialize that
state every time instead of accumulating it:

```lumen
let total = 0
let i = 1
while i <= 5:
    total = total + i   # mutates the OUTER total — carries forward each pass
    i = i + 1             # likewise for the loop counter itself
print total                # -> 15
```

**There is no `break` or `continue`.** This is the same documented, deliberate
gap `for` already has (`PERFORMANCE_HARDENING_STATUS.md`'s known-gaps list) — not an oversight
specific to `while`. The only ways to leave a `while` loop early are: making
its own condition false (the ordinary, structured way), or a `return` that
unwinds the entire enclosing function. Consequently, `while true:` with no
`return` anywhere inside its body is an infinite loop with no way out — write
the actual stopping condition into `while`'s own condition expression instead
of relying on an early exit that does not exist.

Both low-punctuation spellings from §1.13 apply to `while` exactly as they do
to every other block-opening header: the `:` before the body is optional
(`while condition` / `while condition:` are identical), since `while` was
included in §1.13.1's "every block-opening header" list from the day it was
added.

`while`'s condition is an ordinary expression, so §1.9's evaluation-order and
short-circuiting rules apply to it unchanged — `while a() and b():` does not
evaluate `b()` when `a()` is already falsy, on every pass, the same as it
would inside an `if`.

---
