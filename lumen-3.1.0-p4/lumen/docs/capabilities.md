# docs/capabilities.md

# Lumen Language Specification — Capabilities

Status: **Normative.** Introduced in Language Specification 3.0 (Lumen 3.0.0-5a; this text as of 3.0.0-5c).
Publisher: Krish Kumar.

This document defines every capability a Lumen program can need, what it means to
*declare* one, how the *host* grants one, the four checks every sensitive operation
must pass, and the exact meaning of each capability's scope. Chapter 7
(`security.md`) states the security model; `security-model.md` states its threat
model and its limits; this document is the reference the tests in
`tests/conformance/test_capabilities.py` and `tests/conformance/test_security.py` check
line by line.

> **Breaking change from 2.x.** In Language Specification 2.x, executing `use fs`
> *granted* file access. In 3.0 it only *declares* it. A 2.x program that used
> `use fs` and ran with no flags now fails with a `PermissionError` until the run is
> given `--allow-read` / `--allow-write` / `--allow-list` (or `--allow-fs`). This is
> intended: a program's own text must not be able to decide what the program may do.

## 1. The two halves: declaring and granting

| | **Declaring** | **Granting** |
|---|---|---|
| Who | The author of a `.lum` file | The **host**: whoever runs the program |
| How | `use fs.read` at the top level of the file | `lumen run --allow-read=DIR` — or, for an embedder, a `Policy` object |
| Means | "this file needs to read files" | "this run may read files, in DIR" |
| Effect | Makes the need visible to readers and tools; permits the file to *attempt* the operation | Makes the operation possible, within its scope |
| Default | Nothing declared | Nothing granted (the empty policy) |

**Invariant 1 — no capability, no access.** An operation that is not both declared
and granted is not performed.
**Invariant 2 — declaring does not grant.** Writing `use fs.read` never makes a
read succeed by itself.
**Invariant 3 — a grant cannot exceed the granting policy.** Nothing in a `.lum` file
(or in any module it imports) can create, widen, or inspect a grant.
**Invariant 4 — a denial cannot be turned into access.** Catching a
`PermissionError` yields no data and changes no later check.

Granting is only ever done by the host, and only through the interfaces in §3.

## 2. The four checks

Every call to a gated builtin (§4) is validated, in this order, by one function
(`capability_call` in `lumen.py`) that the interpreter and the bytecode VM both
call. There is no second copy of this logic for the two backends to disagree about.

0. **Arguments.** The arguments must be of the right type (`Str` for every
   gated builtin) and the right count; the path or URL must not contain a NUL
   character. Violations are a `TypeError`, never an operation on something else
   (e.g. `read_file(0)` is a `TypeError`, not a read of file descriptor 0).
1. **Declared by the executing module.** The file whose code makes the call must
   have declared the capability. Reason code: `not-declared`.
2. **Declared by every importer.** Every module on the chain of `use "…"` imports
   that led to this module must also have declared it, up to the entry file. *A library
   cannot hold authority its importer lacks.* Reason code: `not-declared-by-importer`.
3. **Granted by the host.** The run's policy must grant the capability at all.
   Reason code: `not-granted`.
4. **Inside the granted scope.** The specific path or host:port must be inside what
   was granted (§5, §6). Reason codes: `out-of-scope`; for a network redirect,
   `redirect-out-of-scope`.

If any check fails the operation is **not performed** — no file is opened, no
connection is attempted — and a `PermissionError` is raised (§7). A failure at any
point is a denial: the layer fails closed.

## 3. How the host grants

### 3.1 Command line

`lumen run`, `lumen test` and `lumen repl` accept these flags; `lumen check` takes none
(it never executes the program).

