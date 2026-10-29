# Lumen Phase 5 — Capability-Based Security — Status

Publisher: Krish Kumar

> **Update — Lumen 3.1.0-p4 (Phase 4, error handling).** The security model, the policy and every requirement below are
> **unchanged** by that release (all 184 adversarial and 102 consent/audit tests pass on it; the capability layer was not
> touched). Two things relate to it: *catching a failure grants no capability* is now tested explicitly, and a denial's
> error value names only `scheme://host:port`, never URL credentials, path or query (`docs/errors.md` §4.4.7). The numbering
> is now: Phase 4 = error handling (`PHASE4_STATUS.md`), Phase 5 = this file, and the old "Phase 4" performance checklist is
> `PERFORMANCE_HARDENING_STATUS.md`. Test counts for the current tree are in `PHASE4_STATUS.md`.

Release: **Lumen 3.0.0-5c** (Language Specification 3.0) — a **pre-release**: milestones 5A and 5B of
three, and the first part of 5C (5a was the core capability model with scoped file and network access; 5b closed the
check-then-use race on scoped file access; 5c adds the consent prompt and the audit log). Tracking against *"Lumen Phase 5 — Capability-Based Security: Definition of
Done & Acceptance Criteria"*. Updated honestly: **unchecked items are genuinely not done**, and
a checked item means a test in this repository exercises it, not that it is beyond attack.

> **Objective (from the spec):** make Lumen secure by default, with explicit permissions,
> least-privilege access, predictable enforcement, and clear permission-denial errors.
>
> **Not claimed:** that Lumen is "completely secure". A passing test suite is necessary but does
> not prove the absence of vulnerabilities, and **no independent security review has been done**.

## Requirements (SEC-001 … SEC-012)

- [x] **SEC-001 Default deny (P0).** The empty policy is the default; with no flags every gated operation
  is denied on both backends; disk and server are checked untouched.
- [x] **SEC-002 Explicit capabilities (P0).** `use fs.read` etc. are static, top-level-only declarations;
  an operation also needs the host's grant. Declaring never grants (tested for all 8 operations).
  *Deviation to confirm with you:* the spec's capability list includes `net.connect`, `env.read` and
  `process.spawn`. Lumen has no builtin that opens a socket, reads the environment or spawns a process, so
  there is nothing to gate; the names are **reserved** and declaring one is a `ModuleError`. They become real
  capabilities only if such builtins are added (which the allowlist test forces to be a deliberate decision).
- [x] **SEC-003 Granular permissions (P0).** `fs.read`, `fs.write`, `fs.list`, `net.http` are independent;
  verified in both directions.
- [x] **SEC-004 Runtime enforcement at the resource boundary (P0).** One function, `capability_call`, for
  both backends; a static scan of `lumen.py` fails if a file/network/process/dynamic-code primitive appears
  outside the reviewed layer; a builtin allowlist test fails if a builtin has no capability decision.
- [x] **SEC-005 CLI permission prompts / consent (P0) — done in 5c, with limits.** `lumen run --ask FILE` lists what the
  program declares, asks per capability for a scope (paths / hosts, the literal `everywhere`, or Enter to deny), prints the
  **effective** permissions and asks `Run with these permissions? [y/N]` (`docs/capabilities.md` §3.3). Checked by tests on a
  real pseudo-terminal, on both backends, against what the program actually did (disk, server hits): the prompt adds only
  declared capabilities no flag granted and never widens a flag's scope; every default denies; needs a terminal on stdin **and**
  stderr or refuses to run (exit 2); declining runs nothing; what is shown equals what is effective. Non-interactive policy is
  explicit and documented: flags only, default-deny, never prompts. **Limits:** it is a UI, not a boundary (the runtime
  enforces whatever Policy results); a person can type `everywhere`; a terminal can be driven by software; the spec's example
  shows scopes in the request, but Lumen declarations carry **no scope**, so the person supplies one; `--ask` is `run` only.
  **Saved grants are deliberately not implemented** (every run starts from the empty policy, so there is nothing to revoke) —
  the spec's checkbox is met only in that trivial sense; **say so if you want persisted grants**.
- [x] **SEC-006 Structured permission-denial errors (P0).** `capability`, `operation`, `resource`, `reason`
  (five reason codes), `kind`; messages say which check failed and how to fix it; no credentials/queries in
  messages; out-of-scope answers are identical whether or not the path exists.
