# docs/modules.md

# Lumen Language Specification — Chapter 5: Modules

Status: **Normative.**

## 5.1 File-to-Module Mapping

Every `.lum` file is a module. There is no package/namespace declaration syntax inside
a file — a module's identity is its filesystem path, resolved relative to the
*importing* file (§5.4), not a declared name. There is no concept of a module spanning
multiple files, and no concept of multiple modules living in one file.

## 5.2 Namespace Model

A module's top-level (global-scope) `let`, `fn`, `struct`, and `enum` declarations are
the entirety of its namespace. There is no explicit export list and no
`public`/`private` visibility keyword in v1.0 — see §5.3.

## 5.3 Visibility: Everything Is Public

**Lumen 1.0 has no visibility control.** Every top-level binding in an imported
module — every `let`, `fn`, `struct`, `enum` — is reachable from the importing file via
the module alias, unconditionally:

```lumen
use "utils.lum" as u
print u.some_helper(5)     # any top-level fn in utils.lum is reachable
print u.SOME_CONSTANT       # any top-level let in utils.lum is reachable
```

This is a deliberate v1.0 simplification, not an oversight: introducing `private`/
`public` is an additive, backward-compatible grammar extension (a new optional keyword
before a top-level declaration) reserved for a future minor version (§5.6). No
conforming 1.0 program's behavior depends on visibility restriction, so this addition
cannot break existing programs when it lands.

Only **module-scope (top-level)** bindings are reachable this way. A `let` declared
inside a function body in the imported module is not part of that module's namespace
and cannot be reached via the alias, by the ordinary scoping rules of `language.md`
§1.11.

## 5.4 Import Syntax and Resolution Order

```lumen
use "path/to/file.lum" as alias
```

- `path` is a string literal, interpreted as a **relative** filesystem path to a file whose
  name ends in `.lum` (*3.0*: an absolute path, or a name that does not end in `.lum`, is
  never loaded and gets exactly the same `cannot find module` error as a file that does
  not exist — importing loads *code*, never data, and the error must not reveal what a
  data file contains or whether it exists; see `security-model.md` §5).
- Resolution is attempted, in order:
  1. Relative to the directory containing the **importing file itself** (not the
     process's current working directory, and not the entry-point file if this import
     is itself nested inside an already-imported module — each module resolves its own
     relative imports against its own directory).
  2. If not found there, relative to a `lumen_packages/` directory that is itself
     relative to the importing file's directory (this is the mechanism `lumen install`
     populates — see `standard-library.md` §8.7 and the CLI reference).
- If neither location yields a file, the import fails with `ModuleError: cannot find
  module '{path}' at line {n} (looked next to this file and in lumen_packages/)`
  (`errors.md` §4.4.9). This is a normal runtime failure and is subject to the same
  `try`/`catch` rules as everything else in `errors.md` §4.2.5 — an unresolved import
  three levels deep in a dependency chain can be caught at the top level if desired.
- There is no notion of an "absolute" import path, a search-path environment variable,
  or a standard-library-style bare-name import (`use "math"` is not resolved against
  any built-in module set — the standard library's built-ins, listed in
  `standard-library.md`, are always in scope with no `use` required at all, and are
  wholly separate from the user-module import mechanism).

## 5.5 Import Semantics

- `use "path" as alias` **executes the imported file's top level, once, at the point of
  the `use` statement**, and binds `alias` in the current scope to a **module
  namespace value** wrapping the result. It is not a textual/macro include — the
  imported file's statements run in their own, fully separate `Interpreter` instance
  (their own global scope, their own function/struct/enum tables), and the importer
  only ever sees `alias.field`/`alias.fn(...)` accesses proxied into that separate
  instance (`grammar.md` §2.5, `MethodCall`/`FieldGet` productions).
- **Every `use` of the same path re-executes and re-instantiates that module from
  scratch.** There is no per-process module cache in v1.0: `use "x.lum" as a` followed
  later by `use "x.lum" as b` in the same file runs `x.lum`'s top level twice,
  producing two independent namespace instances with no shared state between them.
  This is a known v1.0 characteristic (not a defect): it trades away
  "import once, share state" caching semantics for the simplicity of "every `use`
  statement is a fresh, independent, side-effect-free-to-reason-about instantiation."
  A program that relies on shared mutable module-level state across two `use` sites for
  the same path will observe two independent copies of that state in v1.0.
- **Capabilities and imports (*3.0*).** A module's `use fs.read`-style lines *declare* what
  it needs; they grant nothing (`capabilities.md` §1). Declarations are per file and are
  not propagated in either direction, **but authority is bounded by the import chain**: a
  call to a gated builtin made by an imported module is permitted only if that module
  declared the capability **and so did every module that imports it, directly or
  indirectly, up to the entry file**, and the host granted it for the run. A library
  therefore can never hold authority its importer lacks, and importing a module that
  declares `fs` does not give the importer `fs` — the importer must say `use fs.read`
  itself. Host grants (`--allow-*`) apply to the whole run, imported modules included,
  and an imported module cannot widen them (`capabilities.md` §2).
- Importing is not itself a gated operation (it loads Lumen source, chosen by whoever
  wrote the `use` line) — `security-model.md` §5 states exactly what that does and does
  not expose.

## 5.6 Cyclic Dependency Handling

A module may not, directly or transitively, `use` a path that is already in the
process of being loaded on the current import chain. Lumen 1.0 detects this
**precisely and unconditionally**: each `Interpreter` instance created to load a module
shares a single "currently loading" path set with the interpreter that triggered the
import (propagated down through every nested `use`, regardless of depth). Before
opening a file for import, the resolved absolute path is checked against this set; if
present, the import fails immediately with:

```
ModuleError: circular import detected at line {n}: {a.lum -> b.lum -> a.lum} (module
'{path}' is already being loaded higher up this import chain)
```

showing the full cycle by filename. This is a normal, catchable runtime failure
(`errors.md` §4.2.5) — it is not a process crash, and specifically does **not** manifest
as a host-level stack-depth exhaustion (an earlier, pre-freeze build of the reference
implementation lacked this check and did fail via uncontrolled recursion; this was
identified and fixed as part of finalizing this specification, and the behavior
described above is the frozen, correct 1.0 behavior — see the regression test
`cyclic_import_case` in `tests/run_tests.py`).

Two modules that import each other but where neither import is reached during a given
run (e.g. both `use` statements are inside functions that are never called) is legal
and does not trigger this check — the check fires only when an actual `use` statement
executes while its target path is already mid-load, not on static presence of a cycle
in the dependency graph. Lumen 1.0 performs no whole-program static cyclic-dependency
analysis; detection is purely dynamic, at the moment a cycle would actually be entered.

## 5.7 Standard-Library Module Status

Lumen 1.0 has no `.lum`-source-file standard library modules; every name catalogued in
`standard-library.md` is a host-provided built-in, always present in every module's
global scope with no `use` statement of any kind (neither a capability `use` nor a
path `use`). There is therefore no "standard library import" grammar distinct from
what's already specified above — `standard-library.md` is a catalogue of always-present
names, not a set of importable module paths.

---
