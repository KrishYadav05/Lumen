# docs/stdlib-modules.md — Lumen 1.1 Standard Library (std/)

Descriptive, not normative — ordinary `.lum` files, not built-ins. Import:
`use "std/math.lum" as math`. Every function has a `lumen test` suite in
`std/tests/`, passing on both the interpreter and the VM.

**Why these look the way they do:** no first-class functions (`types.md`
§3.10) rules out `map`/`filter`/`reduce`; no `while` loop means recursion,
not a `for`-loop with a reassigned accumulator, carries state across steps
(`language.md` §1.11 Rule B — a `for`-body reassignment only affects that
one iteration, discovered the hard way when a first draft of `math.sqrt()`
silently did zero Newton iterations).

- **std/math.lum** — `abs`, `min`, `max`, `sign`, `clamp`, `floor`, `ceil`,
  `round` (derived from `%`, no truncation builtin exists), `pow`, `sqrt`
  (recursive Newton's method), `PI`, `E`.
- **std/strings.lum** — `substring`, `starts_with`, `ends_with`, `index_of`,
  `contains`, `replace`, `split`, `join`, `to_upper`/`to_lower` (lookup
  table — no `ord`/`chr`), `trim` (spaces only — no escape sequences exist
  to express tab/newline literals), `reverse_str`, `repeat`. Recurses once
  per character; interpreter backend may hit its recursion limit past
  ~900 chars (VM has none).
- **std/collections.lum** — `sum`, `min_list`, `max_list`, `reverse`,
  `contains`, `index_of`, `unique`, `range`, `range_between`, `concat`,
  `flatten`.
- **std/time.lum** — `timer_start`, `elapsed_seconds`, `elapsed_ms`, wrapping
  the new `now()` builtin (seconds since epoch, ungated like `print`).
- **std/io.lum** — `parse_int`, `parse_float`, `read_int`, `read_float`,
  wrapping the new `read_line()` builtin.

**Not provided:** `std/process.lum` never will be — Lumen's capability model
has exactly `fs`/`net`, deliberately no process-spawning capability, and this
library won't add an unguarded escape hatch around that. `json`/`path`/`fs`/
`concurrency`/`net` modules: not yet built (`PHASE3_STATUS.md`).
