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
   → read console_log, find the bug, edit code, go back to 2
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

## MCP tools

| Tool | What it does |
|---|---|
| `ps2_status()` | Config, toolchain presence (`make`, `ps2client`, `ee-gcc`), artifacts, free disk. Call first. |
| `ps2_build(clean?)` | `make` in the workspace; returns `exit_code` + full (head+tail truncated) log. |
| `ps2_push(artifact?)` | `ps2client execee` — uploads and executes an ELF on the console. Default artifact: newest `.elf`. |
| `ps2_log(seconds?)` | `ps2client listen` — captures output from the PS2 network console. |
| `ps2_run(artifact?, seconds?)` | Push + listen while it runs. `push_log` + `console_log` in one response. The workhorse. |

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
- `ps2_run` returned but `console_log` is empty → either the program printed
  nothing, or the console's ps2link did not echo to the network console.
  Try `ps2_log` again with more seconds; if still empty, ask the user to
  check the console is on and ps2link is running.
- Push timed out (exit_code 124) → console off/unreachable, or ps2link hung.
  One console only: console ops are serialized; don't fire them in parallel.
- `GET /artifacts/{path}` is how you bring an ELF back to the agent machine.

## Cautions

- One build at a time and one console op at a time (the bridge enforces a lock).
- Long builds are capped by `PS2BRIDGE_BUILD_TIMEOUT` (default 900 s).
- If the host running the bridge reboots, in-flight operations die — re-run.
- Logs longer than `PS2BRIDGE_MAX_LOG` chars are head+tail truncated with a
  marker line; the ends (where errors live) are always preserved.
