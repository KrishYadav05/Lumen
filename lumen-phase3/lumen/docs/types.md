# docs/types.md

# Lumen Language Specification — Chapter 3: Type System

Status: **Normative.**

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
  structural subtyping and no `interface`/`trait`/protocol mechanism in v1.0.

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
| `Dict` | An unordered-key, heterogeneous key/value map, written `{k1: v1, ...}`, indexed with `dict[key]`. Keys must be **hashable** (in practice: `Int`, `Float`, `Str`, `Bool`; using a `List`, `Dict`, `struct`, or `enum` instance as a key is a runtime `TypeError`). Also reference semantics, identical to `List`. |

## 3.4 User-Defined Types

### 3.4.1 `struct` — nominal record with an exact field set

```lumen
struct Point:
    x
    y
```

- Fields are declared as bare names; there is no per-field type annotation grammar in
  v1.0 (a struct's fields are always effectively `Any`-typed at construction — nothing
  is statically checked about their contents).
- Construction is `Point { x: 1, y: 2 }` and **requires the given field set to be
  exactly equal to the declared field set** — both missing fields and unexpected extra
  fields are rejected with a `TypeError` naming exactly which fields are missing and
  which are unexpected (see `errors.md` §4.6). There are no default field values and
  no optional fields.
- Field access is `.field`; field mutation is `instance.field = expr`, which mutates
  the struct instance in place (structs are reference types, like `List`/`Dict`).

### 3.4.2 `enum` — nominal tagged union

```lumen
enum Option:
    Some(value)
    None
```

- A variant with a parenthesized name list (`Some(value)`) carries that many ordered,
  unnamed payload values, supplied positionally at construction: `Some(5)`.
- A variant with no parentheses (`None`) is a **unit variant** — carries no payload,
  and is referenced as a bare name (`None`), not a call.
- Variant constructors share the global function-call namespace (`call_expr` in
  `grammar.md`); calling a variant constructor with the wrong number of payload values
  is a runtime `TypeError`.
- `enum` values are consumed exclusively through `match` (see `grammar.md` §2.3 and
  §3.6 below).

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

### 3.6 Static checks actually performed

The checker raises a compile-time (pre-execution) `TypeError` in exactly these
situations:

1. `let name: T = expr` where `expr`'s inferred type is not `Any` and is not `T`.
2. A call `f(a1, ..., an)` where `f` has a declared parameter type `Ti` at position
   `i`, `ai`'s inferred type is not `Any`, and it is not `Ti`.
3. A `return expr` inside a function declared `-> T` (where `T` is not one of the
   function's own generic type parameters — see §3.8), where `expr`'s inferred type is
   not `Any` and is not `T`.

Everything else — argument *count* mismatches, struct field mismatches, undefined
names used only inside dead code paths, index-out-of-range, etc. — is **not** statically
checked; those surface as runtime failures per `errors.md`.

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
  uncatchable crash — see `errors.md` §4.3.
- **`== !=`:** defined for all types via host equality; never raises. Equality
  compares numerically across `Bool`/`Int`/`Float` (`true == 1` is `true`), for the
  same underlying-representation reason as `+`/`*` above. Comparing values of
  unrelated types (e.g. `1 == "a"`) never raises — it is simply `false`.
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
- Generics apply to `fn` only in v1.0. There is no generic `struct` or `enum` syntax.

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
have the runtime disagree with it on those specific three check kinds).

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
