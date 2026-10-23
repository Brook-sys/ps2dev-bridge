---
name: ps2dev-bridge
description: Build, deploy and debug PlayStation 2 homebrew on a real console through the ps2dev-bridge service (MCP tools + REST API). Use when the task involves PS2 development — compiling ELF homebrew with the ps2dev toolchain, pushing builds to a PS2 running ps2link, or reading console output and crash logs.
---

# PS2 dev bridge — debug loop for AI agents

The bridge exposes the full PS2 toolchain (from the official `ps2dev` container)
plus `ps2client` console access over MCP and REST. It lives on the same LAN as
the console. The entire debug loop is 4–5 calls:

```
1. ps2_status        (sanity: tools present, sources synced, console configured)
2. sync sources      (tar upload — see below)
3. ps2_build         (make; full log returns in the response)
4. ps2_run           (push ELF + capture console output while it runs)
   → read log, find the bug, edit code, go back to 2
5. ps2_reset         (reboot the console back into a fresh ps2link — needed
   between runs when the program parks; see "The unattended loop" below)
```

## Where the bridge lives

Read from the environment (or ask the user):

- `PS2BRIDGE_URL` — e.g. `http://100.83.108.127:17800`
- `PS2BRIDGE_KEY` — the value of the `X-API-Key` header

If the `ps2dev-bridge` MCP server is registered, prefer its tools. Otherwise
use the REST API below with the header `-H "X-API-Key: $PS2BRIDGE_KEY"`.

## Syncing sources (before the first build, or after edits)

From the project root on the agent's machine:

```sh
tar czf - . | curl -sS -H "X-API-Key: $PS2BRIDGE_KEY" \
  --data-binary @- "$PS2BRIDGE_URL/sync"
```

The tar is extracted into the persistent workspace (`/work`). Only regular
files/dirs are accepted — no symlinks, no absolute paths, no `..` traversal.
Sync only what changed; the volume persists between builds (incremental
`make` is fast).

## How the console side works (read once — verified against ps2link/ps2sdk src)

- The console runs **ps2link** (TCP 18193 requests, UDP 18194 console). The
  bridge probes 18193 before every console call, so a dead console fails in
  ~3 s with `cannot reach ps2link` instead of a hang or a client crash.
- `execee host:<path>` is a fire-and-forget command: **the console pulls the
  ELF from the bridge** over the request channel and then runs it. Without
  the `host:` prefix the console would look for the file on its own side —
  the bridge always adds it; don't pass one yourself.
- While the console pulls (and after it runs the program), the netconsole
  stream — program stdout, ps2link messages, exception dumps — is printed
  by the same ps2client process. `ps2_run`'s `log` is that stream.
- **Netconsole transport (important):** the console sends that stream as a
  **UDP broadcast to 255.255.255.255:18194** — udptty (ps2sdk
  `iop/network/udptty`) implements the `tty:` device all stdout goes to and
  broadcasts every write. The bridge container only receives broadcasts
  because its deployment **publishes `18194/udp`** (docker-proxy relays
  them in). `ps2_run` ok=true with an **empty log** on a deployment without
  that publish is the classic symptom — the program ran fine, the output
  just never reached the container.
