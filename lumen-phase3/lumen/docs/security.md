# docs/security.md

# Lumen Language Specification — Chapter 7: Security Model

Status: **Normative.**

## 7.1 Scope of This Chapter

Lumen 1.0's security model has exactly two, unrelated pillars, which this chapter
covers in full:

1. **Memory safety** (§7.2), which is unconditional and requires no programmer opt-in,
   because Lumen has no mechanism capable of violating it.
2. **I/O capability gating** (§7.3–§7.5), which is an explicit, opt-in, per-module
   permission model covering the language's entire external-effect surface.

There is no third pillar. Lumen 1.0 has no sandbox configuration surface beyond what's
described in §7.3 (no resource-limit/quota system, no filesystem-jail/chroot-equivalent,
no network-allowlist-by-host mechanism) — capability grants are all-or-nothing per
capability name, not fine-grained per-path or per-host.

## 7.2 Memory Safety Guarantee

**Lumen 1.0 provides unconditional memory safety with no exceptions, and has no
mechanism for a program to violate it.** This is not a runtime-enforced restriction on
top of an otherwise-unsafe substrate — it is structural: Lumen has **no pointer type,
no address-of operator, no raw memory access, no manual allocation/deallocation, and no
`unsafe` block of any kind.**

- Every Lumen value (`types.md` Chapter 3) is either an immutable primitive (`Int`,
  `Float`, `Str`, `Bool`) or a reference to a heap-allocated, garbage-collected object
  (`List`, `Dict`, `struct` instance, `enum` instance, function/module/task/channel
  handle). There is no way to obtain, inspect, or synthesize the underlying address of
  any value.
- All memory is reclaimed by the host runtime's automatic garbage collector. There is
  no explicit `free`/`delete`, and consequently **no use-after-free, no double-free, and
  no dangling reference are constructible in Lumen 1.0**, in any program, by any means.
- Out-of-bounds access is impossible to leave unchecked: `list[i]` and `dict[k]` are the
  only indexing forms in the language (`grammar.md` §2.5), and every such access is
  bounds/key-checked, surfacing as a normal, catchable runtime failure
  (`errors.md` §4.4.4) rather than reading or writing outside the object's storage.
- **There is no pointer arithmetic in Lumen 1.0** — the concept does not apply, since
  there are no pointers to perform arithmetic on. This is a stronger guarantee than "we
  checked the arithmetic is safe": there is no operator or operation in the grammar
  (`grammar.md` Chapter 2) that produces or consumes an address-like value at all.

### 7.2.1 Why there is no `unsafe` block

Some hosted/systems languages expose an explicit `unsafe { ... }` escape hatch for
low-level operations the type/memory checker cannot otherwise verify. **Lumen 1.0 has
no such construct, and no way to introduce one from within a `.lum` file.** The
language's entire surface for interacting with anything outside its own memory-safe
value model is the fixed, closed set of built-in functions catalogued in
`standard-library.md`; there is no FFI declaration syntax, no inline-native-code block,
and no reflection mechanism capable of reaching outside the interpreter's own object
model. This is deliberate: memory safety is meant to hold for every conforming Lumen
1.0 program without a programmer needing to reason about an escape hatch at all.

## 7.3 The Capability Model

Lumen 1.0's **only** external-effect surface is I/O: reading/writing files and making
HTTP requests, via exactly three built-in functions (`read_file`, `write_file`,
`http_get` — full signatures in `standard-library.md` §8.7). **No Lumen program has
access to any of these by default.** Access is granted per-capability, per-module
instance, via a `use` statement naming the capability:

```lumen
use fs     # grants read_file and write_file
use net    # grants http_get
```

- `fs` and `net` (`language.md` §1.7) are the **only** two recognized capability names
  in v1.0. `use x` for any other bare name is a `ModuleError` (`errors.md` §4.4.9), not
  silently ignored — there is no way to accidentally grant a capability that doesn't
  exist, and no way to define a new capability name from Lumen source.
- There is no capability for process spawning, environment-variable access,
  arbitrary OS syscalls, raw sockets, or any I/O surface beyond the three functions
  named above, because no built-in function exists in v1.0 that would need such a
  capability — the capability set is exactly as large as the gated surface and no
  larger.

## 7.4 Enforcement Semantics

- Capability grants are tracked **per `Interpreter` instance** (which corresponds
  one-to-one with a loaded module, `modules.md` §5.5) in a single set, checked at the
  point of every `read_file`/`write_file`/`http_get` call
  (`errors.md` §4.4.7 for the exact failure message).
- A `use fs`/`use net` statement takes effect **imperatively, the moment it executes**,
  and, once executed, the grant persists for the remaining lifetime of that module
  instance regardless of what scope the `use` statement was textually inside —
  including a `use` statement inside a function body, only having effect once that
  function has actually been called at least once. A `use` statement inside a branch
  that is never taken (e.g. `if false: use fs`) never takes effect, and the capability
  remains ungranted.
- **Capability grants do not cross module boundaries in either direction**
  (`modules.md` §5.5): a module that imports another does not share its capability
  grants with it, and an imported module granting a capability for its own internal use
  does not grant it back to the importer. Each `.lum` file that itself calls a
  gated built-in must itself contain the corresponding `use` statement, regardless of
  what any importer or importee has granted.
- There is no way to *revoke* a capability once granted within a module instance's
  lifetime, and no scoped/temporary grant form (e.g. "grant `fs` only for this block")
  in v1.0 — grants are monotonic for the remaining life of that module instance.

## 7.5 What the Capability Model Does and Does Not Protect Against

**Does protect against:** a program (or a transitively-imported module) silently
touching the filesystem or network without the top-level author's explicit,
source-visible opt-in. Auditing a Lumen 1.0 program's full I/O surface is exactly
"grep the file (and everything it imports) for `use fs` / `use net`."

**Does not protect against:** anything once a capability has been granted. Granting
`fs` grants *unrestricted* read/write access to any path the host OS user running the
process can reach — there is no path allowlist, no read-only vs. read-write
distinction, and no per-call re-authorization. Granting `net` grants `http_get` access
to any URL — there is no host allowlist. This is a coarse-grained,
capability-*category* model (§7.1), not a fine-grained permission system; treat a
granted capability the same as you would treat handing that code the equivalent
ambient OS-level permission.

## 7.6 FFI Boundary

**Lumen 1.0 has no foreign-function interface.** There is no syntax for declaring an
external function implemented in another language, no dynamic-library-loading
built-in, and no mechanism for a `.lum` program to call, or be called by, code outside
the interpreter's own object model, beyond the fixed built-in surface in
`standard-library.md`. Consequently there is no "FFI boundary" to specify safety rules
for in this version — the boundary doesn't exist. If a future version introduces an
FFI, it would necessarily be the first construct in Lumen capable of violating §7.2's
memory-safety guarantee, and any such addition would need to be a new major version
with its own, separate safety chapter (`modules.md` §5.6 evolution process) rather than
an additive change to 1.0.

---
