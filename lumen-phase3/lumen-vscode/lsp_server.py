#!/usr/bin/env python3
"""Minimal Lumen Language Server: wraps lumen.py's Parser + TypeChecker
and speaks just enough LSP (JSON-RPC over stdio) to publish diagnostics
on open/change. Not a full LSP implementation — no autocomplete/hover yet.
"""
import sys, os, json, re

sys.path.insert(0, os.path.dirname(__file__))
from lumen import lex, Parser, Resolver, TypeChecker, LumenError  # noqa: E402

LINE_RE = re.compile(r"line (\d+)")

def check_text(text):
    """Returns a list of LSP Diagnostic dicts (0 or 1 entries: we stop at first error)."""
    try:
        ast = Parser(lex(text)).parse_program()
        Resolver().resolve_program(ast)
        TypeChecker().check_program(ast)
        return []
    except (SyntaxError, LumenError) as e:
        msg = str(e)
        m = LINE_RE.search(msg)
        line0 = (int(m.group(1)) - 1) if m else 0
        line0 = max(line0, 0)
        return [{
            "range": {"start": {"line": line0, "character": 0},
                      "end": {"line": line0, "character": 999}},
            "severity": 1,  # Error
            "source": "lumen",
            "message": msg,
        }]
    except Exception as e:
        return [{
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 999}},
            "severity": 1, "source": "lumen", "message": f"Internal error: {e}",
        }]

KEYWORDS = ["let","print","if","else","for","in","fn","return","true","false",
            "try","catch","struct","and","or","not","use","as","enum","match","spawn","test"]
BUILTINS = ["len","push","keys","values","read_file","write_file","http_get",
            "fail","wait","chan","send","receive","assert","assert_eq","assert_ne"]
TYPES = ["Int","Float","Str","Bool","List","Dict","Any"]
SYMBOL_RE = re.compile(r"\b(?:fn|let|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")

def extract_symbols(text):
    return sorted(set(SYMBOL_RE.findall(text)))

def completion_items(text):
    items = []
    for kw in KEYWORDS: items.append({"label": kw, "kind": 14})   # Keyword
    for b in BUILTINS: items.append({"label": b, "kind": 3})       # Function
    for t in TYPES: items.append({"label": t, "kind": 7})          # Class/Type
    for name in extract_symbols(text): items.append({"label": name, "kind": 6})  # Variable
    return items

def read_message(inp):
    length = None
    while True:
        line = inp.readline()
        if not line:
            return None
        line = line.decode("utf-8", "replace").strip()
        if line == "":
            break
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length is None:
        return None
    body = inp.read(length)
    return json.loads(body.decode("utf-8"))

def write_message(out, obj):
    body = json.dumps(obj).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    out.write(header)
    out.write(body)
    out.flush()

def publish_diagnostics(out, uri, diagnostics):
    write_message(out, {
        "jsonrpc": "2.0",
        "method": "textDocument/publishDiagnostics",
        "params": {"uri": uri, "diagnostics": diagnostics},
    })

def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    docs = {}
    while True:
        msg = read_message(inp)
        if msg is None:
            break
        method = msg.get("method")
        if method == "initialize":
            write_message(out, {
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"capabilities": {
                    "textDocumentSync": 1,  # full sync
                    "completionProvider": {"triggerCharacters": []},
                }},
            })
        elif method in ("textDocument/didOpen", "textDocument/didChange"):
            td = msg["params"]["textDocument"]
            uri = td["uri"]
            if method == "textDocument/didOpen":
                text = td["text"]
            else:
                text = msg["params"]["contentChanges"][-1]["text"]
            docs[uri] = text
            publish_diagnostics(out, uri, check_text(text))
        elif method == "textDocument/completion":
            uri = msg["params"]["textDocument"]["uri"]
            text = docs.get(uri, "")
            write_message(out, {"jsonrpc": "2.0", "id": msg["id"],
                                 "result": completion_items(text)})
        elif method == "shutdown":
            write_message(out, {"jsonrpc": "2.0", "id": msg["id"], "result": None})
        elif method == "exit":
            break
        # notifications with no "id" and unhandled methods are silently ignored

if __name__ == "__main__":
    main()
