# docs/errors.md

# Lumen Language Specification — Chapter 4: Error-Handling Model

Status: **Normative.** Revised for Language Specification **3.1** (Phase 4: Error Handling & Reliability).
Publisher: Krish Kumar.

Every example in this chapter marked `lumen run` or `lumen error` is executed by
`tests/test_docs.py` on the reference interpreter, on the bytecode VM and on the VM at `-O2`; the
output shown is the output produced. An example that stopped being true would fail the build.

## 4.1 Two-Phase Failure Model

Lumen has exactly two, non-overlapping phases in which a program can fail. They use
different mechanisms and are not to be confused:

1. **Static-phase failure** (before execution starts): a `SyntaxError` from the parser,
   or a `TypeError`/`NameError` from the static passes (`types.md` §3.6, name resolution). If
   either occurs, **the program does not execute at all** — not one statement runs. This
   phase has no recovery mechanism; `try`/`catch` is a runtime construct and cannot
   intercept a static-phase failure, because no code is executing yet when it occurs. Static
   failures carry a stable code (§4.11) and a source location (§4.12).
2. **Runtime-phase failure**: anything raised while statements are executing. Lumen gives this
   phase two mechanisms for two different situations — §4.2.

There is no third phase; Lumen has no separate "linking" or "loading" failure
class distinct from module resolution errors, which are themselves runtime-phase
(§4.7).

## 4.2 Three Situations, Two Mechanisms

*(Replaces the 1.0–3.0 text "Runtime Failures Are Values, Uniformly — There Is No Exception
Hierarchy". What it said about `try`/`catch` and `fail` is still true; what it said about having
**no** other mechanism is not, as of 3.1. The change is additive: every program that ran before
runs the same way — see §4.13 for the corners where it does not.)*

Lumen distinguishes three situations, and gives each its own tool.

| Situation | Examples | Tool |
|---|---|---|
| **Success** | an addition, a lookup that found its key | the value itself |
| **Expected failure** — the operation can fail during normal execution, and the caller is expected to handle that | a string that is not a number, a record that is not in the table, a file that is not there | a value: **`Result<T, E>`** or **`Option<T>`** (§4.2.2), propagated with **`?`** (§4.2.3) |
| **Exceptional failure** — a broken invariant, a programming error, something the operation's normal result type cannot reasonably express | an index past the end, a call with the wrong arguments, a violated `assert` | an exception: raised by the runtime or by **`fail`** / **`fail_with`**, handled by **`try`/`catch`**, cleaned up after by **`finally`** (§4.2.5, §4.6) |

Which situation an operation is in is part of *its* contract, not something the language decides:
a missing file is normally an expected failure, but the 1.x built-ins `read_file` and friends
predate `Result` and still raise (§4.4.8); a program that wants a `Result` from them writes the
wrapper shown in §4.15. Nothing about an *expected* failure is ever caught by
`try`/`catch` by accident — an `Err(...)` is an ordinary value, not an exception (§4.2.5).

### 4.2.1 What the compiler does for expected failures

Because expected failures are values, the static checker can help with them. It rejects, before the
program runs:

- a `match` on a `Result`, an `Option` or any `enum` that leaves a variant uncovered (E0301, §4.4.6);
- a `Result` that is computed and then thrown away (E0405) — a failure must not vanish silently;
- a function declared `-> Result<...>` / `-> Option<...>` that can reach its end without returning
  (E0406), which would quietly return `nothing`;
- a value of the wrong type placed in a `Result`/`Option` (E0201), with the types shown (§4.2.2);
- `?` used on something that is not a `Result`/`Option` (E0401), outside a function (E0402), on the
  wrong kind for the function (E0403), or with an error type the function does not return (E0404).

### 4.2.2 `Result<T, E>` and `Option<T>`

`Option` and `Result` are ordinary generic enums (`types.md` §3.4.2, §3.8) that every program has in
scope, defined in Lumen itself:

```lumen
enum Option<T>:
    Some(T)
    None

enum Result<T, E>:
    Ok(T)
    Err(E)
```

They are consumed with `match`, with `?`, and with the helper built-ins of §4.4.12. They print as
`Ok(1)`, `Err(bad)`, `Some(2)`, `None`, and compare by structure (`Ok(1) == Ok(1)` is `true`).

```lumen run
fn parse(text) -> Result<Int, Str>:
    if text == "":
        return Err("empty input")
    return Ok(len(text))

match parse("abc"):
    Ok(n):
        print "length " + n
    Err(why):
        print "failed: " + why
match parse(""):
    Ok(n):
        print "length " + n
    Err(why):
        print "failed: " + why
```
```output
length 3
failed: empty input
```

**Typing.** `Ok(5)` has the type `Result<Int, _>` — the success type is known, the error type is whatever
the context says (`_` in a message means "not determined here"). It fits any `Result<Int, E>`.
The type arguments of a `Result` or `Option` are checked wherever a type is declared: a `let`
annotation, a parameter, a return type, and a payload.

```lumen error E0201
let result: Result<Int, Str> = Ok("hello")
```
```diagnostic
TypeError at line 1: 'result' is declared as Result<Int, Str> but assigned a Result<Str, _> value
  --> example.lum:1:32
    |
  1 | let result: Result<Int, Str> = Ok("hello")
    |                                ^^^^^^^^^^^
  = code: E0201
  = expected: Result<Int, Str>
  = found: Result<Str, _>
```

The same check applies to the error side, to nested types (`Result<Option<Int>, Str>`), to values
passed as arguments and to values returned:

```lumen error E0201
fn load() -> Result<Int, Str>:
    return Err(404)
```
```diagnostic
TypeError at line 2: function 'load' declares -> Result<Int, Str> but returns a Result<_, Int> value
  --> example.lum:2:12
    |
  2 |     return Err(404)
    |            ^^^^^^^^
  = code: E0201
  = expected: Result<Int, Str>
  = found: Result<_, Int>
```

Typing is gradual (`types.md` §3.1): an unannotated function, parameter or `let` is `Any`, which is
compatible with every type in both directions, so a `Result` that flows through untyped code is
checked only where a type is written down. Using the wrong number of type arguments is E0202:

```lumen error E0202
let r: Result<Int> = Ok(1)
```
```diagnostic
TypeError at line 1: Result takes 2 type argument(s) but 1 were given — write it like Result<Int, Int>
  --> example.lum:1:8
    |
  1 | let r: Result<Int> = Ok(1)
    |        ^^^^^^^^^^^
  = code: E0202
```

**`Option<T>`** is the same idea for "there may be no value" without a reason: `Some(value)` or
`None`.

```lumen run
fn first_even(numbers) -> Option<Int>:
    for n in numbers:
        if n % 2 == 0:
            return Some(n)
    return None

fn describe(found: Option<Int>):
    match found:
        Some(n):
            print "found " + n
        None:
            print "none found"

describe(first_even([1, 3, 4, 5]))
describe(first_even([1, 3]))
```
```output
found 4
none found
```

A program that already declares its own `enum Option`/`enum Result`, a struct of that name, or a
function or variant called `Some`, `None`, `Ok` or `Err`, keeps its own meaning: the library enum is
simply not loaded (§4.13).

### 4.2.3 The `?` operator

`expr?` is **postfix**, binds like a call or a field access (tighter than every binary operator), and
means:

1. evaluate `expr` — **exactly once, and first**: everything the enclosing expression would do after
   this point has not happened yet;
2. if the value is `Ok(v)` or `Some(v)`, the value of `expr?` is `v`;
3. if it is `Err(e)` or `None`, the **enclosing function returns that same value, unchanged** at once.
   Whatever the function was in the middle of — the rest of the expression, the loops it was inside —
   is abandoned, and every `finally` block between the `?` and the function's end runs first, innermost
   first (§4.6).