| Flag | Grants | Scope after `=` |
|---|---|---|
| `--allow-read[=PATH,...]` | `fs.read` | directories or files |
| `--allow-write[=PATH,...]` | `fs.write` | directories or files |
| `--allow-list[=PATH,...]` | `fs.list` | directories |
| `--allow-fs[=PATH,...]` | `fs.read`, `fs.write` and `fs.list` | directories or files |
| `--allow-net[=HOST[:PORT],...]` | `net.http` | hosts, optionally with a port |

Rules:

- A flag **without** `=` grants the capability everywhere (unrestricted). A flag
  **with** `=` grants it only for the listed entries, separated by commas. (A path
  containing a comma cannot be written in a flag; use the embedding API.)
- Flags may appear before or after the file name, and may be repeated; repeated
  scoped flags accumulate, and an unrestricted grant absorbs scoped ones.
- There is **no `--allow-all`**, no environment variable, no config file and no
  other switch that grants. An unrecognised `--allow…` flag, an empty value
  (`--allow-read=`), or an empty list entry is a usage error: `Error: …`, exit
  status **2**, program not run. (A typo can therefore never be silently
  accepted, and a mistyped flag fails closed.)
- Relative scope paths are resolved against the current directory at start-up — the
  directory the file builtins themselves resolve paths against.
- **Non-interactive by default.** Unless `--ask` is given (§3.3) the command line never
  prompts. With no flag, a program is denied; there is no input a program, its stdin or
  its environment can supply that grants anything.
- Grants last for the one run. There is nothing to revoke afterwards because nothing
  is saved — not by flags, not by the prompt: the next run starts from the empty policy
  again. (Saved grants are deliberately not implemented; `PHASE5_STATUS.md` explains why.)

`--allow-read` etc. act on **the whole run**, including every imported module — and
cannot be exceeded by any of them (check 2 and check 4 above).

Two further options are not grants:

| Option | Applies to | Meaning |
|---|---|---|
| `--ask` | `run` only | show what the program declares and ask before running (§3.3) |
| `--audit-log=FILE` | `run`, `test`, `repl` | append a record of every permission decision to `FILE` (§3.4) |
| `--audit-level=all\|denied` | with `--audit-log` | record everything (default), or only denials, declarations and consent |

`--ask` with any other command is a usage error (exit 2, nothing runs). `--audit-log` without a
value, a repeated `--audit-log`, `--audit-level` without a log, an unknown level and an unknown
`--audit…` flag are usage errors too.

### 3.3 The consent prompt (`lumen run --ask FILE`)

A person who would rather decide than write flags can run a program with `--ask`. The prompt is
a **user interface over the same `Policy`** — it is not a security boundary, and the runtime enforces
whatever `Policy` results exactly as it would for flags (§2). What it adds is a way to *see* the request and
answer it.

What happens, in order:

1. The file is checked (syntax and declaration errors are reported before anyone is asked anything).
2. The **entry program's** top-level `use` lines are read. They are the ceiling for the whole run: every
   module a program imports needs its own declaration **and** the entry program's (check 2), so nothing a
   library declares can exceed this list.
3. Capabilities the program declares **and no flag already granted** are the *undecided* ones. If there are none
   (nothing declared, or flags already grant everything declared) there is **no prompt**: the flags are the grant.
4. The program's requests are listed, each marked `granted by flag: …` or `NOT granted`.
5. For each undecided capability the person is asked: *paths* (or `HOST[:PORT]` for `net.http`), comma-separated,
   relative to the current directory — or the **literal word `everywhere`** — or **Enter to deny**.
6. The **effective permissions** are printed, one line per capability, and the person is asked
   `Run with these permissions? [y/N]`. Only `y`/`yes` runs the program.

