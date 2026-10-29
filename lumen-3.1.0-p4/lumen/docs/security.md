# docs/security.md

# Lumen Language Specification — Chapter 7: Security Model

Status: **Normative.** Revised in Language Specification 3.0 (capability-based security).
Publisher: Krish Kumar.

## 7.1 Scope of This Chapter

Lumen is **secure by default**: a program can compute, but it can do nothing that reaches
outside itself — read or write a file, list a directory, make a network request — unless the
person running it *explicitly permits* that operation, for that run. This chapter states the
model; `capabilities.md` is the normative reference for each capability and flag,
`security-model.md` gives the threat model and — equally important — what is **not**
protected, and `security-testing.md` explains how the guarantees are tested.

The model has two unrelated pillars:

1. **Memory safety** (§7.2), which is unconditional and requires no programmer opt-in,
   because Lumen has no mechanism capable of violating it.
2. **Capability-based I/O permissions** (§7.3–§7.5): default-deny, explicit, least-privilege
   access to the language's entire external-effect surface, enforced at the resource
   boundary by the runtime.

There is no third pillar. In particular Lumen 3.0 does **not** claim to be "completely
secure": it has no resource-limit/quota system (a permitted program can still burn CPU, memory
or disk), no sandboxing of the host process itself, and the known limits of the file and network
restrictions are listed in `security-model.md` §5. A passing test suite is necessary but does
not prove the absence of vulnerabilities; independent review has not yet been performed.

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

Lumen's external-effect surface is I/O: reading, writing, appending to and deleting files,
listing directories, and HTTP GET requests — a fixed set of eight built-in functions
(`standard-library.md` §8.4). Each is gated by one **capability**:

| Capability | Gates |
|---|---|
| `fs.read` | `read_file`, `file_exists`, `is_dir` |
| `fs.write` | `write_file`, `append_file`, `delete_file` |
| `fs.list` | `list_dir` |
| `net.http` | `http_get` |

The capabilities are independent (read does not imply write; neither implies list or network).
`net.connect`, `env.read` and `process.spawn` are *reserved* names: no operation exists behind
them, declaring one is an error, and there is no builtin that spawns a process, reads the
environment or opens a raw socket (`capabilities.md` §4, §8).

**Declaring is not granting.** A file states what it needs with a top-level declaration:

```lumen
use fs.read          # "this file reads files" — grants nothing
use net.http
```

`use fs` and `use net` remain as shorthands that declare `fs.read`+`fs.write`+`fs.list` and
`net.http`. A declaration is static (position among top-level statements does not matter), must be
at the top level of the file (inside a function or block it is a `ModuleError`), and an unknown or
reserved name is a `ModuleError` found by `lumen check`. **What a run may actually do is decided
by the host** — `lumen run --allow-read=DIR …`, or the `Policy` an embedding program builds — and
by nothing inside any `.lum` file. By default the host grants nothing (SEC-001).

## 7.4 Enforcement Semantics

Every gated operation passes, at the moment it is attempted, through **one** resource-access
function used by the interpreter and the bytecode VM alike (`capabilities.md` §2). It requires
all of:

1. the module executing the call **declared** the capability;
2. **every module that imports it**, directly or indirectly, up to the entry file, declared it too
   — a library can never hold authority its importer lacks;
3. the host **granted** the capability for this run;
4. the specific resource is inside the **granted scope**: for files, the real path (symlinks and
   `..` resolved) lies inside a granted directory or file, and the operation is then performed relative to a
   pinned descriptor of that directory so it cannot be redirected afterwards; for the network, the host (and port, if
   given) is a granted host, for the first request and for every redirect.

If any check fails, the operation is not performed and a structured `PermissionError`
(`errors.md` §4.4.7: `capability`, `operation`, `resource`, `reason`) is raised, which prints like
any other uncaught error and exits non-zero. The checks fail closed: an argument of the wrong
type, an unrecognised policy entry, a URL scheme other than `http`/`https`, or a capability with no
implementation is a refusal, never a pass-through.

Invariants that hold for every conforming implementation:

- **No capability, no access.** An operation that is not both declared and granted is not performed.
- **Declaring does not grant.** No `.lum` text can create, widen or inspect a grant.
- **A grant cannot exceed the granting policy**, and a library cannot exceed its importer.
- **Every protected operation is mediated.** There is no builtin, module, or syntax that performs
  I/O around the check, in either backend.
- **A denial cannot be converted into access** by catching the error: the operation did not happen,
  `catch` yields no data, and the next call is checked afresh.
- **The interpreter and the VM obey the same policy**, with identical results.

Grants last for one run; the command line prompts only when asked to (`lumen run --ask`, an interactive terminal,
default-deny — `capabilities.md` §3.3) and nothing is saved, so there is nothing to revoke afterwards
(`capabilities.md` §3.1). `--audit-log=FILE` records what a run requested and what was decided, without contents or
credentials (`capabilities.md` §3.4). Within a run there is no way for a program
to give a capability up or to widen one.

## 7.5 What the Capability Model Does and Does Not Protect Against

**Does protect against** a program, or any module it imports, touching the file system or
network beyond what the person running it permitted: default-deny with no flags; read, write/delete
and list as separate permissions; restriction to chosen directories (traversal with `..`, sibling
directories sharing a name prefix, and symlinks pointing outside are all refused); restriction to
chosen hosts and ports, with redirects re-checked so they cannot widen access; libraries bounded by
their importers; and denial messages that reveal neither credentials nor whether an out-of-scope
path exists. Auditing what a program *needs* is "grep the program and everything it imports for
`use`"; what it is *allowed* to do is the list of `--allow-*` flags.

**Does not protect against** (full list and rationale in `security-model.md` §5):

- anything *inside* a granted scope — a program granted `--allow-write=/home/me` can overwrite
  `~/.bashrc`; an unrestricted grant is the user's full ambient authority for that capability;
- **combinations of grants** — a program holding `fs.read` and `net.http` can send what it reads
  to any granted host; there is no information-flow control;
- **resource exhaustion** — CPU, memory, disk, number of tasks, size of output;
- races in which *another process* rearranges directories while the program runs: on POSIX the
  granted directory is pinned and files are opened relative to it without following symlinks, so such a swap
  is refused rather than followed; on Windows (no descriptor-relative open) and for single-file scopes the
  protection is weaker (`security-model.md` §5.2);
- network scoping by *name*: a granted host name that resolves to an unexpected address is
  trusted, and DNS rebinding is not defended against;
- everything outside the language runtime: bugs in the host Python interpreter, the operating
  system, or native extensions (§7.6) — the host is trusted;
- standard input/output and error, and *loading `.lum` modules* named in `use "…"` (code
  loading is not an `fs.read`), which are ambient (`security-model.md` §5.4);
- platforms other than Linux: the semantics were implemented and tested only there
  (`security-model.md` §6).

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

**Native extensions to the interpreter are trusted code.** A host that extends the Python
interpreter (adding builtins, patching `lumen.py`, or loading native modules into the same
process) is outside this model: such code runs with the host's full authority and can bypass
every check in this chapter. The model protects the host *from Lumen programs*, not from
the host's own extensions. Any builtin added to the language must be listed in the
`OPERATIONS` table in `lumen.py` with its capability — an automated check
(`assert CAPABILITY_BUILTIN_NAMES <= BUILTIN_FUNCTION_NAMES`, and the conformance suite's
default-deny matrix) fails if a gated builtin has no capability decision.

---
