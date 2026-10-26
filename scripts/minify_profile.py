#!/usr/bin/env python3
"""Minify a stream profile into the one-line form pasted into Dispatcharr.

Usage: minify_profile.py SOURCE.py

Prints the full Dispatcharr Parameters value:
    -u -c '<minified code>' {streamUrl} {userAgent} {channelId}

The leading comment block of SOURCE (the settings help) is kept as is. Everything
else is re-emitted from the parsed program with one-space indentation, comments and
blank lines dropped, and consecutive simple statements joined with ";".

Safety checks (any failure exits non-zero):
  - the minified code parses to exactly the same program (AST) as SOURCE
  - the minified code contains no single quote (it sits inside a single-quoted
    shell argument that Dispatcharr splits with shlex)
"""
import ast
import io
import json
import sys
import tokenize

COMPOUND = ("if ", "elif ", "else:", "for ", "while ", "try:", "except", "finally:",
            "with ", "def ", "class ", "async ", "@")


def header(src):
    out = []
    for line in src.splitlines():
        if not line.startswith("#"):
            break
        out.append(line)
    return out


def bytes_literal(b):
    out = []
    for c in b:
        ch = chr(c)
        if ch in "\"\\":
            out.append("\\" + ch)
        elif 32 <= c < 127:
            out.append(ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append("\\x%02x" % c)
    return "b\"" + "".join(out) + "\""


def double_quote_strings(src):
    """Rewrite every string literal that uses single quotes as a double-quoted one."""
    toks = []
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.STRING and "'" in t.string:
            v = ast.literal_eval(t.string)
            s = bytes_literal(v) if isinstance(v, bytes) else json.dumps(v, ensure_ascii=False)
            t = t._replace(string=s)
        toks.append(t)
    return tokenize.untokenize(toks)


def logical_lines(src):
    """Yield (indent_level, text) for each logical line of 4-space-indented source."""
    lines = src.splitlines()
    i = 0
    starts = {}
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.NEWLINE:
            starts.setdefault(t.start[0], True)
    # group physical lines into logical lines using NEWLINE token positions
    ends = sorted(starts)
    prev = 0
    for end in ends:
        chunk = lines[prev:end]
        prev = end
        while chunk and not chunk[0].strip():
            chunk = chunk[1:]
        if not chunk:
            continue
        first = chunk[0]
        ind = (len(first) - len(first.lstrip(" "))) // 4
        text = "\n".join([first.strip()] + chunk[1:])
        yield ind, text


def minify(code):
    src = double_quote_strings(ast.unparse(ast.parse(code)))
    items = list(logical_lines(src))
    out = []  # list of [indent, text, is_simple]
    for ind, text in items:
        simple = not text.startswith(COMPOUND)
        if out and simple and out[-1][2] and out[-1][0] == ind:
            out[-1][1] += ";" + text
            continue
        out.append([ind, text, simple])
    # put a block that is a single run of simple statements on its header line
    res = []
    k = 0
    while k < len(out):
        ind, text, simple = out[k]
        if (not simple and text.endswith(":") and k + 1 < len(out)
                and out[k + 1][2] and out[k + 1][0] == ind + 1
                and (k + 2 >= len(out) or out[k + 2][0] <= ind)):
            res.append(" " * ind + text + out[k + 1][1])
            k += 2
            continue
        res.append(" " * ind + text)
        k += 1
    return "\n".join(res)


def main():
    path = sys.argv[1]
    code = open(path, encoding="utf-8").read()
    mini = "\n".join(header(code) + [minify(code)])
    if ast.dump(ast.parse(mini)) != ast.dump(ast.parse(code)):
        sys.exit("%s: minified code is not the same program as the source" % path)
    if "'" in mini:
        sys.exit("%s: minified code contains a single quote" % path)
    sys.stdout.write("-u -c '" + mini + "' {streamUrl} {userAgent} {channelId}\n")


if __name__ == "__main__":
    main()
