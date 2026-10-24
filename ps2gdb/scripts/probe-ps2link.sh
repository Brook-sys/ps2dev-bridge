#!/bin/sh
# ps2link liveness probe — TCP 18193 connect check.
# Tested 2026-10-24 on hardware (repeatedly, across live/dead console states).
#
# Usage:   PS2_HOST=<console-ip> sh probe-ps2link.sh
# Exit:    prints the connect_ex() code; 0 = ps2link alive,
#          11 = EAGAIN (dead console), 111/113 = refused/unreachable.
# Runs inside the ps2dev container (python available there); never probes
# the gdb stub port (12) — see ps2gdb/README.md, "Zero probes" rule.
HOST="${PS2_HOST:?usage: PS2_HOST=<console-ip> sh probe-ps2link.sh}"
exec python -c 'import socket, sys
s = socket.socket()
s.settimeout(3)
print(s.connect_ex((sys.argv[1], 18193)))' "$HOST"
