# ps2dev-bridge — knowledge base

Established 2026-10-24 under the owner's directive: **only duly tested behavior
gets implemented** (and pushed); anything untested, buggy or unexplained is
**documented instead**, so future work starts from what is already known.

| Area | Status | Where |
|---|---|---|
| GDB stub — 5 fixes over upstream `b1f3428` | **implemented** as a cryptographically pinned patch; validated offline (real gdb vs fault-injecting fake stub) and on hardware (2 full sessions, one owner-verified on the TV) | [`ps2gdb/`](../ps2gdb/) |
| Reconstruction kit (apply + build from pinned upstream) | **implemented and tested** end-to-end in the toolchain container (2026-10-24) | `ps2gdb/apply-and-build.sh`, `ps2gdb/apply-patch.py` |
| Session drivers (wire session, TV-proof session, ps2link probe) | **implemented** — the exact command sequences of the validated sessions, host/paths parameterized | `ps2gdb/scripts/` |
| Raw evidence of the validated sessions | **preserved** (wire logs, netconsole captures, bridge responses) | `ps2gdb/evidence/` |
| Validation record (labs, cycles 2–3, anti-forgery proof, owner confirmation) | **documented** | [`ps2gdb-validation.md`](ps2gdb-validation.md) |
| Open issues — strand, ps2ips ≥1028 B root cause, black-screen-after-exit, reset re-exec failure, upstream licensing | **documented, deliberately not "fixed"** (root causes unproven; untested fixes stay out of the code) | [`ps2gdb-open-issues.md`](ps2gdb-open-issues.md) |
| Planned work — unattended-loop experiment (EXP-011), MCP gdb integration, ps2client rebuild, IOP-reset-proof transport | **planned/documented only** | [`future-work.md`](future-work.md) |

Skill-level operational knowledge (park+reset loop, crash parsing, RSP etiquette)
lives in [`skill/ps2dev-bridge/SKILL.md`](../skill/ps2dev-bridge/SKILL.md).