- [x] **SEC-007 Filesystem directory restrictions (P0).** Scoped read/write/list; traversal, prefix-sibling
  directories, symlinks of every shape, dangling links, directory-link create-through, delete-the-link
  semantics, single-file scopes. **5b:** the granted directory is pinned and every operation is performed
  relative to it without following symlinks, so a directory swapped for a link between check and use is
  refused (tested deterministically for every operation, for an ancestor swap, and with a racing thread — the
  same tests fail on the 5a code). **Limits:** Windows has no descriptor-relative open and falls back to the
  path-based check; single-file scopes are anchored through their parent by path.
- [x] **SEC-008 Network host restrictions (P1).** `HOST`/`HOST:PORT`, default ports, redirects re-authorised
  before they are requested, `file:` and non-HTTP schemes refused. **Limit:** matching is by name (no DNS
  rebinding defence, no IP rules).
- [x] **SEC-009 Dependency isolation (P1).** A library needs its own declaration *and* every importer's; host
  scopes bind libraries; importing `std/fs.lum` is not a grant; a module written and imported at run time is
  still bounded. *Related fix:* imports load only relative `*.lum` files (below).
- [x] **SEC-010 Audit logging (P1) — done in 5c, with limits.** `--audit-log=FILE`, `--audit-level=all|denied`:
  JSON lines for the run, each module's declarations (its requests), the prompt (request / consent / confirm) and every gated
  operation (allow before the operation, deny however late; capability, operation, resource, module, reason). No file contents,
  no URL userinfo/path/query, hostile names cannot forge records, a record that cannot be written stops the operation, and the
  program cannot reach its own log (unrestricted `fs.write`, a write scope containing it, or a symlink / hard-link / directory
  log path are refused before the program runs). Identical on both backends. **Limits:** not tamper-evident (no signatures or
  chain; anyone with the host's file permissions can edit it); capability layer only (no stdio, clock, imports); `allow` means
  authorised, not succeeded; resources are logged as the program wrote them; volume is unbounded.
- [ ] **SEC-011 Cross-platform consistent policy (P1) — NOT MET.** Developed and tested on **Linux only**. The
  macOS/BSD path lacks the post-open `/proc` verification; Windows semantics (case, device names, ADS, junctions,
  UNC) are unverified. This environment cannot run those platforms; we have not claimed otherwise.
- [x] **SEC-012 Security documentation (P0).** `docs/security.md` (§7 rewritten), `capabilities.md`,
  `security-model.md` (threat model, what is NOT protected), `security-testing.md`. Subject to review.

## Milestones

- [x] **5A Core capabilities:** default-deny, read/write/list separation, runtime enforcement, structured errors.
- [x] **5B Confinement:** path restrictions ✔, network permissions ✔, dependency boundaries ✔, interpreter/VM
  parity ✔ (every adversarial case runs on both backends; 193 equivalence cases), **descriptor-based path walk
  ✔ (5b)** — closes the check-then-use race on POSIX. **Not done, deliberately or for lack of evidence:** a hard-link
  policy (a pre-existing hard link to an outside file is accessible; documented); an `env.read` capability (an
  unrestricted `--allow-read` can already read `/proc/self/environ`; documented; **your decision** whether to add
  one, which would need an environment-reading builtin first); optional IP-literal/port-range network rules; the
  Windows descriptor-relative equivalent (untestable here).
- [ ] **5C Production readiness:** CLI permission UX ✔ (5c), audit log ✔ (5c), regression suite ✔, docs ✔; **remaining:**
  cross-platform verification (SEC-011) and **an independent security review**; saved/revocable grants deliberately not
  implemented (decision for you).

## Release gate (spec table)

| Gate | Status | Evidence |
|---|---|---|
| Default-deny verified | **PASS** | conformance matrix; adversarial default-deny checks disk + server |
| Explicit declarations enforced | **PASS** | declaration/granting tests, per-file, top-level-only |
| Runtime enforcement verified | **PASS** | four checks; 44/44 mutants killed |
| Filesystem restrictions verified | **PASS** (Linux) | scope/traversal/symlink suites; race suite (deterministic + threaded); Windows fallback documented |
| Structured permission errors | **PASS** | field-level + per-reason tests |
| Interpreter/VM parity | **PASS** | equivalence suite + every adversarial case on both |
| Regression tests for fixed bypasses | **PASS** | `file://`, fd arguments, import-as-read, `--allow-all`, NUL, … |
| No known unresolved critical/high bypass | **PASS, with caveat** | none known to us; the known *limits* (Windows/single-file race fallback, name-based net matching, ambient authority inside a grant) are documented in `docs/security-model.md` §5, and nobody independent has looked |
| Documentation complete | **PASS** (unreviewed) | `capabilities.md` (incl. prompt and audit log), `security-model.md`, `security.md`, `security-testing.md` |
| Permission prompts displayed before granting; denial default; cannot exceed policy | **PASS** | `tests/test_consent_cli.py` (pty), `tests/conformance/test_audit.py` |
| Cross-platform for supported targets | **NOT MET** | Linux only |

## Findings fixed during this phase (all pre-existing in 2.x)

1. **High** — a `net`-only program could read local files via `http_get("file://…")` and via a redirect to `file:`.
2. **High** — `read_file(0)` read file descriptor 0 (and other integers other descriptors); `write_file(p, 5)`
   crashed the interpreter with a raw Python exception.
3. **Medium** — `use "secret.txt" as s` needed no capability, echoed the file's first token in its error, and
   revealed which paths exist. Residual: `.lum` files can still be probed/loaded; documented.
4. **High (in 5a only; found and fixed in 5b)** — check-then-use race on scoped file access: with a directory on the
   path swapped for a symlink after the check, `write_file`/`append_file` created files outside the scope,
   `delete_file` deleted an outside file, and `file_exists`/`is_dir`/`list_dir` revealed or listed outside paths. The
   5a documentation had called the limit "empty file may be created"; the reproduction showed it was worse
   (deletion, listing). Reads and overwrites of existing files were already refused by the Linux post-open check.

Packaging: the 5a zip contained a stray test file (`std/tests/should-not-exist.txt`, a leftover from a test bug fixed before packaging);
removed in 5b, and the stdlib runner now fails if a run changes the repository tree.

Also fixed: `lumen --help` and a missing file crashed with a Python traceback; the VM and the interpreter each had
their own capability implementation (now one); the VM compiler crashed with a raw `KeyError` on an undefined function
called inside a `try` body (pre-existing; not a security finding).

## Test results (this release)

| Suite | Result |
|---|---|
| Regression / CLI (`tests/run_tests.py`) | 37 pass |
| Spec conformance (`tests/run_conformance.py`) | 734 pass (security 105, capabilities 86, fs races 37, **audit + prompt logic 92**, modules 18, errors 38, stdlib 85, …) |
| Interpreter ↔ VM equivalence | 193 pass |
| Optimizer stability | 28 pass |
| Adversarial CLI security suite (`tests/test_security.py`) | 184 pass (each on both backends) |
| **Consent prompt + audit log through the CLI (`tests/test_consent_cli.py`)** | 102 pass (prompt on a real pseudo-terminal; both backends) |
| Standard library (`tests/run_stdlib.py`) | 148 pass on each backend |
| Mutation check (`tests/security_mutation_check.py`) | 44/44 mutants killed |

## What this environment cannot verify

macOS and Windows behaviour (the prompt's pseudo-terminal tests are POSIX-only); behaviour under a hostile *local* second process (races are simulated exactly between the check and the use and exercised with a racing thread,
not with a real hostile process); any independent review.

## Suggested next steps

1. Independent review of `capability_call`, `_fs_target`, `_beneath`, `_open_checked`, `_authorize_url`, `_locate_module`,
   and now `AuditLog`, `Policy.extended`, `request_consent` — the main thing still outstanding.
2. Run the suites on macOS and Windows and fix what they find (SEC-011); implement a Windows equivalent of the
   descriptor walk if Windows is a supported target; Windows has no `pty` module, so the prompt tests need a port.
3. Decide whether you want **saved grants** (a persisted, revocable allow-list per program) — currently deliberately absent.
4. Decide whether `env.read` / `process.spawn` / `net.connect` should become real capabilities (each needs a builtin first).
5. Optional: a tamper-evident audit log (hash chain) if logs will be used as evidence.
