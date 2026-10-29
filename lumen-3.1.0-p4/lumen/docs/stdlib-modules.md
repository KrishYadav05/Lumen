# docs/stdlib-modules.md — Lumen Standard Library (std/)

Descriptive, not normative — ordinary `.lum` files, not built-ins. Import:
`use "std/math.lum" as math`. Every function has a `lumen test` suite in
`std/tests/` (148 tests), passing on both the interpreter and the VM.

**Why these look the way they do:** no first-class functions (`types.md`
§3.10) rules out `map`/`filter`/`reduce`. Under 1.x, a `for`-body
reassignment silently shadowed instead of mutating the outer accumulator
(the "Rule B" bug — discovered the hard way when a first draft of
`math.sqrt()` silently did zero Newton iterations), so every one of these
modules was written recursively to route around it. **That bug is fixed in
2.0** (`language.md` §1.11.1): bare reassignment now correctly mutates the
nearest enclosing scope that already declared the name, exactly like
Python/JS. Combined with the new native `range()` builtin (for driving a
counted loop without recursing to manufacture `[0..n-1]`), every function
below that used to recurse to carry state across steps has been rewritten
as a plain iterative loop — shorter, and no longer at risk of hitting the
interpreter's recursion-depth limit on large inputs. See `CHANGELOG.md`'s
2.0 entry for the full rationale and before/after numbers. A `for` loop
still has no early-exit (`break`/`continue` don't exist), so a few
functions (`contains`, `index_of`) use a boolean/running "found" state
instead of exiting the loop early — still O(n) iterative work, just without
the ability to stop once the answer is known.

- **std/math.lum** — `abs`, `min`, `max`, `sign`, `clamp`, `floor`, `ceil`,
  `round` (derived from `%`, no truncation builtin exists), `PI`, `E`.
  `pow` and `sqrt` (Newton's method, 24 iterations) are now plain iterative
  loops over `range(...)`, not recursive.
- **std/strings.lum** — `substring`, `starts_with`, `ends_with`, `index_of`,
  `last_index_of` (added alongside `std/path.lum` below — rightmost rather
  than leftmost match, via the `reverse_str`+`index_of` trick, since
  `path.lum`'s `dirname`/`basename`/`extension` all need the *last*
  separator), `contains`, `replace`, `split`, `join`, `to_upper`/`to_lower`
  (lookup table — no `ord`/`chr`), `trim` (spaces only — no escape
  sequences exist to express tab/newline literals), `reverse_str`,
  `repeat`. All the
  character-walking functions (`substring`, `to_upper`, `to_lower`,
  `reverse_str`, `trim_start`, `repeat`, `index_of_from`) are now iterative
  `for`-loops over the string's characters or over `range(n)`, not
  per-character recursion — no recursion-depth ceiling on long strings
  anymore (the 1.x version risked hitting it past ~900 chars on the
  interpreter backend). **As of 2.3, `replace` and `split` are iterative
  too** (single pass, a `pending` accumulator that is flushed whenever it
  ends with the needle) — they were the last two recursive functions, one
  call per match, and `split` overflowed the interpreter's stack at ~300
  separators. (`split` with an empty separator still fails with
  `"split: separator must not be empty"`, exactly as before — checked
  against the old implementation.) And `substring` itself now costs O(length of
  the slice), not O(length of the string): the old version walked every
  character of `s` on every call, so `index_of`/`contains`/`starts_with`/
  `ends_with` (all built on it) were quadratic — `index_of` on a 5,000-
  character string went from 92 s to 0.2 s on the interpreter (211 s to
  0.4 s on the VM). Measured; see `CHANGELOG.md`'s **2.3.0** entry.
- **std/collections.lum** — `sum`, `min_list`, `max_list`, `reverse`,
  `contains`, `index_of`, `unique`, `range`, `range_between`, `concat`,
  `flatten`. All iterative. `range`/`range_between` are now thin wrappers
  around the new core `range()` builtin (a bare call to a name that is also
  a builtin always resolves to the builtin, per `language.md` §1.7, so this
  is not infinite recursion).
- **std/time.lum** — `timer_start`, `elapsed_seconds`, `elapsed_ms`, wrapping
  the `now()` builtin (seconds since epoch, ungated like `print`). Unchanged
  in 2.0 — no recursion patterns existed here.
- **std/io.lum** — `parse_int`, `parse_float`, `read_int`, `read_float`,
  wrapping the `read_line()` builtin. `parse_uint`'s digit-accumulation is
  now an iterative loop instead of a recursive accumulator pair; `pow10`
  remains simple recursion (bounded by realistic fractional-digit counts —
  not worth the complexity of rewriting).
- **std/json.lum** (added in Language Specification 2.2) — `json_encode`,
  `json_decode`, `null`, `is_null`. A hand-written recursive-descent decoder
  over a cursor held in a 1-element List (`pos = [0]`, mutated via
  index-assignment — Lumen has no reference parameters, and this is the
  standard workaround), using `while` for the character-by-character scan a
  `for` loop cannot express. Built on three additions made specifically to
  make this module possible: `chr`/`ord` (the only way to produce a literal
  `"` — no string-escape mechanism exists, `language.md` §1.8) and
  `type_of` (safe runtime dispatch — `Bool`/`Int` duck-typing is actively
  unsound here, since `0 == false` and `1 == true` both hold). Supports the
  full JSON grammar including `\uXXXX` escapes and exponents (`1e10`).
  Encodes Int, Float, Bool, Str, List, Dict, and Nothing (as `null`); struct,
  enum, Task, Channel, and Module values fail cleanly on encode (no generic
  reflection exists to give them a shape) and Dict keys must be Str (as JSON
  itself requires). See `docs/language.md` §1.14, `docs/standard-library.md`
  §8.5.1, and `std/json.lum`'s own header comment for the full rationale and
  stated limitations.
- **std/path.lum** (added in implementation release 2.2.1 — library-only,
  no grammar/builtin change) — `is_absolute`, `join`/`join_all`,
  `dirname`/`basename`/`split`, `extension`/`stem`, `normalize`. POSIX-style
  (forward-slash) path manipulation, pure string logic with no filesystem
  access or `fs` capability requirement. `join(a, b)` and `normalize`
  deliberately match Python's `os.path.join`/`os.path.normpath`
  conventions exactly (stated explicitly in the module's header comment,
  since path-joining rules vary by language and this picks one rather than
  leaving it ambiguous) — including `os.path.split`'s least-obvious case,
  that a head of pure `/` characters (the root) is never stripped down to
  `""`, so `dirname("/a") == "/"`, not `""`. Windows-style backslash paths
  are explicitly out of scope. `normalize`'s `..`-resolution needed to drop
  the last collected path segment — ordinarily a list "pop", which this
  language's builtin surface doesn't have (only `push`) — worked around
  with `collections.substring_list(list, 0, len(list) - 1)` rather than
  adding a new core builtin for it. 19 dedicated `lumen test` cases
  (`std/tests/path_test.lum`), passing identically on both backends. See
  `CHANGELOG.md`'s **2.2.1** entry and `std/path.lum`'s own header comment.
- **std/net.lum** (added in implementation release 2.2.2 — library-only,
  no grammar/builtin change) — `get`/`get_json`, `url_encode`/
  `build_query`/`with_query`, `fetch`/`fetch_json`. A thin wrapper over the
  `http_get` builtin (the only network primitive this language exposes —
  no POST, no custom headers, no status codes) plus RFC 3986 query-string
  construction, since parameterizing a GET request is the one thing
  `http_get` alone can't help with; `get_json`/`fetch_json` pipe the
  response straight through `json.json_decode`. **Capabilities (3.0):** this
  module declares `use net`, which only says what it needs — a program using
  `net.get(url)` must declare `use net` itself and be run with
  `--allow-net[=HOST,...]` (a library cannot hold authority its importer
  lacks, `capabilities.md` §2; in 2.x the module's own `use net` was a
  grant, which is no longer so). `url_encode` is explicitly
  scoped to single-byte (0-255) input (`fail()`s on wider code points —
  there is no Bytes type or UTF-8 encode primitive to do it correctly) and
  uses `%20` for spaces, not form-encoding's `+`, since there's no POST
  body for that convention to apply to. 10 dedicated `lumen test` cases
  (`std/tests/net_test.lum`, self-contained and offline like every other
  stdlib test file — a loopback address nothing listens on exercises the
  capability/error-propagation behavior deterministically; run with
  `--allow-net=127.0.0.1`, which the tests also use to check that another
  host is refused), passing identically on both backends. See `CHANGELOG.md`'s **2.2.2** entry and
  `std/net.lum`'s own header comment.

- **std/fs.lum** (added in Language Specification 2.3 together with five
  new `fs`-gated builtins — `append_file`, `file_exists`, `is_dir`,
  `list_dir`, `delete_file`, `standard-library.md` §8.4) — re-exports the
  seven file builtins (as `fs.read_file(...)` etc.; the caller still
  needs its own `use fs…` declaration and the host's grant) and adds `is_file`, `split_lines`/`read_lines`/
  `write_lines`/`append_line`, `file_size`, `copy_file`, `move_file`,
  `delete_if_exists`, `list_files`/`list_subdirs`, and `read_json`/
  `write_json` (via `std/json.lum`). Line-splitting follows Python's
  `splitlines` (a trailing newline adds no empty line); `write_lines`
  terminates every line, so `read_lines(p)` after `write_lines(p, xs)` is
  `xs`. `move_file` is copy-then-delete (no rename builtin) and treats
  moving a path onto itself — however it is spelled, via
  `path.normalize` — as a no-op instead of destroying the only copy.
  **Security note (3.0):** like `std/net.lum`, this module declares `use fs`,
  which only says what it needs; importing it is **not** a grant. A program
  using `fs.read_lines(...)` declares `use fs.read` itself and is run with
  `--allow-read[=DIR,...]` (`capabilities.md` §2). Stated limitations, all from
  missing builtins and
  all in the module's header comment: no mkdir/rmdir/rename/stat, text
  only, whole-file reads, `file_size` counts characters not bytes. 28
  dedicated `lumen test` cases (`std/tests/fs_test.lum`, which writes only
  inside `std/tests/fixtures/scratch/`; run from the repository root with
  `--allow-fs=std/tests/fixtures`, because the `fs` builtins resolve paths
  against the working directory and the tests also check that the directory
  restriction holds), passing identically on both backends.

**Not provided:** `std/process.lum` never will be — Lumen's capability model
has no process-spawning capability (`process.spawn` is reserved but not implemented, `capabilities.md` §4), and this
library won't add an unguarded escape hatch around that. A
`concurrency` module is not built (`PHASE3_STATUS.md`): it is blocked on a
real language gap (no first-class functions — a task body cannot be passed
to a helper), not just unscheduled work. `std/fs.lum` cannot offer
directory creation, rename or file metadata, because Lumen has no `mkdir`/
`rename`/`stat` builtins to build them on.
