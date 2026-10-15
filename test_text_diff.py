"""Tests for text_diff.py: golden output, properties, and the CLI."""

import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from text_diff import MARKER, diff_terms, format_unified, split_terms, unified_diff

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@$")
SCRIPT = Path(__file__).resolve().with_name("text_diff.py")


def display_term(term):
    """A term as it appears in a unified-diff body line (newline stripped)."""
    return term[:-1] if term.endswith("\n") else term


def min_edits(a, b):
    """Independent DP: minimum number of insert/delete operations a -> b."""
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1]
            else:
                cur[j] = 1 + min(prev[j], cur[j - 1])
        prev = cur
    return prev[m]


def blocks(script):
    """Index ranges of maximal runs of non-context entries."""
    out = []
    k = 0
    while k < len(script):
        if script[k][0] == " ":
            k += 1
            continue
        start = k
        while k < len(script) and script[k][0] != " ":
            k += 1
        out.append((start, k))
    return out


def check_unified(case, text, terms_a, terms_b, fromfile="a", tofile="b"):
    """Validate a unified diff string against the terms it was generated from."""
    case.assertTrue(text.endswith("\n"), "output must end with a newline")
    lines = text.split("\n")[:-1]
    case.assertGreaterEqual(len(lines), 2)
    case.assertEqual(lines[0], "--- " + fromfile)
    case.assertEqual(lines[1], "+++ " + tofile)

    i = 2
    old_ranges, new_ranges = [], []
    while i < len(lines):
        match = HUNK_RE.match(lines[i])
        case.assertIsNotNone(match, "expected a hunk header, got {!r}".format(lines[i]))
        old_start, old_count = int(match.group(1)), int(match.group(2) or 1)
        new_start, new_count = int(match.group(3)), int(match.group(4) or 1)
        i += 1

        oi = old_start - 1 if old_count else None
        ni = new_start - 1 if new_count else None
        lacked_newline = False
        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            if line == MARKER:
                case.assertTrue(lacked_newline, "stray 'no newline' marker")
                lacked_newline = False
                i += 1
                continue
            case.assertTrue(line, "empty body line")
            tag, body = line[0], line[1:]
            case.assertIn(tag, (" ", "-", "+"), "bad body line {!r}".format(line))
            if tag == " ":
                case.assertIsNotNone(oi, "context line in pure-insertion hunk")
                case.assertIsNotNone(ni, "context line in pure-deletion hunk")
                left, right = terms_a[oi], terms_b[ni]
                case.assertEqual(left, right, "context term differs between sides")
                case.assertEqual(display_term(left), body)
                oi += 1
                ni += 1
                lacked_newline = not left.endswith("\n")
            elif tag == "-":
                case.assertIsNotNone(oi)
                left = terms_a[oi]
                case.assertEqual(display_term(left), body)
                oi += 1
                lacked_newline = not left.endswith("\n")
            else:
                case.assertIsNotNone(ni)
                right = terms_b[ni]
                case.assertEqual(display_term(right), body)
                ni += 1
                lacked_newline = not right.endswith("\n")
            i += 1
        case.assertFalse(lacked_newline, "missing 'no newline' marker")
        if old_count:
            case.assertEqual(oi, old_start - 1 + old_count, "old side count mismatch")
            old_ranges.append((old_start, old_count))
        else:
            case.assertIsNone(oi)
        if new_count:
            case.assertEqual(ni, new_start - 1 + new_count, "new side count mismatch")
            new_ranges.append((new_start, new_count))
        else:
            case.assertIsNone(ni)
    for ranges in (old_ranges, new_ranges):
        for (s1, c1), (s2, _c2) in zip(ranges, ranges[1:]):
            case.assertGreaterEqual(s2, s1 + c1, "overlapping hunks")


class SplitTermsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(split_terms(""), [])

    def test_single_line(self):
        self.assertEqual(split_terms("abc"), ["abc"])
        self.assertEqual(split_terms("abc\n"), ["abc\n"])

    def test_trailing_blank_line(self):
        self.assertEqual(split_terms("a\n\n"), ["a\n", "\n"])

    def test_blank_text(self):
        self.assertEqual(split_terms("\n"), ["\n"])

    def test_roundtrip(self):
        for text in ["", "a", "a\n", "a\nb", "a\nb\n", "\n", "\n\n", "a\n\nb\n"]:
            self.assertEqual("".join(split_terms(text)), text)


class DiffTermsTests(unittest.TestCase):
    def test_identical_is_all_context(self):
        script = diff_terms(split_terms("a\nb\n"), split_terms("a\nb\n"))
        self.assertEqual(script, [(" ", "a\n"), (" ", "b\n")])

    def test_simple_change_block(self):
        script = diff_terms(split_terms("a\nb\nc\n"), split_terms("a\nx\nc\n"))
        self.assertEqual(script, [(" ", "a\n"), ("-", "b\n"), ("+", "x\n"), (" ", "c\n")])

    def test_missing_final_newline_is_a_change(self):
        self.assertTrue(any(tag != " " for tag, _ in
                            diff_terms(split_terms("abc"), split_terms("abc\n"))))

    def test_empty_inputs(self):
        self.assertEqual(diff_terms([], []), [])
        self.assertEqual(diff_terms([], ["x\n"]), [("+", "x\n")])
        self.assertEqual(diff_terms(["x\n"], []), [("-", "x\n")])

    def test_multiple_blocks(self):
        script = diff_terms(split_terms("a\nb\nc\nd\n"), split_terms("A\nb\nc\nD\n"))
        self.assertEqual(script, [
            ("-", "a\n"), ("+", "A\n"),
            (" ", "b\n"), (" ", "c\n"),
            ("-", "d\n"), ("+", "D\n"),
        ])


class UnifiedFormatTests(unittest.TestCase):
    def test_identical_returns_empty(self):
        self.assertEqual(unified_diff("a\nb\n", "a\nb\n"), "")
        self.assertEqual(unified_diff("", ""), "")
        self.assertEqual(unified_diff("abc", "abc"), "")

    def test_empty_script_returns_empty(self):
        self.assertEqual(format_unified([]), "")

    def test_basic_change_block(self):
        out = unified_diff("one\ntwo\nthree\nfour\nfive\n",
                           "one\nTWO\nthree\nfour\nfive\n")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,5 +1,5 @@\n"
            " one\n"
            "-two\n"
            "+TWO\n"
            " three\n"
            " four\n"
            " five\n"
        ))

    def test_insert_at_end(self):
        out = unified_diff("a\nb\n", "a\nb\nc\n")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,2 +1,3 @@\n"
            " a\n"
            " b\n"
            "+c\n"
        ))

    def test_delete_at_end(self):
        out = unified_diff("a\nb\nc\n", "a\nb\n")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,3 +1,2 @@\n"
            " a\n"
            " b\n"
            "-c\n"
        ))

    def test_insert_at_start_without_context(self):
        out = unified_diff("j\nk\n", "i\nj\nk\n", n=0)
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -0,0 +1 @@\n"
            "+i\n"
        ))

    def test_new_file_layout(self):
        out = unified_diff("", "x\ny\n")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -0,0 +1,2 @@\n"
            "+x\n"
            "+y\n"
        ))

    def test_emptied_file_layout(self):
        out = unified_diff("x\ny\n", "")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,2 +0,0 @@\n"
            "-x\n"
            "-y\n"
        ))

    def test_delete_in_middle_without_context(self):
        out = unified_diff("x\ny\n", "x\n", n=0)
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -2 +1,0 @@\n"
            "-y\n"
        ))

    def test_hunks_merge_when_context_windows_touch(self):
        lines_a = "a\nb\nc\nd\ne\nf\ng\nh\ni\n"
        lines_b = "a\nB\nc\nd\nE\nf\ng\nh\ni\n"
        out = unified_diff(lines_a, lines_b, n=1)
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,6 +1,6 @@\n"
            " a\n"
            "-b\n"
            "+B\n"
            " c\n"
            " d\n"
            "-e\n"
            "+E\n"
            " f\n"
        ))

    def test_hunks_split_when_gap_exceeds_context(self):
        lines_a = "a\nb\nc\nd\ne\nf\ng\nh\ni\n"
        lines_b = "a\nB\nc\nd\ne\nF\ng\nh\ni\n"
        out = unified_diff(lines_a, lines_b, n=1)
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,3 +1,3 @@\n"
            " a\n"
            "-b\n"
            "+B\n"
            " c\n"
            "@@ -5,3 +5,3 @@\n"
            " e\n"
            "-f\n"
            "+F\n"
            " g\n"
        ))

    def test_custom_labels(self):
        out = unified_diff("a\n", "b\n", fromfile="old.txt", tofile="new.txt")
        self.assertTrue(out.startswith("--- old.txt\n+++ new.txt\n"))

    def test_missing_final_newline_both_sides(self):
        out = unified_diff("abc", "abd")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1 +1 @@\n"
            "-abc\n" + MARKER + "\n"
            "+abd\n" + MARKER + "\n"
        ))

    def test_final_newline_added(self):
        out = unified_diff("abc", "abc\n")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1 +1 @@\n"
            "-abc\n" + MARKER + "\n"
            "+abc\n"
        ))

    def test_final_newline_removed(self):
        out = unified_diff("abc\n", "abc")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1 +1 @@\n"
            "-abc\n"
            "+abc\n" + MARKER + "\n"
        ))

    def test_marker_after_context_line_at_eof(self):
        out = unified_diff("x\nsame", "y\nsame")
        self.assertEqual(out, (
            "--- a\n"
            "+++ b\n"
            "@@ -1,2 +1,2 @@\n"
            "-x\n"
            "+y\n"
            " same\n" + MARKER + "\n"
        ))

    def test_no_marker_when_last_line_not_shown(self):
        lines_a = "1\n2\n3\n4\n5\n6\n7\n8\n9\nlast"
        lines_b = "one\n2\n3\n4\n5\n6\n7\n8\n9\nlast"
        out = unified_diff(lines_a, lines_b)
        self.assertIn("+one\n", out)
        self.assertNotIn(MARKER, out)


