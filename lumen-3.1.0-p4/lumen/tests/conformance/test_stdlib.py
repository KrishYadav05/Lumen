"""Conformance tests for docs/standard-library.md."""
import os, tempfile, shutil, subprocess, sys
from harness import check, run_source, print_final_summary, FS_ALL
import harness

LUMEN = os.path.join(os.path.dirname(__file__), "..", "..", "lumen.py")

# ---- §8.3 display conversion table (one case per row) ----
check("standard-library.md §8.3", "Int display", 'print 42', "42")
check("standard-library.md §8.3", "Float display", 'print 3.5', "3.5")
check("standard-library.md §8.3", "Str display (unquoted)", 'print "hello"', "hello")
check("standard-library.md §8.3", "Bool true display", 'print true', "true")
check("standard-library.md §8.3", "Bool false display", 'print false', "false")
check("standard-library.md §8.3", "List display with recursive element conversion",
      'print [1, "a", true]', '[1, a, true]')
check("standard-library.md §8.3", "Dict display, insertion order",
      'print {"b": 1, "a": 2}', '{b: 1, a: 2}')
check("standard-library.md §8.3", "struct display shows declared field order",
      'struct P:\n    x\n    y\nprint P { x: 1, y: 2 }', "P { x: 1, y: 2 }")
check("standard-library.md §8.3", "enum payload variant display",
      'enum E:\n    Pair(a, b)\nprint Pair(1, 2)', "Pair(1, 2)")
check("standard-library.md §8.3", "enum unit variant display (bare name)",
      'enum E:\n    A\nprint A', "A")
check("standard-library.md §8.3", "task handle display", 'fn f():\n    return 1\nlet t = spawn f()\nwait(t)\nprint t', "<task>")
check("standard-library.md §8.3", "channel display", 'print chan()', "<channel>")

# ---- §8.5 collection functions: correct usage ----
check("standard-library.md §8.5", "len() on Str counts characters", 'print len("hello")', "5")
check("standard-library.md §8.5", "len() on List counts elements", 'print len([1,2,3])', "3")
check("standard-library.md §8.5", "len() on Dict counts pairs", 'print len({"a":1,"b":2})', "2")
check("standard-library.md §8.5", "len() on empty collections is 0", 'print len([])', "0")
check("standard-library.md §8.5", "push() mutates in place and returns nothing-visible",
      'let a = [1]\npush(a, 2)\nprint a', "[1, 2]")
check("standard-library.md §8.5", "push() mutation is visible through another reference to the same List",
      'let a = [1]\nlet b = a\npush(b, 2)\nprint a', "[1, 2]")
check("standard-library.md §8.5", "keys() returns insertion-ordered List of keys",
      'print keys({"z": 1, "a": 2})', '[z, a]')
check("standard-library.md §8.5", "values() returns insertion-ordered List of values, parallel to keys()",
      'print values({"z": 1, "a": 2})', '[1, 2]')

# ---- §8.5 collection functions: wrong-type usage is a clean error, never a raw crash ----
check("standard-library.md §8.5", "len() on Int is a clean TypeError", 'print len(5)', expect_ok=False, expect_error_substr="len() expects")
check("standard-library.md §8.5", "len() on Bool is a clean TypeError", 'print len(true)', expect_ok=False, expect_error_substr="len() expects")
check("standard-library.md §8.5", "push() on a Str is a clean TypeError", 'push("hi", 1)', expect_ok=False, expect_error_substr="push() expects a List")
check("standard-library.md §8.5", "push() on a Dict is a clean TypeError", 'push({"a":1}, 2)', expect_ok=False, expect_error_substr="push() expects a List")
check("standard-library.md §8.5", "keys() on a List is a clean TypeError", 'print keys([1,2])', expect_ok=False, expect_error_substr="keys() expects a Dict")
check("standard-library.md §8.5", "values() on an Int is a clean TypeError", 'print values(5)', expect_ok=False, expect_error_substr="values() expects a Dict")
check("standard-library.md §8.5", "wrong-type builtin errors are catchable, not process-fatal",
      'try:\n    push("hi", 1)\ncatch e:\n    print "caught: " + e.message', "caught: TypeError: push() expects a List as its first argument, got str")

# ---- §8.5.1 chr()/ord() ----
check("standard-library.md §8.5.1", "chr() of a known code point", 'print chr(65)', "A")
check("standard-library.md §8.5.1", "chr(34) produces a Str containing a literal double-quote",
      'let q = chr(34)\nprint len(q)\nprint q == q', "1\ntrue")
