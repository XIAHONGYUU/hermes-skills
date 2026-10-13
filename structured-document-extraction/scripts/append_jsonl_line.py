#!/usr/bin/env python3
"""Append exactly one JSON line to a .jsonl file, staging-file style, with verification.

Usage:
    python3 append_jsonl_line.py <staging_file> <target.jsonl>

Context: incremental JSONL extraction (see SKILL.md Step 3b). For batches with many
cases, do NOT compose the whole file in one response -- emit one case per turn.

Workflow this script supports:
  1. `write_file` the case as JSON into a staging file UNDER THE WORKSPACE ROOT
     (e.g. /opt/data/.tmp_batch_caseN.line). NOT /tmp -- write_file is sandboxed to
     HERMES_WRITE_SAFE_ROOT and refuses paths outside it.
  2. Run this script: it parses the staging file (single-line OR pretty JSON),
     re-serializes to one compact line, appends it to the target, prints the new
     line count, and deletes the staging file.

Re-serializing with json.dumps guarantees one object per line with no embedded raw
newlines, so the JSONL stays valid even if the staging file was hand-formatted.
Exits non-zero (leaving the staging file in place) if the staging content is not
valid JSON, so a bad case never contaminates the dataset.
"""
import json
import os
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    if os.path.abspath(src).startswith("/tmp"):
        print(f"WARNING: {src} is under /tmp; write_file cannot create it "
              f"(HERMES_WRITE_SAFE_ROOT). Stage under the workspace root instead.",
              file=sys.stderr)
    try:
        obj = json.loads(open(src, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not parse staging file {src}: {exc}", file=sys.stderr)
        return 1

    line = json.dumps(obj, ensure_ascii=False)
    with open(dst, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    with open(dst, encoding="utf-8") as fh:
        n = sum(1 for _ in fh)
    print(f"OK: appended 1 line -> {dst} now has {n} line(s)")
    os.remove(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
