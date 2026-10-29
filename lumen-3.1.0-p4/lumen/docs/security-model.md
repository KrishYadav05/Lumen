# docs/security-model.md

# Lumen Security Model: Threat Model, Trust Boundaries, and Known Limits

Status: **Informative** (the normative rules are in `security.md` chapter 7 and
`capabilities.md`). Version: Lumen 3.0.0-5c, Language Specification 3.0.
Publisher: Krish Kumar.

This document says what the capability system is *for*, what it trusts, and — the part
most security documents skip — what it does **not** protect. Lumen does not claim to be
"completely secure", and nothing here should be read as a claim that the model has been
independently reviewed: it has not (`PHASE5_STATUS.md`).

## 1. Purpose and the one-sentence model

> A Lumen program can do nothing outside itself unless the person running it explicitly
> permits that operation, for that run, and no code inside the program can change that.

The goal is **least privilege with predictable enforcement**: run a script you downloaded
with `--allow-read=./data` and know that it cannot read `~/.ssh`, cannot write anywhere,
cannot reach the network, and that a library it imports cannot do any of those things either.

## 2. Assets and adversaries

**Assets protected:** the contents and existence of files outside the granted scope; the
integrity of files outside the granted write scope; the ability to make network requests to
non-granted hosts; (indirectly) any secret reachable through those.

**Adversaries considered:**

| Adversary | Example | Handled by |
|---|---|---|
| A *less-trusted program* the user chooses to run | a script from the internet | default-deny; scoped grants; four checks |
| A *less-trusted library* imported by a trusted program | a package in `lumen_packages/` | importer ceiling (a library cannot exceed its importer); host scopes apply to it too |
| A program that *tries to trick the enforcement* | `..`, symlinks, `file://`, redirects, wrong argument types, catching denials, declaring late, importing a data file, writing a module and importing it | §3 |
| A *remote server* the program talks to | a redirect to an internal host | redirect re-checking |

**Not considered adversaries** (out of model): the person running the program (they decide
the grants); the operating system and the Python interpreter; other local users with
write access to directories the program uses *concurrently* (see §5.2); a malicious
embedder; native extensions loaded into the interpreter process.

## 3. Trust boundaries

```
 ┌──────────────────────────── trusted ────────────────────────────┐
 │  the person / CI job                                             │
 │     └─ command-line flags  (--allow-*)   or   embedding `Policy`  │
 │  lumen.py (parser, resolver, interpreter, VM, capability_call)    │
 │  CPython, the standard library, the OS                            │
 └──────────────────────────────────────────────────────────────────┘
                  │  Policy (immutable)                 ▲ results of permitted operations only
                  ▼                                     │
 ┌──────────────────────── untrusted (bounded) ───────────────────┐
 │  the entry .lum program ──imports──► library.lum ──► …           │
 │  each can declare needs; none can grant; each is bounded by      │
 │  its importers and by the Policy                                 │
 └──────────────────────────────────────────────────────────────────┘
```

- **The `Policy` is the only source of authority.** It is created by the host before any
  Lumen code runs, is immutable, and is not reachable from Lumen code in any form (no builtin
  reads, copies or changes it).
- **One mediation point.** Every file, directory and network operation a Lumen program can
  perform goes through `capability_call`, which both backends call; the table `OPERATIONS`
  maps each gated builtin to its capability. A conformance test parses `lumen.py` and fails if
  a file/network/process/dynamic-code primitive appears anywhere outside the reviewed
  resource-access layer, and a second test fails if a builtin is neither gated nor on the
  reviewed list of non-gated builtins (`security-testing.md` §4). These are tripwires for
  *additions*; they do not prove the reviewed list is correct.
- **Libraries.** A module's authority is the intersection of: what it declared, what each
  module on its import chain declared, and what the Policy grants. There is no way for a library
  to be given more than its importer has.
- **Fail closed.** Unknown capability names, reserved capabilities, malformed scopes, wrong
  argument types, URL schemes other than `http(s)`, and operations with no implementation are
  all refusals, not pass-throughs.

## 4. Native extensions and the host

Lumen has no FFI: no syntax to declare a foreign function and no builtin that loads native code
(`security.md` §7.6). **Native extensions to the interpreter are trusted code**: a host that
patches `lumen.py`, registers extra builtins, or loads native modules into the interpreter
process gives that code the host's full authority; it can bypass every check here, and the
model makes no attempt to prevent that. A builtin added to the language must be given a capability
in `OPERATIONS`; the tests above fail until it is.

