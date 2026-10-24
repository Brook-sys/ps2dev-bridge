# ps2gdb — validation record (2026-10-24)

Everything below was **measured**, not inferred. Artifacts are preserved in
[`ps2gdb/evidence/`](../ps2gdb/evidence/) and pinned in
[`ps2gdb/SHA256SUMS`](../ps2gdb/SHA256SUMS). The hardware: one PS2 Slim, ps2link
booted via wLaunchELF, bridge container on the same LAN. The debugger:
`mips64r5900el-ps2-elf-gdb` (GDB 17.0.50.20251110-git) from the ps2dev toolchain.

## Method: labs first, console cycles last

Every fix was validated **offline before spending a console cycle**: real gdb
against a fault-injecting fake stub (python, [`rawladder.py`-style]) with
deterministic corruption, NAK injection and fragmented delivery:

- **Lab v4.1** — corrupted checksum → NAK → **real gdb retransmits and recovers**
  (upstream would call `gdbstub_error()` = infinite `while(1) vprintf` loop and
  die permanently). Also: NAK → chunked resend accepted.
- **Lab v5** — a 1444-byte `g` reply delivered as 512+512+420 chunks is
  transparently reassembled by gdb (upstream's single-`send()` delivery of that
  frame never crosses ps2ips at all — see below).
- **Size ladder on hardware** (the root-cause probe): stub replies ≤517 bytes
  **always** cross; ≥1028 bytes deliver **zero bytes** and kill that connection.
  ps2ips source confirms the structural mechanism: `BUFF_SIZE=1024` clamps and
  copies into a 1056-byte IOP buffer at offset 64 — a single aligned send >992 B
  overflows it. The `g` reply (1444 B on the wire) is every session's first >1 KiB
  packet, which is why **upstream ps2gdb hangs on the first register fetch**.

## Cycle 2 — marker session (`sample2/gdbmark.c`)

Pushed via the bridge (`/run`), single gdb session with
`set remotelogfile` capturing every RSP byte:

- symbolized initial stop (`breakpoint() ps2gdbStub.c:1326`) and breakpoint stop
  (`bump () gdbmark.c:11`) with a 2-frame backtrace;
- `g` packet (1444 B) delivered **18×**, **zero** "Ignoring packet error";
- live writes confirmed on the wire: `M12bed4,4:2a000000` (`g_mark=42`) and
  `M12bed0,4:64000000` (`g_counter=100`), read back by gdb and then **verified by
  the resumed program itself** on screen and netconsole
  ("MARK-GDB: agent wrote g_mark=42 via GDB, counter=101");
- single-step `next` executed `g_counter++` on the EE (`$5 = 101`);
- gdb `-batch` exit 0; the program then exited cleanly (console reset — expected).

Evidence: `rsp-cycle2-final.log`, `gdb-output-cycle2-final.log`,
`netconsole-cycle2-final.json`.

## Cycle 3 — owner-visible TV proof (`sample3/gdbtv.c`)

The anti-forgery protocol, by construction:

1. `gdbtv.elf` built; **sha256 published before any number was chosen**:
   `09eba1802a93e40fc8d7c53106780d0eb2127bc642b0fbd1b8104711471b0e82`
   (identical on host and in `/work`, the artifact actually pushed:
   `loadelf: fname host:/work/sample3/gdbtv.elf`).
2. The console owner then picked **171717** and **424242**.
3. `gdbtv.c` never writes `g_magic` (volatile, starts 0; no input device on the
   console) — the numbers could only appear via live GDB memory writes at two
   breakpoint stops.

Measured on the wire (`rsp-cycle3-tvproof.log`):

| Packet | Meaning |
|---|---|
| `M1030d4,4:0d000500` (×2, with removals) | software breakpoint at `checkpoint` inserted/removed via direct `M` writes |
| `T0525:d4301000…` | stop replies with pc = 0x1030d4 = `checkpoint` |
| `M12bfd4,4:c59e0200` | **171717** (0x00029EC5 LE) written into `g_magic` at 0x12bfd4 |
| `M12bfd4,4:32790600` | **424242** (0x00067932 LE) written at stop #2 |
| 7× `g` requests | register fetches, **zero** packet errors |

The program's own independent witness (netconsole, `netconsole-cycle3-tvproof.json`):

```
GDBTV2: stub alive, demo loop starts
GDBTV-WRITE: magic=171717 write=1 at counter=41
GDBTV-WRITE: magic=424242 write=2 at counter=81
GDBTV-DONE: magic=424242 counter=200 writes=2 - parking
```

gdb reported the designed counter multiples at the stops (`$1 = 40`, `$3 = 80`,
`$4 = 171717` — the first write persisted), a symbolized backtrace
(`checkpoint () gdbtv.c:43` ← `main gdbtv.c:72`), a coherent full register dump,
and exited 0.

**Primary evidence:** the owner, watching the TV, confirmed the parked final line
`PROOF COMPLETE: magic=424242 counter=200 writes=2` — their own number, chosen
after the hash was published, on screen. The TV line parks via `SleepThread()`,
so the screen holds the proof indefinitely.

## Reconstruction test (2026-10-24, in the toolchain container)

From the pinned upstream tarball
(`b1f3428.tar.gz`, sha256 `65d98c34…`) + this kit:

- gate 1 (upstream base `ee/ps2gdbStub.c` = `9d029f9f…`): OK;
- `fixes.patch` applied via the stdlib fallback (`apply-patch.py` — the container
  ships no `patch` utility); result verified byte-identical to the
  hardware-validated stub (`6345b948…`): OK;
- fresh build of the whole tree green: `libps2gdbStub.a`, upstream `testgdb.elf`,
  `gdbmark.elf`, `gdbtv.elf`.

ELF hashes vary per build (DWARF embeds build paths); the pinning that matters is
source-level (the two gates above).

## Scope limits (do not over-read the results)

- **One** console (Slim), **one** gdb build (17.0.50), **one** stub build.
- `G` writes only the low 4 bytes of each 8-byte slot (stub internals are 32-bit);
  128-bit registers are not covered.
- The stub answers the essential RSP subset (`?`, `H`, `g/G`, `m/M`, `c`, `s`);
  extended packets get empty replies and gdb degrades (breakpoints via `M`).
- Boots with ps2ips loaded kill ps2link within 20–40 min (6/6 — the "strand");
  every gdb session must fit inside that window. Root cause unknown — see
  [`ps2gdb-open-issues.md`](ps2gdb-open-issues.md).
