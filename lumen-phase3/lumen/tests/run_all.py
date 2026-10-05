#!/usr/bin/env python3
"""Run everything: the original regression/CLI suite (tests/run_tests.py) plus
the full spec-driven conformance suite (tests/run_conformance.py). This is the
one command to run before calling Lumen 1.0 conformant: `python3 tests/run_all.py`.
"""
import subprocess, sys, os, re

HERE = os.path.dirname(__file__)

def run(script, label):
    r = subprocess.run([sys.executable, os.path.join(HERE, script)], capture_output=True, text=True)
    print(f"\n{'='*60}\n{label}\n{'='*60}")
    print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    return r.returncode == 0

def main():
    ok1 = run("run_tests.py", "REGRESSION + CLI SUITE (tests/run_tests.py)")
    ok2 = run("run_conformance.py", "SPEC-DRIVEN CONFORMANCE SUITE (tests/run_conformance.py)")
    print(f"\n{'='*60}")
    print("RESULT:", "ALL SUITES PASSED" if (ok1 and ok2) else "FAILURES PRESENT")
    print(f"{'='*60}")
    return 0 if (ok1 and ok2) else 1

if __name__ == "__main__":
    sys.exit(main())