check("standard-library.md §8.5.1", "ord() of a single-character Str", 'print ord("A")', "65")
check("standard-library.md §8.5.1", "chr() and ord() round-trip", 'print ord(chr(97))', "97")
check("standard-library.md §8.5.1", "chr(34) lets a program build a string containing a double-quote",
      'let q = chr(34)\nprint q + "hi" + q', '"hi"')
check("standard-library.md §8.5.1", "chr() on a Float is a clean TypeError",
      'print chr(65.0)', expect_ok=False, expect_error_substr="chr() expects an Int")
check("standard-library.md §8.5.1", "chr() on a Bool is a clean TypeError",
      'print chr(true)', expect_ok=False, expect_error_substr="chr() expects an Int")
check("standard-library.md §8.5.1", "chr() out of range is a clean TypeError",
      'print chr(-1)', expect_ok=False, expect_error_substr="out of the valid code point range")
check("standard-library.md §8.5.1", "chr() out of range (too high) is a clean TypeError",
      'print chr(9999999)', expect_ok=False, expect_error_substr="out of the valid code point range")
check("standard-library.md §8.5.1", "ord() on a multi-character Str is a clean TypeError",
      'print ord("ab")', expect_ok=False, expect_error_substr="ord() expects a single-character Str")
check("standard-library.md §8.5.1", "ord() on an empty Str is a clean TypeError",
      'print ord("")', expect_ok=False, expect_error_substr="ord() expects a single-character Str")
check("standard-library.md §8.5.1", "ord() on an Int is a clean TypeError",
      'print ord(65)', expect_ok=False, expect_error_substr="ord() expects a single-character Str")

# ---- §8.5.1 type_of() ----
check("standard-library.md §8.5.1", "type_of() on Int", 'print type_of(5)', "Int")
check("standard-library.md §8.5.1", "type_of() on Float", 'print type_of(5.0)', "Float")
check("standard-library.md §8.5.1", "type_of() on Bool true", 'print type_of(true)', "Bool")
check("standard-library.md §8.5.1", "type_of() on Bool false", 'print type_of(false)', "Bool")
check("standard-library.md §8.5.1", "type_of() distinguishes Bool from Int despite 0==false/1==true",
      'print type_of(0)\nprint type_of(1)\nprint type_of(false)\nprint type_of(true)',
      "Int\nInt\nBool\nBool")
check("standard-library.md §8.5.1", "type_of() on Str", 'print type_of("hi")', "Str")
check("standard-library.md §8.5.1", "type_of() on List", 'print type_of([1,2])', "List")
check("standard-library.md §8.5.1", "type_of() on Dict", 'print type_of({"a":1})', "Dict")
check("standard-library.md §8.5.1", "type_of() on Nothing (a function falling off its end)",
      'fn f():\n    let x = 1\nprint type_of(f())', "Nothing")
check("standard-library.md §8.5.1", "type_of() on a struct value returns its declared type name",
      'struct P:\n    x\nprint type_of(P { x: 1 })', "P")
check("standard-library.md §8.5.1", "type_of() on an enum value returns the enum's type name, not the variant name",
      'enum Opt:\n    Some(v)\n    None\nprint type_of(Some(1))\nprint type_of(None)', "Opt\nOpt")
check("standard-library.md §8.5.1", "type_of() on a channel", 'print type_of(chan())', "Channel")
check("standard-library.md §8.5.1", "type_of() on a task handle",
      'fn f():\n    return 1\nlet t = spawn f()\nprint type_of(t)\nwait(t)', "Task")
check("standard-library.md §8.5.1", "type_of() never raises, even on every value kind in one pass",
      'print type_of(1) + " " + type_of(1.0) + " " + type_of(true) + " " + type_of("s") '
      '+ " " + type_of([1]) + " " + type_of({1: 1})',
      "Int Float Bool Str List Dict")

# ---- §8.6 fail() ----
check("standard-library.md §8.6", "fail() message passed through unmodified",
      'try:\n    fail("exact text")\ncatch e:\n    print e.message', "exact text")

# ---- §8.7 I/O signatures (capability-gated behavior re-verified from the stdlib-contract angle) ----
def io_roundtrip():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "f.txt")
        out, err = run_source(f'use fs\nprint write_file("{p}", "content")', policy=FS_ALL)
        ok1 = err is None and out.strip() == "nothing"
        out2, err2 = run_source(f'use fs\nprint read_file("{p}")', policy=FS_ALL)
        ok2 = err2 is None and out2.strip() == "content"
        if ok1 and ok2:
            harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[standard-library.md §8.4/§8.7] write_file returns nothing, read_file round-trips: "
                                     f"write=({out!r},{err!r}) read=({out2!r},{err2!r})")
    finally:
        shutil.rmtree(d)
io_roundtrip()

