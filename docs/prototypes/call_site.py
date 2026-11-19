import ast, hashlib, json, os, sys

RECORD = os.environ.get("XP_OUT", "observations.jsonl")

def _call_site(frame):
    code = frame.f_code
    pos = list(code.co_positions())[frame.f_lasti // 2]
    return code.co_filename, pos, code.co_qualname

def _find_call(tree, pos):
    l, el, c, ec = pos
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and (n.lineno, n.end_lineno, n.col_offset, n.end_col_offset) == (l, el, c, ec):
            return n
    return None

def expect(actual, *args):
    fn, pos, qual = _call_site(sys._getframe(1))
    src = open(fn, encoding="utf-8").read()
    call = _find_call(ast.parse(src), pos)
    seg = ast.get_source_segment(src, call)
    status = "missing" if not args else ("ok" if args[0] == actual else "mismatch")
    with open(RECORD, "a") as f:
        f.write(json.dumps({"file": fn, "pos": pos, "qual": qual, "status": status,
            "segment_sha": hashlib.sha256(seg.encode()).hexdigest()[:12], "actual": repr(actual)}) + "\n")
    if status == "mismatch":
        raise AssertionError(f"{actual!r} != {args[0]!r}")

def splice(path, pos, new_arg_src):
    src = open(path, encoding="utf-8").read()
    call = _find_call(ast.parse(src), pos)
    lines = src.splitlines(keepends=True)
    def off(line, col):  # ast cols are UTF-8 byte offsets
        return sum(len(x) for x in lines[:line-1]) + len(lines[line-1].encode()[:col].decode())
    if call.args[1:]:
        a = call.args[1]; s, e = off(a.lineno, a.col_offset), off(a.end_lineno, a.end_col_offset)
        new = src[:s] + new_arg_src + src[e:]
    else:
        a = call.args[0]; e = off(a.end_lineno, a.end_col_offset)
        new = src[:e] + ", " + new_arg_src + src[e:]
    compile(new, path, "exec")
    return new
