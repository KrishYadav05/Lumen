import subprocess, json, sys, os

SERVER = os.path.join(os.path.dirname(__file__), "lsp_server.py")

def send(p, obj):
    body = json.dumps(obj).encode()
    p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    p.stdin.flush()

def recv(p):
    length = None
    while True:
        line = p.stdout.readline().decode().strip()
        if line == "": break
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = p.stdout.read(length)
    return json.loads(body)

def run_case(name, source, expect_diag):
    p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    reply = recv(p)
    assert "result" in reply, f"{name}: bad initialize reply {reply}"
    send(p, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
              "params": {"textDocument": {"uri": "file:///t.lum", "text": source}}})
    diag_msg = recv(p)
    diags = diag_msg["params"]["diagnostics"]
    ok = (len(diags) > 0) == expect_diag
    print(("PASS" if ok else "FAIL"), name, "->", diags)
    p.stdin.close(); p.terminate()
    return ok

results = []
results.append(run_case("valid_program", 'let x = 5\nprint x + 1\n', expect_diag=False))
results.append(run_case("type_error", 'let x: Int = "oops"\n', expect_diag=True))
results.append(run_case("syntax_error", 'let x = \n', expect_diag=True))

def run_completion_case(name, source, expect_label):
    p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    recv(p)
    send(p, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
              "params": {"textDocument": {"uri": "file:///t.lum", "text": source}}})
    recv(p)  # diagnostics notification
    send(p, {"jsonrpc": "2.0", "id": 2, "method": "textDocument/completion",
              "params": {"textDocument": {"uri": "file:///t.lum"}, "position": {"line": 0, "character": 0}}})
    reply = recv(p)
    labels = [i["label"] for i in reply["result"]]
    ok = expect_label in labels and "let" in labels and "Int" in labels
    print(("PASS" if ok else "FAIL"), name, "-> has", expect_label, ":", expect_label in labels,
          "| total items:", len(labels))
    p.stdin.close(); p.terminate()
    return ok

results.append(run_completion_case("completion_finds_user_fn", 'fn square(x):\n    return x * x\n', "square"))

results.append(run_case("undefined_name_now_caught", 'print totally_undefined\n', expect_diag=True))

# Phase 5 (spec 3.0): capability declarations are checked statically, so the editor shows the errors too.
results.append(run_case("capability_declaration_ok", 'use fs.read\nuse net.http\nprint 1\n', expect_diag=False))
results.append(run_case("capability_alias_ok", 'use fs\nuse net\nprint 1\n', expect_diag=False))
results.append(run_case("unknown_capability_flagged", 'use fs.chmod\n', expect_diag=True))
results.append(run_case("reserved_capability_flagged", 'use process.spawn\n', expect_diag=True))
results.append(run_case("nested_capability_declaration_flagged", 'fn f():\n    use fs.read\n    return 1\n', expect_diag=True))

results.append(run_completion_case("completion_offers_2_3_file_builtin", 'let x = 1\n', "list_dir"))
results.append(run_completion_case("completion_offers_range", 'let x = 1\n', "range"))

def builtin_lists_match_the_interpreter():
    """The extension keeps its own copies of the builtin-name list (for
    autocomplete and for syntax highlighting). They drifted once — `range`,
    `now` and `read_line` were missing for several releases — so compare both
    against the interpreter's single source of truth, from the bundled lumen.py."""
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import lumen
    import lsp_server
    truth = set(lumen.BUILTIN_FUNCTION_NAMES) | {"print"}   # `print` is a statement, but highlighted as a builtin
    lsp = set(lsp_server.BUILTINS)
    with open(os.path.join(here, "syntaxes", "lumen.tmLanguage.json")) as f:
        grammar = json.load(f)
    pattern = None
    def walk(node):
        nonlocal pattern
        if isinstance(node, dict):
            if node.get("name") == "support.function.builtin.lumen":
                pattern = node["match"]
            for v in node.values(): walk(v)
        elif isinstance(node, list):
            for v in node: walk(v)
    walk(grammar)
    tm = set(re.search(r"\\b\((.*)\)\\b", pattern).group(1).split("|"))
    ok = True
    # print is a keyword in the LSP's KEYWORDS list, so it is not required in BUILTINS
    if lsp != (truth - {"print"}):
        print("FAIL lsp BUILTINS differ: missing", sorted((truth - {"print"}) - lsp), "extra", sorted(lsp - (truth - {"print"})))
        ok = False
    if tm != truth:
        print("FAIL tmLanguage builtins differ: missing", sorted(truth - tm), "extra", sorted(tm - truth))
        ok = False
    if ok:
        print("PASS builtin lists (autocomplete + highlighting) match lumen.BUILTIN_FUNCTION_NAMES")
    return ok