# ---- §8.4, 2.3's additions: append_file / file_exists / is_dir / list_dir / delete_file ----
def fs_case(spec_ref, name, setup, src_template, expect_stdout=None, expect_error_substr=None):
    """Runs `src_template` (with {d} = a fresh temp directory) after `setup(d)`,
    checking stdout and/or an error substring. In-process, like check()."""
    d = tempfile.mkdtemp()
    try:
        if setup:
            setup(d)
        out, err = run_source(src_template.format(d=d), policy=FS_ALL)
        ok = True
        if expect_error_substr is None and err is not None:
            ok = False
        if expect_stdout is not None and out.strip() != expect_stdout.strip():
            ok = False
        if expect_error_substr is not None and (err is None or expect_error_substr not in err):
            ok = False
        if ok:
            harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[{spec_ref}] {name}: out={out!r} err={err!r} "
                                     f"(wanted out={expect_stdout!r} err~{expect_error_substr!r})")
    finally:
        shutil.rmtree(d)

def mkfile(rel, content=""):
    def setup(d):
        with open(os.path.join(d, rel), "w") as f:
            f.write(content)
    return setup

def mkfiles(*names):
    def setup(d):
        for n in names:
            if n.endswith("/"):
                os.makedirs(os.path.join(d, n))
            else:
                open(os.path.join(d, n), "w").close()
    return setup

S = "standard-library.md §8.4"
fs_case(S, "file_exists: true for a file", mkfile("f"), 'use fs\nprint file_exists("{d}/f")', "true")
fs_case(S, "file_exists: true for a directory too", None, 'use fs\nprint file_exists("{d}")', "true")
fs_case(S, "file_exists: false for a missing path", None, 'use fs\nprint file_exists("{d}/nope")', "false")
fs_case(S, "file_exists never raises: the empty path is just false", None, 'use fs\nprint file_exists("")', "false")
fs_case(S, "is_dir: true for a directory", None, 'use fs\nprint is_dir("{d}")', "true")
fs_case(S, "is_dir: false for a regular file", mkfile("f"), 'use fs\nprint is_dir("{d}/f")', "false")
fs_case(S, "is_dir: false (not an error) for a missing path", None, 'use fs\nprint is_dir("{d}/nope")', "false")
fs_case(S, "file vs directory vs nothing is distinguishable with file_exists + is_dir",
        mkfiles("f", "sub/"),
        'use fs\n'
        'fn kind(p):\n'
        '    if not file_exists(p):\n'
        '        return "nothing"\n'
        '    if is_dir(p):\n'
        '        return "dir"\n'
        '    return "file"\n'
        'print kind("{d}/f") + " " + kind("{d}/sub") + " " + kind("{d}/zzz")',
        "file dir nothing")
fs_case(S, "append_file creates a missing file and returns nothing",
        None, 'use fs\nprint append_file("{d}/new", "abc")\nprint read_file("{d}/new")', "nothing\nabc")
fs_case(S, "append_file appends without disturbing existing contents",
        mkfile("f", "start-"), 'use fs\nappend_file("{d}/f", "one-")\nappend_file("{d}/f", "two")\nprint read_file("{d}/f")',
        "start-one-two")
fs_case(S, "write_file still overwrites (it never appends)",
        mkfile("f", "old"), 'use fs\nwrite_file("{d}/f", "new")\nprint read_file("{d}/f")', "new")
def exact_msg(spec_ref, name, body, template):
    """`body` runs inside a try/catch printing e.message; asserts the message
    equals template with {d} filled in — the documented errors.md §4.4.8 shape,
    verified character for character, and that the failure was catchable."""
    d = tempfile.mkdtemp()
    try:
        out, err = run_source(f'use fs\ntry:\n    {body.format(d=d)}\ncatch e:\n    print e.message', policy=FS_ALL)
        want = template.format(d=d)
        if err is None and out.strip() == want:
            harness.PASS += 1
        else:
            harness.FAIL += 1
            harness.FAILURES.append(f"[{spec_ref}] {name}: out={out!r} err={err!r} want={want!r}")
    finally:
        shutil.rmtree(d)

exact_msg("errors.md §4.4.8", "append_file failure message is exactly could not append to '<path>': <reason>",
          'append_file("{d}/no/such/dir/f", "x")',
          "could not append to '{d}/no/such/dir/f': No such file or directory")
exact_msg("errors.md §4.4.8", "list_dir failure message is exactly could not list directory '<path>': <reason>",
          'list_dir("{d}/nope")', "could not list directory '{d}/nope': No such file or directory")
exact_msg("errors.md §4.4.8", "delete_file failure message is exactly could not delete '<path>': <reason>",
          'delete_file("{d}/nope")', "could not delete '{d}/nope': No such file or directory")
