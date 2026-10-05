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

print("\nALL PASS" if all(results) else "SOME FAILED")
