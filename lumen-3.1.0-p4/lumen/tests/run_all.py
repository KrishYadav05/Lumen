#!/usr/bin/env python3
"""Run everything: the regression/CLI suite (run_tests.py), the spec-driven
conformance suite (run_conformance.py), interpreter<->VM equivalence, optimizer
stability, the adversarial security suite (test_security.py) and the standard
library's own tests on both backends (run_stdlib.py), and the consent-prompt / audit-log CLI
suite (test_consent_cli.py), the Phase 4 error-handling suite (test_phase4.py) and the verified
documentation examples (test_docs.py). This is the one command
to run before a release: `python3 tests/run_all.py`.

Not included (slower, run on demand): `python3 tests/security_mutation_check.py`,
which weakens each capability check in a copy and verifies the suites notice, and
`python3 tests/phase4_mutation_check.py`, which does the same for the error-handling rules.

Publisher: Krish Kumar
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
    ok3 = run("test_equivalence.py", "INTERPRETER <-> VM EQUIVALENCE SUITE (tests/test_equivalence.py)")
    ok4 = run("test_optimizer.py", "OPTIMIZER STABILITY SUITE (tests/test_optimizer.py, -O0/-O1/-O2)")
    ok5 = run("test_security.py", "SECURITY / CAPABILITY-BYPASS SUITE (tests/test_security.py)")
    ok6 = run("run_stdlib.py", "STANDARD-LIBRARY SUITE, both backends (tests/run_stdlib.py)")
    ok7 = run("test_consent_cli.py", "CONSENT PROMPT + AUDIT LOG, through the CLI (tests/test_consent_cli.py)")
    ok8 = run("test_phase4.py", "PHASE 4 - ERROR HANDLING & RELIABILITY (tests/test_phase4.py)")
    ok9 = run("test_docs.py", "VERIFIED DOCUMENTATION EXAMPLES (tests/test_docs.py)")
    all_ok = ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8 and ok9
    print(f"\n{'='*60}")
    print("RESULT:", "ALL SUITES PASSED" if all_ok else "FAILURES PRESENT")
    print(f"{'='*60}")
    return 0 if all_ok else 1

if __name__ == "__main__":
    sys.exit(main())
