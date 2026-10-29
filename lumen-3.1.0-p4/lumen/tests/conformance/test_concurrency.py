"""Conformance tests for docs/concurrency.md."""
from harness import check, print_final_summary

# ---- §6.2 spawn/wait basic semantics ----
check("concurrency.md §6.2/§6.3", "spawn + wait returns the function's return value",
      'fn sq(n):\n    return n * n\nlet t = spawn sq(6)\nprint wait(t)', "36")
check("concurrency.md §6.2", "spawn args are evaluated on the calling thread before handoff",
      'let log = []\nfn tag(n):\n    push(log, n)\n    return n\nfn f(x):\n    return x\nlet t = spawn f(tag(1))\nwait(t)\nprint log', "[1]")
check("concurrency.md §6.3", "wait() is idempotent (same result twice)",
      'fn sq(n):\n    return n * n\nlet t = spawn sq(7)\nprint wait(t)\nprint wait(t)', "49\n49")
check("concurrency.md §6.3", "wait() re-raises the spawned call's exact failure (division by zero)",
      'fn boom():\n    return 1 / 0\nlet t = spawn boom()\ntry:\n    wait(t)\ncatch e:\n    print e.message', "division by zero")
check("concurrency.md §6.3", "wait() re-raises a custom fail() message unmodified",
      'fn boom():\n    fail("custom failure")\nlet t = spawn boom()\ntry:\n    wait(t)\ncatch e:\n    print e.message', "custom failure")
check("concurrency.md §6.3", "wait() re-raises a genuinely runtime-only NameError from the spawned call "
      "(inner is declared somewhere so the resolver accepts it -- Tier 2 -- but its registering statement "
      "never runs, so it's still undefined at the moment boom() actually calls it)",
      ('fn outer():\n    fn inner():\n        return 1\n    return 0\n'
       'fn boom():\n    return inner()\n'
       'let t = spawn boom()\ntry:\n    wait(t)\ncatch e:\n    print "caught"'), "caught")
check("concurrency.md §6.3", "wait() on a non-task value is a TypeError",
      'print wait(42)', expect_ok=False, expect_error_substr="wait() expects a task")
check("concurrency.md §6.2", "spawn is grammatically restricted to a call expression",
      'let x = 5\nlet t = spawn x', expect_ok=False)
check("concurrency.md §6.2", "spawn of an undefined function fails immediately (not deferred to wait)",
      'let t = spawn nope(1)', expect_ok=False, expect_error_substr="NameError")
check("concurrency.md §6.3", "an un-waited spawned task is not an error",
      'fn f():\n    return 1\nlet t = spawn f()\nprint "done"', "done")

# ---- §6.4 channels ----
check("concurrency.md §6.4", "single send/receive round-trip",
      'let ch = chan()\nsend(ch, 42)\nprint receive(ch)', "42")
check("concurrency.md §6.4", "channel preserves FIFO order",
      'let ch = chan()\nsend(ch, 1)\nsend(ch, 2)\nsend(ch, 3)\nprint receive(ch)\nprint receive(ch)\nprint receive(ch)', "1\n2\n3")
check("concurrency.md §6.4.1", "producer/consumer pattern across a spawned thread",
      ('fn producer(ch):\n    send(ch, 1)\n    send(ch, 2)\n    return 0\n'
       'let ch = chan()\nlet t = spawn producer(ch)\nprint receive(ch)\nprint receive(ch)\nwait(t)'),
      "1\n2")
check("concurrency.md §6.4", "send() never blocks even with no receiver yet (queue is unbounded)",
      'let ch = chan()\nsend(ch, 1)\nsend(ch, 2)\nsend(ch, 3)\nprint "all sent"', "all sent")
check("concurrency.md §6.4", "send() on a non-channel value is a TypeError",
      'send(5, 1)', expect_ok=False, expect_error_substr="send() expects a channel")
check("concurrency.md §6.4", "receive() on a non-channel value is a TypeError",
      'print receive(5)', expect_ok=False, expect_error_substr="receive() expects a channel")
check("concurrency.md §6.4", "a channel is a reference type shared across holders",
      ('fn drain(ch):\n    return receive(ch)\n'
       'let ch = chan()\nsend(ch, 99)\nprint drain(ch)'),
      "99")

# ---- §6.5 shared mutable state: no atomicity guarantee for compound ops, but no crash ----
check("concurrency.md §6.5", "spawned tasks mutating a shared List do not corrupt/crash the process",
      ('fn adder(box):\n    push(box, 1)\n    return 0\n'
       'let box = []\nlet t1 = spawn adder(box)\nlet t2 = spawn adder(box)\nwait(t1)\nwait(t2)\nprint len(box)'),
      "2")
check("concurrency.md §6.1", "multiple concurrent tasks all complete and are individually retrievable",
      ('fn sq(n):\n    return n * n\n'
       'let t1 = spawn sq(2)\nlet t2 = spawn sq(3)\nlet t3 = spawn sq(4)\n'
       'print wait(t1)\nprint wait(t2)\nprint wait(t3)'),
      "4\n9\n16")

print_final_summary()
