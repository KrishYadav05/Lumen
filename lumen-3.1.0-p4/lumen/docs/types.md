# docs/types.md

# Lumen Language Specification — Chapter 3: Type System

Status: **Normative.** Revised for Language Specification **3.1** (Phase 4: Error Handling & Reliability).
Publisher: Krish Kumar.

Every example in this chapter marked `lumen run` or `lumen error` is executed by `tests/test_docs.py` on the
reference interpreter, on the bytecode VM and on the VM at `-O2`; the output shown is the output produced.
(Blocks marked plain `lumen` are fragments that are not run.)

## 3.1 Classification: Gradual, Optional, Nominal-for-user-types

Lumen 1.0 is a **gradually typed** language:

- Every binding, parameter, and return value has a type at all times.
- Where the programmer omits an annotation, the type is `Any` — a real type in the
  system, not "no type." `Any` is bidirectionally compatible with every other type: a
  value of any type may flow into an `Any`-typed slot, and an `Any`-typed value may
  flow into a slot expecting a specific type without static complaint.
- Type checking is a **separate, complete static pass** (`TypeChecker`) that runs after
  parsing and before any statement executes. If it reports an error, **the program does
  not run at all** — not even the first statement. This is distinct from the runtime
  error model in `errors.md`: type errors are caught before execution begins, runtime
  errors happen during execution.
- User-defined types (`struct`, `enum`) are **nominal**: two structs with identical
  field sets but different names are distinct, incompatible types. There is no
  structural subtyping and no `interface`/`trait`/protocol mechanism in v1.0. *(3.1)* Generic user
  types are nominal too: `Pair<Int, Str>` and `Pair<Str, Str>` are different types (§3.8).

```lumen error E0201
struct Celsius:
    degrees: Int

struct Kelvin:
    degrees: Int

fn freezing(t: Celsius) -> Bool:
    return t.degrees <= 0

print freezing(Kelvin { degrees: 3 })
```
```diagnostic
TypeError at line 10: 'freezing' argument 1 expects Celsius but got Kelvin
  --> example.lum:10:16
     |
  10 | print freezing(Kelvin { degrees: 3 })
     |                ^^^^^^^^^^^^^^^^^^^^^
  = code: E0201
  = expected: Celsius
  = found: Kelvin
```

## 3.2 Primitive Types

