# Future work — planned, designed, deliberately not yet built

Everything here follows the owner's standing rule (2026-10-24): **only duly
tested behavior gets implemented**. These items are designed from validated
foundations but have **not** been executed, so they live here as plans — each
one names what must be measured before any of it becomes code.

## EXP-011 — unattended run/reset loop, including a large program

**Status: PLANNED — never executed.** (The console died mid-attempt context: see
`ps2gdb-open-issues.md` #4; the experiment was not started.)

**Motivation (owner's requirement):** a fully automated dev loop — send new code,
run it, observe it, repeat — with no manual console interaction, **regardless of
program size**.

**What is already proven (measured, in SKILL.md):** the park+reset loop runs
unattended on clean boots (`ps2_run` → `ps2_reset` → repeat, nobody at the
console). What is **not** yet measured is (a) sustained multi-turn automation with
a real edit→sync→build between every run, and (b) how push time behaves as the ELF
grows — the shipped `ps2client` predates the upstream TCP_NODELAY fix, so big ELFs
pull slowly (`exit_code 124` on the push window is the known symptom).

**Design:**

- One source, `autoloop.c`, versioned via `#define AUTOLOOP_VERSION N`, printing
  its version and parking (never returns from `main`); **no ps2ips** — plain
  bridge loop, no strand budget.
- 4 fully scripted host-side turns, each = edit (bump version / swap in the big
  variant) → `/sync` → `/build` → `/run` (verify the version line in the log) →
  `/reset` → probe-until-alive (reset returns `ok:false` whenever ps2link needs
  >30 s — re-probe loop, do not treat as failure).
- Turn 4 swaps in a large variant: ~6 MB `const` blob (real bytes in the ELF),
  checksummed and printed from the program to prove the whole ELF actually loaded.
  Push window generous (120–300 s); a 124 there is a **finding**, not a failure —
  it measures the ps2client ceiling.
- **Success criteria:** 4/4 turns with zero manual console interaction; every
  version line verified; per-turn wall times recorded (sync/build/push+window/
  reset); the large turn completes (or its ceiling is documented with numbers).
- **Honest limits to state up front:** "restart-free" means *no human* restarts —
  the remote `ps2_reset` between programs is the mechanism (one program per boot
  is a PS2 constraint; hot-reload does not exist). Programs that reset the IOP
  (real games) still blind the bridge today — see "IOP-reset-proof transport".

## MCP integration for gdb sessions

**Status: designed on a validated base — not built (untested code stays out).**

The validated pieces already exist: the RSP protocol knowledge, the tested session
drivers (`ps2gdb/scripts/`), the reconstruction kit, and the bridge's own
probe/push/log/reset machinery. A future `ps2_gdb` bridge tool would wrap:

1. staging the gdb command file (the drivers' `gdbcmds.txt` pattern) inside the
   container;
2. running `mips64r5900el-ps2-elf-gdb -batch -x` with `set remotelogfile`, under
   `timeout -k`;
3. returning the gdb console output + wire log + the netconsole window
   (`POST /log` in parallel) as one response;
4. enforcing the protocol rules (zero port-12 probes pre-session, pristine-boot
   requirement, single-session-per-boot budget).

**What must be validated before it becomes a tool:** session lifecycle (what the
bridge does when gdb blocks in a final `continue` — cap-kill is the proven exit),
wire-log retrieval, and concurrent `/log` + gdb behavior — all measured by hand
already, but never through a service code path.

## ps2client rebuild (TCP_NODELAY) for large-ELF pushes

**Status: untested fix — documented only.** The symptom is measured (slow pulls /
124 timeouts on big ELFs); the upstream fix (TCP_NODELAY on the client socket) is
known. Rebuilding `ps2client` inside the container would make pushes
predictable. Requires: building from ps2link/ps2client sources, a pull-speed
before/after measurement on ELFs of several sizes, and a regression pass on the
existing run/reset loop. EXP-011's turn 4 produces the "before" numbers for free.

## IOP-reset-proof debug transport (for the future game loop)

**Status: design avenue — far from buildable yet.** A real game (ours included)
calls `SifIopReset` at boot, which kills every bridge transport at once
(netconsole, ELF pull, command socket) while the program keeps running — today
that means push-blind-run-blind-power-cycle. The avenue: the future engine loads
its **own** IOP modules after the reset (SMAP/INET + a udptty-style console and a
small command channel), i.e. the game becomes its own debug target. Any work here
belongs to the game's technical design (game repo), not the bridge — the bridge
just needs to know how to talk to the game's channel once it exists.

## Upstream contribution path

**Status: blocked on licensing clarity + maintainer engagement — owner decision
2026-10-24 was consolidation here instead of a PR.** The value for upstream is
high (their stub is broken on real hardware; our five fixes are measured and
evidence-backed), and the kit is deliberately contribution-ready: one patch, five
logical commits' worth of hunks, wire logs as attachments. Before any PR:
normalize the in-code comment labels, split into logical commits, open an issue
with the evidence first. The upstream README's non-commercial clause also needs
reading by the owner before contributing.