| Rule | Why |
|---|---|
| Needs a real terminal on **both** stdin and stderr, else `Error: --ask needs an interactive terminal…`, exit **2**, nothing runs | a pipe, a file or a script cannot answer for a person; typed-ahead text on a pipe is not an answer |
| Only capabilities the program **declared** are offered | a prompt cannot grant what the program did not claim to need |
| Only capabilities **no flag granted** are offered; a flag's scope is never widened or replaced | a prompt adds, it never rewrites (`Policy.extended`) |
| Every default is a denial: Enter, `n`, end of input (Ctrl-D), three invalid answers, anything but `y`/`yes` at the end | denial is the default response |
| `y` / `yes` at "which paths?" is **not** a grant — it is re-asked | agreeing is not the same as saying what to allow |
| `everywhere` must be typed exactly; `Everywhere`, `*`, `all`, `/` are read as paths | unrestricted access cannot be granted by a slip |
| Declining or cancelling at the end runs **nothing** (exit 1, `lumen: cancelled — nothing was run`) | no half-granted state |
| The effective lines show the scope as it will be **enforced** (real paths, symlinks resolved), and a flag grant for something the program does not declare is shown as `denied (granted by a flag, but the program does not declare it)` | what is displayed is what is effective |
| Control characters in names and paths are shown escaped (`\x1b`) | a hostile file name cannot rewrite the terminal |

Example (abridged):

```
$ lumen run --ask app.lum
app.lum requests these permissions (declared with `use`; a declaration grants nothing):

  Filesystem
    fs.read   read file contents, and ask whether a path exists or is a directory  [NOT granted]

  Network
    net.http  make HTTP and HTTPS GET requests  [NOT granted]

Nothing is allowed unless you allow it here or with --allow-* flags. Enter denies.

Allow fs.read? Enter paths (comma-separated), 'everywhere', or press Enter to deny: ./config
Allow net.http? Enter HOST or HOST:PORT (comma-separated), 'everywhere', or press Enter to deny: api.example.com:443

Effective permissions for this run:
  fs.read   allowed: /home/me/app/config
  fs.write  denied
  fs.list   denied
  net.http  allowed: api.example.com:443

Run with these permissions? [y/N] y
```

Limits: the prompt cannot protect a person who types `everywhere` for a program they should not trust, and
a terminal can be driven by software (`script`, `expect`), so "a terminal is present" shows a person *can* be
there, not that one is. Declarations name capabilities but not scopes, so the person supplies the scope; the
program's own claim of what it needs is the only thing the prompt can show.

### 3.4 The audit log (`--audit-log=FILE`)

An optional, host-controlled record of what a run **requested**, was **granted**, and was **decided**: one JSON
object per line, appended, with no sensitive data in it. It is for the person running the program (and their tools),
not for the program, which has no handle on it.

| `event` | When | Fields (besides `seq`, `ts`, `event`) |
|---|---|---|
| `run` | the run starts (`run`, `test`, `repl`) | `command`, `backend` (`interpreter`/`vm`), `file`, `grants` (`{capability: "everywhere" \| [scope,…]}`) |
| `declare` | a module is loaded and declares capabilities (its **requests**) | `module`, `capabilities` |
| `request`, `consent`, `confirm` | the `--ask` prompt | `program`, `declared`, `granted_by_flags`, `undecided` / `capability`, `decision` (`granted`/`denied`), `scope` / `decision` (`run`/`cancelled`) |
| `access` | every gated operation | `decision` (`allow`/`deny`), `capability`, `operation`, `resource`, `module`, and for a denial `reason` (the five reason codes of §7); `redirect: true` on a permitted redirect hop |

`seq` counts records from 1; `ts` is UTC, ISO-8601, milliseconds.

What the log **never** contains: file contents or anything written, the arguments other than the resource, URL
userinfo / path / query / fragment (a URL is reduced to `scheme://host:port`, exactly as in a denial), the
environment, or anything the program computed. A resource is the path **as the program wrote it** (relative paths
are not resolved) capped at 300 characters. JSON encoding escapes every control character, quote and newline, so a
hostile file name stays inside one record and cannot forge another.

Guarantees and rules:

- **Allow is recorded before the operation** (after all four checks passed); **deny is recorded however late it
  arises**, including a swapped directory caught during the open (§5, races) — that operation shows an `allow` and then a
  `deny`. So `allow` means *authorised*, not *succeeded*: an OS error afterwards ("No such file") still shows `allow`.