The Python interpreter and the operating system are trusted. A memory-safety bug or sandbox
escape in CPython, or a kernel bug, is outside the model; Lumen's own memory-safety claim
(`security.md` §7.2) is that *Lumen* has no construct that can violate it.

## 5. What is NOT protected, and known limitations

Read this section before relying on the model. Each item states the limit, whether it is
by design or a gap, and what to do about it.

### 5.1 By design: anything inside a granted scope

- **An unrestricted grant is ambient authority.** `--allow-read` with no `=` lets the program
  read everything the user can read — including `~/.ssh/*`, `.env` files, browser profiles — and,
  on Linux, `/proc/self/environ`, i.e. the process **environment** (the reserved `env.read`
  capability is not implemented, so an unrestricted `fs.read` is also an environment read).
  `--allow-net` with no `=` lets it contact any host. Scope your grants.
- **Write is powerful.** An unrestricted `--allow-write`, or a scope that contains a startup
  file (`~/.bashrc`, `~/.ssh/authorized_keys`, a cron directory, a directory whose scripts you
  later run, the Lumen installation itself), is equivalent to arbitrary code execution as you,
  later. `fs.write` also permits deleting any file in scope.
- **Combinations exfiltrate.** `fs.read` plus `net.http` lets a program send what it reads to
  any granted host (in a URL — `http_get` is GET-only). The model has no information-flow
  control; grant both only to code you trust with both.
- **Scope entries are trusted as written.** `--allow-read=/` is "everything". A scope that
  includes `/proc` or `/dev` exposes process memory maps/environ or devices; special files
  (FIFOs, devices) inside a scope can be opened and may block.

### 5.2 Races with another process (mostly closed in 3.0.0-5b)

Scoped file access resolves the requested path to select the granted directory and check
containment, then performs the operation **relative to a pinned descriptor of that directory**, one
component at a time with `O_NOFOLLOW|O_DIRECTORY`, never by path string (`capabilities.md` §5 rule 4).
An attacker who can rearrange directories concurrently — another process or user; the Lumen program
cannot, it has no `mkdir`, `rename` or `symlink` builtin — therefore cannot redirect an operation:
swapping a directory on the path for a link, or swapping an ancestor of the granted directory, makes the
walk fail and the operation is refused. This is tested deterministically (the swap is injected between the check
and the use, for every operation, and for an ancestor of the granted directory, also with the Linux `/proc`
backstop disabled) and with a real racing thread; the same tests fail against the 3.0.0-5a code, where a lost
race could create files outside the scope, delete an outside file, and reveal or list outside paths.

What remains:

- **No descriptor-relative open on Windows.** There scoped access falls back to the earlier path-based check
  (resolve, check, open without following the final component): the 5a limits apply — a lost race can create an
  empty file outside the scope, and existence/type/listing/delete have no post-check. Windows is untested
  anyway (§5.6).
- **Single-file scopes and scopes that did not exist when the policy was built** are anchored through their
  parent directory *by path*; on Linux the opened parent is verified through `/proc`, elsewhere a swap of an
  ancestor of that parent in the instant before it is opened is not caught.
- **The grant follows the directory, not the name.** If the host deletes or renames a granted directory while the
  run is in progress, the program keeps operating on the original directory (or, if it was deleted, operations
  fail) until the next run. This is deliberate: a path that is re-resolved is a path that can be redirected.
- Races that move a file to *another place inside the scope* are harmless by definition.
- Not exercised: a real hostile second process on a real multi-user system. The tests simulate the attacker's
  moves exactly and with a racing thread, which is evidence, not proof.

### 5.3 Gap (by design of the platform): hard links and special files

A **hard link** inside the scope to a file outside it is the same file; it is accessible as
any in-scope file. A Lumen program cannot create links (no builtin), but pre-existing ones are
honoured. A symlink *named `*.lum`* that points at a data file is loadable as a module (§5.4),
for the same reason: the model trusts the directory contents the host chose to expose.

### 5.4 Ambient things that are not capabilities

- **Standard streams.** `print` writes to stdout; `read_line()` reads stdin; both are always
  available. A program can print terminal escape sequences, and can read whatever you pipe
  into it. Do not pipe secrets into a program you do not trust.
