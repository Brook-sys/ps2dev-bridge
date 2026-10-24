#!/usr/bin/env python3
"""Minimal unified-diff applier for the ps2gdb kit (stdlib only).

The ps2dev toolchain container ships no `patch` utility and no busybox
`patch` applet (tested 2026-10-24), so apply-and-build.sh falls back to
this script when `patch` is absent. It supports exactly the diff format
generated for this kit — unified hunks, in order, one file, no
create/delete/rename, LF endings — and rejects anything else loudly.

Every application is additionally verified by the sha256 gate in
apply-and-build.sh (the patched result must hash equal to the
hardware-validated stub), so a silent wrong apply is impossible by
construction.

Usage: apply-patch.py <patch-file> <orig-file> <out-file>
"""
import sys


def fail(msg):
    sys.stderr.write("apply-patch.py: %s\n" % msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 4:
        fail("usage: apply-patch.py <patch> <orig> <out>")
    patch_path, orig_path, out_path = sys.argv[1:4]

    with open(patch_path, "rb") as f:
        plines = f.read().splitlines(keepends=True)
    with open(orig_path, "rb") as f:
        orig = f.read().splitlines(keepends=True)

    # Parse hunks: list of (old_start_1based, body_lines).
    hunks = []
    i = 0
    seen_header = False
    while i < len(plines):
        ln = plines[i]
        if ln.startswith(b"--- ") or ln.startswith(b"diff "):
            i += 1
            continue
        if ln.startswith(b"+++ "):
            seen_header = True
            i += 1
            continue
        if ln.startswith(b"@@"):
            if not seen_header:
                fail("hunk before file header")
            fields = ln[3:].split(b"@@")[0].split()
            if len(fields) < 2:
                fail("bad hunk header: %r" % ln)
            try:
                old_start = int(fields[0].split(b",")[0].lstrip(b"-"))
            except ValueError:
                fail("bad old range: %r" % ln)
            i += 1
            body = []
            while i < len(plines) and not plines[i].startswith(b"@@"):
                body.append(plines[i])
                i += 1
            for bl in body:
                if bl.startswith(b"--- ") or bl.startswith(b"+++ "):
                    fail("second file header inside hunk body — "
                         "multi-file patches are not supported")
                if bl[:1] not in (b" ", b"+", b"-"):
                    fail("unsupported patch construct: %r" % bl)
            hunks.append((old_start, body))
            continue
        if ln.strip() == b"":
            i += 1
            continue
        fail("unexpected line in patch: %r" % ln)

    if not hunks:
        fail("no hunks found")

    result = []
    consumed = 0  # 0-based index into orig
    for old_start, body in hunks:
        idx = old_start - 1
        if idx < consumed:
            fail("hunk out of order: old_start=%d consumed=%d"
                 % (old_start, consumed))
        result.extend(orig[consumed:idx])
        consumed = idx
        old_block = [l[1:] for l in body if l[:1] in (b" ", b"-")]
        new_block = [l[1:] for l in body if l[:1] in (b" ", b"+")]
        if consumed + len(old_block) > len(orig):
            fail("hunk overruns file: old_start=%d len=%d"
                 % (old_start, len(old_block)))
        if orig[consumed:consumed + len(old_block)] != old_block:
            fail("context mismatch at hunk old_start=%d — wrong base file?"
                 % old_start)
        result.extend(new_block)
        consumed += len(old_block)
    result.extend(orig[consumed:])

    with open(out_path, "wb") as f:
        f.write(b"".join(result))


if __name__ == "__main__":
    main()