- **Program lifecycle:** the executed ELF runs as a *thread* of ps2link,
  which stays resident underneath. But a ps2sdk program must never return
  from `main()`: crt0 turns that into the kernel's `Exit()`
  (ps2sdk `ee/startup/src/crt0.c`), which resets the console to its **native
  browser screen** and kills ps2link. Three consequences:
  - if a program exits, ps2link is gone and the user must reload it on the
    console by hand (the bridge's probe will keep failing honestly);
  - **one program at a time:** while a program is resident, a second exec
    is **silently refused** by ps2link — `pkoExecEE` checks its user thread
    and returns -1 before pulling anything (verified on hardware). The
    refusal looks like `ok: true` + empty log: no crash, no corruption,
    nothing runs. `ps2_reset` between runs is the rule;
  - `reset` (ps2link command, PKO_RESET) reboots the console back into a
    **fresh ps2link from RAM** — it terminates the user thread, resets the
    IOP and re-executes itself (IRXs embedded in its ELF, network config
    cached). That's what `ps2_reset` drives.
- The TV is the ground truth: it shows `executing file ...`, program output,
  or the native browser (meaning the last program exited).

## The unattended loop: park + reset

`ps2_reset` only works while ps2link is alive — i.e. while the last program
is **still resident**. So test programs should **park** at the end instead
of exiting: end `main` with a sleep-forever call and ps2link survives
underneath. Then the loop needs no human at the console:

```c
#include <stdio.h>
#include <kernel.h>

int main(void) {
    printf("hello\n");
    SleepThread();   /* park: sleep forever, never reach crt0's Exit() */
    return 0;        /* not reached */
}
```

```
ps2_run   → ps2_reset   → ps2_run   → ...   (ps2link_alive=true between runs)
```

`ps2_run` reports `ps2link_alive` after the capture window: `true` means
the program parked (or is long-running) — call `ps2_reset` and move on;
`false` means it exited and reset the console to the browser (or reset the
IOP — see below) — ask the user to reload ps2link. If you write or edit
test programs yourself, always end them with the park.

## Heavy programs: memory and the IOP (measured on hardware)

All numbers below were measured on a real console through this bridge
(stack pointer probed at `main`, 24 MB heap stress, an actual IOP reset):

- **RAM:** the program owns the whole GAME region
  (0x00100000–0x02000000, 31 MB). ps2link lives **below** it, in BIOS
  unused memory (0x00094000–0x00100000), and is never in the program's
  way. `malloc(24 MB)` + full-page write/verify passed; the heap starts
  right after `_end` (~0x0012B40C for a hello-sized program).
- **Stack:** the 16 KB buffer inside ps2link is only the *bootstrap*
  stack — the program's own crt0 hands the thread the stack declared in
  **its linkfile** (`SetupThread`, syscall 60). Stock ps2sdk linkfile
  (`_stack = -1`, `_stack_size = 128 * 1024`) → measured `sp@main =
  0x01FFED30`: a real 128 KB stack just under the top of GAME memory.
  **The EE has no MMU and no guard pages:** exceeding the stack region
  never faults — it silently writes into whatever is below it (usually
  your own heap). Deep-recursion / big-local programs should ship a
  custom linkfile with an explicit stack region and a capped heap, e.g.
  `PROVIDE(_heap_size = 0x01D00000 - _end); PROVIDE(_stack = 0x01E00000);
  PROVIDE(_stack_size = 1024 * 1024);` — measured `sp@main = 0x01EFFD30`
  (region [0x01E00000, 0x01F00000]); heap and stack can never meet.
- **IOP reset = the bridge goes blind.** Everything the bridge talks to —
  netconsole (udptty), the ELF pull channel, the command/reset socket —
  lives **on the IOP**. A program calling `SifIopReset` (what real games
  do at boot) kills all of them at once while the program keeps running
  on screen. Measured signature: `ps2_run` ok=true, log **ends at the
  reset point**, `ps2link_alive: false`, then `/log` and `/reset` fail
  with `cannot reach ps2link`. Recovery is a **manual ps2link reload** —
  run such programs only when a hand reload afterwards is acceptable.

## Crash debugging (measured on hardware)

When a program dies from an exception, ps2link's handler (ee/excepHandler.c)
prints a full dump to the netconsole — the `log` you get back — and
terminates the program thread. ps2link itself survives.

- **The bridge parses it for you:** `ps2_run` responses carry a `crash`
  object — cpu (`EE`/`IOP`), type, cause, badvaddr, status, epc, and ra/sp
  from the register block. When the ELF was built with debug info
  (`-gdwarf-2`), `crash.symbols` maps EPC and ra to `function` +
  `file:line`, and the note names the crash site. Real example (2026-10-23):
  type `"TLB store"`, `"epc": "0x0011DA4C"` → `crash.c:21 (level3)`.
- **Post-crash rule (measured):** the exec slot stays occupied — a second
  `ps2_run` is silently refused until `ps2_reset`. The crash note says so.
- **Coverage:** 12 of the 14 EE exceptions are hooked (TLB refill/mod/load/
  store, address load/store, bus errors, reserved instruction, coprocessor
  unusable, arithmetic overflow, trap). Deliberately NOT hooked: syscall (8)
  and **breakpoint (9) — a `break` instruction is NOT caught**. IOP
  exceptions print module name + relative EPC and parse the same way.
- **Not debuggable via crash dumps:** silent corruption (stack overflow past
  the linkfile region, wild writes into valid RAM — no MMU, no guard pages),
  integer division by zero (MIPS doesn't trap it), timing-dependent bugs
  (printf changes timing). For those: PCSX2.
- **Known dead end (measured):** `ps2client dumpmem`/`dumpreg` exist in the
  CLI, but the console-side `host:` file server refuses open-for-write
  (`open fd = -1`) and the command hangs — they never produce a dump. Don't
  burn time on them; the crash dump already carries the register state.
- Symbolize any other address (map, jump table, whatever) with
  `ps2_symbolize` — same addr2line the bridge uses; `??` means no debug info
  at that address.

## MCP tools

| Tool | What it does |
|---|---|
| `ps2_status()` | Config, toolchain presence (`make`, `ps2client`, `ee-gcc`), artifacts, free disk. Call first. |
| `ps2_build(clean?)` | `make` in the workspace; returns `exit_code` + full (head+tail truncated) log. |
| `ps2_push(artifact?)` | `ps2client execee host:<path>` — console pulls the ELF and runs it. Default artifact: newest `.elf`. Blocks ≈ `PS2BRIDGE_PUSH_TIMEOUT` (60 s). |
| `ps2_log(seconds?)` | `ps2client listen` — captures the netconsole stream for an idle window (default 15 s). |
| `ps2_run(artifact?, seconds?)` | Probe → netdump → push+capture in one sequential flow; `log` = netconsole stream, `ps2link_alive` = console state afterwards. When the program dies from an exception, the response also carries a structured `crash` object (type, cause, badvaddr, epc, ra, sp — plus `symbols` with file:line when the ELF has debug info). The workhorse. |
| `ps2_symbolize(artifact?, addrs)` | addr2line wrapper: maps 0x-hex addresses (EPC/ra from a crash, or any map address) to `function` + `file:line`. Default artifact: newest `.elf`; needs the build to carry `-gdwarf-2`. |
| `ps2_reset()` | `ps2client reset` — console reboots back into a fresh ps2link (≈5–40 s; ok=true only when ps2link answers again). Call between parked runs. |

## REST API (mirror of the tools)

| Endpoint | Notes |
|---|---|
| `GET /health` | No auth. Same payload as `ps2_status`. |
| `POST /sync` | Body: tar stream (`application/x-tar+gzip` or similar). |
| `POST /build?clean=1` | |
| `GET /artifacts` | Lists `.elf`/`.iso` with size and mtime. |
| `GET /artifacts/{path}` | Downloads any workspace file (retrieve ELFs, maps, logs). |
| `POST /push?artifact=...` | |
| `POST /log?seconds=...` | |
| `POST /run?artifact=...&seconds=...` | |
| `POST /reset` | |
| `POST /symbolize?artifact=...&addr=0x...&addr=...` | `addr` is repeatable; artifact defaults to the newest `.elf`. |

## Reading the results

- Build failed → the full compiler output is in `log`; fix the code, re-sync, rebuild.
- `ok: false` with `PS2_HOST is not configured` → the bridge has no console IP;
  that's a deployment problem (env `PS2_HOST`), not a code problem.
- `ok: false` with `cannot reach ps2link` → the pre-flight probe failed: the
  console is off, or the last program exited and reset the console to its
  browser (ps2link gone). Ask the user to reload ps2link on the console.
  `ps2_reset` cannot help in this state — it needs a live ps2link.
- `ps2_run` ok=true + `crash` object → the program died from an exception;
  the note carries the symbolized crash site. ps2_reset before the next run —
  the exec slot stays occupied after a crash (measured).
- `ps2_run` ok=true, `ps2link_alive=true` → the program is parked/resident;
  call `ps2_reset` before the next run (do NOT ps2_run on top of a parked
  program — ps2link silently refuses the second exec and returns an empty
  log, which is easy to misread as "my program printed nothing").
- `ps2_run` ok=true, `ps2link_alive=false` → the program exited and the
  console is on its browser — or it reset the IOP and killed ps2link's
  network (the log ending mid-program is the tell; see "Heavy programs").
  Either way the user must reload ps2link.
- `ps2_reset` ok=true → ps2link is back; proceed with the next ps2_run.
  ok=false with "did not go down" → the reset didn't take (retry once, then
  look at the TV); "did not answer again" → the reboot started but ps2link
  didn't return in time (wait a few seconds, probe again, check the TV).
- `ps2_run` ok=true but `log` is empty → four known causes, in order of
  likelihood: (1) **a program is still resident and ps2link silently
  refused the exec** (you forgot `ps2_reset` — the TV still shows the
  previous program; no loadelf lines is the giveaway); (2) **the
  deployment doesn't publish `18194/udp`** — the netconsole is a UDP
  broadcast from the console and never reaches the container (check the
  compose file; every run comes back empty, the TV still shows the
  program output); (3) the program printed nothing in the window (retry
  with more seconds); (4) it never ran — the TV says which: `executing
  file` + output means it ran; `could not execute` means the loader
  rejected the ELF (inspect it via `GET /artifacts/{path}`).
- exit_code 124 (timeout) → the pull window ran out; the shipped ps2client
  predates the upstream TCP_NODELAY fix, so big ELFs can pull slowly. Retry
  with a larger `seconds`.
- Push/run ok=true is weak evidence by itself — ps2client exits 0 as long as
  the command was sent. Treat `log` content and the TV as the truth.
- `GET /artifacts/{path}` is how you bring an ELF back to the agent machine.

## Cautions

- One build at a time and one console op at a time (the bridge enforces a
  lock). Never run a second ps2client by hand while a bridge console op is
  in flight — the protocol has a single netconsole port and the second
  client crashes.
- If you run `ps2client` by hand anyway (docker exec), ALWAYS pass
  `-t <seconds>` — commands hang forever against an unreachable or occupied
  console (measured: `dumpreg` never returned) and leave zombie processes
  behind. Kill zombies by PID, never with `pkill -f` (the pattern matches
  your own wrapper shell and kills it — also measured).
- **Never let a test program return from main()** — that resets the console
  to its browser and kills ps2link (someone has to reload it by hand). End
  programs with the park pattern and use `ps2_reset` between runs.
- Stack: with the stock ps2sdk linkfile the program gets a real 128 KB
  stack at the top of GAME memory (measured; ps2link's 16 KB buffer is only
  the pre-`main` bootstrap). Overflow is **silent corruption** — the EE has
  no guard pages — so deep-recursion/big-local programs need a custom
  linkfile with a bigger explicit stack region; see "Heavy programs" for
  the measured recipe.
- `ps2_push`/`ps2_run` block for the whole idle window even on success —
  that's how the pull/capture protocol works, not a hang.
- Long builds are capped by `PS2BRIDGE_BUILD_TIMEOUT` (default 900 s).
- If the host running the bridge reboots, in-flight operations die — re-run.
- Logs longer than `PS2BRIDGE_MAX_LOG` chars are head+tail truncated with a
  marker line; the ends (where errors live) are always preserved.
