#!/bin/sh
# Owner-visible TV-proof GDB session driver — the exact command sequence
# validated on hardware 2026-10-24 (cycle 3, sample gdbtv.elf), including the
# anti-forgery protocol. See docs/ps2gdb-validation.md.
#
# RUNS INSIDE the ps2dev toolchain container (gdb + ELF live there).
# The console push/netlog happen via the bridge from the host.
#
# Anti-forgery protocol (mandatory order):
#   1. Build the ELF and PUBLISH its sha256.
#   2. The console owner picks TWO DIFFERENT integers (only now — the binary
#      is already pinned by the hash and cannot know them).
#   3. Run this script with those numbers. gdbtv.c never writes g_magic
#      itself (volatile, starts 0) — the numbers can only appear on the TV
#      via live GDB memory writes at two breakpoint stops.
#   4. The final PROOF COMPLETE line parks on screen; the owner confirms
#      with their own eyes.
#
# Timeline produced on the TV:
#   ticks c~10->40 | stop#1: bt+registers+prints, 20 s frozen at c=40,
#   write NUM1, resume -> "GDB WROTE magic=NUM1"
#   ticks c=41->80  | stop#2: 20 s frozen at c=80, write NUM2, resume
#   -> "GDB WROTE magic=NUM2" -> ticks to 200 -> PROOF COMPLETE (parked).
#
# Usage:
#   PS2_HOST=<console-ip> GDBTV_ELF=/work/sample3/gdbtv.elf \
#     sh gdb-session-tv.sh NUM1 NUM2
# gdb exit 0 = clean batch end (validated); exit 124 = cap kill while the
# target runs free after the final continue — also OK by design.
NUM1="${1:?NUM1 (owner-picked integer)}"
NUM2="${2:?NUM2 (owner-picked integer, DIFFERENT from NUM1)}"
HOST="${PS2_HOST:?set PS2_HOST to the console IP (ps2link IP; stub listens on :12)}"
ELF="${GDBTV_ELF:-/work/sample3/gdbtv.elf}"
LAB="${GDB_LAB_DIR:-/tmp/lab}"

mkdir -p "$LAB"
rm -f "$LAB/rsp-session.log" "$LAB/gdbcmds.txt"

{
    echo "set height 0"
    echo "set pagination off"
    echo "set confirm off"
    echo "file $ELF"
    echo "set remotelogfile $LAB/rsp-session.log"
    echo "target remote $HOST:12"
    echo "break checkpoint"
    echo "continue"
    echo "bt"
    echo "info registers"
    echo "print g_counter"
    echo "print g_magic"
    echo "shell sleep 20"
    echo "set var g_magic = $NUM1"
    echo "continue"
    echo "print g_counter"
    echo "print g_magic"
    echo "shell sleep 20"
    echo "set var g_magic = $NUM2"
    echo "delete"
    echo "continue"
} > "$LAB/gdbcmds.txt"

echo "=== tv-proof session: numbers $NUM1 then $NUM2; cap 240s (-k 5) ==="
timeout -k 5 240 mips64r5900el-ps2-elf-gdb -batch -x "$LAB/gdbcmds.txt" 2>&1
rc=$?
echo "=== gdb exit: $rc (124 = cap kill while target runs free: OK by design) ==="
if [ -f "$LAB/rsp-session.log" ]; then
    wc -c "$LAB/rsp-session.log"
else
    echo "(no rsp log produced)"
fi
exit $rc