```lumen run
fn read_pair(a, b) -> Result<Int, Str>:
    let x = parse_number(a)?
    let y = parse_number(b)?
    return Ok(x + y)

fn parse_number(text) -> Result<Int, Str>:
    if text == "1":
        return Ok(1)
    if text == "2":
        return Ok(2)
    return Err("not a number: " + text)

print read_pair("1", "2")
print read_pair("1", "x")
print read_pair("y", "x")
```
```output
Ok(3)
Err(not a number: x)
Err(not a number: y)
```

The second call never evaluates `Ok(x + y)`; the third never evaluates `parse_number(b)` at all.

**Where `?` is allowed.** Only inside a function body (a `?` returns from *a function*): not in top-level
code and not directly in a `test` block (E0402), and not inside a `finally` block (E0407), where a
cleanup must not change how the function exits. On a value that is statically known not to be a
`Result`/`Option` it is E0401. On a value of unknown type (`Any`) it is accepted, and a value that
turns out not to be a `Result`/`Option` is a runtime `TypeError` (`R0002`) on both engines:

```lumen run
fn unwrap_it(value):
    let inner = value?
    return Some(inner)

try:
    unwrap_it(5)
catch e:
    print e.message
```
```output
TypeError: the '?' operator expects a Result or an Option, got Int
```

**Kinds must agree.** `?` on an `Option` returns `None`; that is only meaningful in a function that
returns an `Option` (or whose return type is not declared). `?` on a `Result` likewise needs a
`Result`-returning function. Mixing them is E0403, with the explicit conversion to use:

```lumen error E0403
fn head(items) -> Result<Int, Str>:
    let first = first_of(items)?
    return Ok(first)

fn first_of(items) -> Option<Int>:
    if len(items) == 0:
        return None
    return Some(items[0])
```
```diagnostic
TypeError at line 2: '?' on a Option returns None from 'head', but the function is declared -> Result<Int, Str>
  --> example.lum:2:17
    |
  2 |     let first = first_of(items)?
    |                 ^^^^^^^^^^^^^^^^
  = code: E0403
  = this expression is a Option<Int>
  = the function returns Result<Int, Str>
  = help: convert explicitly with `ok_or(option, error)` (Option -> Result), or `match` and return the failure you mean
```

```lumen run
fn first_of(items) -> Option<Int>:
    if len(items) == 0:
        return None
    return Some(items[0])

fn head(items) -> Result<Int, Str>:
    let first = ok_or(first_of(items), "the list is empty")?
    return Ok(first)

print head([7, 8])
print head([])
```
```output
Ok(7)
Err(the list is empty)
```

**`?` in the middle of an expression.** The operand is evaluated first and a failure abandons the rest:

```lumen run
fn trace(label, value):
    print "evaluating " + label
    return value

fn total(first, second) -> Result<Int, Str>:
    let sum = trace("a", 1) + trace("b", first?) + trace("c", second?)
    return Ok(sum)

print total(Ok(10), Ok(20))
print total(Err("first failed"), Ok(20))
```
```output
evaluating a
evaluating b
evaluating c
Ok(31)
evaluating a
Err(first failed)
```

A `?` directly after the argument of a *paren-free* call (`language.md` §1.13.2) is ambiguous — does it
apply to the argument or to the call? — and is a syntax error that asks for parentheses:
`(f x)?` for the call's result, `f (x?)` for the argument's.

**`?` is not an exception.** It *returns*. A `catch` around a `?` does not see the failure it
propagates, and `try`/`catch` does not see an `Err` value that merely sits in a variable (§4.2.5).

### 4.2.4 No implicit error conversion — exact matching

`?` never converts an error. When the operand is a `Result<T, E>` and the function returns
`Result<U, F>`, **`E` must be `F`** (compatibility is the ordinary assignability of `types.md` §3.6;
`Any` fits anything). The success types `T` and `U` are unrelated. If they differ the program is
rejected (E0404), and the message names both types and says what to do instead:

```lumen error E0404
fn read() -> Result<Int, Str>:
    return Err("io")

fn parse() -> Result<Int, Int>:
    let v = read()?
    return Ok(v)
```
```diagnostic
TypeError at line 5: incompatible error type: '?' would return a Str error, but 'parse' returns errors of type Int
  --> example.lum:5:13
    |
  5 |     let v = read()?
    |             ^^^^^^^
  = code: E0404
  = expected error type: Int
  = found error type: Str
  = help: '?' never converts errors; `match` the result and build the Int error yourself: Err(<Int>)
```

The conversion is written out where it happens, with `match`:

```lumen run
enum AppError:
    Io(Str)
    Parse(Str)

fn read() -> Result<Int, Str>:
    return Err("disk gone")

fn load() -> Result<Int, AppError>:
    match read():
        Ok(value):
            return Ok(value)
        Err(message):
            return Err(Io(message))

match load():
    Ok(value):
        print "loaded " + value
    Err(problem):
        match problem:
            Io(message):
                print "io error: " + message
            Parse(message):
                print "parse error: " + message
```
```output
io error: disk gone
```

Lumen has no `From`-style trait and no first-class functions (`types.md` §3.10), so there is nothing
that *could* convert implicitly. The named conversions that exist are `ok_or` (`Option` → `Result`),
`unwrap` / `unwrap_or` (`Result`/`Option` → value or exception, §4.4.12), and `is_ok` / `is_err` /
`is_some` / `is_none`.

### 4.2.5 Exceptions: `try`, `catch`, `fail`

An **exception** is a runtime failure that unwinds the call stack until a `catch` takes it. Exceptions
are raised by the runtime (every row of §4.4) and by the program with `fail` / `fail_with` (§4.4.11).
The **first enclosing `try`/`catch`** on the stack intercepts it: execution jumps to that `catch`'s
body, with `catch NAME` binding `NAME` to an **error value** (§4.2.6). If no `try`/`catch` encloses it,
the failure reaches the top level, the runtime prints it (§4.8) and the process exits with status 1.
**This applies uniformly**: a `NameError` from a typo and a deliberate `fail` are both catchable if
something catches them and both terminate the program if nothing does. No failure is uncatchable in
principle (§4.3).

What a `try`/`catch` does **not** catch:

- an `Err(...)` or `None` *value* — a function that returns `Err("x")` has not failed, it has answered;
- the early return caused by `?`;
- a static-phase failure (§4.1), which happens before any code runs.

```lumen run
fn lookup(key) -> Result<Int, Str>:
    return Err("no such key: " + key)

try:
    let outcome = lookup("a")
    print "no exception was raised"
    print outcome
catch e:
    print "caught (this line does not run)"
```
```output
no exception was raised
Err(no such key: a)
```

A `catch` handles *exceptional* failures. Reach for `Result` first when the failure is something the
caller should plan for, and for `try`/`catch` when it is something nobody planned for.

### 4.2.6 The error value

`catch e` binds `e` to a value of the built-in struct type `Error` with these fields:

| Field | Type | Meaning |
|---|---|---|
| `message` | `Str` | the failure's message, exactly as in §4.4 (`fail` adds no prefix) |
| `kind` | `Str` | the leading `XError` word of the message (`"TypeError"`, `"PermissionError"`, …), else `"Error"` |
| `code` | `Str` | the stable code (§4.11): `R0001` for `fail`, the program's own for `fail_with` |
| `cause` | `Option<Error>` | `Some(error)` when this failure was raised on account of another (§4.10), else `None` |
| `trace` | `List` of `Str` | the calls that led here, outermost first, as `"function (file:line)"` (§4.8) |

