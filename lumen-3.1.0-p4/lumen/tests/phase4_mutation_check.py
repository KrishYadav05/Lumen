#!/usr/bin/env python3
"""Mutation check for the Phase 4 error-handling implementation.

Publisher: Krish Kumar

A passing test suite only proves the suite passes. This script checks that tests/test_phase4.py is not
vacuous: it copies the repository, deliberately BREAKS one piece of Phase 4 behaviour at a time in the
copy's lumen.py (`?` returns the wrong thing, a finalizer is skipped, the error-type check is dropped, a
trace loses its line, ...) and runs the Phase 4 suite against the broken copy. Every mutant must be KILLED
(the suite fails). A SURVIVOR is behaviour that no test would notice losing.

    python3 tests/phase4_mutation_check.py          # all mutants
    python3 tests/phase4_mutation_check.py 3 7      # only mutants #3 and #7

The mutants match exact source text; when the code is refactored a mutant reports "pattern-miss", which is
a failure of this script, not a pass. Exit status 0 only if every mutant was killed.
"""
import os, shutil, subprocess, sys, tempfile
SRC = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WORK = os.path.join(tempfile.mkdtemp(prefix="lumen-p4-mutants-"), "tree")

MUTANTS = [
 # ---- `?` semantics, shared by both engines
 ("? on Err continues instead of returning",
  'return (True, _enum_payload(v)) if v.variant == "Ok" else (False, v)', 'return (True, _enum_payload(v))'),
 ("? on None continues instead of returning",
  'if v.variant == "None" and not v.fields:\n                return False, v', 'if v.variant == "None" and not v.fields:\n                return True, None'),
 ("? accepts any value (no runtime TypeError)",
  'raise LumenError(f"TypeError: the \'?\' operator expects a Result or an Option, got {type_name_of(v)}")', 'return True, v'),
 ("interpreter: ? returns the value instead of leaving the function",
  '        raise ReturnSignal(value)\n\n    def _eval_MethodCall', '        return value\n\n    def _eval_MethodCall'),
 ("VM: PROPAGATE never skips the return stub",
  'self.pc = arg      # skip the stub that returns the failure', 'pass'),
 # ---- finally
 ("VM: RERAISE does not re-raise the parked failure",
  'entry = self.try_stack.pop()\n                raise entry[7]', 'entry = self.try_stack.pop()'),
 ("VM: a returning try body keeps the body's scopes in the finalizer",
  'entry = self.try_stack.pop()\n                self.scopes = entry[2]', 'entry = self.try_stack.pop()'),
 ("VM: no finalizer is emitted for return / ? out of a try body",
  'finals = [i for i, r in enumerate(recs) if r[0] == "finally"]', 'finals = []'),
 ("VM: a finalizer failure does not chain the failure it replaced",
  'attach_pending(e, extra)\n                continue', 'continue'),
 ("interpreter: finalizer not run when a return / ? passes through",
  'except ReturnSignal:\n            self.exec_block(node.finalizer, Env(env))\n            raise', 'except ReturnSignal:\n            raise'),
 ("interpreter: finalizer not run when the body fails",
  'note_failure_here(e)\n            try:\n                self.exec_block(node.finalizer, Env(env))', 'note_failure_here(e)\n            try:\n                pass'),
 ("interpreter: finalizer not run after a normal body",
  '            raise\n        except ReturnSignal:\n            self.exec_block(node.finalizer, Env(env))\n            raise\n        self.exec_block(node.finalizer, Env(env))',
  '            raise\n        except ReturnSignal:\n            self.exec_block(node.finalizer, Env(env))\n            raise'),
 ("a failing finalizer loses the failure it replaced",
  'if getattr(e, "cause", None) is None:\n        e.cause = value', 'if False:\n        e.cause = value'),
 # ---- type checking
 ("assignable() accepts everything (no generic type checking)",
  'if expected == actual or expected == "Any" or actual == "Any":', 'if True:'),
 ("? with an incompatible error type is accepted (E0404 dropped)",
  'if not assignable(want, found):\n                self._fail(self._diag(\n                    "E0404"', 'if False:\n                self._fail(self._diag(\n                    "E0404"'),
 ("? kind mismatch is accepted (E0403 dropped)",
  'if rn != kind:\n            self._fail(self._diag(\n                "E0403"', 'if False:\n            self._fail(self._diag(\n                "E0403"'),
 ("a discarded Result is accepted (E0405 dropped)",
  'if split_type(t)[0] == "Result" and not isinstance(node.expr, Propagate):', 'if False:'),
 ("non-exhaustive match is accepted (E0301 dropped)",
  'missing = [v for v in enum["order"] if v not in covered]\n            if missing:', 'missing = [v for v in enum["order"] if v not in covered]\n            if False:'),
 ("a Result function may fall off its end (E0406 dropped)",
  'if rn in ("Result", "Option") and not self._always_exits(node.body):', 'if False:'),
 # ---- runtime values, codes, chaining, traces
 ("enum values compare by identity again",
  'and self.variant == other.variant and self.fields == other.fields)', 'and False)'),
 ("fail(message, cause) accepts a cause that is not an error",
  'if cause is not None and not _is_error_value(cause):', 'if False:'),
 ("an error value never carries its cause",
  '"cause": EnumInstance("Option", "Some", {"0": cause}) if cause is not None', '"cause": EnumInstance("Option", "Some", {"0": cause}) if False'),
 ("an uncaught failure does not print its causes",
  '    while depth < 8:\n        cause = cur.fields["cause"]', '    while depth < 0:\n        cause = cur.fields["cause"]'),
 ("a permission denial loses its stable code",
  '"PermissionError": "R0008", ', ''),
 ("interpreter: a while condition keeps the previous line in a trace",
  'fr[2] = node.line     # the condition is part of the `while` line, every time round', 'pass'),
 ("interpreter: statements stop recording their line for a trace",
  '        fr = env.frame\n        if fr is not None:\n            fr[2] = node.line\n        fn(self, node, env)', '        fn(self, node, env)'),
 ("interpreter: a function call's scope does not carry its own frame (lines land in the caller's record)",
  '        call_env.frame = rec\n', ''),
 ("VM: a MatchError is reported on the last arm's line",
  'self.cur_line = node.line        # the failure belongs to the `match`, not to the last arm\'s body', 'pass'),
 ("VM: a trace has no line numbers",
  'frames.append((name, file, self._line_at(program, pc)))', 'frames.append((name, file, 0))'),
 ("VM: a failure is recorded again every time it is seen (duplicate frames)",
  'if self.vm_id in seen:\n            return', 'if False:\n            return'),
 ("interpreter: a failure is recorded again every time it is seen",
  'if _TLS.token in seen:\n        return', 'if False:\n        return'),
 ("diagnostics lose the caret line",
  "out.append(f\"  {pad} | {lead}{'^' * width}\")", "pass"),
 ("diagnostics lose the code",
  'out.append(f"  = code: {code}")', 'pass'),
 ("modulo by zero crashes again",
  'raise LumenRaise("modulo by zero", code="R0005")', 'raise ZeroDivisionError("modulo by zero")'),
 # ---- VM state (the pre-Phase-4 bugs this phase fixed)
 ("VM: RETURN leaves stale iterators",
  '                del self.iter_stack[iter_base:]\n                # A `return` inside a `try` body', '                # A `return` inside a `try` body'),
 ("typed struct field: assigning the wrong type is accepted",
  'if not assignable(want, vt):\n                    self._fail(self._diag(\n                        "E0201", "TypeError",\n                        f"field \'{node.field}\' of struct {sname} expects', 'if False:\n                    self._fail(self._diag(\n                        "E0201", "TypeError",\n                        f"field \'{node.field}\' of struct {sname} expects'),
 ("a failed task nobody waited for is not reported (silent loss)",
  '    lost = lost_task_failures()\n    out = stream', '    lost = []\n    out = stream'),
 ("interpreter: wait() does not mark the task observed (failure reported twice)",
  '            _task_observed(task)\n            task.done.wait()\n            if task.error:\n                note_failure_here(task.error)', '            task.done.wait()\n            if task.error:\n                note_failure_here(task.error)'),
 ("VM: wait() does not mark the task observed (failure reported twice)",
  '                    _task_observed(task)\n                    task.done.wait()\n                    if task.error: raise task.error', '                    task.done.wait()\n                    if task.error: raise task.error'),
 ("VM: a caught failure leaves stale operands and iterators",
  '            del self.stack[stack_len:]\n            del self.iter_stack[iter_len:]\n            if kind == "catch":', '            if kind == "catch":'),
]

def main():
    only = sys.argv[1:]
    survivors = []
    for i, (name, old, new) in enumerate(MUTANTS):
        if only and str(i) not in only:
            continue
        if os.path.exists(WORK):
            shutil.rmtree(WORK)
        shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        p = os.path.join(WORK, "lumen.py")
        s = open(p).read()
        if s.count(old) != 1:
            print(f"[{i}] PATTERN-MISS {name}: count={s.count(old)}", flush=True)
            survivors.append((name, "pattern-miss"))
            continue
        open(p, "w").write(s.replace(old, new))
        try:
            r = subprocess.run([sys.executable, "tests/test_phase4.py"], cwd=WORK, capture_output=True, text=True, timeout=150)
            killed = r.returncode != 0
            tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr.strip()[-200:]
        except subprocess.TimeoutExpired:
            killed, tail = True, "timeout (hung)"
        print(f"[{i}] {'KILLED  ' if killed else 'SURVIVED'} {name}", flush=True)
        if not killed:
            survivors.append((name, "survived"))
    shutil.rmtree(os.path.dirname(WORK), ignore_errors=True)
    print(f"\n{len(MUTANTS) if not only else len(only)} mutants run; survivors: {survivors}")
    return 1 if survivors else 0

sys.exit(main())
