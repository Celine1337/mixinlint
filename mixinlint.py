#!/usr/bin/env python3
"""mixinlint - static consistency linter for SpongePowered Mixin sources.

Scans Java files annotated with @Mixin and *.mixins.json configs, checking
handler/annotation consistency that otherwise only blows up at runtime:
CallbackInfo parameters, cancellable flags, @Redirect return types, @Overwrite
misuse, and mixin config integrity. Single file, standard library only.
"""

import argparse
import json
import os
import re
import sys

__version__ = "1.0.0"

ERROR = "error"
WARN = "warning"

INJECTORS = {
    "Inject", "Redirect", "ModifyArg", "ModifyArgs", "ModifyConstant",
    "ModifyVariable", "ModifyExpressionValue", "ModifyReceiver",
    "WrapOperation", "WrapWithCondition",
}
CI_TYPES = {"CallbackInfo", "CallbackInfoReturnable"}
MODIFIERS = {
    "public", "private", "protected", "static", "final", "abstract",
    "native", "synchronized", "default", "strictfp", "transient", "volatile",
}
COMPAT = {"JAVA_" + str(n) for n in range(6, 26)}
PRIMITIVES = {
    "V": "void", "Z": "boolean", "B": "byte", "C": "char", "S": "short",
    "I": "int", "J": "long", "F": "float", "D": "double",
}
WRAPPERS = {
    "Boolean": "boolean", "Byte": "byte", "Character": "char", "Short": "short",
    "Integer": "int", "Long": "long", "Float": "float", "Double": "double",
    "Void": "void",
}


class Issue:
    __slots__ = ("code", "severity", "file", "line", "message")

    def __init__(self, code, severity, file, line, message):
        self.code = code
        self.severity = severity
        self.file = file
        self.line = line
        self.message = message

    def as_dict(self):
        return {"code": self.code, "severity": self.severity, "file": self.file,
                "line": self.line, "message": self.message}


# --------------------------------------------------------------------------
# lightweight Java scanning
# --------------------------------------------------------------------------

def skip_string(s, i):
    q = s[i]
    j = i + 1
    n = len(s)
    while j < n:
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        j += 1
    return n


def strip_comments(src):
    out = list(src)
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = i
            while j < n and src[j] != "\n":
                out[j] = " "
                j += 1
            i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            out[i] = out[i + 1] = " "
            j = i + 2
            while j + 1 < n and not (src[j] == "*" and src[j + 1] == "/"):
                if src[j] != "\n":
                    out[j] = " "
                j += 1
            if j + 1 < n:
                out[j] = out[j + 1] = " "
                j += 2
            i = j
        elif c in "\"'":
            i = skip_string(src, i)
        else:
            i += 1
    return "".join(out)


def blank_strings(s):
    out = list(s)
    i = 0
    n = len(s)
    while i < n:
        if s[i] in "\"'":
            j = skip_string(s, i)
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def match_bracket(s, i):
    opener = s[i]
    closer = {"(": ")", "[": "]", "{": "}"}[opener]
    depth = 0
    j = i
    n = len(s)
    while j < n:
        c = s[j]
        if c in "\"'":
            j = skip_string(s, j)
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def split_members(body):
    members = []
    i = 0
    n = len(body)
    start = 0
    paren = bracket = 0
    while i < n:
        c = body[i]
        if c in "\"'":
            i = skip_string(body, i)
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == "{" and paren == 0 and bracket == 0:
            end = match_bracket(body, i)
            members.append((body[start:end], start))
            i = start = end
            continue
        elif c == ";" and paren == 0 and bracket == 0:
            members.append((body[start:i + 1], start))
            start = i + 1
        i += 1
    return members


def parse_annotations(member):
    anns = []
    i = 0
    n = len(member)
    while i < n and member[i].isspace():
        i += 1
    while i < n and member[i] == "@":
        m = re.match(r"@([\w.]+)", member[i:])
        if not m:
            break
        name = m.group(1).split(".")[-1]
        j = i + m.end()
        while j < n and member[j].isspace():
            j += 1
        args = ""
        if j < n and member[j] == "(":
            k = match_bracket(member, j)
            args = member[j + 1:k - 1]
            j = k
        anns.append((name, args, i))
        i = j
        while i < n and member[i].isspace():
            i += 1
    return anns, member[i:], i