A **permission denial** additionally has `capability`, `operation`, `resource` and `reason` (§4.4.7).
No other structure is exposed. There is still **no typed exception hierarchy and no catch-by-type**:
a handler that wants to treat failures differently reads `e.code` or `e.kind`.

```lumen run
try:
    print 10 / 0
catch e:
    print e.message
    print e.kind
    print e.code
    print e.trace
```
```output
division by zero
Error
R0005
[<main> (example.lum:2)]
```

## 4.3 Panic Conditions Requiring No `try`/`catch` to Reach Safely

The following are **not** failures that terminate the process outright when uncaught in
some unrecoverable way beyond normal propagation — they follow the exact rule of §4.2 
(catchable if wrapped, terminating with a printed message if not). Lumen has **no**
failure category that skips normal `try`/`catch` interception (no `abort()`, no
segfault-equivalent, no unrecoverable panic distinct from an ordinary uncaught
failure), because Lumen has no manually-managed memory or pointers for such a
distinct category to exist over (`security.md` §6.1). *(3.1: this includes runaway recursion —
§4.4.13 — and a `%` by zero, which before 3.1 crashed the host with a Python traceback instead of
raising the catchable failure below.)*

## 4.4 Runtime Failure Catalogue

Every message the reference implementation can produce, grouped by cause. `Str`
interpolations are shown in `{braces}`. Each failure also has a stable code (§4.11).

### 4.4.1 Arithmetic and comparison

| Trigger | Message | Code |
|---|---|---|
| `/` with a zero divisor | `division by zero` | R0005 |
| `%` with a zero divisor *(3.1; before, an uncatchable host crash)* | `modulo by zero` | R0005 |
| Incompatible operand types for `+ - * / % == != < > <= >=` | `TypeError: cannot apply '{op}' to {type1} and {type2}` | R0002 |

Both are ordinary failures — catchable, with a code — on both engines:

```lumen run
try:
    print 10 / 0
catch e:
    print e.code + " " + e.message
try:
    print 10 % 0
catch e:
    print e.code + " " + e.message
```
```output
R0005 division by zero
R0005 modulo by zero
```

### 4.4.2 Name resolution

| Trigger | Message |
|---|---|
| Reading an unbound variable | `NameError: '{name}' is not defined` |
| Calling an unbound function name | `NameError: function '{name}' is not defined` |
| `spawn`-ing an unbound function name | `NameError: function '{name}' is not defined (line {n})` |
| Reading a nonexistent field on a module namespace | `NameError: module '{alias}' has no value '{field}'` |
| Calling a nonexistent function on a module namespace | `NameError: module '{alias}' has no function '{name}'` |
| Reading a nonexistent field on a struct instance | `NameError: {StructName} has no field '{field}'` |

### 4.4.3 Function and constructor arity/shape

| Trigger | Message |
|---|---|
| Wrong argument count to a user function | `TypeError: {name}() expects {n} arguments, got {m}` |
| Wrong payload-value count to an `enum` variant constructor | `TypeError: {Variant}() expects {n} value(s), got {m}` |
| `struct` literal with missing and/or extra fields | `TypeError: {StructName} {{ }} missing [...], unexpected [...]` (only the applicable one of "missing"/"unexpected" is included) |

### 4.4.4 Collections and indexing

| Trigger | Message |
|---|---|
| `list[i]` with `i` out of range | `index {i} is out of range (length {n})` |
| `dict[k]` with `k` absent | `key {k!r} not found` |
| Indexing a value that supports neither list- nor dict-style indexing | `TypeError: cannot index into this value with {k!r}` |
| Assigning through an index on a non-indexable value | `TypeError: cannot assign index on this value ({detail})` |

A failed lookup is a failure to catch, not a `None`; when absence is expected, check first (`len` for a list, `keys` for a dict) or keep the
lookup behind a function that returns an `Option` (§4.15):

```lumen run
let items = [1, 2, 3]
let table = {"a": 1}
try:
    print items[7]
catch e:
    print e.code + " " + e.message
try:
    print table["b"]
catch e:
    print e.code + " " + e.message
```
```output
R0006 index 7 is out of range (length 3)
R0007 key 'b' not found
```

### 4.4.5 Field access/mutation on non-structs

| Trigger | Message |
|---|---|
| `.field` read on a value that is not a struct or module | `TypeError: cannot read field '{field}' on non-struct value` |
| `.field = ...` write on a value that is not a struct | `TypeError: cannot set field '{field}' on non-struct value` |
| Calling `.method(...)` on a value that is not a module namespace | `TypeError: '{name}' is not callable on this value` |

### 4.4.6 `match` exhaustiveness

| Trigger | Message |
|---|---|
| No `match_case` pattern matches the scrutinee, and control falls off the end of the `match` | `MatchError at line {n}: no pattern matched {value}` (code R0004) |

On a value of unknown type the check cannot be made statically, so the runtime failure remains:

```lumen run
fn describe(value):
    match value:
        1:
            return "one"
        2:
            return "two"

try:
    print describe(3)
catch e:
    print e.code
    print e.message
```
```output
R0004
MatchError at line 2: no pattern matched 3
```

**Static exhaustiveness *(3.1; in 1.0–3.0 `match` was never checked)*.** When the checker knows the
scrutinee's type — a `Result`, an `Option`, or any `enum` the program declares, as a parameter with a
declared type, a `let` with an annotation, or the result of a typed call — a `match` with no catch-all arm
(`_`, or a plain binding name) that leaves a variant uncovered is a **static** `TypeError` (E0301) that
lists exactly what is missing:

```lumen error E0301
fn label(found: Option<Int>) -> Str:
    match found:
        Some(n):
            return "number " + n
    return "nothing"
```
```diagnostic
TypeError at line 2: this match on Option<Int> does not cover: None
  --> example.lum:2:5
    |
  2 |     match found:
    |     ^^^^^^^^^^^^
  = code: E0301
  = help: add an arm for each missing variant, or a final `_` arm
```

When the scrutinee's type is *not* known (an unannotated parameter, say) the checker cannot decide,
and the program is accepted with a **warning** (W0301, printed by `lumen check`) if its arms name
variants of one enum without a catch-all; if an unlisted variant then arrives, the program fails at run
time with the `MatchError` above, as in 1.0. A wrong variant name in a pattern is E0302 and a wrong
number of fields E0303.

Tightening this was the one deliberate narrowing of what 1.0–3.0 accepted (§4.13): a program that
relied on a non-exhaustive `match` over a typed value now needs a `_` arm.

### 4.4.7 Capabilities (see `capabilities.md` for the full model)

A denial prints as `PermissionError: {message}` and, when caught, is an `Error` value whose
`.kind` is `"PermissionError"`, with these extra fields: `.capability` (`fs.read`, `fs.write`,
`fs.list`, `net.http`), `.operation` (the builtin), `.resource` (the path as written, or
`scheme://host:port` for a URL — never a credential, path or query string) and `.reason`.
The operation is **not performed** in any of these cases. Checks run in this order:

