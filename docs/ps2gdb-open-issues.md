# ps2gdb — open issues (known, measured, deliberately unfixed)

Per the owner's directive (2026-10-24): these are **documented, not patched** —
either the root cause is unproven, or a fix exists but was never tested, or the
behavior contradicts a previous measurement. Nothing here should be "fixed" in
code without a new validation cycle.

## 1. The strand — ps2link dies 20–40 min after any ps2ips boot (6/6)

- **Measured:** every boot that loads `ps2ips.irx` (the stub's TCP dependency)
  kills ps2link within ~20–40 min — with or without a successful gdb session, with
  or without the program parking. Six of six boots.
- **Workaround (validated):** treat it as a hard per-boot budget — plan the whole
  gdb cycle inside ~20 min; a parked program keeps running on screen after the
  network death, so the TV survives, but the bridge loses all transport.
- **Root cause: UNKNOWN.** Suspects (not discriminated): IOP resource conflict
  between udptty and the second TCP/IP stack, socket/buffer leak in ps2ips, or an
  lwIP timer failure. Needs a dedicated experiment (IOP-side logging before
  death) — never done.
- **Recovery:** manual power-cycle + ps2link reload. No remote path exists once
  the request socket is dead.

## 2. ps2ips single-`send()` ≥ ~1 KiB never delivers — exact mechanism open

- **Measured:** ≤517-byte stub replies always cross; ≥1028-byte sends deliver
  zero bytes and kill the connection. The hardened stub's ≤512-byte chunked sends
  work in every direction tested (the validated workaround, in `fixes.patch`).
- **Structural pointer (source, not runtime-proven):** ps2ips clamps to
  `BUFF_SIZE=1024` and copies into a 1056-byte IOP buffer at offset 64 — an
  aligned send >992 B overflows it.
- **Open question:** which lwIP error actually occurs (`ERR_MEM` from the copy?
  a silent truncation? a pbuf chain failure?) — never instrumented. Fixing
  **ps2ips itself** (e.g. larger buffers, scatter/gather) is **untested work** and
  stays out of the code until measured.

## 3. Black screen after a clean exit (deviation from the 2026-10-23 baseline)

- **Expected (measured 2026-10-23):** a program returning from `main()` triggers
  crt0's `Exit()` → console resets to its **native browser**.
- **Observed (2026-10-24, cycle 2):** after the marker program's clean exit, the
  screen went **black** and the ps2link probe failed (rc=11). One occurrence.
- **Status: open.** Not reproducible yet, not explained. The TV-proof demo
  deliberately **parks instead of exiting** to avoid this ambiguity — it sidesteps
  the question, it does not answer it.

## 4. `ps2_reset` re-exec failure after a ps2ips boot (2026-10-24)

- **Event:** with gdbtv parked (ps2ips loaded, strand window elapsed), a bridge
  `POST /reset` took the console down **but ps2link never came back** — not within
  the endpoint's 30 s internal wait, not minutes later (probe rc=11 throughout).
  Evidence: `ps2gdb/evidence/reset-2026-10-24-reexec-failed.json`. Required a
  manual power-cycle.
- **Context:** in clean (non-ps2ips) boots the reset has been reliable across many
  measured cycles (the hello/heavy run-reset loops). Plausibly strand-related
  (the re-exec races a degraded IOP) — **unproven**.
- **Operational consequence (documented in SKILL.md):** after a gdb/ps2ips boot,
  treat `ok:false "did not answer again"` as "possibly dead for good": re-probe
  after ~30 s, then expect a manual power-cycle.

## 5. Reconnect-after-final-continue is attach-to-a-moving-target

After a final `continue` the stub parks in `accept()` and a fresh gdb connection
re-enters without a reboot (measured) — but the **program keeps running** while
gdb reads it, so any "stop" the new session observes is just wherever the program
happened to break next. One program, one meaningful session per boot is the
reliable pattern; everything after the final `continue` is best-effort.

## 6. Upstream licensing (constraints on what we ship)

- `ps2dev/ps2gdb` has **no LICENSE file** (GitHub: `license: null`). Its README
  carries a custom © 2003 Ray Donnelly notice: BSD-style conditions **plus**
  "use … for academic, research and non-commercial purposes only", and the author
  retains a non-exclusive royalty-free license to licensee modifications.
- **Consequences (already applied):** the stub source is **not vendored** into this
  MIT-licensed repository; the fixes ship as `fixes.patch` + a reconstruction kit
  (see `ps2gdb/README.md`). An upstream PR remains possible in principle — it
  would need maintainer engagement and license clarity first.

## 7. Upstream claims vs. measured reality

The upstream README (2024, "Current State by crt0") claims
*"Writing/Reading variables, memory, registers (only as 32bit), call stack —
works."* On real hardware, upstream `b1f3428` **cannot complete a session** — it
hangs on the first `g` reply (see `docs/ps2gdb-validation.md`, "Size ladder").
The five fixes are what makes the claimed functionality actually true.
