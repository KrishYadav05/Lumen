#!/usr/bin/env python3
"""Runs the standard-library test suite (std/tests/*.lum) on BOTH backends with
the host grants those tests are written for, from the repository root (the fs
builtins resolve paths against the working directory).

    lumen test std/tests/ --allow-fs=std/tests/fixtures --allow-net=127.0.0.1

Also checks the suite left the scratch directory clean: the tests write only
inside std/tests/fixtures/scratch/ and must remove everything they create.
It also compares the whole repository tree before and after: any file the run
creates anywhere (a test that escaped its scope, as an early fs test once did)
or deletes fails the suite.
"""
import os, re, subprocess, sys

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FLAGS = ["--allow-fs=std/tests/fixtures", "--allow-net=127.0.0.1"]
RESULT = re.compile(r"test result: (ok|FAILED)\. (\d+) passed; (\d+) failed")

def snapshot():
    out = set()
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", ".git")]
        out.update(os.path.relpath(os.path.join(d, f), ROOT) for f in files if not f.endswith(".pyc"))
    return out

def main():
    ok = True
    before = snapshot()
    for label, extra in (("interpreter", []), ("vm", ["--vm"])):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "lumen.py"), "test"] + extra + ["std/tests/"] + FLAGS,
                           cwd=ROOT, capture_output=True, text=True)
        m = RESULT.search(r.stdout)
        if r.returncode != 0 or not m or m.group(1) != "ok":
            ok = False
            print(f"STDLIB ({label}): FAILED")
            print(r.stdout[-3000:], r.stderr[-1000:])
        else:
            print(f"STDLIB ({label}): {m.group(2)} passed, {m.group(3)} failed")
    left = sorted(os.listdir(os.path.join(ROOT, "std", "tests", "fixtures", "scratch")))
    if left != ["README.txt"]:
        ok = False
        print(f"STDLIB: scratch directory not clean: {left}")
    after = snapshot()
    if after != before:
        ok = False
        print(f"STDLIB: the run changed the tree: created {sorted(after - before)}, removed {sorted(before - after)}")
    print("STDLIB RESULT:", "ok" if ok else "FAILED")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
