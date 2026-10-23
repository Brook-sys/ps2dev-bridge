# ps2dev-bridge

[![build](https://github.com/Brook-sys/ps2dev-bridge/actions/workflows/build.yml/badge.svg)](https://github.com/Brook-sys/ps2dev-bridge/actions/workflows/build.yml)

**MCP + REST debug bridge for PlayStation 2 homebrew development**, built as a
derived image of the official [`ghcr.io/ps2dev/ps2dev`](https://github.com/ps2dev/ps2dev)
toolchain container. It turns the classic PS2 debug loop into 3–4 API calls so
that AI agents (or humans with `curl`) can build, push and observe homebrew on a
**real console** without ever opening a shell on the container host.

```
edit code → ps2_build → ps2_run (push via ps2link + capture console log)
          → read log → ps2_reset (console reboots back into ps2link) → repeat
```

With programs that **park** at the end (sleep-forever loop instead of
returning from `main`), this loop runs unattended: no one has to touch the
console between runs.

## How it works

```
┌ your machine ────────┐                    ┌ container host (same LAN as the PS2) ─┐
│ AI agent / curl      │                    │ ghcr.io/brook-sys/ps2dev-bridge        │
│  MCP client  ───────┼── X-API-Key ──────►│  ├ REST: /health /sync /build          │
│  skill (UX layer)   │                    │  │   /artifacts /push /log /run /reset │
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
| `ps2_push(artifact?)` | `ps2client execee host:<path>` — the console pulls the ELF from the bridge and runs it |
| `ps2_log(seconds?)` | `ps2client listen` — capture the PS2 network console |
| `ps2_run(artifact?, seconds?)` | Probe → netdump → push + netconsole capture in one sequential flow; response includes `ps2link_alive` |
| `ps2_reset()` | `ps2client reset` — reboot the console back into a fresh ps2link (watches it go down and come back) |

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
POST /reset
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
  via FreeMcBoot), reachable by IP. The console IP is configured once via
  `PS2_HOST`; ps2link's request port (TCP 18193 by default) can be overridden
  with `PS2BRIDGE_PS2LINK_PORT`.
- Protocol (verified against the ps2client sources and real hardware):
  `execee host:<path>` is fire-and-forget — **the console pulls the ELF from
  the bridge** over the request channel, and the netconsole stream (program
  stdout, ps2link messages, exception dumps) flows back to the same
  ps2client process. The bridge pre-probes the console so a missing ps2link
  fails fast with an honest error.
- `ps2_push`/`ps2_run` send `netdump` (best effort) so ps2link echoes
  stdout/exceptions to the network console.
- **Netconsole transport (why `18194/udp` must be published):** the console
  sends its whole console stream as a **UDP broadcast to
  255.255.255.255:18194**. udptty (ps2sdk `iop/network/udptty`) implements
  the `tty:` device that all program stdout goes to, and every write is
  broadcast. The bridge container therefore publishes `18194/udp` —
  docker-proxy on the host receives the broadcasts and relays them into the
  container, where ps2client's console socket picks them up. Without that
  publish, `ps2_run`/`ps2_log` succeed but every log comes back **empty**;
  the publish must be unscoped (a host-IP-scoped publish never matches
  broadcast packets).
- **Program lifecycle (verified against the ps2sdk/ps2link sources):** the
  executed ELF runs as a *thread* of ps2link, which stays resident underneath.
  When the program **exits** (returns from `main`), ps2sdk's crt0 calls the
  kernel's `Exit()`, which resets the console to its browser and ps2link dies.
  To keep the loop unattended, end test programs with a **park** (sleep-forever
  loop, e.g. `SleepThread();`) and call `ps2_reset` after each run: ps2link's
  reset handler terminates the program, resets the IOP and re-executes itself
  from RAM, so the console is back at a fresh ps2link without anyone touching
  it. Programs that exit instead need a manual ps2link reload on the console.
- One console op at a time; the netconsole is a single UDP port, so concurrent
  ps2client invocations are illegal (the bridge serializes its own, and
  nothing else should run one meanwhile).

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
- **UDP 18194 is published on all interfaces** (required — the PS2 netconsole
  is a link-layer broadcast, see the console-requirements section). Anything
  on the LAN can send UDP packets to that port; the worst case is fake text
  injected into a captured log. The TCP API port (17800) is the one that
  matters: bind it to a trusted interface (see the commented example in
  `compose.yaml`) and keep the key secret.
- **The container runs as root** (the ps2dev toolchain expects it). Treat the
  API key as root-equivalent for anything inside the workspace: bind the
  published port to a trusted interface (see the commented example in
  `compose.yaml`) and keep the key secret.
- No `docker.sock`, no `--privileged`, no host mounts besides the workspace
  volume.

## Limitations (honest ones)

- Protocol behavior (ps2link ports 18193/18194, `host:` pull, single
  netconsole, idle-window timeouts, program-exits-→-console-reset lifecycle,
  and the udptty **broadcast** transport) was verified against the
  ps2client/ps2link/ps2sdk sources and a real console in Oct/2026 —
  including the empty-log failure mode when `18194/udp` is not published
  (broadcasts stop at the host; docker bridge containers never see them).
- The ps2client binary shipped in the base image predates the upstream
  `TCP_NODELAY` fix, so the console's file pull can be slow; and ps2client
  itself SIGSEGVs on refused connections / concurrent instances. The bridge
  mitigates both (pre-flight probe + strictly sequential ops) but cannot fix
  the client; replacing it with a fresh build is a deliberate future option.
- One console, one build at a time (enforced by a lock; a second call waits).
- `ps2_push`/`ps2_run` block for the whole idle window even on success —
  that's how the pull/capture protocol works.
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
then builds and publishes `ghcr.io/brook-sys/ps2dev-bridge` with tags `X.Y.Z`,
`X.Y` and `latest` (image tags drop the `v` prefix — standard Docker
convention; git tags keep it). The base image tag is pinned in the `Dockerfile`
— bump it deliberately.

## Credits

- [ps2dev](https://github.com/ps2dev) — the entire toolchain, container image
  and `ps2client`. This project is a thin bridge on top of their work.
- [FastMCP](https://github.com/PrefectHQ/fastmcp) — MCP server framework.

## License

[MIT](LICENSE)