class SweepTests(unittest.TestCase):
    def test_random_pairs(self):
        rng = random.Random(20261015)
        alphabet = ["alpha\n", "beta\n", "gamma\n", "delta\n", "eps\n"]
        seen = {name: 0 for name in (
            "empty", "insert_only", "delete_only", "replace",
            "context", "marker", "merged", "split",
        )}
        for _ in range(600):
            a = [rng.choice(alphabet) for _ in range(rng.randrange(0, 9))]
            b = [rng.choice(alphabet) for _ in range(rng.randrange(0, 9))]
            if a and rng.random() < 0.25:
                a = a[:-1] + [a[-1][:-1]]  # strip final newline
            if b and rng.random() < 0.25:
                b = b[:-1] + [b[-1][:-1]]
            text_a, text_b = "".join(a), "".join(b)

            script = diff_terms(split_terms(text_a), split_terms(text_b))

            # minimal edit count, checked against an independent DP
            self.assertEqual(sum(1 for tag, _ in script if tag != " "), min_edits(a, b))
            # reconstruction round-trip
            self.assertEqual([t for tag, t in script if tag in (" ", "-")], a)
            self.assertEqual([t for tag, t in script if tag in (" ", "+")], b)
            # removals before additions inside every change block
            for start, end in blocks(script):
                tags = [tag for tag, _ in script[start:end]]
                self.assertEqual(sorted(tags, key=lambda t: {"-": 0, "+": 1}[t]), tags)
            # determinism
            self.assertEqual(script, diff_terms(split_terms(text_a), split_terms(text_b)))

            n = rng.choice([0, 1, 2, 3, 5])
            out = format_unified(script, n=n)
            if not any(tag != " " for tag, _ in script):
                self.assertEqual(a, b)
                self.assertEqual(out, "")
                seen["empty"] += 1
                continue

            case = self
            check_unified(case, out, a, b)

            for start, end in blocks(script):
                tags = {tag for tag, _ in script[start:end]}
                if tags == {"+"}:
                    seen["insert_only"] += 1
                elif tags == {"-"}:
                    seen["delete_only"] += 1
                else:
                    seen["replace"] += 1
            if any(tag == " " for tag, _ in script):
                seen["context"] += 1
            if MARKER in out:
                seen["marker"] += 1
            hunks = sum(1 for line in out.split("\n") if line.startswith("@@ "))
            if hunks > 1:
                seen["split"] += 1
            if hunks < len(blocks(script)):
                seen["merged"] += 1

        for name, count in seen.items():
            self.assertGreater(count, 0, "sweep never exercised: " + name)

    def test_larger_files(self):
        rng = random.Random(7)
        base = ["line {}\n".format(i) for i in range(600)]
        changed = list(base)
        for idx in rng.sample(range(600), 35):
            changed[idx] = "changed {}\n".format(idx)
        text_a, text_b = "".join(base), "".join(changed)
        out = unified_diff(text_a, text_b, fromfile="old", tofile="new")
        self.assertIn("@@ ", out)
        check_unified(self, out, split_terms(text_a), split_terms(text_b),
                      fromfile="old", tofile="new")

    def test_deterministic_output(self):
        a = "a\nb\nc\nd\ne\n"
        b = "a\nx\nc\ny\ne\n"
        self.assertEqual(unified_diff(a, b), unified_diff(a, b))