- **The clock.** `now()` is always available (and permits timing measurement).
- **Loading `.lum` modules.** `use "x.lum" as m` reads and runs another Lumen file. It is
  *code loading*, chosen by whoever wrote the `use` line, and is **not** gated by `fs.read`.
  Mitigations in 3.0: only **relative** paths ending in **`.lum`** load; any other name — including
  every non-Lumen data file such as `secret.txt`, `.env`, `/etc/passwd` — gets exactly the same
  `cannot find module` error as a file that does not exist, so imports cannot be used to read or probe
  data files (found and fixed in Phase 5; before this a parse error echoed part of the file).
  *Residual:* a program can `use "../../elsewhere/x.lum"` and (a) learn whether a **`.lum`** file
  exists there, (b) see a parse/runtime error naming tokens of it if it is not a valid module, and
  (c) run it — bounded by the same importer ceiling and the same Policy, so it gains no
  capability. Do not keep secrets in `.lum` files; run untrusted programs in a directory that
  contains only what they may import.
- **Resource use.** CPU, memory, disk, thread/task count, response size (an `http_get` body is
  read whole), and output volume are unbounded. A program can loop forever or exhaust memory
  with no capability at all. There are no quotas in this version.

### 5.5 Network

- **Matching is by name, not address.** `localhost` and `127.0.0.1` are different hosts to the
  policy; a granted name is trusted to resolve to something sensible. **DNS rebinding** (a name that
  resolves to an internal address at request time) and **SSRF** against a granted host that
  proxies requests inward are not defended against. There are no wildcards or IP-range rules.
- A trailing-dot name (`example.com.`) or non-ASCII host does not match an ASCII scope entry (it is
  denied — fail closed).
- Environment proxy settings (`HTTP_PROXY`, …) are honoured by the HTTP library; the scope is
  checked against the URL the program wrote, not the proxy. The environment is host-controlled.
- Responses are read whole into memory; the timeout is 5 s per request.
- Only GET exists; credentials in URLs are sent if the program supplies them (and are redacted
  from denial messages).

### 5.6 Platforms

Capability enforcement was written and tested **on Linux only**. Not verified on macOS or
Windows: case-insensitive file systems (scope comparison is by normalised case on Windows and
exact on POSIX, which fails closed), Windows reserved device names, alternate data streams,
8.3 short names, junctions and UNC paths. SEC-011 (cross-platform consistency) is therefore
**not met**; see `PHASE5_STATUS.md`.

### 5.7 Process-level

- No sandboxing of the interpreter process: the program runs in the user's account; a granted
  capability is *that account's* authority. For hostile code also use OS facilities
  (containers, seccomp, a dedicated user). The model is defence in depth, not a substitute.
- Errors reveal what the program itself supplied (the path or host it asked for) and the
  capability and reason, nothing about the rest of the file system. Messages for failures
  *after* the checks pass (for example "No such file or directory") describe the permitted
  resource only.

### 5.8 The consent prompt and the audit log (3.0.0-5c)

Both are conveniences around the runtime, and neither adds enforcement (`capabilities.md` §3.3, §3.4).

**The consent prompt (`lumen run --ask`).** A user interface, not a boundary: whatever the person types becomes a
`Policy`, and the runtime enforces that `Policy` exactly as it would flags. What it can and cannot do:

- It can only **add** capabilities the entry program declared and no flag granted; it cannot widen a flag's
  scope, offer an undeclared capability, or run without a terminal on stdin **and** stderr.
- Every default — Enter, end of input, a wrong answer, anything but `y` at the end — is a denial or a cancellation
  that runs nothing. `everywhere` has to be typed in full.
- It does **not** protect a person from themselves: someone who types `everywhere` for a program they should not
  trust has granted it. The display shows only what the program *declared*; a program may declare more than it
  needs, and what it needs is not verified.
- "A terminal is present" is a heuristic for "a person is present". Software can drive a terminal (`script`,
  `expect`, a pty), so `--ask` must not be used where that matters; use explicit `--allow-*` flags instead.
- Declarations carry no scope, so the person supplies one; a program cannot propose its own.
- **Not implemented: saved grants.** Every run starts from the empty policy. A persisted grant would be standing
  authority that outlives the run, with its own revocation, integrity and staleness problems, and nothing in the
  spec's checklist requires one beyond "follow a documented policy and can be revoked", which "none are saved"
  satisfies trivially. This is a decision to confirm, not an oversight.