| `.reason` | Trigger | Message |
|---|---|---|
| *(none — `TypeError`)* | a gated builtin called with the wrong argument count or a non-`Str` argument, or a path/URL containing a NUL character | `TypeError: read_file() expects a Str path as argument 1, got Int` (also `a Str URL`, `a Str`; `expects 1 argument, got 0`; `argument 1 must not contain a NUL character`) |
| `not-declared` | the module making the call did not declare the capability | `PermissionError: {fn}() needs the '{cap}' capability, which {who} has not declared — add 'use {cap}' at the top of {file}` |
| `not-declared-by-importer` | the module declared it but a module that imports it (directly or indirectly) did not | `PermissionError: {fn}() needs the '{cap}' capability: '{lib}' declares it, but '{importer}', which imports it (directly or indirectly), does not — a library cannot hold authority its importer lacks` |
| `not-granted` | declared throughout, but the host did not grant the capability for this run | `PermissionError: {fn}() requires the '{cap}' capability, which was not granted for this run — grant it with {flag} (declaring it with 'use' does not grant it)` |
| `out-of-scope` | granted, but the path or host:port is outside the granted scope | `PermissionError: {fn}('{path}') is outside the paths granted for '{cap}'` / `PermissionError: http_get() requested '{url}', which is not among the hosts granted for 'net.http'` |
| `redirect-out-of-scope` | an HTTP redirect led outside the granted hosts (the redirect target is never contacted) | `PermissionError: http_get() redirected to '{url}', which is not among the hosts granted for 'net.http'` |

`{who}` is `'{file}'` or `this program`; `{flag}` is `--allow-read[=PATH,...]`, `--allow-write[=PATH,...]`,
`--allow-list[=PATH,...]` or `--allow-net[=HOST[:PORT],...]`. A denial raised while a module's
top-level code runs is reported as `ModuleError: error loading '{path}': PermissionError: …`.

**Catching a failure grants nothing.** A `catch` (or `finally`) block runs with exactly the authority of the
code around it: a denied operation is still denied inside the handler, after the handler, and in the cleanup.
An error value is data — holding a `PermissionError` does not carry the capability that was missing.

```lumen run
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
```
```output
caught R0008
still denied: not-granted
denied in finally too: not-granted
```

**What a diagnostic does not contain.** An error value and an uncaught report name what the *program* asked
for — a path as written, `scheme://host:port` for a URL, the capability, the operation — and the source
location. They never contain file contents, environment variables, or the credentials, path or query of a URL
(`user:secret@` and `?token=` are dropped before a denied URL is named):

```lumen run
use net.http

try:
    http_get("http://user:secretpw@127.0.0.1:1/path?token=abc")
catch e:
    print e.resource
    print e.capability
```
```output
http://127.0.0.1:1
net.http
```

`http_get` with a URL whose scheme is not `http`/`https` raises the ordinary `Error`
`http_get failed: only http:// and https:// URLs are supported (got '{scheme}' scheme)` — *after*
the capability checks, and never reads a file.

### 4.4.8 I/O failures (only reachable once every capability check has passed)

| Trigger | Message |
|---|---|
| `read_file` on a path that cannot be opened/read | `could not read '{path}': {os_reason}` |
| `write_file` on a path that cannot be opened/written | `could not write '{path}': {os_reason}` |
| `append_file` on a path that cannot be opened for appending *(2.3)* | `could not append to '{path}': {os_reason}` |
| `list_dir` on a path that does not exist, is not a directory, or cannot be read *(2.3)* | `could not list directory '{path}': {os_reason}` |
| `delete_file` on a path that does not exist, is a directory, or cannot be removed *(2.3)* | `could not delete '{path}': {os_reason}` |
| `http_get` failure of any kind — DNS resolution, timeout, connection refused, or the server responding with a non-2xx status (a 404, 500, etc. is treated as a failure, not a returned value — see `standard-library.md` §8.4) | `http_get failed: {reason}` |

`file_exists` and `is_dir` (2.3) have no row here by design: they never raise beyond the capability check in §4.4.7 — a path that cannot be inspected is reported as `false`.

### 4.4.9 Modules

| Trigger | Message |
|---|---|
| `use "path" as name` where `path` cannot be found (searched next to the importing file, then in `lumen_packages/` — `modules.md` §5.4), **or** is absolute, **or** does not name a `.lum` file (*3.0*: deliberately the same message as "does not exist") | `ModuleError: cannot find module '{path}' at line {n} (looked next to this file and in lumen_packages/)` |
| A static-phase (parse or type-check) failure *inside* an imported module, discovered while loading it | `ModuleError: error loading '{path}': {inner message}` |
| `use x` where `x` is a bare name that is not a capability (or alias `fs`, `net`) | `ModuleError: unknown module '{name}' at line {n}` |
| `use fs.chmod` — a dotted name that is not one of `fs.read`, `fs.write`, `fs.list`, `net.http` | `ModuleError: unknown capability '{name}' at line {n} (known: fs.list, fs.read, fs.write, net.http)` |
| `use process.spawn`, `use env.read`, `use net.connect` — reserved names with no operation behind them *(3.0)* | `ModuleError: capability '{name}' at line {n} is reserved but not implemented in this version, so it cannot be declared (unsupported capabilities fail closed)` |
| a `use <capability>` that is not at the top level of the file (inside a function, `if`, loop, …) *(3.0)* | `ModuleError: 'use {cap}' at line {n} must be at the top level of the file …` |

### 4.4.10 Concurrency

`wait(t)` where `t` is not a value produced by `spawn`: `TypeError: wait() expects a
task returned by spawn`. All other concurrency failure modes are described in
`concurrency.md` §6.4 (`wait` re-raising the spawned call's own failure is not a new
message form — it re-raises the *original* failure, of whatever kind above, exactly as
it would have surfaced had the call not been spawned).

### 4.4.11 Explicit programmer-raised failures

`fail(message)` — a built-in (`standard-library.md` §8.6) — raises a runtime failure
whose `.message` is exactly the `Str` passed to it (no prefix is added) and whose code is `R0001`.
It is for exceptional failures: library authors use it to signal domain-specific failures
without a language-level way to define new named error kinds.

Two optional additions *(3.1)*:

- `fail(message, cause)` — `cause` must be an error value bound by a `catch` (§4.10); anything
  else is `TypeError: the cause of an error must be the error a `catch` bound, got {type}`.
- `fail_with(code, message)` and `fail_with(code, message, cause)` — the same, with a code the
  program chooses. A code is a `Str` of letters, digits, `_`, `-` and `.`, starting with a letter,
  at most 64 characters; anything else is `TypeError: an error code must be a Str of letters, …`.
  The code is what a handler tests (`e.code == "E_DB"`) instead of parsing the message.

```lumen run
fn connect():
    fail_with("E_DB", "database unreachable")

try:
    connect()
catch e:
    print e.code
    print e.message
```
```output
E_DB
database unreachable
```

### 4.4.12 `Result` / `Option` helpers *(3.1)*

Seven built-ins move between `Option`, `Result` and an exception. They are the **only** conversions
(§4.2.4); each is explicit at the call site.

