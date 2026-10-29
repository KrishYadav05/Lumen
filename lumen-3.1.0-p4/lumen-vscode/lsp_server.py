#!/usr/bin/env python3
"""Minimal Lumen Language Server: runs lumen.py's own front end (`analyze`: parser, the Result/Option
prelude, name resolution and the type checker) and speaks just enough LSP (JSON-RPC over stdio) to
publish diagnostics on open/change, and completion. Not a full LSP implementation — no hover yet.

Publisher: Krish Kumar

Language Specification 3.1: a diagnostic carries the compiler's own code (E0201, ...), the exact
line and column range of the offending code, and its `expected`/`found` notes and `help` — the same
fields `lumen check` prints — and a warning (W0301) is shown as a warning, not an error.
"""
import sys, os, json, re
from urllib.parse import urlparse, unquote

sys.path.insert(0, os.path.dirname(__file__))
from lumen import analyze, LumenError  # noqa: E402

LINE_RE = re.compile(r"line (\d+)")


def _uri_path(uri):
    """The file a `file://` URI names, or None (an untitled buffer)."""
    u = urlparse(uri or "")
    return unquote(u.path) if u.scheme == "file" and u.path else None


def _range(line, col, end_col):
    """An LSP range for a 1-based line and 1-based, end-exclusive columns; the whole line when the
    compiler gave no columns (a syntax error knows only its line)."""
    line0 = max((line or 1) - 1, 0)
    if col:
        return {"start": {"line": line0, "character": col - 1},
                "end": {"line": line0, "character": max((end_col or col + 1) - 1, col)}}
    return {"start": {"line": line0, "character": 0}, "end": {"line": line0, "character": 999}}


def _message(e):
    """The compiler's message plus what `lumen check` prints under it: the notes and the help."""
    text = getattr(e, "text", None) or str(e)
    lines = [text]
    lines += list(getattr(e, "notes", None) or [])
    if getattr(e, "help", None):
        lines.append("help: " + e.help)
    return "\n".join(lines)


def _from_error(e, severity):
    if isinstance(e, LumenError):
        line = getattr(e, "line", None)
        if not line:
            m = LINE_RE.search(str(e))
            line = int(m.group(1)) if m else 1
        d = {"range": _range(line, getattr(e, "col", None), getattr(e, "end_col", None)),
             "severity": severity, "source": "lumen", "message": _message(e)}
        if getattr(e, "code", None):
            d["code"] = e.code
        return d
    m = LINE_RE.search(str(e))          # a SyntaxError: its message names the line
    return {"range": _range(int(m.group(1)) if m else 1, None, None),
            "severity": severity, "source": "lumen", "message": str(e)}


def check_text(text, uri=None):
    """Returns a list of LSP Diagnostic dicts: the first error (the checker stops there), then the
    warnings found before it. `uri` lets imports (`use "x.lum" as x`) be checked against the right folder."""
    path = _uri_path(uri)
    warnings = []
    try:
        analyze(text, os.path.basename(path) if path else "<editor>",
                os.path.dirname(path) if path else None, warnings=warnings)
        return [_from_error(w, 2) for w in warnings]          # 2 = Warning
    except (SyntaxError, LumenError) as e:
        return [_from_error(e, 1)] + [_from_error(w, 2) for w in warnings]   # 1 = Error
    except Exception as e:
        return [{
            "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 999}},
            "severity": 1, "source": "lumen", "message": f"Internal error: {e}",
        }]

KEYWORDS = ["let","print","if","else","for","in","while","fn","return","true","false",
            "try","catch","finally","struct","and","or","not","use","as","enum","match","spawn","test"]
# Must stay equal to lumen.BUILTIN_FUNCTION_NAMES (test_lsp.py checks this):
# it had silently drifted — `range`, `now` and `read_line` were missing — before
# that test existed.
BUILTINS = ["len","push","keys","values","read_file","write_file","http_get",
            "fail","fail_with","ok_or","unwrap","unwrap_or","is_ok","is_err","is_some","is_none",
            "wait","chan","send","receive","assert","assert_eq","assert_ne",
            "now","read_line","range",
            "chr","ord","type_of",
            "file_exists","append_file","list_dir","delete_file","is_dir"]
TYPES = ["Int","Float","Str","Bool","List","Dict","Any","Result","Option"]
VARIANTS = ["Ok","Err","Some","None"]       # the library enums' variants, always in scope (3.1)
SYMBOL_RE = re.compile(r"\b(?:fn|let|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")

def extract_symbols(text):
    return sorted(set(SYMBOL_RE.findall(text)))

def completion_items(text):
    items = []
    for kw in KEYWORDS: items.append({"label": kw, "kind": 14})   # Keyword
    for b in BUILTINS: items.append({"label": b, "kind": 3})       # Function
    for t in TYPES: items.append({"label": t, "kind": 7})          # Class/Type
    for v in VARIANTS: items.append({"label": v, "kind": 20})      # EnumMember
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
            publish_diagnostics(out, uri, check_text(text, uri))
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