- **If a record cannot be written, the operation is not performed** and the program sees an `AuditError`.
  (A denial whose record cannot be written is also an `AuditError`: nothing is granted either way.)
- **The program must not be able to reach its own log.** A policy is refused (`Error: …`, exit 2, the program
  does not run; for embedders `ValueError`) when it would combine the log with an unrestricted `fs.write` or with an
  `fs.write` scope that contains the log, including a scope reached through a symlink. The log path itself must not
  be a symbolic link, a directory or a file with other hard links. The file is created private (mode 0600) and the host
  holds it open; the program is never given a handle. `--allow-read` may include the log (it holds only paths and
  decisions), and then the program can read it.
- `test` and `repl` write a `run` record too; `--ask` and the log work together.
- Records from several processes sharing one log do not interleave within a line (append mode); `seq` is per process.
- Volume is not bounded — a loop of a million reads writes a million records. `--audit-level=denied` keeps the
  declarations, the prompt and every denial and skips the allows.

Not guaranteed: the log is **not tamper-evident** (no signatures or hash chain); anyone with the host's file
permissions can edit it. It does not record standard streams, the clock, or imports (loading `.lum` source is not a
gated operation, `security-model.md` §5.4).

### 3.2 Embedding

A host program that embeds the interpreter builds a `Policy` and passes it in:

```python
from lumen import Policy, Interpreter
policy = Policy({
    "fs.read": ["/srv/data"],            # a list of paths: scoped
    "net.http": ["api.example.com:443"], # HOST or HOST:PORT
    "fs.write": None,                    # None: unrestricted
})
Interpreter(base_dir=".", policy=policy).run(ast)
```

- `Policy()` / `Policy.deny_all()` grants nothing. A capability absent from the
  mapping is denied. An empty scope list is rejected as an error (it would be
  ambiguous between "nothing" and "everything").
- `Policy.unrestricted()` grants every capability everywhere; it exists for tests and
  for embedders that mean it. The command line never builds it implicitly.
- A `Policy` is immutable once built. Filesystem scopes are resolved to real paths
  (symlinks and `..` removed) when it is built.
- Unknown capability names, and network scopes that are not `HOST` or `HOST:PORT`,
  raise `ValueError` at construction rather than being ignored.

## 4. The capabilities

| Capability | Operations it gates | What it permits |
|---|---|---|
| `fs.read` | `read_file`, `file_exists`, `is_dir` | reading file contents; asking whether a path exists or is a directory |
| `fs.write` | `write_file`, `append_file`, `delete_file` | creating, overwriting, appending to, or deleting files |
| `fs.list` | `list_dir` | listing the entries of a directory |
| `net.http` | `http_get` | HTTP and HTTPS GET requests |

The three `fs.*` capabilities are **independent**: granting `fs.read` does not allow
writing, listing or deleting; `fs.list` does not allow reading file contents;
`fs.write` does not allow reading. A program that wants "write, then read back" must
declare and be granted both. `net.http` never implies file access, and file access never
implies network access.

**Aliases (declaration only).** `use fs` declares `fs.read`, `fs.write` and `fs.list`;
`use net` declares `net.http`. They exist so 2.x source still parses. Because declaring
never grants, an alias costs nothing except a longer list of declared needs — prefer the
precise names.

