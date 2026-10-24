# ps2gdb — hardened GDB stub for real-hardware PS2 debugging (validated 2026-10-24)

This directory is the **validated reconstruction kit** for a fixed version of the
[ps2dev/ps2gdb](https://github.com/ps2dev/ps2gdb) EE-side GDB stub (RSP server over
TCP, port 12). The upstream stub **does not work on real hardware**; the five fixes
here were validated **offline first** (real gdb 17.0.50 against a fault-injecting
fake stub) and then in **two full on-console sessions** — the second one ending in an
**owner-visible, owner-verified proof on the TV** (the owner picked two numbers after
the ELF's sha256 was published; those numbers appeared on screen only via live GDB
memory writes). Full record: [`docs/ps2gdb-validation.md`](../docs/ps2gdb-validation.md).

## Why a patch and not the source (licensing)

The upstream repository **has no LICENSE file** — GitHub reports `license: null` —
but its `README` carries a custom copyright notice: © 2003 Ray Donnelly, BSD-style
redistribution conditions **plus a clause granting use "for academic, research and
non-commercial purposes only"**, with the author retaining a royalty-free license to
licensees' modifications. Because this repository is public and MIT-licensed, the
upstream source is **deliberately NOT vendored** — that would misrepresent the
licensing of the embedded code. What ships here instead:

- `fixes.patch` — the exact diff (5 hunks, 168 lines) from upstream `b1f3428` to the
  hardware-validated stub, regenerable and applicable with `patch -p1`;
- `samples/`, `Makefile.experiment`, `scripts/`, `apply-and-build.sh` — original work
  written during the validation sessions (marker sample, TV-proof sample, root
  Makefile, session drivers, reconstruction script);
- `evidence/` — the wire logs, netconsole captures and bridge responses recorded
  during the validated sessions.

If you use the reconstructed stub, the upstream README's terms apply to it
(non-commercial use etc.). The patch itself is our modification work — per the
README's clause 3 the author retains a non-exclusive royalty-free license to it.

## Upstream claims vs. measured reality

The upstream `README` (updated 2024, "Current State by crt0") claims
*"Writing/Reading variables, memory, registers (only as 32bit), call stack — works."*
Measured on real hardware (Slim + ps2link + gdb 17.0.50 of the ps2dev toolchain),
upstream `b1f3428` **hangs on the first register fetch** and cannot complete a
session. The five defects and their evidence:

| # | Defect in upstream `b1f3428` | Measured failure | Fix | Validated |
|---|---|---|---|---|
| 1 | `g` packet sends 4 bytes/register (360 B total) while its own `G` consumer expects 8 B/register | gdb merges 32-bit pairs into 64-bit regs (`zero=0x100000000`, `pc=0x2f` which was really `cp0_index`) | 90 registers × 8 bytes LE = 720 B body (1444 B on the wire), layout cross-checked with gdb's own `maint print registers` | hardware: cycle 2 (18× `g` deliveries) + cycle 3 (7× deliveries), zero packet errors |
| 2 | `G` packet layout inconsistent with `g` (reads 8 B/slot but writes 4 B fields at wrong offsets) | code analysis: offsets desync between `g`/`G` | consumes the same 720 B layout, writes the low 4 bytes of each slot | indirect: same layout accepted by gdb in the final session's register fetches |
| 3 | first stop reply preceded by a stray `-` byte | lab: gdb retransmits, session derails ("Bogus trace status reply from target: OK" — fatal) | no `-` before the first stop; gdb's `?` gets `S05` normally | offline lab + every hardware session |
| 4 | `getpacket` on bad checksum calls `gdbstub_error()` — literally `while (1) vprintf(...)`, never returns | one corrupted TCP payload = stub dead forever | NAK + rescan loop; gdb's retransmission is picked up (standard RSP recovery) | offline lab v4.1: real gdb retransmits after NAK and recovers |
| 5 | `putpacket` (a) waits forever for `+` (deadlock on NAK); (b) sends each frame in a **single `send()`** — ps2ips never delivers frames ~1 KiB | size ladder on hardware: replies ≤517 B always cross; ≥1028 B deliver **zero bytes** and kill the connection. The `g` reply (1444 B) is the session's first >1 KiB packet — hence upstream hangs on the first register fetch | (a) NAK → resend loop; (b) frames sent in ≤512 B `send()` chunks (inside the measured envelope) | lab v4.1 (NAK→resend accepted), lab v5 (1444 B in 3 chunks reassembled; NAK→chunked resend accepted) + hardware final sessions |