results.append(builtin_lists_match_the_interpreter())

# ---- Language Specification 3.1 (Phase 4): Result / Option / `?` / finally, and exact, coded diagnostics ----
def first_diagnostics(source, uri="file:///t.lum"):
    p = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    recv(p)
    send(p, {"jsonrpc": "2.0", "method": "textDocument/didOpen",
              "params": {"textDocument": {"uri": uri, "text": source}}})
    diags = recv(p)["params"]["diagnostics"]
    p.stdin.close(); p.terminate()
    return diags

def diag_case(name, source, code, line0, start, end, severity=1, words=()):
    diags = first_diagnostics(source)
    d = diags[0] if diags else {}
    r = d.get("range", {})
    ok = (d.get("code") == code and d.get("severity") == severity
          and r.get("start") == {"line": line0, "character": start} and r.get("end") == {"line": line0, "character": end}
          and all(w in d.get("message", "") for w in words))
    print(("PASS" if ok else "FAIL"), name, "->", d)
    return ok

def clean_case(name, source):
    diags = first_diagnostics(source)
    print(("PASS" if not diags else "FAIL"), name, "->", diags)
    return not diags

# the spec's required negative test: exact span (the `Ok("hello")`), the code, and the expected/found notes
results.append(diag_case("result_payload_mismatch_is_located_and_coded",
                         'let result: Result<Int, Str> = Ok("hello")\n', "E0201", 0, 31, 42,
                         words=("expected: Result<Int, Str>", "found: Result<Str, _>")))
results.append(diag_case("incompatible_error_type_for_question_mark",
                         'fn read() -> Result<Str, Int>:\n    return Err(1)\n\nfn calc() -> Result<Int, Str>:\n    let v = read()?\n    return Ok(1)\n',
                         "E0404", 4, 12, 19, words=("help:",)))
results.append(diag_case("wrong_number_of_type_arguments_underlines_the_type",
                         'let r: Result<Int> = Ok(1)\n', "E0202", 0, 7, 18))
results.append(diag_case("a_match_that_may_not_cover_everything_is_a_warning_not_an_error",
                         'fn f(x):\n    match x:\n        Some(v):\n            return v\n', "W0301", 1, 4, 12, severity=2,
                         words=("does not cover: None",)))
results.append(clean_case("result_option_question_mark_program_is_clean",
                          'fn parse(t: Str) -> Result<Int, Str>:\n    if t == "":\n        return Err("empty")\n    return Ok(len(t))\n\n'
                          'fn twice(t: Str) -> Result<Int, Str>:\n    let n = parse(t)?\n    return Ok(n * 2)\n\nprint twice("ab")\n'))
results.append(clean_case("try_catch_finally_is_clean",
                          'fn f():\n    try:\n        fail("x")\n    catch e:\n        print e.message\n    finally:\n        print "done"\nf()\n'))
results.append(clean_case("generic_enum_and_typed_struct_are_clean",
                          'enum Tree<T>:\n    Leaf\n    Node(left: Tree<T>, value: T, right: Tree<T>)\n\nstruct Pair<A, B>:\n    first: A\n    second: B\n\n'
                          'let t: Tree<Int> = Node(Leaf, 1, Leaf)\nlet p: Pair<Int, Str> = Pair { first: 1, second: "a" }\nprint p.first\n'))
results.append(run_case("finally_return_is_rejected", 'fn f() -> Result<Int, Str>:\n    try:\n        return Ok(1)\n    finally:\n        return Ok(2)\n', expect_diag=True))
results.append(run_completion_case("completion_offers_ok_or", 'let x = 1\n', "ok_or"))
results.append(run_completion_case("completion_offers_Result", 'let x = 1\n', "Result"))
results.append(run_completion_case("completion_offers_finally", 'let x = 1\n', "finally"))
results.append(run_completion_case("completion_offers_Some", 'let x = 1\n', "Some"))

print("\nALL PASS" if all(results) else "SOME FAILED")
