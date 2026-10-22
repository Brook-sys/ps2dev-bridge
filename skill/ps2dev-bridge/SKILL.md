---
name: ps2dev-bridge
description: Build, deploy and debug PlayStation 2 homebrew on a real console through the ps2dev-bridge service (MCP tools + REST API). Use when the task involves PS2 development — compiling ELF homebrew with the ps2dev toolchain, pushing builds to a PS2 running ps2link, or reading console output and crash logs.
---

# PS2 dev bridge — debug loop for AI agents

The bridge exposes the full PS2 toolchain (from the official `ps2dev` container)
plus `ps2client` console access over MCP and REST. It lives on the same LAN as
the console. The entire debug loop is 3–4 calls:

```
1. ps2_status        (sanity: tools present, sources synced, console configured)
2. sync sources      (tar upload — see below)
3. ps2_build         (make; full log returns in the response)
4. ps2_run           (push ELF + capture console output while it runs)
   → read log, find the bug, edit code, go back to 2
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

## How the console side works (read once — verified against ps2client src)

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
- **ps2link dies with every program it executes** (the ELF replaces it in EE
  RAM). After each successful run, ask the user to reload ps2link on the
  console before the next run. The TV is the ground truth: it shows
  `executing file ...` and the program output.

## MCP tools

| Tool | What it does |
|---|---|
| `ps2_status()` | Config, toolchain presence (`make`, `ps2client`, `ee-gcc`), artifacts, free disk. Call first. |
| `ps2_build(clean?)` | `make` in the workspace; returns `exit_code` + full (head+tail truncated) log. |
| `ps2_push(artifact?)` | `ps2client execee host:<path>` — console pulls the ELF and runs it. Default artifact: newest `.elf`. Blocks ≈ `PS2BRIDGE_PUSH_TIMEOUT` (60 s). |
| `ps2_log(seconds?)` | `ps2client listen` — captures the netconsole stream for an idle window (default 15 s). |
| `ps2_run(artifact?, seconds?)` | Probe → netdump → push+capture in one sequential flow; `log` = netconsole stream. The workhorse. |

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

## Reading the results

- Build failed → the full compiler output is in `log`; fix the code, re-sync, rebuild.
- `ok: false` with `PS2_HOST is not configured` → the bridge has no console IP;
  that's a deployment problem (env `PS2_HOST`), not a code problem.
- `ok: false` with `cannot reach ps2link` → the pre-flight probe failed: the
  console is off, or ps2link isn't loaded (maybe because the previous run's
  program replaced it). Ask the user to check the console and reload ps2link.
- `ps2_run` ok=true but `log` is empty → either the program printed nothing
  in the window, or it never ran (the TV screen says which — `executing file`
  + program output means it ran). Retry with more seconds if the program may
  print later; if the TV shows `could not execute`, the ELF itself was
  rejected by the loader — inspect it (`GET /artifacts/{path}` + readelf).
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
- **After each executed program, ps2link is gone** — the user must reload it
  before the next run. Plan your loop: one run, then ask for a reload.
- `ps2_push`/`ps2_run` block for the whole idle window even on success —
  that's how the pull/capture protocol works, not a hang.
- Long builds are capped by `PS2BRIDGE_BUILD_TIMEOUT` (default 900 s).
- If the host running the bridge reboots, in-flight operations die — re-run.
- Logs longer than `PS2BRIDGE_MAX_LOG` chars are head+tail truncated with a
  marker line; the ends (where errors live) are always preserved.
