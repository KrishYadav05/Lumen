# docs/concurrency.md

# Lumen Language Specification — Chapter 6: Concurrency

Status: **Normative.**

## 6.1 Model: OS Threads, Task Handles, Channels

Lumen 1.0 provides exactly three concurrency primitives, all covered in full below:
`spawn` (start concurrent work), `wait` (join and retrieve its outcome), and
`chan`/`send`/`receive` (a thread-safe message queue). There is no `async`/`await`,
no actor model, and no structured-concurrency scoping construct (no "all children
must finish before this block exits" guarantee) in v1.0.

## 6.2 `spawn` — Execution Semantics

```lumen
let t = spawn f(a, b)
```

`spawn` is grammatically restricted to wrapping exactly one function call expression
(`grammar.md` §2.5); `spawn (a + b)` or `spawn x` is a syntax error.

Execution order, precisely:

1. `a` and `b` are evaluated on the **calling** thread, left to right, exactly as an
   ordinary (non-spawned) call's arguments would be (`language.md` §1.9). Argument
   evaluation is **not** concurrent with anything.
2. A new **OS thread** (the reference implementation uses the host's native threading
   facility) is created and started. `f`'s body begins executing on that thread,
   with a fresh call scope chained to the global scope, exactly as it would for an
   ordinary call (`language.md` §1.11 Rule C applies identically to spawned calls).
3. `spawn f(a, b)` itself returns **immediately** on the calling thread — it does not
   block — yielding a **task handle** value. The task handle is an opaque value whose
   only defined operation is being passed to `wait` (§6.3); it has no other readable
   fields or methods in v1.0.
4. The spawned thread runs concurrently with the thread that called `spawn`, and with
   any other threads spawned before or after it, until `f` returns or fails.

## 6.3 `wait` — Join Semantics

```lumen
let result = wait(t)
```

- Blocks the calling thread until the task represented by `t` has finished (returned or
  failed). While blocked, the calling thread does not busy-poll — it is a true blocking
  wait on the host's thread-synchronization primitive.
- If `f`'s call completed normally, `wait(t)` returns its return value.
- If `f`'s call raised a runtime failure of any kind (`errors.md` §4.2 — the uniform
  failure model applies identically to spawned calls), that **exact same failure** is
  re-raised on the *calling* thread at the point of the `wait(t)` call, and is
  catchable by an enclosing `try`/`catch` on the calling thread exactly as if the call
  had not been spawned at all (`errors.md` §4.7 covers this precisely). A spawned
  failure is never observed on the spawning thread before `wait` is called on its
  handle.
- `wait` is **idempotent**: calling `wait(t)` more than once on the same handle after
  the task has completed returns (or re-raises) the same outcome every time; it does
  not re-run `f`.
- Calling `wait` on a value that was not produced by `spawn` is a runtime `TypeError`
  (`errors.md` §4.4.10).
- **A spawned task whose handle is never passed to `wait` is not an error.** If the
  process exits with such a task still running or already finished-but-unobserved, it
  is silently discarded — there is no "detached task" warning, no requirement that
  every `spawn` be `wait`ed, and no join-on-exit behavior in v1.0.

## 6.4 Channels

```lumen
let ch = chan()
send(ch, value)
let v = receive(ch)
```

- `chan()` creates a new, **unbounded**, FIFO message queue.
- `send(ch, value)` enqueues `value` and returns immediately — it **never blocks**,
  because the queue has no capacity limit in v1.0 (there is no bounded-channel /
  backpressure form).
- `receive(ch)` dequeues and returns the oldest not-yet-received value, **blocking the
  calling thread indefinitely** if the channel is currently empty, until some thread
  calls `send` on the same channel. There is no non-blocking "try-receive" and no
  receive-with-timeout in v1.0.
- A channel value may be freely passed as an ordinary function argument (including to
  a `spawn`ed call, as in the producer/consumer pattern below) — it is a reference
  type like `List`/`Dict` (§types.md §3.3): all holders of the same `chan()` result
  observe the same underlying queue.