def split_top(s, sep):
    out = []
    cur = []
    depth = 0
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            j = skip_string(s, i)
            cur.append(s[i:j])
            i = j
            continue
        if c in "([{<":
            depth += 1
        elif c in ")]}>":
            depth -= 1
        if c == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def first_top_paren(s):
    depth = 0
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            i = skip_string(s, i)
            continue
        if c in "[{<":
            depth += 1
        elif c in "]}>":
            depth -= 1
        elif c == "(":
            if depth == 0:
                return i
        i += 1
    return -1


def base_type(t):
    t = t.strip()
    t = re.sub(r"<.*>", "", t)
    t = t.replace("...", "").replace("[]", "").strip()
    return t.split(".")[-1]


def parse_params(s):
    params = []
    for part in split_top(s, ","):
        p = part.strip()
        if not p:
            continue
        p = re.sub(r"@[\w.]+(?:\([^()]*\))?\s*", "", p).strip()
        toks = p.split()
        if not toks:
            continue
        if len(toks) == 1:
            params.append((toks[0], ""))
        else:
            params.append((" ".join(toks[:-1]), toks[-1]))
    return params


def parse_method(sig):
    sig = sig.strip()
    pidx = first_top_paren(sig)
    if pidx < 0:
        return None
    pre = sig[:pidx].strip()
    close = match_bracket(sig, pidx)
    params = parse_params(sig[pidx + 1:close - 1])
    toks = pre.split()
    mods = set()
    while toks and toks[0] in MODIFIERS:
        mods.add(toks.pop(0))
    if not toks:
        return None
    name = toks[-1]
    ret = " ".join(toks[:-1])
    return {"name": name, "ret": ret, "params": params, "mods": mods}


def desc_return_java(desc):
    p = desc.rfind(")")
    if p < 0:
        return None
    r = desc[p + 1:].strip()
    if not r:
        return None
    return descriptor_type(r)


def descriptor_type(d):
    if not d:
        return None
    c = d[0]
    if c in PRIMITIVES:
        return PRIMITIVES[c]
    if c == "L":
        end = d.find(";")
        if end < 0:
            return None
        return d[1:end].split("/")[-1].split("$")[-1]
    if c == "[":
        inner = descriptor_type(d[1:])
        return inner + "[]" if inner else None
    return None


def norm_type(t):
    b = base_type(t)
    return WRAPPERS.get(b, b)


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def ann_args(anns, name):
    for n, args, _ in anns:
        if n == name:
            return args
    return None


def has_flag(args, flag):
    return bool(re.search(r"\b" + flag + r"\s*=\s*true", args or ""))


