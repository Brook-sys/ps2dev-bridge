#!/bin/sh
# ps2gdb reconstruction kit — rebuild the hardened, hardware-validated stub tree
# from the pinned upstream snapshot + this repo's tested patch and samples.
#
# Validated 2026-10-24 (docs/ps2gdb-validation.md): the two sha256 gates pin the
# exact source bytes that passed the on-console sessions; the fresh-tree build was
# tested inside the ps2dev toolchain container.
#
# Usage (needs PS2SDK + ee-gcc on PATH, e.g. the ps2dev container):
#   ./apply-and-build.sh /path/to/ps2gdb-b1f3428295b928ee1b635ee38a1ff2e5744e4d00
#
# Upstream snapshot pin:
#   https://github.com/ps2dev/ps2gdb/archive/b1f3428.tar.gz
#   sha256 65d98c347dbae64ceadadce07c4099263b0bd4f7f7ebd7599aa01a937fc7c348
#   -> extracts to ps2gdb-b1f3428295b928ee1b635ee38a1ff2e5744e4d00
set -e

KIT_DIR="$(cd "$(dirname "$0")" && pwd)"
TREE="${1:?usage: apply-and-build.sh <extracted-upstream-b1f3428-tree>}"

BASE_SHA=9d029f9f6aeaf034a722725d4e54f084651678c7b935882aa6c270202ebcee8e
FIXED_SHA=6345b9487bb4aca77fe076c4281b6a0b625bee2ae7613f0ebf1e8d359c459f29

echo "[1/5] gate: ee/ps2gdbStub.c must be the pinned upstream base (b1f3428)"
echo "$BASE_SHA  $TREE/ee/ps2gdbStub.c" | sha256sum -c -

echo "[2/5] applying fixes.patch (5 fixes, hardware-validated 2026-10-24)"
if command -v patch >/dev/null 2>&1; then
    patch -d "$TREE" -p1 < "$KIT_DIR/fixes.patch"
else
    # The ps2dev toolchain container ships no `patch` (tested 2026-10-24):
    # stdlib-only fallback. The sha256 gate below makes a wrong apply
    # impossible by construction.
    echo "(no 'patch' utility — using the stdlib fallback apply-patch.py)"
    python3 "$KIT_DIR/apply-patch.py" "$KIT_DIR/fixes.patch" \
        "$TREE/ee/ps2gdbStub.c" "$TREE/ee/ps2gdbStub.c.new"
    mv "$TREE/ee/ps2gdbStub.c.new" "$TREE/ee/ps2gdbStub.c"
fi

echo "[3/5] gate: patched stub must be the hardware-validated bytes"
echo "$FIXED_SHA  $TREE/ee/ps2gdbStub.c" | sha256sum -c -

echo "[4/5] installing validated samples + experiment root Makefile"
cp -r "$KIT_DIR/samples/gdbmark" "$TREE/sample2"
cp -r "$KIT_DIR/samples/gdbtv" "$TREE/sample3"
mv "$TREE/Makefile" "$TREE/Makefile.upstream"
cp "$KIT_DIR/Makefile.experiment" "$TREE/Makefile"

echo "[5/5] building (ee lib + upstream sample + gdbmark + gdbtv)"
mkdir -p "$TREE/lib"
make -C "$TREE"

echo
echo "=== build OK — artifacts ==="
ls -l "$TREE/sample2/gdbmark.elf" "$TREE/sample3/gdbtv.elf"
sha256sum "$TREE/sample2/gdbmark.elf" "$TREE/sample3/gdbtv.elf"
echo
echo "Note: ELF hashes differ per build (DWARF embeds build paths)."
echo "The pinning that matters is source-level — both gates above passed."
