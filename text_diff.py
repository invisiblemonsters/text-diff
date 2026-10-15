#!/usr/bin/env python3
"""text-diff -- a tiny, dependency-free unified diff for text.

The output mirrors the classic ``diff -u`` format (minus file timestamps)::

    --- old.txt
    +++ new.txt
    @@ -1,3 +1,3 @@
     keep
    -before
    +after
     keep

Exit status follows ``diff``: 0 = identical, 1 = different, 2 = trouble.

Library API:
    split_terms(text)                -> lines with their newline attached
    diff_terms(terms_a, terms_b)     -> edit script [(" ", line), ("-", line), ("+", line)]
    format_unified(script, n, ...)   -> unified diff text ("" when nothing changed)
    unified_diff(text_a, text_b, n)  -> convenience wrapper

Standard library only.
"""

from __future__ import annotations

import argparse
import sys

__version__ = "1.0.0"

MARKER = "\\ No newline at end of file"

_ANSI = {
    "header": "\x1b[1m",
    "hunk": "\x1b[36m",
    "delete": "\x1b[31m",
    "insert": "\x1b[32m",
}
_RESET = "\x1b[0m"


def split_terms(text: str) -> list[str]:
    """Split *text* into terms: one per line, trailing newline kept attached.

    The newline travels with each line, so a file ending in "abc" compares
    differently from one ending in "abc\\n".  Empty text -> [].
    """
    if text == "":
        return []
    parts = text.split("\n")
    if parts[-1] == "":
        parts.pop()
        return [part + "\n" for part in parts]
    return [part + "\n" for part in parts[:-1]] + [parts[-1]]


def diff_terms(terms_a: list[str], terms_b: list[str]) -> list[tuple[str, str]]:
    """Return an edit script turning *terms_a* into *terms_b*.

    Each entry is (tag, term) with tag " " (context), "-" (removed) or
    "+" (added).  The script uses a longest-common-subsequence base, so it
    is minimal, and it is deterministic; inside a run of edits, removals
    come before additions.
    """
    n, m = len(terms_a), len(terms_b)

    # dp[i][j] = length of the LCS of terms_a[:i] and terms_b[:j]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, prev = dp[i], dp[i - 1]
        term_a = terms_a[i - 1]
        for j in range(1, m + 1):
            if term_a == terms_b[j - 1]:
                row[j] = prev[j - 1] + 1
            else:
                row[j] = prev[j] if prev[j] >= row[j - 1] else row[j - 1]

    ops: list[tuple[str, str]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and terms_a[i - 1] == terms_b[j - 1]:
            ops.append((" ", terms_a[i - 1]))
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or dp[i - 1][j] >= dp[i][j - 1]):
            ops.append(("-", terms_a[i - 1]))
            i -= 1
        else:
            ops.append(("+", terms_b[j - 1]))
            j -= 1
    ops.reverse()

    # Normalise: inside each change block, removals go before additions.
    script: list[tuple[str, str]] = []
    k = 0
    while k < len(ops):
        if ops[k][0] == " ":
            script.append(ops[k])
            k += 1
            continue
        removed, added = [], []
        while k < len(ops) and ops[k][0] != " ":
            (removed if ops[k][0] == "-" else added).append(ops[k])
            k += 1
        script.extend(removed)
        script.extend(added)
    return script


def _fmt_range(start: int, count: int) -> str:
    """Hunk-header range field; ',1' is omitted like diff does."""
    return str(start) if count == 1 else "{},{}".format(start, count)


