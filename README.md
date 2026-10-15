# text-diff

A tiny, dependency-free unified diff for text files — pure Python, standard library only.

It prints classic `diff -u` style output (minus file timestamps): `---`/`+++` headers, `@@` hunk headers, context lines, `-`/`+` changes, and `\ No newline at end of file` markers.

## Usage

```
python text_diff.py OLD NEW
python text_diff.py OLD NEW -U 0            # no context lines
python text_diff.py OLD NEW --color always
cat old.txt | python text_diff.py - new.txt
```

Exit status follows `diff`: 0 = identical, 1 = different, 2 = error.

Example:

```
$ python text_diff.py old.txt new.txt
--- old.txt
+++ new.txt
@@ -1,4 +1,4 @@
 keep
-before
+after
 keep
```

## How it works

- Each file is split into lines *with* their trailing newline attached, so a missing final newline counts as a real difference.
- The edit script comes from a classic longest-common-subsequence dynamic program; inside a change block, removals print before additions.
- Change blocks are grouped into hunks with N lines of context (default 3); neighbouring hunks merge when their context windows touch (gap ≤ 2N), just like `diff`.
- `-U 0` gives minimal, context-free hunks.
- Edits are minimal (longest-common-subsequence based); when several minimal scripts exist, one is picked deterministically — it can differ from GNU diff's tie-breaking while staying equally minimal.
- Handy as a library too: `from text_diff import unified_diff`.

## Tests

```
python -m unittest test_text_diff.py -v
```

The suite covers golden output cases, round-trip and minimality property tests over seeded random inputs, a hunk validator, and CLI tests for exit statuses, stdin, and flags.

## License

MIT