- The queue's own enqueue/dequeue operations are internally synchronized by the host
  runtime and are safe to call concurrently from any number of threads without
  additional locking on the Lumen programmer's part. This is the **only** built-in
  concurrency-safe data structure in v1.0 (see §6.5).

### 6.4.1 Canonical producer/consumer pattern

```lumen
fn producer(ch):
    send(ch, 1)
    send(ch, 2)
    send(ch, 3)
    return 0

let ch = chan()
let t = spawn producer(ch)
print receive(ch)
print receive(ch)
print receive(ch)
wait(t)
```

## 6.5 Shared Mutable State, Race Conditions, and What Is Guaranteed

This section states, precisely, what Lumen 1.0 does and does not guarantee about
concurrent access to ordinary (non-channel) data:

- `List`, `Dict`, and `struct` instances are reference types (`types.md` §3.3, §3.4).
  If two threads (e.g. the spawning thread and a spawned task, or two spawned tasks)
  both hold a reference to the **same** `List`/`Dict`/`struct` instance and both
  mutate it (e.g. both call `push(shared_list, x)`, or both write
  `shared_struct.field = ...`), **Lumen 1.0 provides no atomicity, ordering, or
  mutual-exclusion guarantee for that access.** The result is a data race in the
  ordinary sense: the final state is not well-defined by the language and may vary
  between runs. **There is no `lock`/`mutex`/`atomic` primitive of any kind in v1.0.**
  This is a known, explicit gap, not a silent omission — the only Lumen 1.0 mechanism
  for safe cross-thread data sharing is passing messages through a `chan()` (§6.4).
- Because the reference implementation is hosted on a runtime with a single global
  interpreter lock serializing bytecode-level execution, individual **primitive**
  operations (a single dict key lookup, a single list append call) will not corrupt
  their host-level data structure or crash the process even under concurrent access —
  but this is **not** the same as Lumen-level operations being atomic. A
  Lumen-level compound operation — e.g. "read a field, compute a new value from it,
  write it back" — is **not** atomic, and two threads interleaving such a
  read-modify-write on the same `struct` field can and will lose updates. Programmers
  MUST NOT rely on any ordering or atomicity for shared mutable state beyond what is
  stated here; use a channel to communicate values that need coordinated updates
  instead of sharing and mutating a data structure directly across threads.
- Global (module-top-level) `let` bindings observed inside a spawned function's body
  (`language.md` §1.11 Rule C) are subject to the identical rule: reading a global from
  multiple threads is safe in the sense that it will not crash, but there is no
  ordering guarantee about *which* writer's value is observed if a global is being
  concurrently written from another thread while being read.

## 6.6 Deadlocks

Because channels are unbounded (§6.4), `send` never blocks, which removes the classic
"both sides blocked on a full/empty bounded channel, waiting on each other"
deadlock shape entirely for pure send/receive usage. The one blocking primitive is
`receive` (and, transitively, `wait`, which blocks on a task's internal completion
event, not on anything a Lumen program can itself hold a lock on). Consequently, the
only deadlock-shaped failure mode available in Lumen 1.0 is a **liveness bug**, not a
classical deadlock with circular lock acquisition: a thread calls `receive(ch)` (or
`wait(t)`) for a value that no other thread will ever produce. This is indistinguishable
at the language level from any other infinite loop — Lumen 1.0 has no deadlock
detector, no timeout-by-default, and no diagnostic for a program that hangs this way.

## 6.7 Memory Model Summary (informative cross-reference)

Lumen 1.0 does not define an independent, from-scratch memory consistency model (the
kind of formal happens-before relation found in specifications for languages with
manual memory management or lock-free primitives), because it has neither manual
memory management nor any lock-free/atomic primitive for such a model to constrain
(`security.md` §6.1). The concurrency guarantees that do exist are exhaustively stated
in §6.3 (task completion visibility via `wait`), §6.4 (channel FIFO ordering and
thread-safety), and §6.5 (the explicit absence of any other guarantee). Any concurrent
Lumen 1.0 program's correctness must be derivable from those three sections alone.

---
