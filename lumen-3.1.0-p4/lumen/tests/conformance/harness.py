"""Shared harness for Lumen's conformance test suite.

Runs source in-process (no subprocess spawn) for speed, using the exact same
pipeline as `lumen run`: lex -> parse -> library prelude -> resolve -> type-check -> interpret.
Each `check()` call is tagged with the spec section it verifies, so a failure
tells you exactly which normative claim in docs/ broke.
"""
import sys, os, io, contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from lumen import (analyze, Interpreter, LumenError, LumenRaise,  # noqa: E402
                   Policy, DENY_ALL)

# Host policies the tests hand to the interpreter. A test grants authority the
# way a real host does — by constructing a Policy — never by writing `use fs`
# in the program under test (that only DECLARES; see docs/capabilities.md).
FS_ALL = Policy.unrestricted(["fs.read", "fs.write", "fs.list"])
NET_ALL = Policy.unrestricted(["net.http"])
ALL = Policy.unrestricted()

PASS = 0
FAIL = 0
FAILURES = []


def run_source(src, base_dir=".", policy=None):
    """Returns (stdout_text, error_message_or_None). Mirrors `run_file`'s exact
    pipeline: lex -> parse -> resolve -> type-check -> interpret. `policy` is
    the host's grant decision; None is the empty policy (default-deny)."""
    try:
        ast, _resolver = analyze(src)      # the one front end `lumen run` uses (library enums included)
    except (SyntaxError, LumenError) as e:
        return "", str(e)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            Interpreter(base_dir=base_dir, policy=policy).run(ast)
    except (LumenError, LumenRaise) as e:
        msg = e.message if isinstance(e, LumenRaise) else str(e)
        return buf.getvalue(), msg
    return buf.getvalue(), None


def check(spec_ref, name, src, expect_stdout=None, expect_error_substr=None,
          expect_ok=None, base_dir=".", policy=None):
    """
    spec_ref: e.g. "language.md §1.10" -- which normative claim this verifies.
    expect_stdout: exact expected stdout (after stripping trailing whitespace).
    expect_error_substr: a substring that MUST appear in the error message.
    expect_ok: True => must succeed, False => must fail (either check is
               independent of expect_stdout/expect_error_substr, which may
               also be supplied for a more precise assertion).
    """
    global PASS, FAIL
    out, err = run_source(src, base_dir=base_dir, policy=policy)
    ok = True
    detail = ""
    if expect_ok is True and err is not None:
        ok, detail = False, f"expected success, got error: {err}"
    elif expect_ok is False and err is None:
        ok, detail = False, f"expected failure, got success (stdout={out!r})"
    if ok and expect_stdout is not None and out.strip() != expect_stdout.strip():
        ok, detail = False, f"stdout mismatch: expected {expect_stdout!r}, got {out!r}"
    if ok and expect_error_substr is not None:
        if err is None or expect_error_substr not in err:
            ok, detail = False, f"expected error containing {expect_error_substr!r}, got {err!r}"
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"[{spec_ref}] {name}: {detail}")


def summary(label):
    total = PASS + FAIL
    print(f"{label}: {PASS}/{total} passed")


def print_final_summary():
    print(f"\n{'='*60}\nCONFORMANCE TOTAL: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
    if FAILURES:
        print(f"\n{len(FAILURES)} failures:")
        for f in FAILURES:
            print("  FAIL:", f)
    return FAIL == 0