**The audit log (`--audit-log=FILE`).** A record of requests, grants and decisions, with no contents, credentials
or URL paths/queries. What it does not do:

- It is **not tamper-evident**. The host opens it (mode 0600) and the program has no handle, and a policy that would
  let the program write or delete it is refused at start-up — but anyone with the host's file permissions can edit it,
  and nothing signs or chains the records.
- It records the **capability layer only**: not stdin/stdout, the clock, memory or CPU use, and not imports (loading
  `.lum` source is not a gated operation, §5.4). It is a record of *decisions*, not a trace of behaviour.
- `allow` means *authorised before the operation*, not *the operation succeeded*.
- Resources are logged **as the program wrote them**: a relative path is not resolved, and a path can itself be
  sensitive (the log is private for that reason). URLs are reduced to `scheme://host:port`.
- Volume is unbounded for a program that loops; `--audit-level=denied` is the cheap mode. A log that cannot be
  written stops the operation (fail closed), so a full disk stops an audited program rather than letting it run
  unrecorded.
- A program granted `--allow-read` over the log's directory can read it (paths and decisions only).

## 6. Requirement status (Phase 5 spec)

| ID | Requirement | Status in 3.0.0-5c |
|---|---|---|
| SEC-001 | Default deny | **Met** (empty policy; tested on both backends) |
| SEC-002 | Explicit capabilities | **Met** for the four implemented capabilities; `net.connect`, `env.read`, `process.spawn` reserved, not implemented |
| SEC-003 | Granular permissions | **Met** (read / write / list / net independent) |
| SEC-004 | Runtime enforcement at the resource boundary | **Met**, with the limits in §5.2 (race-safe on POSIX) |
| SEC-005 | CLI permission prompts / consent | **Met, with the limits in §5.8**: `run --ask` (interactive terminal only; non-interactive policy is flags-only default-deny, documented); saved grants deliberately not implemented |
| SEC-006 | Structured denial errors | **Met** |
| SEC-007 | Filesystem directory restrictions | **Met** for traversal/symlinks/prefix siblings and, on POSIX, check-then-use races; Windows fallback §5.2 |
| SEC-008 | Network host restrictions | **Met** (host/port, redirects); name-based limits §5.5 |
| SEC-009 | Dependency isolation | **Met** (importer ceiling; scopes apply to libraries) |
| SEC-010 | Audit logging | **Met, with the limits in §5.8**: opt-in `--audit-log`, no sensitive data, fails closed; not tamper-evident |
| SEC-011 | Cross-platform consistency | **Not met**: Linux only |
| SEC-012 | Security documentation | **Met** by this set of documents, subject to review |

## 7. Findings fixed during Phase 5

Writing the tests found three pre-existing gaps in Lumen 2.x and, in 5b, one gap in the 5a implementation (all fixed, all with regression
tests that verify actual access, not messages):

1. **`file://` through `http_get`.** A program granted only `net` could read any local file with
   `http_get("file:///…")` (and via a redirect to a `file:` URL), because the HTTP library
   opens `file:` URLs. Now only `http`/`https` are accepted, at the first URL and at every redirect.
2. **Integer arguments reached the OS.** `read_file(0)` read file descriptor 0 (stdin) and other
   integers named other descriptors; `write_file(path, 5)` crashed the interpreter with a raw
   Python exception. Gated builtins now validate argument types and counts first.
3. **Imports as a read primitive.** `use "secret.txt" as s` was not gated and its error echoed
   the file's first token; the error also revealed which paths exist. Only relative `*.lum` files
   load now, with one error for "missing" and "not importable".

4. **Check-then-use races (found and fixed in 5b).** In 3.0.0-5a an attacker who could rename a directory inside the
   granted scope and put a symlink in its place, in the window after the check, could make a scoped `write_file`/
   `append_file` create a file outside the scope, `delete_file` delete an outside file, and `file_exists`/`is_dir`/
   `list_dir` reveal or list outside paths. A deterministic reproduction and a real racing thread now fail on the 5a
   code and pass on 5b (§5.2). Reads and writes of existing files were already refused by the post-open check on Linux.

These were found by writing adversarial tests; absence of further findings does not mean absence
of vulnerabilities. Independent review is recommended before relying on this model against a
determined attacker.