fs_case(S, "list_dir returns bare entry names, files and directories together, sorted",
        mkfiles("b.txt", "a.txt", "sub/"), 'use fs\nprint list_dir("{d}")', "[a.txt, b.txt, sub]")
fs_case(S, "list_dir sorts in code-point order (uppercase before lowercase)",
        mkfiles("b", "a", "B", "A"), 'use fs\nprint list_dir("{d}")', "[A, B, a, b]")
fs_case(S, "list_dir of an empty directory is []", None, 'use fs\nprint list_dir("{d}")', "[]")
fs_case(S, "list_dir never includes . or ..",
        mkfile("f"), 'use fs\nlet names = list_dir("{d}")\nprint len(names)\nprint names[0]', "1\nf")
fs_case("errors.md §4.4.8", "list_dir failure message names the path and the OS reason",
        mkfile("f"), 'use fs\nlist_dir("{d}/f")', expect_error_substr="could not list directory '")
fs_case("errors.md §4.4.8", "list_dir on a regular file fails with the OS reason",
        mkfile("f"), 'use fs\nlist_dir("{d}/f")', expect_error_substr="Not a directory")
fs_case(S, "delete_file removes the file and returns nothing",
        mkfile("f"), 'use fs\nprint delete_file("{d}/f")\nprint file_exists("{d}/f")', "nothing\nfalse")
fs_case("errors.md §4.4.8", "delete_file of a missing file fails: could not delete '<path>': <reason>",
        None, 'use fs\ndelete_file("{d}/nope")', expect_error_substr="could not delete '")
fs_case(S, "delete_file never deletes a directory: it fails, and the directory is still there",
        mkfiles("sub/"),
        'use fs\ntry:\n    delete_file("{d}/sub")\ncatch e:\n    print "refused"\nprint is_dir("{d}/sub")',
        "refused\ntrue")

# ---- §8.8 CLI (subprocess — these exercise the actual entry point, not just in-process pipeline) ----
def cli_check(spec_ref, name, args, expect_substr=None, expect_rc=0):
    r = subprocess.run([sys.executable, LUMEN] + args, capture_output=True, text=True)
    ok = r.returncode == expect_rc and (expect_substr is None or expect_substr in r.stdout)
    if ok: harness.PASS += 1
    else:
        harness.FAIL += 1
        harness.FAILURES.append(f"[{spec_ref}] {name}: rc={r.returncode} stdout={r.stdout!r}")

d = tempfile.mkdtemp()
with open(os.path.join(d, "ok.lum"), "w") as f: f.write('print "hi"')
with open(os.path.join(d, "bad.lum"), "w") as f: f.write('let x: Int = "s"')
cli_check("standard-library.md §8.8", "lumen run executes a valid file", ["run", os.path.join(d, "ok.lum")], "hi")
cli_check("standard-library.md §8.8", "lumen check reports success without executing",
          ["check", os.path.join(d, "ok.lum")], "No errors found")
cli_check("standard-library.md §8.8", "lumen check reports a static error", ["check", os.path.join(d, "bad.lum")],
          "TypeError", expect_rc=1)
cli_check("standard-library.md §8.8", "lumen run --vm executes the supported subset",
          ["run", "--vm", os.path.join(d, "ok.lum")], "hi")
shutil.rmtree(d)

d2 = tempfile.mkdtemp()
r = subprocess.run([sys.executable, LUMEN, "new", os.path.join(d2, "proj")], capture_output=True, text=True)
ok = (r.returncode == 0
      and os.path.isfile(os.path.join(d2, "proj", "src", "main.lum"))
      and os.path.isfile(os.path.join(d2, "proj", "lumen.toml"))
      and os.path.isfile(os.path.join(d2, "proj", "tests", "main_test.lum"))
      and os.path.isfile(os.path.join(d2, "proj", "README.md")))
if ok: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] lumen new scaffolds src/main.lum + tests/ + README.md + lumen.toml: rc={r.returncode}")

r_run = subprocess.run([sys.executable, LUMEN, "run"], capture_output=True, text=True, cwd=os.path.join(d2, "proj"))
ok_run = r_run.returncode == 0 and "Hello from" in r_run.stdout
if ok_run: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] `lumen run` with zero args works via lumen.toml's entry: rc={r_run.returncode} stdout={r_run.stdout!r}")

r2 = subprocess.run([sys.executable, LUMEN, "install"], capture_output=True, text=True, cwd=os.path.join(d2, "proj"))
ok2 = r2.returncode == 0 and "No dependencies" in r2.stdout
if ok2: harness.PASS += 1
else:
    harness.FAIL += 1
    harness.FAILURES.append(f"[standard-library.md §8.8] lumen install with no deps reports cleanly: stdout={r2.stdout!r}")
shutil.rmtree(d2)

print_final_summary()
