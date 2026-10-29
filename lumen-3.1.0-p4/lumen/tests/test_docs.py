#!/usr/bin/env python3
"""Verified documentation examples (ERR-012).

Publisher: Krish Kumar

An example in the specification that nobody runs is a claim, not a fact. This suite finds the examples
that docs/*.md marks as checkable and runs them:

    ```lumen run            a program; the next block, ```output, is exactly what it prints — on the
    ...                     reference interpreter, on the VM and on the VM at -O2 (all three must
    ```                     print it). If the program ends in a failure nobody catches, the text
    ```output               printed for that failure is the last part of the output block.
    ...
    ```

    ```lumen error E0201    a program the compiler must refuse with that code; the next block,
    ...                     ```diagnostic, is exactly what `lumen check` prints for it (file name
    ```                     example.lum)
    ```diagnostic
    ...
    ```

Examples are independent single files named example.lum. A mismatch fails the suite and names the
document and line — so a doc and the implementation cannot drift apart unnoticed.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from lumen import analyze, LumenError, render_diagnostic  # noqa: E402
from engines import run_program, ENGINES  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
FENCE = re.compile(r"^```(.*)$")
PASS = 0
FAIL = 0
FAILURES = []
COUNTS = {}


def blocks(path):
    """[(info string, text, first line number)] for every fenced block in the file."""
    out, lines, i = [], open(path).read().split("\n"), 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if m and m.group(1).strip():
            info, start, body = m.group(1).strip(), i + 1, []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            out.append((info, "\n".join(body) + "\n", start))
        i += 1
    return out


def record(ok, label):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label)


def check_file(name):
    path = os.path.join(DOCS, name)
    bs = blocks(path)
    n = 0
    for idx, (info, text, line) in enumerate(bs):
        words = info.split()
        if words[:2] == ["lumen", "run"]:
            nxt = bs[idx + 1] if idx + 1 < len(bs) else None
            if not nxt or nxt[0] != "output":
                record(False, f"{name}:{line}: a `lumen run` example needs an ```output block right after it")
                continue
            n += 1
            want = nxt[1].rstrip("\n")
            for eng in ENGINES:
                out, failure = run_program(text, eng, "example.lum")
                got = out + (failure + "\n" if failure else "")
                record(got.rstrip("\n") == want,
                       f"{name}:{line} [{eng}]: output differs\n--- documented ---\n{want}\n--- actual ---\n{got.rstrip(chr(10))}")
        elif words[:2] == ["lumen", "error"] and len(words) == 3:
            code = words[2]
            nxt = bs[idx + 1] if idx + 1 < len(bs) else None
            if not nxt or nxt[0] != "diagnostic":
                record(False, f"{name}:{line}: a `lumen error` example needs a ```diagnostic block right after it")
                continue
            n += 1
            try:
                analyze(text, "example.lum")
            except (SyntaxError, LumenError) as e:
                got = render_diagnostic(e) if isinstance(e, LumenError) else str(e)
                record(getattr(e, "code", None) == code and got.rstrip("\n") == nxt[1].rstrip("\n"),
                       f"{name}:{line}: expected {code} and the documented diagnostic\n--- documented ---\n{nxt[1].rstrip(chr(10))}\n--- actual ({getattr(e, 'code', None)}) ---\n{got}")
            else:
                record(False, f"{name}:{line}: documented as an error ({code}) but the program is accepted")
    COUNTS[name] = n


# every chapter that documents Phase 4 behaviour must carry verified examples (a floor, so the examples
# cannot be deleted one by one without this suite noticing)
MINIMUM = {"errors.md": 40, "types.md": 8, "language.md": 5, "grammar.md": 2}
for doc in sorted(MINIMUM):
    check_file(doc)
for doc, minimum in MINIMUM.items():
    record(COUNTS.get(doc, 0) >= minimum, f"{doc}: only {COUNTS.get(doc, 0)} verified examples, expected at least {minimum}")


def main():
    print("=" * 60)
    print("VERIFIED DOCUMENTATION EXAMPLES (tests/test_docs.py)")
    print("=" * 60)
    print("examples run: " + ", ".join(f"{k}: {v}" for k, v in sorted(COUNTS.items())))
    print(f"DOC EXAMPLES: {PASS} passed, {FAIL} failed (of {PASS + FAIL})")
    for f in FAILURES:
        print()
        print(f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