**Reserved names.** `net.connect`, `env.read` and `process.spawn` are reserved in the
capability taxonomy but have **no operation behind them in this version**. Declaring one
is a `ModuleError` ("reserved but not implemented — unsupported capabilities fail
closed"), so a file can never appear to have been granted process spawning or
environment access that nothing enforces. There is no builtin for any of them (§8).

### 4.1 Declarations

```lumen
use fs.read          # at the top level of the file
use net.http
```

- A declaration is **static**: it is read before the program runs, applies to the whole
  file, and its position among other top-level statements does not matter.
- It must be at the **top level** of the file. Inside a function, `if`, loop or other
  block it is a `ModuleError` at check time ("must be at the top level of the file").
  A declaration cannot be acquired late, conditionally, or by calling a function.
- An unknown capability name is a `ModuleError` found by `lumen check`, not discovered
  mid-run.
- Declarations are per file. An imported module's declarations are its own; they
  neither add to nor are added to by the importer's (§2, check 2).

## 5. Filesystem scope (`fs.read`, `fs.write`, `fs.list`)

An unrestricted fs grant (`--allow-read`) permits any path the operating-system user
running the process can reach, used exactly as the program wrote it. A scoped grant
(`--allow-read=DIR,FILE`) obeys these rules:

1. **Resolve, then compare.** The requested path is resolved to its real location
   (every symlink and `..` resolved) and tested for containment in a scope entry that
   was itself resolved when the policy was built. Comparison is by path component, so
   `/data-evil` is **not** inside `/data`.
2. **Operate on what was checked.** The operation uses the resolved path that was
   authorised, not the original string.
3. **Symlinks cannot escape.** A symlink inside the scope that points outside it is
   denied for reading, writing, appending, listing and existence checks, whether the
   link is a file, a directory component, or dangling; a write through a dangling or
   directory link never creates the target. A symlink that resolves to somewhere inside the
   scope is allowed. A scope given as a symlink covers the real directory behind it.
4. **The check cannot be raced (POSIX).** The resolved path in rule 1 only selects *which
   granted directory* applies. The granted directory itself is **pinned**: it is opened,
   without following a symlink, when the policy is built (or on first use, if it did not
   exist yet), and every scoped operation is then performed **relative to that descriptor**,
   one path component at a time, each opened with `O_NOFOLLOW|O_DIRECTORY` — `openat`-style.
   Nothing is looked up by path string after the check. So if another process, between the
   check and the use, renames a directory on the path and puts a symlink in its place (or
   swaps an ancestor of the granted directory), the walk cannot follow it: the operation is
   refused with `PermissionError … out-of-scope` ("the path changed while it was being
   opened") instead of reaching outside. This holds for every operation — read, write, append,
   delete, existence, directory test and listing — and creates no file outside the scope.
   Writes also truncate only after the opened file has been verified, and on Linux the opened
   descriptor's real path is re-checked as a second layer.
   *Consequences:* the grant follows the **directory that was granted**, not the name — if the
   host deletes and recreates it mid-run the program keeps seeing the old one (operations fail)
   until the next run. A single-file scope, or a directory that did not exist when the policy was
   built, is anchored through its parent directory (verified through `/proc` on Linux).
   *Where this is unavailable* (Windows has no descriptor-relative open) scoped access falls
   back to checking the resolved path, with the weaker guarantees listed in
   `security-model.md` §5.2.
5. **Deletion removes the name, not the target.** `delete_file` on a symlink removes the
   link (a name inside the scope) and never the file it points to.
6. **Denials do not leak.** `file_exists`, `is_dir`, `read_file` and the rest answer a path
   outside the scope with the same `PermissionError … out-of-scope` whether or not that path
   exists, so a program cannot use them to learn what lies outside. (Resolving a path to
   its real location necessarily looks at the symlinks along it; the program sees none of
   that, only the allow/deny result.)
7. **A file can be a scope.** `--allow-read=/etc/app.conf` grants exactly that file.

The scope does not include operating-system facilities the program has no builtin for
(there is no `mkdir`, `rename` or permissions builtin — see §8).

## 6. Network scope (`net.http`)

`http_get(url)`:

1. Only `http://` and `https://` URLs are accepted. Any other scheme (`file:`, `ftp:`,
   `data:`, …) is refused with an error that is not a capability bypass: `http_get` is
   *network* access and will never read a local file, even when `net` is granted
   everywhere.
2. The URL's host and port are matched against the scope. An unrestricted grant
   (`--allow-net`) permits any host. A scoped grant (`--allow-net=api.example.com:443,10.0.0.5`)
   permits a request only when its host equals a scope host **and** (the scope entry has no
   port, or the ports are equal). A URL without a port means 80 for `http` and 443 for `https`.
3. **Matching is by name, case-insensitively, not by resolved address.** `localhost` and
   `127.0.0.1` are different hosts to the policy; granting one does not grant the other.
   There are no wildcards and no CIDR ranges. Scope entries must look like a host name or IP
   (`[A-Za-z0-9._-]+`, or an IPv6 literal) with an optional port 1–65535; anything else is
   rejected when the policy is built.
4. **Redirects cannot widen access.** Every redirect hop is checked against the same
   scope *before the request to it is sent*; a redirect to an ungranted host or port is
   denied (`redirect-out-of-scope`), and the target receives no request. A redirect to a
   non-HTTP scheme is refused.
5. **No credentials in errors.** A denial reports `scheme://host:port` only — never
   userinfo, path or query string.
6. Requests have a 5-second timeout. HTTP proxy environment variables
   (`HTTP_PROXY`, …) are honoured by the underlying library (`security-model.md` §5).

## 7. Errors

A denial is a runtime error that prints exactly like any other uncaught error
(`PermissionError: …`, exit status 1) and, when caught, is an `Error` value with these
fields in addition to `message` and `kind`:

| Field | Meaning |
|---|---|
| `kind` | `"PermissionError"` (for every error, `kind` is the leading `XError` of its message, else `"Error"`) |
| `capability` | the capability that was needed: `fs.read`, `fs.write`, `fs.list`, `net.http` |
| `operation` | the builtin: `read_file`, `http_get`, … |
| `resource` | what was asked for: the path as written, or `scheme://host:port` |
| `reason` | `not-declared`, `not-declared-by-importer`, `not-granted`, `out-of-scope`, `redirect-out-of-scope` |

Messages always say which check failed and how to fix it, e.g.

```
PermissionError: read_file() needs the 'fs.read' capability, which 'main.lum' has not declared — add 'use fs.read' at the top of main.lum
PermissionError: read_file() needs the 'fs.read' capability: 'lib.lum' declares it, but 'main.lum', which imports it (directly or indirectly), does not — a library cannot hold authority its importer lacks
PermissionError: read_file() requires the 'fs.read' capability, which was not granted for this run — grant it with --allow-read[=PATH,...] (declaring it with 'use' does not grant it)
PermissionError: read_file('/etc/hostname') is outside the paths granted for 'fs.read'
PermissionError: http_get() requested 'http://example.com:8080', which is not among the hosts granted for 'net.http'
```

If a module's top level is denied while it loads, the failure keeps its structure:
`ModuleError: error loading 'x.lum': PermissionError: …`.

Other I/O failures (file not found, connection refused, …) are ordinary `Error`s and are
reachable only after all four checks passed; a program therefore cannot tell *"this is
denied"* from *"this does not exist"* for a path outside its scope.

## 8. What is deliberately not here

- No `process.spawn`, `env.read` or raw-socket capability: no builtin exists for them,
  and declaring them is an error (§4).
- No `eval`, `exec`, `system`, reflection, or dynamic-code builtin: `NameError`.
- No FFI (`security.md` §7.6). Native extensions to the *interpreter* are trusted code
  and outside this model (`security-model.md` §4).
- No directory creation/removal/rename, no permission or ownership changes.

## 9. Parity and testing

The interpreter and the VM obey exactly the same policy: the equivalence suite
(`tests/test_equivalence.py`) and the adversarial suite (`tests/test_security.py`) run
every protected scenario on both and require identical results, and the adversarial suite
verifies *actual* resource access (file contents on disk, requests a real server received),
not merely that an error message was printed. See `security-testing.md`.