def check_member(member, off, code, path, issues, stats):
    anns, sig, sigoff = parse_annotations(member)
    if not anns:
        return
    names = {n for n, _, _ in anns}
    method = parse_method(sig)
    abs_base = off
    line = code.count("\n", 0, abs_base + sigoff) + 1

    def add(code_id, sev, msg, at=None):
        ln = code.count("\n", 0, abs_base + (at if at is not None else sigoff)) + 1
        issues.append(Issue(code_id, sev, path, ln, msg))

    is_injector = bool(names & INJECTORS)
    is_overwrite = "Overwrite" in names
    is_shadow = "Shadow" in names

    if is_injector:
        stats["handlers"] += 1

    if method is None:
        return

    params = method["params"]
    ci = [(t, n) for t, n in params if base_type(t) in CI_TYPES]
    body_m = re.search(r"\{(.*)\}\s*$", member.strip(), re.S)
    body = blank_strings(body_m.group(1)) if body_m else ""

    if is_injector:
        if "private" not in method["mods"]:
            add("M007", WARN, "injector handler '%s' should be private" % method["name"])
        if "Inject" in names and not ci:
            add("M001", ERROR,
                "@Inject handler '%s' has no CallbackInfo/CallbackInfoReturnable parameter"
                % method["name"])
        if ci:
            ci_type, ci_name = ci[0]
            ci_base = base_type(ci_type)
            cancels = bool(re.search(r"\b%s\s*\.\s*cancel\s*\(" % re.escape(ci_name), body))
            sets_ret = bool(re.search(r"\b%s\s*\.\s*setReturnValue\s*\(" % re.escape(ci_name), body))
            if sets_ret and ci_base == "CallbackInfo":
                add("M004", ERROR,
                    "'%s' calls setReturnValue on plain CallbackInfo (needs CallbackInfoReturnable)"
                    % method["name"])
            if "Inject" in names:
                iargs = ann_args(anns, "Inject") or ""
                cancellable = has_flag(iargs, "cancellable")
                if (cancels or sets_ret) and not cancellable:
                    add("M002", ERROR,
                        "'%s' cancels/sets return value but @Inject is missing cancellable = true"
                        % method["name"])
                if cancellable and not (cancels or sets_ret):
                    add("M003", WARN,
                        "@Inject on '%s' is cancellable but never cancels" % method["name"])
                if not re.search(r"\bmethod\s*=", iargs):
                    add("M009", ERROR, "@Inject on '%s' has no method target" % method["name"])
                if not re.search(r"\bat\s*=", iargs):
                    add("M010", ERROR, "@Inject on '%s' has no at = @At(...)" % method["name"])
        if "Redirect" in names:
            check_redirect(anns, method, add)

    if is_overwrite and ci:
        add("M006", ERROR,
            "@Overwrite method '%s' must not take a CallbackInfo parameter" % method["name"])

    if is_shadow and body.strip() and "abstract" not in method["mods"] and "native" not in method["mods"]:
        add("M008", WARN,
            "@Shadow method '%s' has a body; shadow methods should be abstract or native"
            % method["name"])


def check_redirect(anns, method, add):
    args = ann_args(anns, "Redirect") or ""
    at_m = re.search(r"target\s*=\s*\"([^\"]+)\"", args)
    if not at_m:
        return
    target = at_m.group(1)
    if "->" not in target or "(" not in target:
        return
    want = desc_return_java(target)
    if want is None:
        return
    got = method["ret"]
    if not got:
        return
    if norm_type(got) != norm_type(want):
        add("M005", ERROR,
            "@Redirect handler '%s' returns %s but target %s returns %s"
            % (method["name"], got, target.split("->")[-1], want))


def scan_java(path, text, issues, stats):
    code = strip_comments(text)
    if "@Mixin" not in code:
        return
    m = re.search(r"\b(?:class|interface|enum)\s+(\w+)[^{;]*\{", code)
    if not m:
        return
    stats["mixins"] += 1
    body_open = m.end() - 1
    body_close = match_bracket(code, body_open)
    body = code[body_open + 1:body_close - 1]
    for member, off in split_members(body):
        check_member(member, body_open + 1 + off, code, path, issues, stats)


def scan_config(path, data, java_names, issues, stats):
    stats["configs"] += 1

    def add(code_id, sev, msg, line=1):
        issues.append(Issue(code_id, sev, path, line, msg))

    if not isinstance(data, dict):
        add("C000", ERROR, "mixin config must be a JSON object")
        return
    if "package" not in data:
        add("C001", ERROR, "missing required 'package'")
    compat = data.get("compatibilityLevel")
    if compat is None:
        add("C002", ERROR, "missing required 'compatibilityLevel'")
    elif compat not in COMPAT:
        add("C006", ERROR, "invalid compatibilityLevel '%s'" % compat)
    lists = []
    for key in ("mixins", "client", "server"):
        val = data.get(key, [])
        if isinstance(val, list):
            lists.extend((key, v) for v in val)
    if not lists:
        add("C003", WARN, "no mixin classes listed (mixins/client/server all empty)")
    seen = {}
    for key, v in lists:
        if not isinstance(v, str):
            continue
        if v in seen:
            add("C005", WARN, "duplicate mixin entry '%s' (also in %s)" % (v, seen[v]))
        else:
            seen[v] = key
        simple = v.split(".")[-1]
        if java_names and simple not in java_names:
            add("C004", WARN, "listed mixin '%s' not found among scanned sources" % v)
    if "refmap" not in data:
        add("C007", WARN, "no 'refmap' set (needed for production remapping)")