| Call | Result | Failure |
|---|---|---|
| `ok_or(option, error)` | `Some(v)` → `Ok(v)`; `None` → `Err(error)` | `TypeError` if the first argument is not an `Option` |
| `unwrap(x)` | the payload of `Ok(v)` / `Some(v)` | on `Err(e)`: `called unwrap on an Err value: {e}`; on `None`: `called unwrap on a None value` (both code R0011; when `e` is itself an error value it becomes the new failure's cause) |
| `unwrap_or(x, default)` | the payload, or `default` for `Err`/`None` | `TypeError` if `x` is neither |
| `is_ok(r)`, `is_err(r)` | `Bool` | `TypeError` if `r` is not a `Result` |
| `is_some(o)`, `is_none(o)` | `Bool` | `TypeError` if `o` is not an `Option` |

`unwrap` turns an expected failure into an exception; use it where an `Err` would be a bug.

```lumen run
let missing: Option<Int> = None
print unwrap_or(missing, 0)
print ok_or(missing, "nothing there")
print is_some(Some(1))
try:
    unwrap(Err("bad"))
catch e:
    print e.message
    print e.code
```
```output
0
Err(nothing there)
true
called unwrap on an Err value: bad
R0011
```

### 4.4.13 Stack overflow

Unbounded recursion fails with `stack overflow: maximum call depth exceeded` (code R0009) — a
catchable failure on both engines, never a host crash and never an out-of-memory kill.

```lumen run
fn forever(n):
    return forever(n + 1)

try:
    forever(0)
catch e:
    print e.message
    print e.code
```
```output
stack overflow: maximum call depth exceeded
R0009
```

*The depth at which each engine gives up is not part of the language.* The interpreter's is whatever the
host's Python stack allows (a few hundred Lumen calls); the VM's is 10,000. A program must not depend on
either number (§4.14).

## 4.5 `match` Pattern Resolution Order (an explicitly resolved ambiguity)

A bare-name pattern (`NAME` with no parentheses, `grammar.md` §2.3) is ambiguous on its
face — it could mean "match only this specific unit variant" or "match anything and
bind it to this name." Lumen resolves this **at match time**, not parse time, by a
fixed rule: if `NAME` is the name of a declared **unit** variant (an `enum` variant
with no payload fields) reachable in the current scope, it is a **variant-equality
pattern** — it matches only an instance of that exact variant and binds nothing. In
every other case (an ordinary identifier, or the name of a *payload-carrying* variant
used without parentheses), it is a **catch-all binding pattern** — it always matches,
and binds the entire scrutinee value to that name in the case's block scope. There is
no way in v1.0 to write a binding pattern whose chosen name happens to coincide with a
unit variant's name; that combination is always resolved as the variant-equality
reading. `_` is reserved specifically as the unconditional wildcard (matches anything,
binds nothing, and is never treated as a variant name even if a variant is
(illegally, since `_` cannot be declared as a variant name — it collides with no
keyword restriction here but is conventionally reserved) named `_`).

A unit variant name in a pattern is the variant, anything else binds (`other` above matches `Green` *and* the
`Int` `42`; `Red` matches only `Red`):

```lumen run
enum Light:
    Red
    Green

fn name(x):
    match x:
        Red:
            return "stop"
        other:
            return "not red"

print name(Red)
print name(Green)
print name(42)
```
```output
stop
not red
not red
```

## 4.6 Cleanup: `finally` *(3.1)*

*(Replaces "Lumen 1.0 has no `finally` clause …". That gap is closed.)*

```
try:
    body
finally:
    cleanup
```

`finally` may follow a `catch` (`try … catch e: … finally: …`) or stand alone. **The `cleanup` block
runs exactly once however the `body` ends:**

| How the body ends | What happens |
|---|---|
| it finishes normally | the cleanup runs, then execution continues after the `try` |
| a `return` | the cleanup runs, then the function returns the value it was about to return |
| a `?` that found `Err` / `None` | the cleanup runs, then the function returns that `Err` / `None` |
| a failure (exception) | the cleanup runs, then the failure **continues**, unchanged, to the next enclosing handler |

With a `catch`, the order is the body, then the `catch` if the body failed, then the `finally`; a failure
inside the `catch` handler still runs the `finally`. Nested `finally` blocks run innermost first.

```lumen run
fn read_record(id):
    try:
        print "open " + id
        if id == 2:
            fail("record 2 is corrupt")
        return "record " + id
    finally:
        print "close " + id

print read_record(1)
try:
    read_record(2)
catch e:
    print "caught: " + e.message
```
```output
open 1
close 1
record 1
open 2
close 2
caught: record 2 is corrupt
```

```lumen run
fn load(ok) -> Result<Int, Str>:
    if ok:
        return Ok(1)
    return Err("load failed")

fn pipeline(ok) -> Result<Int, Str>:
    try:
        let value = load(ok)?
        print "loaded"
        return Ok(value)
    finally:
        print "release"

print pipeline(true)
print pipeline(false)
```
```output
loaded
release
Ok(1)
release
Err(load failed)
```

**Scope.** The cleanup block runs in the scope the `try` statement is in: it sees the variables outside the
`try`, **not** the ones the body declared (even if the body's own variable shadows an outer name).

**A cleanup that fails.** If the cleanup block itself fails, that failure replaces the one it was running
for, and the failure it replaced becomes its **cause** (§4.10), so neither is lost:

```lumen run
fn run():
    try:
        fail("the work failed")
    finally:
        fail("closing the resource failed")

try:
    run()
catch e:
    print e.message
    match e.cause:
        Some(earlier):
            print "after: " + earlier.message
        None:
            print "no earlier failure"
```
```output
closing the resource failed
after: the work failed
```

If the cleanup's failure already names a cause of its own (`fail("x", other)` inside the `finally`), that
explicit cause stays the cause, and the failure it replaced is kept beside it for the uncaught-failure
report (§4.8) — but is **not** readable by a `catch` as a field. This is the one place a failure is not
fully visible to a program (§4.14).

**What a `finally` may not do.** `return` and `?` inside a `finally` block are rejected (E0407): a cleanup
must not change how the function exits, and that would silently discard the failure in flight.

```lumen error E0407
fn close(handle):
    try:
        print "working"
    finally:
        return 0
```
```diagnostic
TypeError at line 5: 'return' is not allowed in a 'finally' block — cleanup must not change how the function exits
  --> example.lum:5:9
    |
  5 |         return 0
    |         ^^^^^^^^
  = code: E0407
```

**What `finally` does not do.** It is not a destructor and not a transaction: mutations the body made to
lists, dicts, structs and global variables stay made (§4.7). It runs when the `try` is *left*; it does not
run if the process is killed, and a `spawn`ed task's `finally` runs on the task's thread when that task's
body ends. Cleanup for a resource that was never acquired is the program's concern: put the acquisition
*before* the `try`.

## 4.7 Stack Unwinding Semantics

Runtime failures propagate up the call stack until a `catch` takes them (§4.2.5), running every
`finally` block they pass (§4.6). Unwinding a Lumen call stack has the following precise effects, all of
which follow directly from `language.md` §1.11's scoping rules:

- Every block-local scope created between the failure site and the intercepting
  `catch` (or the top level) is discarded with the unwind; none of its bindings are
  observable afterward.
- Because function calls create a scope chained to the **global** scope (not the
  caller's local scope — `language.md` §1.11 Rule C), an unwind through a function call
  cannot corrupt or partially-mutate the *caller's local bindings* through the act of
  unwinding itself. It CAN leave global bindings, and the contents of any `List`/
  `Dict`/`struct` reachable from the call, in a partially-mutated state — reference
  types are not rolled back on unwind (§4.6 applies here too: there is no transactional
  rollback of mutations).
- **The same holds on both engines for what the unwind leaves behind (3.1).** A failure that leaves a
  `for` loop, or the middle of an expression, leaves no half-finished iteration or operand behind on
  the bytecode VM; before 3.1 it could, and a later `return` or a later loop could then see the stale
  state (a wrong value, or a loop that never ended). The Phase 4 regression tests pin this.
- A `spawn`ed task that fails is **not** unwound onto the spawning thread's stack at
  the point of failure. It is captured on the task handle and only re-raised, on the
  spawning thread, at the point where that thread calls `wait()` on the handle
  (`concurrency.md` §6.4). *(3.1; before, a failure nobody waited for was silently discarded.)* A
  spawned task whose failure is never `wait`ed on is **reported** when the program ends: `lumen run` prints
  `Lumen: warning: a spawned task failed and nothing waited for it, so its failure would otherwise be lost:`
  and then the failure, with its code and trace, on **standard error**, on both engines. Standard output and
  the exit status are not changed by it. A task still running at the end gets a 0.1 s grace, then is abandoned
  as it always was, and its later failure is not seen — so "reported" holds for a task that has failed by the
  time the program ends, not for one that outlives it.

The unwinding leaves nothing half-finished behind on either engine — the loop below is cut off in the middle, and a
later loop and a later call run normally:

```lumen run
fn risky(n):
    for i in range(0, n):
        if i == 2:
            fail("stopped at 2")
    return n

try:
    risky(5)
catch e:
    print e.message
let total = 0
for i in range(0, 3):
    total = total + i
print total
print risky(2)
```
```output
stopped at 2
3
2
```

A failure inside a `spawn`ed task surfaces where the task is `wait`ed on, and only there:

```lumen run
fn work(n):
    if n == 2:
        fail("task 2 failed")
    return n * 10

let a = spawn work(1)
let b = spawn work(2)
print wait(a)
try:
    print wait(b)
catch e:
    print e.message
```
```output
10
task 2 failed
```

## 4.8 Stack Traces *(3.1)*

Every failure records **the calls that led to it**, at the moment it happened. The record is the
error value's `trace` field (§4.2.6) and is printed when a failure goes uncaught.

**Format.** One entry per active call, **outermost first, the failing call last**:
`function (file:line)`, where `file` is the source file's name and `line` is the line of the *statement*
that call was executing — for the outer entries, the statement containing the call to the next one.

| Entry | Meaning |
|---|---|
| `<main> (file:line)` | the top level of `file` (a module's top level when its `use` runs) |
| `name (file:line)` | a call of the function `name` defined in `file` |
| `test "title" (file:line)` | the body of that `test` block |

A function in an imported module appears with the *module's* file name. A trace longer than 40 entries (an
infinite recursion that was cut off) shows its first 20 and last 20 with `... N more frames ...` between.

```lumen run
fn inner(x):
    if x > 2:
        fail("too big")
    return x

fn middle(x):
    return inner(x) + 1

fn outer(x):
    try:
        return middle(x)
    catch e:
        for line in e.trace:
            print line
        return -1

print outer(5)
```
```output
<main> (example.lum:17)
outer (example.lum:11)
middle (example.lum:7)
inner (example.lum:3)
-1
```

**Uncaught failures.** When nothing catches a failure the program's own output so far is kept, then the
runtime prints — to standard output, as 1.0 always did — the **message line exactly as before 3.1**, then
the new lines: the code, the trace, and one `caused by:` block per cause (§4.10), and exits with status 1:

```lumen run
fn save(name):
    fail_with("E_DISK", "disk is full")

fn export():
    print "exporting"
    save("report")

export()
```
```output
exporting
Uncaught error: disk is full
  code: E_DISK
  trace (most recent call last):
    <main> (example.lum:8)
    export (example.lum:6)
    save (example.lum:2)
```

A failure that is not a `fail` (a `TypeError`, a `NameError`, …) prints its message line as before with no
`Uncaught error:` prefix, followed by the same code and trace lines.

**Rules.**

- The trace is captured **once, when the failure first appears** — not when it is caught. Catching it,
  running `finally` blocks over it and re-raising it (a `finally` that does not itself fail) do not change it.
- A *new* failure — `fail("…")` inside a `catch` handler, say — has its own trace, from where *it* was raised;
  the old one is reachable as its cause.
- Lines are statement-granular: a call spanning several lines is reported at its statement's first line, and
  the condition of a `while` is reported at the `while` line on every iteration.
- A failure from a `spawn`ed task is captured on the task's thread (its trace starts at the spawned function)
  and, when `wait` re-raises it, the waiting thread's calls are added after the task's. The two engines
  produce the same shape (tested); what a *program* can rely on is only that the entries are present.
- Columns are recorded for compile-time diagnostics (§4.12), not for traces.

## 4.9 Phase 2 Addendum: Static Name Resolution and §4.1's Boundary

Phase 2 introduces a static Name Resolution pass (see the language implementation's
`Resolver`) that runs between parsing and type-checking. It moves a *subset* of what
was, under Phase 1's interpreter alone, a purely runtime `NameError` into the
static-failure phase (§4.1) instead: specifically, a reference to a name that does not
exist anywhere in the compilation unit, and that is not lexically inside a `try`
block's own body. This is deliberately scoped, not a blanket change:

- **§4.2.5's uniform-catchability guarantee is fully preserved, unchanged.** Any name
  reference lexically inside a `try` body — including one that turns out to be
  undefined everywhere — is *not* statically rejected; it is deferred to the exact
  same runtime check Phase 1 always had, and remains catchable exactly as documented
  above and exactly as tested by the existing conformance suite. `try: print nope
  catch e: ...` still works precisely as shown earlier in this chapter.
- **What does change:** a reference to an undefined name that is *not* inside any
  `try` now causes the failure to move from "some prior statements' side effects may
  have already happened, then the program fails" to "static-phase failure, §4.1: the
  program does not execute at all, not even earlier statements." Concretely,
  `print "before"\nprint nope` printed `before` under the Phase 1 interpreter alone;
  under Phase 2's full pipeline it prints nothing and fails before either statement
  runs. No test in the frozen Phase 1 conformance suite relied on the older timing
  (only `SyntaxError`/static-`TypeError` cases, which already worked this way, were
  tested), so this is an addition, not a violation, of the Phase 1 freeze — but it is
  a real, observable difference and is recorded here rather than left implicit.

## 4.10 Error Chaining *(3.1)*

A failure can carry the failure it happened *because of*. `fail(message, cause)` and
`fail_with(code, message, cause)` (§4.4.11) raise a new failure whose `cause` is the error value a `catch`
bound. A handler reads it from `e.cause`, an `Option<Error>`, and can walk the whole chain.

```lumen run
fn read_config():
    fail_with("E_IO", "cannot open settings.toml")

fn start():
    try:
        read_config()
    catch e:
        fail_with("E_START", "service failed to start", e)

try:
    start()
catch e:
    print e.code + ": " + e.message
    match e.cause:
        Some(inner):
            print "caused by " + inner.code + ": " + inner.message
        None:
            print "no cause"
```
```output
E_START: service failed to start
caused by E_IO: cannot open settings.toml
```

Uncaught, the whole chain is printed, each cause with its own code and trace:

```lumen run
fn read_config():
    fail_with("E_IO", "cannot open settings.toml")

fn start():
    try:
        read_config()
    catch e:
        fail_with("E_START", "service failed to start", e)

start()
```
```output
Uncaught error: service failed to start
  code: E_START
  trace (most recent call last):
    <main> (example.lum:10)
    start (example.lum:8)
  caused by: cannot open settings.toml
    code: E_IO
    trace (most recent call last):
      <main> (example.lum:10)
      start (example.lum:6)
      read_config (example.lum:2)
```

Causes are also recorded by the runtime itself: a failing `finally` block's failure has the failure it
replaced as its cause (§4.6), and `unwrap` of an `Err` that holds an error value makes that error the cause
of the `unwrap` failure (§4.4.12). A chain is printed to a depth of 8; a longer chain is cut there.

Because a cause is the error value `catch` bound, a failure cannot be its own cause and a cycle cannot
be built: the cause of a failure exists before the failure does.

## 4.11 Error Codes *(3.1)*

Every failure has a stable code that tools and programs can test instead of parsing messages. Codes never
change meaning and are never reused.

**Compile-time diagnostics** (`E` codes; `lumen check` prints them, §4.12):

| Code | Meaning |
|---|---|
| E0001 | syntax error *(reserved: syntax errors still print without a code in 3.1)* |
| E0002 | invalid module or capability declaration |
| E0101 | undefined name, or assignment to a name never declared with `let` |
| E0102 | unknown struct |
| E0201 | type mismatch (`let`, argument, return value, payload, field) — carries `expected`/`found` |
| E0202 | wrong number of type arguments |
| E0203 | a generic type that is not declared |
| E0204 | a type parameter bound to two different types in one call |
| E0301 | `match` does not cover every variant |
| E0302 | a pattern names something that is not a variant of the scrutinee's type |
| E0303 | a pattern with the wrong number of fields |
| E0304 | a variant constructed with the wrong number of values |
| E0401 | `?` applied to something that is not a `Result` or an `Option` |
| E0402 | `?` outside a function body |
| E0403 | `?` on an `Option` in a `Result` function, or the reverse |
| E0404 | `?` would return an error of a type the function does not return |
| E0405 | a `Result` value that is discarded |
| E0406 | a `Result`/`Option` function that can reach its end without returning |
| E0407 | `return` or `?` inside a `finally` block |
| W0301 | *(warning)* a `match` that may not cover every variant, on a value of unknown type |

### 4.11.1 The compile-time codes, shown

Each of these is a program the compiler refuses before anything runs; the block under it is exactly what
`lumen check` prints (`example.lum` is the file name).

**E0101** — a name that nothing declares (the first line keeps the form every release has printed):

```lumen error E0101
fn f():
    return totl + 1
```
```diagnostic
NameError: 'totl' is not defined (unresolved by static name resolution) at line 2
  --> example.lum:2:12
    |
  2 |     return totl + 1
    |            ^^^^
  = code: E0101
```

**E0203** and **E0204** — generic types:

```lumen error E0203
let m: Maybe<Int> = None
```
```diagnostic
TypeError at line 1: unknown generic type 'Maybe'
  --> example.lum:1:8
    |
  1 | let m: Maybe<Int> = None
    |        ^^^^^^^^^^
  = code: E0203
```

```lumen error E0204
fn same<T>(a: T, b: T) -> T:
    return a

print same(1, "x")
```
```diagnostic
TypeError at line 4: type parameter 'T' inferred as both Int and Str in call to 'same'
  --> example.lum:4:7
    |
  4 | print same(1, "x")
    |       ^^^^^^^^^^^^
  = code: E0204
```

**E0302**, **E0303** and **E0304** — patterns and constructors that do not fit the type:

```lumen error E0302
fn f(r: Result<Int, Str>):
    match r:
        Ok(v):
            return v
        Maybe(x):
            return 0
        Err(e):
            return 1
```
```diagnostic
TypeError at line 5: 'Maybe' is not a variant of Result<Int, Str>
  --> example.lum:5:9
    |
  5 |         Maybe(x):
    |         ^^^^^^^^
  = code: E0302
  = help: its variants are: Ok, Err
```

```lumen error E0303
fn f(r: Result<Int, Str>):
    match r:
        Ok(a, b):
            return a
        Err(e):
            return 1
```
```diagnostic
TypeError at line 3: the pattern Ok(...) names 2 field(s) but the variant has 1
  --> example.lum:3:9
    |
  3 |         Ok(a, b):
    |         ^^^^^^^^
  = code: E0303
```

```lumen error E0304
let r: Result<Int, Str> = Ok(1, 2)
```
```diagnostic
TypeError at line 1: Ok() expects 1 value(s), got 2
  --> example.lum:1:27
    |
  1 | let r: Result<Int, Str> = Ok(1, 2)
    |                           ^^^^^^^^
  = code: E0304
```

**E0401** and **E0402** — `?` where it has nothing to unwrap, or nothing to return from:

```lumen error E0401
fn f() -> Result<Int, Str>:
    let n = 5
    let m = n?
    return Ok(m)
```
```diagnostic
TypeError at line 3: the '?' operator applies to a Result or an Option, but this is a Int
  --> example.lum:3:13
    |
  3 |     let m = n?
    |             ^^
  = code: E0401
  = help: '?' unwraps `Ok(v)`/`Some(v)` and returns `Err(e)`/`None` from the function
```

```lumen error E0402
let r: Result<Int, Str> = Ok(1)
let v = r?
print v
```
```diagnostic
TypeError at line 2: '?' can only be used inside a function (it returns from the function), not in top-level code
  --> example.lum:2:9
    |
  2 | let v = r?
    |         ^^
  = code: E0402
  = help: match on the value instead, or move this code into a function
```

**E0405** and **E0406** — a `Result` that is dropped, and a function that promises one and may not deliver it:

```lumen error E0405
fn save(name) -> Result<Int, Str>:
    if name == "":
        return Err("empty")
    return Ok(1)

save("report")
```
```diagnostic
TypeError at line 6: the Result<Int, Str> value is discarded, so a failure would be ignored without a trace
  --> example.lum:6:1
    |
  6 | save("report")
    | ^^^^^^^^^^^^^^
  = code: E0405
  = help: handle it with `match`, propagate it with `?`, or discard it on purpose with `let _ = ...`
```

```lumen error E0406
fn find(n: Int) -> Option<Int>:
    if n > 0:
        return Some(n)
```
```diagnostic
TypeError at line 1: function 'find' is declared -> Option<Int> but can reach its end without returning a value
  --> example.lum:1:1
    |
  1 | fn find(n: Int) -> Option<Int>:
    | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  = code: E0406
  = help: end every path with `return Ok(...)`, `return Err(...)`, `return Some(...)` or `return None`
```

### 4.11.2 The runtime codes

**Runtime failures** (`R` codes; the `code` of an error value):

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| R0001 | raised by the program with `fail` | R0009 | stack overflow |
| R0002 | `TypeError` | R0010 | `ModuleError` |
| R0003 | `NameError` | R0011 | `unwrap` of an `Err` or `None` |
| R0004 | `MatchError` | R0012 | assertion failed |
| R0005 | division or modulo by zero | R0013 | end of input (`read_line`) |
| R0006 | index out of range | R0014 | file or directory operation failed |
| R0007 | key not found | R0015 | network request failed |
| R0008 | `PermissionError` | *(program)* | any code given to `fail_with` |

A program's own codes are free-form within the rules of §4.4.11; the `R`/`E`/`W` + four digits namespace is
Lumen's.

## 4.12 Diagnostics *(3.1)*

A compile-time failure identifies **where** the problem is and **explains** it. Every static diagnostic has
a stable code (§4.11), a line and — for the checker's diagnostics — a column range, and `lumen check`,
`lumen run` and `lumen test` print it as:

```
TypeError at line 5: <the message, exactly the form the language has always printed>
  --> file.lum:5:13
    |
  5 |     let v = read()?
    |             ^^^^^^^
  = code: E0404
  = expected error type: Int
  = found error type: Str
  = help: <what to do instead>
```

The first line is the long-standing `Kind at line N: …` message, so anything that reads it keeps working.
Below it: the position (`file:line:column`, columns counted from 1), the source line with the offending code
underlined, the code, the notes the checker attached (typically `expected` / `found`), and a `help` line where
the fix is not obvious. Warnings (W-codes) are printed the same way by `lumen check`, which still succeeds and
exits 0 with them; the line `No errors found in … (N warnings)` counts them.

A syntax error is reported with its line (`Lumen: expected … at line N`), without a column or a code in 3.1.
A tool that wants the structure rather than the text reads `LumenError.code`, `.line`, `.col`, `.end_col`,
`.help` and `.notes` (the Python API) — the same fields the VS Code extension's language server uses.

## 4.13 What 3.1 Changed for a Program Written Before It

Phase 4 is additive. A 3.0 program with none of the following behaves as it did; these are the
corners where it can differ, all deliberate, all disclosed here and in `CHANGELOG.md`.

1. **`finally` is a keyword.** A program that used `finally` as a variable or function name no longer
   parses. (The same kind of narrow, disclosed exception `test`, `returns`, `is` and `while` were.)
2. **`Option`, `Result`, `Some`, `None`, `Ok`, `Err` are always in scope.** A program that *declares* an
   enum or struct with one of those names, or an enum variant or function called `Some`, `None`, `Ok` or `Err`,
   keeps its own meaning (the library enum is simply not loaded). A program that merely *uses* one of those
   names as a plain variable is unaffected.
3. **`match` is statically checked for exhaustiveness** where the scrutinee's type is known (§4.4.6). A
   program that matched a typed value non-exhaustively, and relied on the runtime `MatchError`, must add a `_`
   arm.
4. **Declared type names are now checked in signatures.** A parameter or return type that names a struct or
   enum the program declares (`fn area(s: Shape)`) was previously treated as an implicit generic placeholder
   (`language.md` §1.13.3); it is now that type, and a value of another type is rejected (E0201).
5. **The error value has more fields** (`kind` since 3.0, now also `code`, `cause`, `trace`), so `print e`
   shows more. `e.message` is unchanged.
6. **An uncaught failure prints more** — its code, trace and causes follow the message line, which is
   unchanged and still first. The output is still on standard output and the exit status is still 1. Anything
   that compares the *whole* output of a failing program must account for the extra lines.
7. **Enum and struct values compare by structure.** `Ok(1) == Ok(1)` was `false` (identity) and is now
   `true`; two struct values of the same type with equal fields are equal. (`types.md` §3.6.1.) The same
   rule decides when two values are the same `Dict` key: `d[Circle(1)] = 1` twice leaves one entry, not two.
   (A `struct` key hashes by its type name, so changing one of its fields does not lose its entry.)
8. **A failed task nobody waited for is now reported** — on standard error, after the program's own output (§4.7). A
   program that spawned a task which failed and never `wait`ed for it used to say nothing; it now prints a warning to
   standard error. Standard output and the exit status are unchanged.
9. **`%` by zero is a catchable failure.** It used to crash the interpreter with a host traceback and was not
   catchable at all on the VM.

Two defects of the bytecode VM, present before 3.1, were fixed on the way (§4.7, §4.4.13): a failure or an
early `return` inside a `for` loop could leave the loop's iterator behind (wrong values, a loop that never
ended), and two `spawn`ed tasks calling the same imported function corrupted each other. Neither is a
language change — the interpreter always behaved as specified — but a program that depended on the buggy
output of either would see a difference.

## 4.14 Interpreter / VM Parity, and Known Limits

**Parity.** Both engines implement the same semantics for everything in this chapter: the same results,
the same output, the same failure messages and codes, the same traces (file, line and order), the same
exit status. The decisions are made in **one** place, shared by both engines, not re-implemented twice:
what `?` does to a value (`propagate_step`), what `fail`/`fail_with`/`unwrap`/`ok_or` do, how an error value
and an uncaught report are built. The engines differ only in *how they unwind* — the interpreter with host
exceptions, the VM with a handler stack and explicit cleanup entries — and the Phase 4 suites run every
case on the interpreter, the VM and the VM at `-O2`, comparing the output byte for byte.

**Known limits.** These are stated so nobody has to discover them.

- *The depth at which runaway recursion is stopped differs* between the engines (§4.4.13). The failure and its
  code are the same.
- *Type checking is gradual.* Anything that flows through an unannotated parameter, variable or function is
  `Any` and is not checked; a call through an imported module is checked when the module's file can be read at
  check time (its function signatures are used), otherwise it is not. `List` and `Dict` do not take type
  arguments (`List<Int>` is E0202). Inside a generic function's body a value of type `T` is treated as `Any`.
- *Reassignment of a name is checked only when its declared type is not a primitive* (`Result`, `Option`, a
  user or generic type). `let x: Int = 1` followed by `x = "s"` is not rejected (`types.md` §3.6). A write
  into a typed struct field, `point.x = "s"`, **is** checked (E0201) — but only when the object's type is
  known to the checker (§3.4.1).
- *A `finally` whose own failure already has an explicit cause* keeps the failure it replaced out of reach of
  a `catch` (§4.6); an uncaught report does print it.
- *Built-ins still raise.* `read_file`, `http_get` and the other 1.x built-ins signal expected failures (a
  missing file, a refused connection) by raising, as before; §4.15 shows the wrapper that turns that into a
  `Result`.
- *`?` cannot be used directly in a `test` block or at the top level*, only in a function; and not in a
  `finally` block.
- *Traces are statement-granular and have no columns*, and a failure that happens while a module's top level
  runs is re-reported by the importer as `ModuleError: error loading '…'`; the inner failure's trace is part of
  that message's text, not a `cause`.
- *Syntax errors have no code and no column* (E0001 is reserved).
- *A task that outlives the program is not reported.* A `spawn`ed task whose failure nobody `wait`ed for is
  reported on standard error when it had failed by the time the program ended (§4.7); one that is still running
  after the 0.1 s grace is abandoned and its later failure is not seen. The report does not change the exit
  status (making an unwaited failure exit non-zero is a decision still open — `PHASE4_STATUS.md`).
- *A function and an enum variant that share a name* are resolved differently by the two engines (the interpreter
  prefers the variant, the VM the function). This predates 3.1 and is unrelated to error handling; a program
  should not declare both.
- *Two engines, one host.* Everything here is about the reference implementation; there is no native backend.

## 4.15 Recovery Patterns

Four patterns, each of which runs as written.

**1. Handle an expected failure where you can; propagate it where you cannot.**

```lumen run
fn parse_age(text) -> Result<Int, Str>:
    if text == "":
        return Err("age is empty")
    if text == "abc":
        return Err("age is not a number")
    return Ok(len(text))

fn describe(text) -> Result<Str, Str>:
    let age = parse_age(text)?
    return Ok("age " + age)

fn show(text):
    match describe(text):
        Ok(line):
            print line
        Err(why):
            print "cannot show: " + why

show("42")
show("")
show("abc")
```
```output
age 2
cannot show: age is empty
cannot show: age is not a number
```

**2. Give a default instead of failing.**

```lumen run
fn port_from(text) -> Option<Int>:
    if text == "8080":
        return Some(8080)
    return None

print unwrap_or(port_from("8080"), 80)
print unwrap_or(port_from("oops"), 80)
```
```output
8080
80
```

**3. Turn an exception into a `Result` at a boundary.** A built-in that raises, or a piece of code that may
`fail`, is wrapped once, and everything above it uses `Result`:

```lumen run
fn risky(n):
    if n > 2:
        fail("too big: " + n)
    return n * 10

fn attempt(n) -> Result<Int, Error>:
    try:
        return Ok(risky(n))
    catch e:
        return Err(e)

fn report(n):
    match attempt(n):
        Ok(value):
            print "value " + value
        Err(e):
            print "failed (" + e.code + "): " + e.message

report(1)
report(5)
```
```output
value 10
failed (R0001): too big: 5
```

**4. Always release what you acquired, and keep the reason when cleanup fails.**

```lumen run
fn with_lock(name):
    print "lock " + name
    try:
        print "working under " + name
        fail("the work failed")
    finally:
        print "unlock " + name

try:
    with_lock("db")
catch e:
    print "recovered: " + e.message
```
```output
lock db
working under db
unlock db
recovered: the work failed
```

---