def format_unified(script, n: int = 3, fromfile: str = "a", tofile: str = "b") -> str:
    """Render an edit script as unified diff text.

    Returns "" when the script contains no changes.
    """
    if n < 0:
        raise ValueError("context lines must be >= 0")
    total = len(script)

    # Maximal runs of non-context entries are the change blocks.
    blocks = []
    k = 0
    while k < total:
        if script[k][0] == " ":
            k += 1
            continue
        start = k
        while k < total and script[k][0] != " ":
            k += 1
        blocks.append((start, k))
    if not blocks:
        return ""

    # Merge blocks whose context windows touch.
    hunks = []
    for start, end in blocks:
        if hunks and start - hunks[-1][1] <= 2 * n:
            hunks[-1][1] = end
        else:
            hunks.append([start, end])

    # How many old / new side lines sit before each entry.
    old_before = [0] * (total + 1)
    new_before = [0] * (total + 1)
    for t, (tag, _line) in enumerate(script):
        old_before[t + 1] = old_before[t] + (tag != "+")
        new_before[t + 1] = new_before[t] + (tag != "-")

    lines = ["--- {}".format(fromfile), "+++ {}".format(tofile)]
    for start, end in hunks:
        lo = max(0, start - n)
        hi = min(total, end + n)
        old_count = old_before[hi] - old_before[lo]
        new_count = new_before[hi] - new_before[lo]

        old_start = new_start = None
        for t in range(lo, hi):
            tag = script[t][0]
            if old_start is None and tag != "+":
                old_start = old_before[t] + 1
            if new_start is None and tag != "-":
                new_start = new_before[t] + 1
            if old_start is not None and new_start is not None:
                break
        if old_start is None:  # pure insertion: point just before the new lines
            old_start = old_before[lo]
        if new_start is None:  # pure deletion
            new_start = new_before[lo]

        lines.append("@@ -{} +{} @@".format(
            _fmt_range(old_start, old_count), _fmt_range(new_start, new_count)))

        for t in range(lo, hi):
            tag, line = script[t]
            body = line[:-1] if line.endswith("\n") else line
            lines.append(tag + body)
            if not line.endswith("\n"):
                lines.append(MARKER)
    return "".join(line + "\n" for line in lines)


def unified_diff(text_a: str, text_b: str, n: int = 3,
                 fromfile: str = "a", tofile: str = "b") -> str:
    """Return the unified diff of two texts, or "" when they are identical."""
    return format_unified(
        diff_terms(split_terms(text_a), split_terms(text_b)),
        n=n, fromfile=fromfile, tofile=tofile,
    )


def _colorize(line: str) -> str:
    if line.startswith("---") or line.startswith("+++"):
        return _ANSI["header"] + line + _RESET
    if line.startswith("@@"):
        return _ANSI["hunk"] + line + _RESET
    if line.startswith("-"):
        return _ANSI["delete"] + line + _RESET
    if line.startswith("+"):
        return _ANSI["insert"] + line + _RESET
    return line


def _read_source(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as handle:
        return handle.read()


def _stdout_isatty() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="text-diff",
        description="Show a unified diff between two text files (diff -u style, no timestamps).",
        epilog="Exit status: 0 identical, 1 different, 2 error.",
    )
    parser.add_argument("old", metavar="OLD", help="original file, or - to read stdin")
    parser.add_argument("new", metavar="NEW", help="new file, or - to read stdin")
    parser.add_argument("-U", "--context", type=int, default=3, metavar="N",
                        help="lines of context around changes (default: 3)")
    parser.add_argument("-u", action="store_true",
                        help="accepted for diff compatibility (output is always unified)")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="colorize the output (default: auto)")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="print nothing; the exit status tells whether they differ")
    args = parser.parse_args(argv)

    if args.context < 0:
        parser.error("argument -U/--context: must be >= 0")
    if args.old == "-" and args.new == "-":
        parser.error("only one of OLD and NEW may be read from stdin")

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="surrogateescape")
            except (ValueError, OSError):
                pass

    try:
        old_text = _read_source(args.old)
        new_text = _read_source(args.new)
    except OSError as exc:
        name = exc.filename or "input"
        print("text-diff: cannot read {}: {}".format(name, exc.strerror), file=sys.stderr)
        return 2

    script = diff_terms(split_terms(old_text), split_terms(new_text))
    if not any(tag != " " for tag, _ in script):
        return 0
    if args.quiet:
        return 1

    output = format_unified(script, n=args.context, fromfile=args.old, tofile=args.new)
    if args.color == "always" or (args.color == "auto" and _stdout_isatty()):
        output = "".join(_colorize(line) + "\n" for line in output.split("\n")[:-1])
    sys.stdout.write(output)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