| Type | Backing representation | Notes |
|---|---|---|
| `Int` | Arbitrary-precision integer (delegates to the host's native integer type) | No fixed bit-width in v1.0; there is no overflow, ever. A future native (non-hosted) backend MAY introduce a fixed-width `Int` and a separate `BigInt`, but that is an additive post-1.0 change (`modules.md` §5.6), not a 1.0 guarantee to rely on. |
| `Float` | IEEE-754 double precision (64-bit) | Produced by any literal containing `.`, and by `/` regardless of its operands' types. |
| `Str` | Immutable text | See `language.md` §1.3 for the (no-escape, no-embedded-quote) literal grammar. Internally UTF-8. |
| `Bool` | Two values, `true` and `false` | See §3.7 for truthiness of *other* types in boolean context. |

There is no `Char` type; a single character is a `Str` of length 1.

## 3.3 Composite Built-in Types

| Type | Description |
|---|---|
| `List` | An ordered, heterogeneous, growable sequence, written `[e1, e2, ...]`. Indexed with `list[i]` (0-based). Passed and stored **by reference**: assigning a `List` to a new binding, or passing it as a function argument, shares the same underlying sequence — mutating it through either name is visible through the other. |
| `Dict` | An unordered-key, heterogeneous key/value map, written `{k1: v1, ...}`, indexed with `dict[key]`. Keys must be **hashable**: `Int`, `Float`, `Str`, `Bool`, and — *(3.1: see §3.6.1)* — `enum` and `struct` instances, which are keyed by value; using a `List` or a `Dict` as a key is a runtime `TypeError`. (The 1.0–3.0 text said a `struct` or `enum` key was also a `TypeError`; the implementation never raised it — such a key was matched by identity.) Also reference semantics, identical to `List`. |

## 3.4 User-Defined Types

### 3.4.1 `struct` — nominal record with an exact field set

```lumen
struct Point:
    x
    y
```

- Fields are declared as bare names, or — *(3.1)* — with a type, `x: Int`. An untyped field is
  effectively `Any`: nothing is checked about its contents. A typed field is checked wherever the checker can
  see a value going into it (below).
- A struct may declare **type parameters** *(3.1)*, `struct Pair<A, B>:`, which its field types may use
  (§3.8).
- Construction is `Point { x: 1, y: 2 }` and **requires the given field set to be
  exactly equal to the declared field set** — both missing fields and unexpected extra
  fields are rejected with a `TypeError` naming exactly which fields are missing and
  which are unexpected (see `errors.md` §4.4.3). That check happens when the literal is evaluated;
  it is not a static check. There are no default field values and no optional fields.
- Field access is `.field`; field mutation is `instance.field = expr`, which mutates
  the struct instance in place (structs are reference types, like `List`/`Dict`).
- *(3.1)* A value written into a **typed** field is checked against the field's type, statically, in the
  struct literal and in a field assignment (E0201, with `expected` and `found`). A struct whose
  fields are untyped is unchanged from 3.0.

```lumen run
struct Point:
    x: Int
    y: Int

let p = Point { x: 1, y: 2 }
p.x = 10
print p.x + p.y
```
```output
12
```

```lumen error E0201
struct Point:
    x: Int
    y: Int

let p = Point { x: "left", y: 2 }
```
```diagnostic
TypeError at line 5: field 'x' of struct Point expects Int but got Str
  --> example.lum:5:20
    |
  5 | let p = Point { x: "left", y: 2 }
    |                    ^^^^^^
  = code: E0201
  = expected: Int
  = found: Str
```

```lumen error E0201
struct Point:
    x: Int
    y: Int

let p = Point { x: 1, y: 2 }
p.x = "left"
```
```diagnostic
TypeError at line 6: field 'x' of struct Point expects Int but got Str
  --> example.lum:6:7
    |
  6 | p.x = "left"
    |       ^^^^^^
  = code: E0201
  = expected: Int
  = found: Str
```

Typing is gradual (§3.1): a value that reaches the field through something the checker knows nothing about —
an unannotated parameter, say — is not checked, and an untyped field is never checked.

```lumen run
struct Point:
    x
    y

let p = Point { x: 1, y: "two" }
print p
```
```output
Point { x: 1, y: two }
```

### 3.4.2 `enum` — nominal tagged union

```lumen
enum Shape:
    Circle(radius: Int)
    Rect(width: Int, height: Int)
    Dot
```

- A variant with a parenthesized list (`Circle(radius: Int)`) carries that many ordered
  payload values, supplied positionally at construction: `Circle(5)`.
- Each payload field is a bare label (`radius`, unchecked — the 1.0–3.0 form, still accepted), a label with
  a type (`radius: Int`), or a bare type (`Int`, `T`, `Tree<T>`). *(3.1)* A typed payload is checked at the
  construction site (E0201); a wrong number of values is E0304 where the checker can see the call.
  A bare name is read as a type when it is a built-in type, one of the enum's type parameters or a type the
  program declares; any other bare name is a label, as before.
- An enum may declare **type parameters** *(3.1)*, `enum Tree<T>:` (§3.8).
- A variant with no parentheses (`Dot`) is a **unit variant** — carries no payload,
  and is referenced as a bare name (`Dot`), not a call.
- Variant constructors share the global function-call namespace (`call_expr` in
  `grammar.md`); calling a variant constructor with the wrong number of payload values
  is a `TypeError` (static, E0304, when the checker sees the call; otherwise at run time).
- `enum` values are consumed exclusively through `match` (see `grammar.md` §2.3 and
  §3.6 below). *(3.1)* A `match` on a value whose type is known must cover every variant or have a
  catch-all arm (E0301, `errors.md` §4.4.6).

```lumen run
enum Shape:
    Circle(radius: Int)
    Rect(width: Int, height: Int)
    Dot

fn area(s: Shape) -> Int:
    match s:
        Circle(r):
            return 3 * r * r
        Rect(w, h):
            return w * h
        Dot:
            return 0

print area(Circle(2))
print area(Rect(3, 4))
print area(Dot)
```
```output
12
12
0
```

```lumen error E0201
enum Shape:
    Circle(radius: Int)
    Dot

let s = Circle("big")
```
```diagnostic
TypeError at line 5: Circle() argument 1 expects Int but got Str
  --> example.lum:5:16
    |
  5 | let s = Circle("big")
    |                ^^^^^
  = code: E0201
  = expected: Int
  = found: Str
```

### 3.4.3 `Option<T>` and `Result<T, E>` *(3.1)*

These two are ordinary generic enums that every program has in scope — `Some(T)`/`None` and `Ok(T)`/`Err(E)` —
defined in Lumen itself (`errors.md` §4.2.2). Everything this chapter says about generic enums applies to
them: their type arguments are checked wherever a type is written, a payload of the wrong type is E0201 with
the types shown, and `match` on them must be exhaustive. A program that declares its own enum or struct named
`Option` or `Result` (or a function or variant called `Some`, `None`, `Ok` or `Err`) keeps its own meaning.

```lumen error E0201
fn lookup(id: Int) -> Option<Str>:
    return Some(id)
```
```diagnostic
TypeError at line 2: function 'lookup' declares -> Option<Str> but returns a Option<Int> value
  --> example.lum:2:12
    |
  2 |     return Some(id)
    |            ^^^^^^^^
  = code: E0201
  = expected: Option<Str>
  = found: Option<Int>
```

## 3.5 Type Inference

Inference is **local and non-flow-sensitive** — it does not perform whole-program
unification. The rules the checker applies, exhaustively:

| Expression | Inferred type |
|---|---|
| Integer literal (no `.`) | `Int` |
| Float literal (contains `.`) | `Float` |
| String literal | `Str` |
| `true` / `false` | `Bool` |
| `[...]` | `List` (element types are not tracked individually) |
| `{...}` | `Dict` (key/value types are not tracked individually) |
| A variable reference | Its declared annotation if one was given at `let`/parameter binding, else the type inferred for its initializer, else `Any` |
| `not x`, `a and b`, `a or b` | `Bool` |
| `a == b`, `a < b`, etc. | `Bool` (operand types are not required to match statically — a mismatch is a *runtime* `TypeError` from `apply_op`, not a static one) |
| `a + b`, `a - b`, `a * b`, `a % b`, `a / b` (non-comparison `BinOp`) | Mirrors the exact runtime rules in §3.6.1, not a generic "same type" fallback: `+`/`*` know about `Str` coercion and `List`/`Str` repetition; `/` always infers `Float` for any numeric pair; mixed `Int`/`Float`/`Bool` numeric operands infer `Float` if either side is `Float`, else `Int`. Only genuinely unresolvable combinations (e.g. either operand is itself `Any`, or the combination has no defined runtime meaning) infer `Any` — the checker does not attempt to *prove* a mismatched-type operation like `[1] - [2]` is wrong ahead of time; that remains a *runtime* check (see §3.6.1) since the checker's job here is accurate inference, not exhaustive static rejection |
| `f(args)` where `f` has a declared signature | The function's declared return type (or `Any` if the return type is a generic parameter not resolvable at the call site — see §3.8), after checking each argument against the corresponding declared parameter type |
| `f(args)` where `f` has no declared signature (untyped function, or a name the checker cannot resolve to a known signature) | `Any` |
| *(3.1)* `Ok(e)`, `Err(e)`, `Some(e)`, `None` | `Result<T, _>` where `T` is the type of `e`; `Result<_, E>`; `Option<T>`; `Option<_>`. A `_` is a part the expression does not determine — it fits whatever the context asks for (`Ok(5)` fits `Result<Int, Str>`) |
| *(3.1)* a variant constructor of a generic enum, e.g. `Node(Leaf, 1, Leaf)` | the enum applied to the types its payloads bind, e.g. `Tree<Int>`; a parameter nothing binds stays unknown |
| *(3.1)* a struct literal | the struct's name, or for a generic struct the name applied to what its fields bind (`Pair<Int, Str>`) |
| *(3.1)* `e?` | the success type of `e`: `T` for `Result<T, E>` and for `Option<T>`; `Any` when `e` is `Any` (`errors.md` §4.2.3) |

```lumen error E0201
let n: Int = None
```
```diagnostic
TypeError at line 1: 'n' is declared as Int but assigned a Option<_> value
  --> example.lum:1:14
    |
  1 | let n: Int = None
    |              ^^^^
  = code: E0201
  = expected: Int
  = found: Option<_>
```

```lumen error E0201
fn f(r: Result<Str, Str>) -> Result<Int, Str>:
    let n: Int = r?
    return Ok(n)
```
```diagnostic
TypeError at line 2: 'n' is declared as Int but assigned a Str value
  --> example.lum:2:18
    |
  2 |     let n: Int = r?
    |                  ^^
  = code: E0201
  = expected: Int
  = found: Str
```

### 3.6 Static checks actually performed

The checker raises a compile-time (pre-execution) error in exactly these situations; each carries the code
shown (`errors.md` §4.11) and a source location (§4.12):

| # | Check | Code |
|---|---|---|
| 1 | `let name: T = expr` where `expr`'s inferred type is not `Any` and is not `T` | E0201 |
| 2 | A call `f(a1, ..., an)` where `f` has a declared parameter type `Ti` at position `i`, and `ai`'s inferred type is not `Any` and is not `Ti` | E0201 |
| 3 | A `return expr` inside a function declared `-> T` (where `T` is not one of the function's own type parameters — §3.8), where `expr`'s inferred type is not `Any` and is not `T` | E0201 |
| 4 | *(3.1)* A payload given to a typed enum variant, a value given to a typed struct field (in a literal and in `obj.field = expr`), or a name that was declared with a non-primitive type and is assigned something else | E0201 |
| 5 | *(3.1)* A type written with the wrong number of arguments, an unknown generic type, or one type parameter bound to two types in a single call | E0202, E0203, E0204 |
| 6 | *(3.1)* A `match` that leaves a variant of a known enum uncovered, names something that is not a variant of it, or gives a variant the wrong number of fields; a variant constructed with the wrong number of values | E0301, E0302, E0303, E0304 |
| 7 | *(3.1)* A misuse of `?` (not a `Result`/`Option`, outside a function, the wrong kind for the function, an error type the function does not return, inside `finally`) | E0401–E0404, E0407 |
| 8 | *(3.1)* A `Result` that is computed and discarded; a `-> Result`/`-> Option` function that can reach its end without returning | E0405, E0406 |

A `match` on a value of unknown type that may not cover every variant is a *warning* (W0301), not an error.
Undefined names and unknown structs are reported by the static name resolution pass (`errors.md` §4.9; E0101,
E0102), before the type checker runs.

Everything else — argument *count* mismatches for a function, struct literals that miss or add a field,
index-out-of-range, the type of what an `Any` value turns out to hold, etc. — is **not** statically checked;
those surface as runtime failures per `errors.md`. Reassigning a name whose declared type is a primitive
(`let n: Int = 1` and later `n = "x"`) is re-inferred rather than rejected, as it always was.

```lumen run
fn add(a, b):
    return a + b

try:
    print add(1)
catch e:
    print e.message
```
```output
TypeError: add() expects 2 arguments, got 1
```

### 3.6.1 Runtime coercion and operator rules (`apply_op`)

Because static checking is optional and non-exhaustive, the following **runtime**
rules govern every binary operator, and apply regardless of whether either operand
was statically annotated. Lumen's binary operators are implemented by delegating
directly to the host runtime's native operators on the underlying value — this is a
deliberate implementation choice (not an accident) that has two consequences beyond
ordinary numeric arithmetic, both stated precisely below: `Bool` participates in
arithmetic as `0`/`1`, and `+`/`*` are also defined for sequence types.

- **`+`:**
  - If **either** operand is a `Str`, both operands are converted to their display
    form (`standard-library.md` §8.3) and concatenated. This is the primary,
    intentional coercion in Lumen: `"n=" + 5` yields `"n=5"`.
  - If both operands are `List`, `+` concatenates them into a new `List`:
    `[1] + [2]` yields `[1, 2]`.
  - Otherwise, `+` is native numeric addition. `Int`/`Float` mixing silently
    promotes to `Float`. **`Bool` operands participate as `0` (`false`) or `1`
    (`true`)** — `true + 1` yields `2`, `true + true` yields `2` — because `Bool` is
    implemented as a numeric subtype at the host level and no separate boolean-vs-
    numeric restriction is layered on top of `+`. This is frozen v1.0 behavior, not
    an oversight: it mirrors the same "booleans are just 0/1" convention found in C
    and several other languages, applied consistently rather than special-cased away.
  - Any other operand combination (e.g. a `struct` on either side, or a `Dict`) is a
    runtime `TypeError`.
- **`*`:** numeric operands multiply as above (with the same `Bool`-as-`0`/`1` and
  `Int`/`Float`-promotion behavior). **`List * Int` and `Str * Int` are also
  defined and perform repetition**: `[1, 2] * 3` yields `[1, 2, 1, 2, 1, 2]`,
  `"ab" * 3` yields `"ababab"` (order does not matter: `3 * [1, 2]` behaves the
  same). Any other combination is a runtime `TypeError`.
- **`- %`:** numeric operands only (with the same `Bool`-as-`0`/`1` and
  `Int`/`Float`-promotion behavior as `+`). Unlike `+`/`*`, these have **no**
  sequence-type meaning: `[1, 2, 3] - [1]` and `"ab" - "a"` both raise a runtime
  `TypeError` naming the operator and both operand types — the host's native
  operators simply don't define `-`/`%` for sequences, so there is nothing for
  Lumen to delegate to.
- **`/`:** numeric operands only, same promotion rule. **Always** produces a
  `Float`, even for two `Int` operands (`7 / 2` is `3.5`, not `3`). Division by
  zero is a runtime failure (`"division by zero"`), not a static error and not an
  uncatchable crash — see `errors.md` §4.4.1. The same holds for `%` *(3.1; before, a host crash)*.
- **`== !=`:** defined for all types; never raises. Equality compares numerically across
  `Bool`/`Int`/`Float` (`true == 1` is `true`), for the same underlying-representation reason as
  `+`/`*` above. Comparing values of unrelated types (e.g. `1 == "a"`) never raises — it is simply
  `false`. *(3.1)* **`enum` and `struct` values compare by structure**: two values are equal when they are the
  same variant (or the same struct type) with equal payloads (or fields). Before 3.1 they compared by
  identity, so `Ok(1) == Ok(1)` was `false`; that is a disclosed change (`errors.md` §4.13). The same
  rule decides when two values are the same `Dict` key, and a `struct` used as a key is still found through
  that same value after one of its fields has been changed (a `List` or a `Dict` cannot be a key at all):

```lumen run
enum Shape:
    Circle(r)
    Dot

struct Point:
    x
    y

let seen = {}
seen[Circle(1)] = "first"
seen[Circle(1)] = "second"
seen[Dot] = "dot"
print len(seen)
print seen[Circle(1)]

let p = Point { x: 1, y: 2 }
seen[p] = "point"
p.x = 10
print seen[p]

try:
    seen[[1, 2]] = "list"
catch e:
    print e.message
```
```output
2
second
point
TypeError: cannot assign index on this value (unhashable type: 'list')
```

Equality by structure, on its own:

```lumen run
struct Point:
    x: Int
    y: Int

enum Shape:
    Circle(radius: Int)
    Dot

print Point { x: 1, y: 2 } == Point { x: 1, y: 2 }
print Point { x: 1, y: 2 } == Point { x: 1, y: 3 }
print Circle(2) == Circle(2)
print Circle(2) == Dot
print Ok(1) == Ok(1)
```
```output
true
false
true
false
true
```

- **`< > <= >=`:** defined for numeric operands (using host ordering, with the same
  `Bool`-as-`0`/`1` behavior) and for two `Str` operands (lexicographic order).
  Comparing incompatible types (e.g. `Int < Str`) raises a runtime `TypeError`.

There is **no implicit coercion of any type into `Str`** outside the `+` rule above
(use `print`/string concatenation for display; there is no dedicated
`to_string`/`str()` builtin in v1.0 — see `standard-library.md` for the closest
available primitives).

## 3.7 Truthiness (boolean context)

`if`, `while`-equivalents (there is no `while`; loops are `for`-only), and `and`/`or`
short-circuiting all use a single, uniform truthiness rule, applied to *any* value
regardless of static type:

| Value | Truthy? |
|---|---|
| `0`, `0.0` | false |
| any other `Int`/`Float` | true |
| `""` (empty string) | false |
| any other `Str` | true |
| `[]` (empty list) | false |
| any other `List` | true |
| `{}` (empty dict) | false |
| any other `Dict` | true |
| `false` | false |
| `true` | true |
| any `struct` or `enum` instance | true (unconditionally — instances have no "empty" state) |

## 3.8 Generics

```lumen
fn identity<T>(x: T) -> T:
    return x

fn first<T>(a: T, b: T) -> T:
    return a
```

- Type parameters are declared with `<T, U, ...>` immediately after the function name
  and are visible as ordinary type-annotation names (`T`) within that function's own
  parameter list and return-type position only.
- **Unification happens per call-site, across that call's arguments only.** When
  checking a call `f(a1, ..., an)` where `f` is generic: for each parameter position
  annotated with a type parameter `T`, the checker records the inferred type of the
  corresponding argument as `T`'s binding for *this call*. If `T` is used at more than
  one parameter position and the arguments' inferred types disagree, this is a
  compile-time `TypeError` naming both conflicting types (e.g. `first(1, "two")` is
  rejected because `T` cannot be both `Int` and `Str` in that call). The return type,
  if it is `T`, is substituted with `T`'s binding for that call (or `Any` if `T` was
  never bound because all arguments were themselves `Any`).
- **Inside the function's own body, a parameter annotated with a type parameter is
  treated as `Any`**, not as an abstract, constrained type. This is a deliberate,
  documented limitation, not an oversight: Lumen 1.0 generics provide call-site
  argument-consistency checking, but **do not** verify that the function body itself
  uses `T`-typed values consistently (a generic function's body can, today, silently
  misuse its type-parameter-typed values in a way that would only fail at runtime, not
  statically, on a mismatched instantiation). A future minor version may tighten this
  by threading `T` through body-internal checking as an opaque, non-`Any` type; doing
  so is additive and does not change any conforming 1.0 program's *accepted* status
  (only, in principle, could newly reject some program that 1.0 accepted — such a
  change would therefore ship as `2.0`, per `modules.md` §5.6).
- *(3.1)* **`enum` and `struct` declarations may take type parameters too**, with the same `<T, U, ...>`
  syntax after the name: `enum Tree<T>`, `struct Pair<A, B>`. The parameters are visible in that declaration's
  payload and field types. `Option<T>` and `Result<T, E>` (§3.4.3) are such enums.
- *(3.1)* A type is written with its arguments, `Tree<Int>`, `Pair<Int, Str>`, `Result<Option<Int>, Str>`,
  and the arguments are checked all the way down. There is no subtyping: `Tree<Int>` is not a `Tree<Str>`.
  A part the checker cannot determine is unknown (`_` in a message) and fits anything, so gradual code keeps
  working. The wrong number of arguments is E0202, a generic type nothing declares is E0203, and one
  parameter bound to two different types in one call is E0204 (`errors.md` §4.11.1).
- *(3.1)* A construction infers the arguments from its values (`Node(Leaf, 1, Leaf)` is a `Tree<Int>`), and an
  inferred type must fit the annotation it is given:

```lumen run
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

print depth(Node(Node(Leaf, 1, Leaf), 2, Leaf))
```
```output
2
```

```lumen error E0201
enum Tree<T>:
    Leaf
    Node(left: Tree<T>, value: T, right: Tree<T>)

let t: Tree<Int> = Node(Leaf, "x", Leaf)
```
```diagnostic
TypeError at line 5: 't' is declared as Tree<Int> but assigned a Tree<Str> value
  --> example.lum:5:20
    |
  5 | let t: Tree<Int> = Node(Leaf, "x", Leaf)
    |                    ^^^^^^^^^^^^^^^^^^^^^
  = code: E0201
  = expected: Tree<Int>
  = found: Tree<Str>
```

```lumen run
struct Pair<A, B>:
    first: A
    second: B

let p: Pair<Int, Str> = Pair { first: 1, second: "b" }
print p.first
print p.second
```
```output
1
b
```

```lumen error E0201
struct Pair<A, B>:
    first: A
    second: B

let p: Pair<Int, Str> = Pair { first: "a", second: "b" }
```
```diagnostic
TypeError at line 5: 'p' is declared as Pair<Int, Str> but assigned a Pair<Str, Str> value
  --> example.lum:5:25
    |
  5 | let p: Pair<Int, Str> = Pair { first: "a", second: "b" }
    |                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  = code: E0201
  = expected: Pair<Int, Str>
  = found: Pair<Str, Str>
```

A generic function's result is inferred the same way, from its arguments:

```lumen run
fn wrap<T>(x: T) -> Option<T>:
    return Some(x)

let a: Option<Int> = wrap(1)
print a
```
```output
Some(1)
```

```lumen error E0201
fn wrap<T>(x: T) -> Option<T>:
    return Some(x)

let b: Option<Str> = wrap(1)
```
```diagnostic
TypeError at line 4: 'b' is declared as Option<Str> but assigned a Option<Int> value
  --> example.lum:4:22
    |
  4 | let b: Option<Str> = wrap(1)
    |                      ^^^^^^^
  = code: E0201
  = expected: Option<Str>
  = found: Option<Int>
```

And an unannotated function is `Any`, which fits everything — the check is made where a type is written down:

```lumen run
fn id(x):
    return x

let r: Result<Int, Str> = id(Ok(1))
print r
let s: Result<Int, Str> = id(Ok("oops"))
print s
```
```output
Ok(1)
Ok(oops)
```

The body of a generic *function* still sees its type-parameter-typed values as `Any`, as described above.

## 3.9 Type Safety Guarantee (and its precise boundary)

Lumen provides **memory safety unconditionally** (see `security.md` §6.1 for the exact
mechanism — there is no way to construct an invalid reference, out-of-bounds pointer,
or use-after-free in Lumen 1.0; there is no pointer type at all). Lumen does **not**
provide unconditional *type* safety in the sense of "a well-typed program cannot go
wrong": because typing is gradual and `Any` is pervasive by default, a program with no
annotations at all receives no static guarantee whatsoever, and even a fully-annotated
program can still fail at runtime in the specific, enumerated ways in §3.6.1 (this is
inherent to any gradual type system, not a Lumen-specific gap). What annotations *do*
guarantee, when present: if the static pass accepts a program, no `let`, argument, or
`return` covered by an annotation will ever be observed violating that annotation
during execution of that same, unmodified program (the checker's guarantees are sound
with respect to the checks enumerated in §3.6 — it does not silently accept and then
have the runtime disagree with it on those specific check kinds). *(3.1)* The boundary is the same one:
a value that reaches a typed struct field, a typed payload or a `Result<...>` type through `Any` — an
unannotated parameter or function — is not checked there, so `Any` can put a value of the wrong type
into a typed place and the program will only find out when something uses it. Nothing in 3.1 turns a
type annotation into a runtime assertion.

## 3.10 Functions Are Not First-Class Values

**A function or `enum` variant constructor name can only be used as the target of a
direct call expression (`grammar.md` §2.5.2, `name(args)`); it cannot be read,
assigned, stored, or passed as an ordinary value in Lumen 1.0.** Function names live in
a namespace entirely separate from variable bindings (`let`), and only the call-syntax
resolution path (`grammar.md` §2.5.2) ever looks a name up there. Consequently, all of
the following — which resemble working code in most languages with first-class
functions — are `NameError`s in Lumen 1.0, not because `double` is undefined as a
*function* (it works fine when called directly, `double(5)`), but because reading it
as a bare value looks it up in the *variable* namespace, where it was never bound:

```lumen
fn double(n):
    return n * 2

let g = double          # NameError: 'double' is not defined
print apply(double, 5)  # NameError: 'double' is not defined (as an argument, same rule)
let list_of_fns = [double]  # NameError: 'double' is not defined (inside a literal, same rule)
```

This means Lumen 1.0 has **no higher-order functions**: no function can accept another
function as a parameter, return one, or store one in a `List`/`Dict`/`struct` field —
there is no syntax that would let such a value be produced in the first place. `spawn`
(`concurrency.md` §6.2) is not an exception to this: `spawn f(args)` is a dedicated
grammar production that names `f` directly in call position, the same restricted
context every other call already requires — it does not pass `f` around as a value
either.

This is a deliberate v1.0 scope boundary, consistent with `language.md` §1.1 goal 5
(small orthogonal core): introducing first-class functions is additive at the value-
model level (it would require function names to also resolve through the ordinary
variable-lookup path, and a new callable-value kind) and is a natural candidate for a
future minor version, but is out of scope for the 1.0 freeze.

---
