# Evidence — captured during the validated sessions (2026-10-24)

Raw artifacts backing `docs/ps2gdb-validation.md`. Do not edit; hashes are
recorded in `../SHA256SUMS`.

| File | What it is | Session |
|---|---|---|
| `rsp-cycle2-final.log` | Full RSP wire log (both directions) of the cycle-2 marker session — 18× `g` packet deliveries (1444 B each), live `M` writes `0x2a`/`0x64`, `T05` stops, breakpoint insert/remove via `M` | cycle 2 |
| `gdb-output-cycle2-final.log` | gdb console output of the same session: symbolized stops (`bump () gdbmark.c:11`), backtrace, live writes, `next` → `$5 = 101`, exit 0 | cycle 2 |
| `netconsole-cycle2-final.json` | Bridge netconsole capture of the same session: MARK2..MARK5 lines — udptty survives ps2ips load + the full GDB session | cycle 2 |
| `run-cycle2-final.json` | Bridge `/run` response for the cycle-2 push | cycle 2 |
| `rsp-cycle3-tvproof.log` | RSP wire log of the TV-proof session: breakpoint `M1030d4,4:0d000500` insert/remove ×2, owner numbers as wire bytes `M12bfd4,4:c59e0200` (171717) and `M12bfd4,4:32790600` (424242), `T05` stops at `checkpoint`, 7× `g` deliveries, zero packet errors | cycle 3 |
| `gdb-output-cycle3-tvproof.log` | gdb console output: stops at `checkpoint () gdbtv.c:43` ← `main gdbtv.c:72`, `$1 = 40` / `$3 = 80` (the designed multiples), `$4 = 171717` (first write persisted), exit 0 | cycle 3 |
| `netconsole-cycle3-tvproof.json` | Independent netconsole capture: the program's own view — `GDBTV-WRITE: magic=171717 write=1 at counter=41`, `magic=424242 write=2 at counter=81`, `GDBTV-DONE: magic=424242 counter=200 writes=2 - parking` | cycle 3 |
| `run-cycle3-boot.json` | Bridge `/run` push of the proof ELF (`loadelf: host:/work/sample3/gdbtv.elf`, `GDBTV1`, `PS2IPS: Module Loaded.`) | cycle 3 |
| `reset-2026-10-24-reexec-failed.json` | The post-gdb-boot `POST /reset` that took the console down but whose ps2link re-exec did not return (open issue — see `docs/ps2gdb-open-issues.md`) | post cycle 3 |

The ELF pushed in cycle 3 is not committed (repo hygiene); its hash is
`09eba1802a93e40fc8d7c53106780d0eb2127bc642b0fbd1b8104711471b0e82` and it is
reproducible from the pinned sources (see `../README.md`, "Reconstruction").
