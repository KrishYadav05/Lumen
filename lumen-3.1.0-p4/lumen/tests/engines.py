"""Run a Lumen program in-process on each execution engine (shared by the Phase 4 test files).

Publisher: Krish Kumar

`run_program(src, engine, label)` returns (stdout, failure_text_or_None) exactly as the
command line would print them: the program's own output, then — if a failure nobody caught ended
it — the text `lumen` prints for it. A compile-time error is returned as ('', 'STATIC <code> <text>').
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from lumen import (analyze, Interpreter, IRBuilder, IRValidator, IROptimizer, Compiler, VM,  # noqa: E402
                   LumenError, LumenRaise, failure_text)

ENGINES = ("interp", "vm", "vm-O2")


def run_program(src, engine, label="t.lum"):
    try:
        ast, resolver = analyze(src, label)
    except (SyntaxError, LumenError) as e:
        return "", f"STATIC {getattr(e, 'code', None)} {e}"
    buf = io.StringIO()
    failure = None
    with contextlib.redirect_stdout(buf):
        try:
            if engine == "interp":
                Interpreter(label=label).run(ast)
            else:
                ir = IRBuilder(resolver.module_globals).build_program(ast)
                errs = IRValidator().validate(ir)
                if errs:
                    raise AssertionError("IR validation failed: " + "; ".join(errs))
                if engine == "vm-O2":
                    ir = IROptimizer(2).optimize(ir)
                vm = VM()
                vm.file = label
                vm.run(Compiler().compile_program(ir))
        except (LumenError, LumenRaise, SyntaxError) as e:
            failure = failure_text(e)
    return buf.getvalue(), failure
