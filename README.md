# ps2dev-bridge

[![build](https://github.com/Brook-sys/ps2dev-bridge/actions/workflows/build.yml/badge.svg)](https://github.com/Brook-sys/ps2dev-bridge/actions/workflows/build.yml)

**MCP + REST debug bridge for PlayStation 2 homebrew development**, built as a
derived image of the official [`ghcr.io/ps2dev/ps2dev`](https://github.com/ps2dev/ps2dev)
toolchain container. It turns the classic PS2 debug loop into 3–4 API calls so
that AI agents (or humans with `curl`) can build, push and observe homebrew on a
**real console** without ever opening a shell on the container host.

```
edit code → ps2_build → ps2_run (push via ps2link + capture console log) → read log → repeat
```

## How it works

```
┌ your machine ────────┐                    ┌ container host (same LAN as the PS2) ─┐
│ AI agent / curl      │                    │ ghcr.io/brook-sys/ps2dev-bridge        │
│  MCP client  ───────┼── X-API-Key ──────►│  ├ REST: /health /sync /build          │
│  skill (UX layer)   │                    │  │   /artifacts /push /log /run         │
└──────────────────────┘                    │  ├ MCP:   POST /mcp (streamable HTTP) │
                                            │  └ ps2dev toolchain + ps2client        │
                                            │       workspace volume: /work          │
                                            └────────────┬───────────────────────────┘
                                                         │ LAN (ps2link protocol)
                                                   ┌─────▼─────┐
                                                   │  PS2 real │
                                                   └───────────┘
```

The base image ships the complete environment — EE/IOP/DVP toolchains,
ps2sdk, gsKit, ps2sdk-ports, ps2-packer and `ps2client` (per the
[ps2dev release notes](https://github.com/ps2dev/ps2dev/releases)). This image
adds one small Python service (FastMCP + Starlette) that exposes it over
HTTP. No `docker.sock`, no SSH into the host, no second container.

## Tools

MCP (registered server or direct streamable HTTP at `/mcp`):

| Tool | Does what |
|---|---|
| `ps2_status()` | Config, toolchain presence, artifacts, free disk |
| `ps2_build(clean?)` | `make` in the workspace, full log in the response |
| `ps2_push(artifact?)` | `ps2client execee` — upload + execute an ELF on the console |
| `ps2_log(seconds?)` | `ps2client listen` — capture the PS2 network console |
| `ps2_run(artifact?, seconds?)` | Push + listen while it runs (push_log + console_log) |

REST mirror (same operations, for scripts/CI):

```
GET  /health                     # no auth — liveness + status
POST /sync                       # body = tar stream of the project sources
POST /build?clean=1
GET  /artifacts                  # list .elf/.iso
GET  /artifacts/{path}          # download any workspace file
POST /push?artifact=...
POST /log?seconds=...
POST /run?artifact=...&seconds=...
```

Everything except `/health` requires `X-API-Key`.

## Quickstart

```sh
cp .env.example .env      # set PS2BRIDGE_API_KEY and PS2_HOST (console IP)
docker compose up -d
curl http://localhost:17800/health
```

Sync sources and run a build:

```sh
tar czf - . | curl -sS -H "X-API-Key: $PS2BRIDGE_API_KEY" \
  --data-binary @- http://localhost:17800/sync

curl -sS -H "X-API-Key: $PS2BRIDGE_API_KEY" -X POST http://localhost:17800/build
```

Register the MCP server in your client (OpenCode example — remote server with
static headers):

```jsonc
"ps2dev-bridge": {
  "type": "remote",
  "url": "http://<bridge-host>:17800/mcp",
  "headers": { "X-API-Key": "<your-key>" }
}
```

An OpenCode-ready skill lives in [`skill/ps2dev-bridge/`](skill/ps2dev-bridge/SKILL.md).

## Console requirements

- A PS2 on the same LAN as the bridge host, running **ps2link** (e.g. loaded
  via FreeMcBoot), reachable by IP. `ps2client`'s default port conventions
  apply; the console IP is configured once via `PS2_HOST`.
- `ps2_push`/`ps2_run` call `netdump` (best effort) so ps2link exceptions are
  echoed to the network console and show up in `console_log`.

## Security model (read this before exposing the port)

- Auth: static `X-API-Key` header, constant-time comparison, enforced at the
  ASGI layer on every route (MCP included) except `/health`.
- The service executes only a fixed set of commands (`make`, `ps2client`,
  `tar` extraction). There is deliberately **no** shell-out endpoint.
- Source upload is hardened: tar members with absolute paths, `..` segments,
  links or device nodes are rejected; payload size is capped
  (`PS2BRIDGE_SYNC_MAX`, default 512 MB).
- All console/toolchain calls have hard timeouts — a hung PS2 can never hang
  the bridge.
- **The container runs as root** (the ps2dev toolchain expects it). Treat the
  API key as root-equivalent for anything inside the workspace: bind the
  published port to a trusted interface (see the commented example in
  `compose.yaml`) and keep the key secret.
- No `docker.sock`, no `--privileged`, no host mounts besides the workspace
  volume.

## Limitations (honest ones)

- **Unvalidated against real hardware so far**: unit tests cover the service
  (auth, sync, REST, MCP handshake), but the `ps2client` push/listen
  semantics (`netdump` echo, log completeness, idle-timeout behavior) get
  validated on the first console session. Expect small adjustments.
- One console, one build at a time (enforced by a lock; a second call waits).
- `ps2_run` always waits out the full capture window (`seconds`, default 15)
  before responding — there is no "exit early on program end" detection yet.
- ELF/ps2link flow only; ISO→OPL deployment is out of scope for now.
- `linux/amd64` only (that's what the toolchain hosts run).
- Over-long logs are head+tail truncated (~40 KB default) — the ends, where
  errors live, are always preserved.

## Development

```sh
python -m pip install ./bridge
python -m pip install pytest httpx
python -m pytest bridge/tests -v
```

CI (`.github/workflows/build.yml`) runs the same tests on every PR and push,
then builds and publishes `ghcr.io/brook-sys/ps2dev-bridge` (tags `vX.Y.Z`,
`vX.Y` and `latest` on the default branch). The base image tag is pinned in
the `Dockerfile` — bump it deliberately.

## Credits

- [ps2dev](https://github.com/ps2dev) — the entire toolchain, container image
  and `ps2client`. This project is a thin bridge on top of their work.
- [FastMCP](https://github.com/PrefectHQ/fastmcp) — MCP server framework.

## License

[MIT](LICENSE)