class CliTests(unittest.TestCase):
    def run_cli(self, args, stdin=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT)] + args,
            input=stdin, capture_output=True, text=True, encoding="utf-8",
        )

    def write(self, directory, name, content):
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8", newline="")
        return str(path)

    def test_identical_exit_0(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = self.write(d, "a.txt", "same\n")
            p2 = self.write(d, "b.txt", "same\n")
            res = self.run_cli([p1, p2])
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout, "")

    def test_different_exit_1_and_format(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = self.write(d, "a.txt", "one\ntwo\n")
            p2 = self.write(d, "b.txt", "one\nTWO\n")
            res = self.run_cli([p1, p2])
            self.assertEqual(res.returncode, 1)
            self.assertEqual(res.stdout,
                             unified_diff("one\ntwo\n", "one\nTWO\n", fromfile=p1, tofile=p2))

    def test_context_flag(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = self.write(d, "a.txt", "x\ny\nz\n")
            p2 = self.write(d, "b.txt", "x\nq\nz\n")
            res = self.run_cli([p1, p2, "-U", "0"])
            self.assertEqual(res.returncode, 1)
            self.assertEqual(res.stdout,
                             unified_diff("x\ny\nz\n", "x\nq\nz\n", n=0, fromfile=p1, tofile=p2))

    def test_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = self.write(d, "a.txt", "a\n")
            p2 = self.write(d, "b.txt", "b\n")
            res = self.run_cli([p1, p2, "-q"])
            self.assertEqual(res.returncode, 1)
            self.assertEqual(res.stdout, "")

    def test_color_always(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = self.write(d, "a.txt", "a\n")
            p2 = self.write(d, "b.txt", "b\n")
            res = self.run_cli([p1, p2, "--color", "always"])
            self.assertEqual(res.returncode, 1)
            self.assertIn("\x1b[31m", res.stdout)
            self.assertIn("\x1b[32m", res.stdout)

    def test_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            p2 = self.write(d, "b.txt", "new\n")
            res = self.run_cli(["-", p2], stdin="old\n")
            self.assertEqual(res.returncode, 1)
            self.assertTrue(res.stdout.startswith("--- -\n+++ "))

    def test_missing_file_exit_2(self):
        res = self.run_cli(["no-such-file.txt", "also-missing.txt"])
        self.assertEqual(res.returncode, 2)
        self.assertIn("cannot read", res.stderr)

    def test_double_stdin_rejected(self):
        res = self.run_cli(["-", "-"], stdin="")
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