Root-cause pointers for #5b: ps2ips `BUFF_SIZE=1024` clamps and copies into a
1056-byte IOP buffer at offset 64 — a single aligned send >992 B overflows it. The
precise lwIP-internal failure mode remains open (see
[`docs/ps2gdb-open-issues.md`](../docs/ps2gdb-open-issues.md)); the chunked-send
workaround is fully validated.

## Reconstruction (cryptographically pinned to the tested bytes)

```sh
# 1. fetch the pinned upstream snapshot and verify it
curl -fL https://github.com/ps2dev/ps2gdb/archive/b1f3428.tar.gz -o ps2gdb-b1f3428.tar.gz
echo "65d98c347dbae64ceadadce07c4099263b0bd4f7f7ebd7599aa01a937fc7c348  ps2gdb-b1f3428.tar.gz" | sha256sum -c -
tar xzf ps2gdb-b1f3428.tar.gz   # -> ps2gdb-b1f3428295b928ee1b635ee38a1ff2e5744e4d00/

# 2. apply the patch, install the validated samples and build
#    (needs PS2SDK + ee-gcc on PATH — e.g. inside the ps2dev toolchain container)
./apply-and-build.sh ps2gdb-b1f3428295b928ee1b635ee38a1ff2e5744e4d00
```

`apply-and-build.sh` enforces two sha256 gates so the reconstruction cannot silently
drift from the tested bytes:

- **gate 1** — `ee/ps2gdbStub.c` must be the upstream base:
  `9d029f9f6aeaf034a722725d4e54f084651678c7b935882aa6c270202ebcee8e`
- **gate 2** — after patching, it must be the hardware-validated stub:
  `6345b9487bb4aca77fe076c4281b6a0b625bee2ae7613f0ebf1e8d359c459f29`

`SHA256SUMS` pins every artifact in this kit (patch, samples, Makefile, evidence).

> Note on ELF hashes: a freshly built `gdbtv.elf` will **not** hash equal to the
> artifact pushed during validation (`09eba180…`, recorded in
> `docs/ps2gdb-validation.md`) — DWARF embeds build-directory paths. The pinning
> that matters is at the **source** level (the two gates above); the build proves
> the tree compiles and links with the validated Makefiles.

## What the samples are for

| Sample | Installs as | Purpose | Validated |
|---|---|---|---|
| `samples/gdbmark` | `<tree>/sample2` | netconsole marker program: proves udptty survives ps2ips load + a full GDB session; breakpoint target `bump()`, live-write check via `g_mark` | cycle 2 (2026-10-24) |
| `samples/gdbtv` | `<tree>/sample3` | owner-visible TV proof: counter loop, `checkpoint()` breakpoint target every 40 iterations, `g_magic` written only by GDB, final `PROOF COMPLETE` line parks on screen | cycle 3 (2026-10-24), owner-confirmed |

## Operational protocol (measured — see SKILL.md for the full etiquette)

- **Zero probes on TCP port 12 before the session** — the gdb connection must be the
  stub's first accepted connection on a pristine boot.
- Real gdb inserts software breakpoints via direct `M` writes (the stub answers the
  essential RSP subset: `?`, `H`, `g/G`, `m/M`, `c`, `s`; extended packets get empty
  replies and gdb degrades gracefully).
- `set remotelogfile <file>` in gdb records every RSP byte both ways — use it before
  theorizing about protocol weirdness.
- **Strand budget:** any boot with ps2ips loaded kills ps2link within ~20–40 min
  (6/6 measured). Plan the whole gdb session inside that window.
- Session drivers used in the validated runs: `scripts/` (host/paths parameterized;
  command sequences are the tested ones).

## Scope & limitations (honest inventory)

Validated on **one** Slim console, one toolchain gdb (17.0.50), one stub build.
`G` writes only the low 4 bytes of each 8-byte slot (stub internals are 32-bit);
128-bit registers are not covered. Reconnection after a final `continue` re-enters
the stub's `accept()` (measured) but the program keeps running — you are attaching
to a moving target. Open issues (strand root cause, ps2ips ≥1028 B failure mode,
black-screen-after-exit deviation, a post-ps2ips reset re-exec failure) are
documented in [`docs/ps2gdb-open-issues.md`](../docs/ps2gdb-open-issues.md).
