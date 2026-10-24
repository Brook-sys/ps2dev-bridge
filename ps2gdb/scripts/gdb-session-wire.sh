#!/bin/sh
# Wire-truth GDB session driver — the exact command sequence validated on
# hardware 2026-10-24 (cycle 2, marker sample gdbmark.elf). See
# docs/ps2gdb-validation.md and evidence/gdb-output-cycle2-final.log.
#
# RUNS INSIDE the ps2dev toolchain container (gdb + ELF live there).
# The console push/netlog happen via the bridge from the host; this script
# only drives the gdb side. NO port-12 probe — gdb is the only connection.
#
# Usage:
#   PS2_HOST=<console-ip> GDBMARK_ELF=/work/sample2/gdbmark.elf \
#     sh gdb-session-wire.sh
# Session facts (all measured):
#   - stub answers ?, H, g/G, m/M, c, s; extended packets (Z0, qOffsets,
#     qSymbol) get empty replies and gdb degrades (breakpoints via M writes)
#   - remotelogfile records every RSP byte in both directions
#   - final `continue` leaves the target running by design: gdb -batch exits
#     after the command file is exhausted (exit 0 in the validated session)
HOST="${PS2_HOST:?set PS2_HOST to the console IP (ps2link IP; stub listens on :12)}"
ELF="${GDBMARK_ELF:-/work/sample2/gdbmark.elf}"
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
    echo "break bump"
    echo "continue"
    echo "bt"
    echo "print g_counter"
    echo "print g_mark"
    echo "set var g_mark = 42"
    echo "set var g_counter = 100"
    echo "print g_mark"
    echo "print g_counter"
    echo "x/4xw &g_mark"
    echo "x/4xw &g_counter"
    echo "next"
    echo "bt"
    echo "print g_counter"
    echo "delete 1"
    echo "continue"
} > "$LAB/gdbcmds.txt"

echo "=== wire-truth session: gdbmark, cap 60s (-k 5) ==="
timeout -k 5 60 mips64r5900el-ps2-elf-gdb -batch -x "$LAB/gdbcmds.txt" 2>&1
rc=$?
echo "=== gdb exit: $rc ==="
if [ -f "$LAB/rsp-session.log" ]; then
    wc -c "$LAB/rsp-session.log"
else
    echo "(no rsp log produced)"
fi
exit $rc