# --------------------------------------------------------------------------
# scanning / reporting
# --------------------------------------------------------------------------

def collect(root):
    if os.path.isfile(root):
        return [root]
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "build", ".gradle", "node_modules")]
        for fn in sorted(filenames):
            if fn.endswith(".java") or fn.endswith(".mixins.json"):
                found.append(os.path.join(dirpath, fn))
    return found


def audit(root):
    files = collect(root)
    issues = []
    stats = {"files": 0, "mixins": 0, "handlers": 0, "configs": 0}
    java_names = set()
    parsed = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            issues.append(Issue("E000", ERROR, path, 1, "cannot read: %s" % exc))
            continue
        stats["files"] += 1
        rel = os.path.relpath(path, root) if os.path.isdir(root) else os.path.basename(path)
        if path.endswith(".mixins.json"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                issues.append(Issue("C000", ERROR, rel, exc.lineno, "invalid JSON: %s" % exc.msg))
                continue
            parsed.append((rel, data))
        else:
            before = stats["mixins"]
            scan_java(rel, text, issues, stats)
            if stats["mixins"] > before:
                m = re.search(r"\b(?:class|interface|enum)\s+(\w+)", strip_comments(text))
                if m:
                    java_names.add(m.group(1))
    for rel, data in parsed:
        scan_config(rel, data, java_names, issues, stats)
    return stats, issues


def colorize(text, color, enabled):
    if not enabled:
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "bold": "1", "dim": "2"}
    return "\x1b[%sm%s\x1b[0m" % (codes[color], text)


def report(stats, issues, color, as_json):
    errors = [i for i in issues if i.severity == ERROR]
    warns = [i for i in issues if i.severity == WARN]
    if as_json:
        return json.dumps({
            "summary": stats,
            "errors": len(errors),
            "warnings": len(warns),
            "issues": [i.as_dict() for i in issues],
        }, indent=2)
    lines = []
    lines.append(colorize("SUMMARY", "bold", color))
    lines.append("  files     %d" % stats["files"])
    lines.append("  mixins    %d" % stats["mixins"])
    lines.append("  handlers  %d" % stats["handlers"])
    lines.append("  configs   %d" % stats["configs"])
    if not issues:
        lines.append(colorize("clean: no issues found", "green", color))
        return "\n".join(lines)
    if errors:
        lines.append(colorize("ERRORS (%d)" % len(errors), "red", color))
        for i in errors:
            lines.append("  %s:%d: [%s] %s" % (i.file, i.line, i.code, i.message))
    if warns:
        lines.append(colorize("WARNINGS (%d)" % len(warns), "yellow", color))
        for i in warns:
            lines.append("  %s:%d: [%s] %s" % (i.file, i.line, i.code, i.message))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mixinlint", description="Static consistency linter for SpongePowered Mixin sources.")
    parser.add_argument("path", help="Java source directory, single .java file, or .mixins.json")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON report")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures too")
    parser.add_argument("--version", action="version", version="mixinlint " + __version__)
    opts = parser.parse_args(argv)

    if not os.path.exists(opts.path):
        sys.stderr.write("mixinlint: path not found: %s\n" % opts.path)
        return 2
    color = not opts.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    try:
        stats, issues = audit(opts.path)
    except OSError as exc:
        sys.stderr.write("mixinlint: %s\n" % exc)
        return 2
    print(report(stats, issues, color, opts.json))
    errors = sum(1 for i in issues if i.severity == ERROR)
    warns = sum(1 for i in issues if i.severity == WARN)
    if errors or (opts.strict and warns):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())