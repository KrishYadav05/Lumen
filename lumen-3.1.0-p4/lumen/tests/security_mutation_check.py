#!/usr/bin/env python3
"""Mutation check for the capability layer (docs/security-testing.md §6).

A passing security suite only proves the suite passes. This script checks that
the suite is not vacuous: it copies the repository, deliberately WEAKENS one
enforcement check at a time in the copy's lumen.py (drop the grant check, let
`file://` through, compare paths by text prefix, stop re-authorising redirects,
...), and runs the security suites against the weakened copy. Every mutant must
be KILLED — at least one suite must fail. A SURVIVOR means an enforcement
check exists that no test would notice losing.

    python3 tests/security_mutation_check.py          # all mutants (several minutes)
    python3 tests/security_mutation_check.py 3 7      # only mutants #3 and #7

Exit status is 0 only if every mutant was killed. The mutants match exact
source text, so when enforcement code is refactored a mutant reports
"pattern-miss" and must be updated — that is a failure, not a pass.
"""
import os, shutil, subprocess, sys, time, tempfile
SRC = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WORK = os.path.join(tempfile.mkdtemp(prefix="lumen-mutants-"), "tree")
MUTANTS = [
 ("scope check always true (fs_allows)",
  "return scope is None or any(_is_within(resolved_path, s) for s in scope)", "return True"),
 ("prefix-sibling: _is_within without separator",
  "return p.startswith(s if s.endswith(os.sep) else s + os.sep)", "return p.startswith(s)"),
 ("net scope ignores port",
  "any(h == host and (p is None or p == port) for h, p in scope)", "any(h == host for h, p in scope)"),
 ("net scope always true",
  "return scope is None or any(h == host and (p is None or p == port) for h, p in scope)", "return True"),
 ("redirects not re-authorized",
  '            _authorize_url(ctx, "http_get", newurl, redirect=True)\n', "            pass\n"),
 ("file:// scheme allowed",
  'if scheme not in ("http", "https"):', 'if False:'),
 ("importer ceiling dropped",
  "    if blocker is not None:\n        _deny(capability, op, resource, \"not-declared-by-importer\",", "    if False:\n        _deny(capability, op, resource, \"not-declared-by-importer\","),
 ("declaration check dropped",
  "    if blocker is ctx:\n        _deny(capability, op, resource, \"not-declared\",", "    if False:\n        _deny(capability, op, resource, \"not-declared\","),
 ("grant check dropped",
  "    if not ctx.policy.granted(capability):\n        _deny(capability, op, resource, \"not-granted\",", "    if False:\n        _deny(capability, op, resource, \"not-granted\","),
 ("non-Str argument accepted",
  "        if not isinstance(a, str):\n            what =", "        if False:\n            what ="),
 ("NUL check dropped",
  'if i == 0 and "\\x00" in a:', "if False:"),
 ("import accepts non-.lum files (import-as-read)",
  'if not path.endswith(".lum") or "\\x00" in path or os.path.isabs(path):', 'if "\\x00" in path or os.path.isabs(path):'),
 ("unmediated file read added elsewhere (tripwire)",
  "def _is_within(path, scope):", "def sneaky_read(p):\n    return open(p).read()\n\n\ndef _is_within(path, scope):"),
 ("walk follows directory symlinks (race window reopens)",
  "fd = os.open(name, _O_DIR | os.O_NOFOLLOW, dir_fd=fd)", "fd = os.open(name, _O_DIR, dir_fd=fd)"),
 ("descriptor-relative access disabled (path-based fallback)",
  "_HAVE_DIRFD = bool(", "_HAVE_DIRFD = False and bool("),
 ("granted directory not pinned",
  "        fd = self._pinned_fd(entry)\n        if fd is not None:\n            return fd, parts, None", "        fd = None\n        if fd is not None:\n            return fd, parts, None"),
 ("a swapped-in link in the walk is not refused (ELOOP/ENOTDIR ignored)",
  "if e.errno == errno.ELOOP or (e.errno == errno.ENOTDIR and _is_symlink_at(fd, name)):", "if False:"),
 ("Linux's ENOTDIR-for-a-symlink is not recognised",
  "(e.errno == errno.ENOTDIR and _is_symlink_at(fd, name))", "False"),
 ("O_NOFOLLOW dropped",
  'flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)', 'flags |= getattr(os, "O_CLOEXEC", 0)'),
 # ---- 5C: audit log ----
 ("audit: an allowed access is not recorded",
  '        if audit is not None:\n            audit.access("allow", capability, op, resource, ctx.label)\n        return _fs_perform',
  '        return _fs_perform'),
 ("audit: a denial is not recorded",
  '        if audit is not None:\n            audit.access("deny"', '        if False:\n            audit.access("deny"'),
 ("audit: a request's URL is logged unredacted",
  'audit.access("allow", "net.http", "http_get", _redact_url(url), ctx.label)', 'audit.access("allow", "net.http", "http_get", url, ctx.label)'),
 ("audit: a redirect hop's URL is logged unredacted",
  '_redact_url(newurl), ctx.label, redirect=True)', 'newurl, ctx.label, redirect=True)'),
 ("audit: resources are not length-capped",
  'return value if len(value) <= _AUDIT_FIELD_MAX else value[:_AUDIT_FIELD_MAX] + "...[truncated]"', 'return value'),
 ("audit: declarations are not recorded",
  'if new and audit is not None:', 'if False:'),
 ("audit: --audit-level=denied still records allows",
  'if decision == "allow" and self.level != "all":', 'if False:'),
 ("audit: an unwritable log does not stop the operation (fail open)",
  '                raise AuditError(f"AuditError: the audit log could not be written ({e.strerror}); "\n                                 f"the operation was not performed")',
  '                return'),
 ("audit: unrestricted fs.write allowed next to a log",
  'if "fs.write" in built_grants and scope is None:', 'if False:'),
 ("audit: a write scope containing the log allowed",
  'if _is_within(self.path, entry):', 'if False:'),
 ("audit: a log with several hard links accepted",
  'if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:', 'if False:'),
 ("audit: a symlink accepted as the log path",
  '                 | getattr(os, "O_NOFOLLOW", 0))\n        try:\n            fd = os.open(self.path', '                 )\n        try:\n            fd = os.open(self.path'),
 ("audit: the audit log is lost when a policy is extended",
  'return Policy(merged, audit=self.audit)', 'return Policy(merged)'),
 # ---- 5C: consent prompt ----
 ("prompt: runs without a terminal (TTY check dropped)",
  'if not (_is_tty(sys.stdin) and _is_tty(sys.stderr)):', 'if False:'),
 ("prompt: only stdin has to be a terminal",
  'if not (_is_tty(sys.stdin) and _is_tty(sys.stderr)):', 'if not _is_tty(sys.stdin):'),
 ("prompt: capabilities the program did not declare are offered",
  'undecided = [c for c in CAPABILITIES if c in declared and not policy.granted(c)]', 'undecided = [c for c in CAPABILITIES if not policy.granted(c)]'),
 ("prompt: 'everywhere' is case-insensitive",
  'entries = None if answer == "everywhere" else', 'entries = None if answer.lower() == "everywhere" else'),
 ("prompt: the final question defaults to yes",
  'confirmed = answer.lower() in ("y", "yes")', 'confirmed = answer.lower() not in ("n", "no")'),
 ("prompt: end of input counts as yes",
  '        if line == "":\n            raise EOFError\n', '        if line == "":\n            return "y"\n'),
 ("prompt: a flag's grant can be replaced by the prompt",
  '            if cap not in merged:\n                merged[cap] = scope', '            if True:\n                merged[cap] = scope'),
 ("prompt: control characters sent to the terminal",
  'c if c.isprintable() else', 'c if True else'),
 ("prompt: a refused scope (e.g. one reaching the audit log) crashes the prompt instead of being re-asked",
  'except ValueError as e:      # a bad scope, or one the audit log refuses: ask again, never crash', 'except KeyError as e:      # (mutant)'),
 ("prompt: undecodable input is not treated as a cancellation",
  'except (EOFError, KeyboardInterrupt, UnicodeDecodeError):', 'except (EOFError, KeyboardInterrupt):'),
 ("prompt: text from a refusal reaches the terminal unescaped",
  'out(f"  {_printable(problem)}")', 'out(f"  {problem}")'),
 ("prompt: effective permissions ignore the declaration ceiling",
  'if cap in declared and policy.granted(cap):\n            lines.append(f"  {cap:<9} allowed:', 'if policy.granted(cap):\n            lines.append(f"  {cap:<9} allowed:'),
]
def run(cmd, cwd):
    t=time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1500)
    return r.returncode, r.stdout[-400:], time.time()-t
