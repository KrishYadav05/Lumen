#!/usr/bin/env python3
"""Runs every spec-driven conformance file under tests/conformance/ and reports
a single aggregate total. Each file maps to one docs/*.md chapter and is also
independently runnable (`python3 tests/conformance/test_types.py`) during
development. This script is what CI / `lumen Phase 1 sign-off` should run.
"""
import subprocess, sys, os, re

CONF_DIR = os.path.join(os.path.dirname(__file__), "conformance")
FILES = [
    "test_language.py",
    "test_grammar.py",
    "test_types.py",
    "test_errors.py",
    "test_modules.py",
    "test_concurrency.py",
    "test_security.py",
    "test_stdlib.py",
    "test_name_resolution.py",
    "test_ir.py",
    "test_testing.py",
]

TOTAL_RE = re.compile(r"CONFORMANCE TOTAL: (\d+) passed, (\d+) failed")

def main():
    grand_pass = grand_fail = 0
    any_crash = False
    print(f"{'CHAPTER':<24}{'PASS':>8}{'FAIL':>8}")
    print("-" * 40)
    for fname in FILES:
        path = os.path.join(CONF_DIR, fname)
        r = subprocess.run([sys.executable, path], capture_output=True, text=True)
        m = TOTAL_RE.search(r.stdout)
        chapter = fname.replace("test_", "").replace(".py", ".md")
        if r.returncode not in (0,) and m is None:
            print(f"{chapter:<24}{'CRASHED':>8}")
            print(r.stdout[-2000:])
            print(r.stderr[-2000:])
            any_crash = True
            continue
        p, f = int(m.group(1)), int(m.group(2))
        grand_pass += p
        grand_fail += f
        print(f"{chapter:<24}{p:>8}{f:>8}")
        if f:
            # surface the specific failures inline
            for line in r.stdout.splitlines():
                if line.strip().startswith("FAIL:"):
                    print("    " + line.strip())
    print("-" * 40)
    print(f"{'TOTAL':<24}{grand_pass:>8}{grand_fail:>8}")
    print()
    ok = grand_fail == 0 and not any_crash
    if ok:
        print(f"All {grand_pass} spec-driven conformance tests passed.")
    else:
        print("CONFORMANCE SUITE FAILED.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