def main():
    only = sys.argv[1:] 
    survivors = []
    for i,(name, old, new) in enumerate(MUTANTS):
        if only and str(i) not in only: continue
        if os.path.exists(WORK): shutil.rmtree(WORK)
        shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        p = os.path.join(WORK, "lumen.py")
        s = open(p).read()
        if s.count(old) != 1:
            print(f"[{i}] SKIP {name}: pattern count={s.count(old)}"); survivors.append((name,"pattern-miss")); continue
        open(p,"w").write(s.replace(old,new))
        killed_by=[]
        # cheapest suites first; the first one that fails kills the mutant
        for label, cmd in (("conf-audit", [sys.executable,"tests/conformance/test_audit.py"]),
                           ("conf-security", [sys.executable,"tests/conformance/test_security.py"]),
                           ("conf-capabilities", [sys.executable,"tests/conformance/test_capabilities.py"]),
                           ("conf-races", [sys.executable,"tests/conformance/test_fs_races.py"]),
                           ("consent-cli", [sys.executable,"tests/test_consent_cli.py"]),
                           ("adversarial", [sys.executable,"tests/test_security.py"])):
            r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=1500)
            import re as _re
            m = _re.search(r"(?:CONFORMANCE TOTAL|SECURITY HARDENING|CONSENT \+ AUDIT CLI SUITE): (\d+) passed, (\d+) failed", r.stdout)
            if r.returncode != 0 or m is None or int(m.group(2)) > 0:
                killed_by.append(label)
                break
        print(f"[{i}] {'KILLED' if killed_by else 'SURVIVED'} {name}: {killed_by}", flush=True)
        if not killed_by: survivors.append((name,"survived"))
    print("SURVIVORS:", survivors)
    shutil.rmtree(os.path.dirname(WORK), ignore_errors=True)
    return 1 if survivors else 0
sys.exit(main())
