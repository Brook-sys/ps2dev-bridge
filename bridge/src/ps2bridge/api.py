"""Application assembly: MCP tools + REST routes on a single Starlette app.

Layout of the service:

    ┌────────────────────────────────────────────────┐
    │ Starlette (one port)                           │
    │  ├── REST routes: /health /sync /build         │
    │  │   /artifacts /push /log /run /reset         │
    │  │   /symbolize                                 │
    │  └── Mount("/") → FastMCP http_app  → POST /mcp│
    │  middleware: X-API-Key on everything but /health│
    └────────────────────────────────────────────────┘
"""

from __future__ import annotations

import hmac

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route

from . import core
from .config import Config

PUBLIC_PATHS = {"/health"}


class ApiKeyMiddleware:
    """Pure-ASGI middleware: require X-API-Key everywhere except /health.

    Written at the raw ASGI level (instead of BaseHTTPMiddleware) so it never
    interferes with the SSE/streaming responses used by the MCP transport.
    """

    def __init__(self, app, api_key: str | None):
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "") in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        if not self._api_key:
            response = JSONResponse(
                {"ok": False, "error": "PS2BRIDGE_API_KEY is not configured on the bridge"},
                status_code=503,
            )
            await response(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        provided = headers.get("x-api-key", "")
        if not hmac.compare_digest(provided, self._api_key):
            response = JSONResponse(
                {"ok": False, "error": "invalid or missing X-API-Key header"},
                status_code=401,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _b(value: str | None) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


def create_app(cfg: Config) -> Starlette:
    mcp = FastMCP("ps2dev-bridge")

    # ------------------------------------------------------------ MCP tools

    @mcp.tool
    def ps2_status() -> dict:
        """Bridge status: config, toolchain presence, artifacts, free disk.

        Call this first: it tells you whether sources are synced (artifacts
        present), whether the console (PS2_HOST) is configured, and which
        tools (make, ps2client, ee-gcc) are available.
        """
        return core.status(cfg)

    @mcp.tool
    def ps2_build(clean: bool = False) -> dict:
        """Run `make` in the synced workspace and return the full build log.

        Use clean=True for a from-scratch rebuild. ok=True means the build
        succeeded; exit_code and the complete (possibly truncated) log are
        always included.
        """
        return core.build(cfg, clean=clean)

    @mcp.tool
    def ps2_push(artifact: str | None = None) -> dict:
        """Send an ELF to the PS2 console: `ps2client execee host:<path>` via ps2link.

        The console PULLS the file from the bridge during ps2client's idle
        window, so the call blocks roughly push_timeout seconds. artifact is
        a workspace-relative path; when omitted, the newest .elf is used.
        ok=true only means the command was sent and the window served — the
        console screen or a follow-up ps2_run log is the real execution
        evidence. A probe fails fast (ok=false) when ps2link is unreachable.
        """
        return core.push(cfg, artifact)

    @mcp.tool
    def ps2_log(seconds: int | None = None) -> dict:
        """Capture output from the PS2 network console (ps2client listen).

        seconds is the idle window to wait for console output (default 15) —
        activity extends it. Fails fast when ps2link is unreachable.
        """
        return core.console_log(cfg, seconds)

    @mcp.tool
    def ps2_run(artifact: str | None = None, seconds: int | None = None) -> dict:
        """Push an ELF to the PS2 and capture the netconsole output while it runs.

        The debug-loop workhorse. One sequential ps2client flow: probe
        ps2link → netdump (echo stdout/exceptions) → execee host:<path>,
        where the same process serves the console's file pull AND prints the
        netconsole stream. `log` holds that stream (program output, ps2link
        messages, exception dumps). seconds is the idle capture window
        (default 15); the response takes ≈ pull + runtime + seconds. The
        program runs as a ps2link thread: if it parks (ends with a
        sleep-forever loop) ps2link stays alive and `ps2link_alive` in the
        response is true — then call ps2_reset to reboot the console back
        into a fresh ps2link. If the program exits, the console resets to
        its browser and ps2link must be reloaded by hand.
        If the program CRASHES, ps2link's exception handler dumps the
        registers into the log and the response carries a structured
        `crash` object (cpu, type, cause, badvaddr, status, epc, ra, sp —
        and `symbols` with function + file:line for EPC/RA when the ELF
        was built with debug info). After a crash ps2link is alive but the
        exec slot stays occupied: ps2_reset is required before the next run.
        """
        return core.run_on_console(cfg, artifact, seconds)

    @mcp.tool
    def ps2_symbolize(artifact: str | None = None, addrs: list[str] | None = None) -> dict:
        """Map raw console addresses to source lines with the toolchain's addr2line.

        Pass one or more 0x-prefixed hex addresses (EPC and ra from a crash
        dump, or any address from a map) and get function + file:line for
        each. artifact defaults to the newest .elf in the workspace; it must
        have been built with debug info (-gdwarf-2) for lines to resolve —
        '??' means no debug info at that address. ps2_run already calls this
        automatically for a detected crash's EPC/RA; use it for any other
        address you want to pin down.
        """
        return core.symbolize(cfg, artifact, addrs)

    @mcp.tool
    def ps2_reset() -> dict:
        """Reboot the PS2 console back into a fresh ps2link (`ps2client reset`).

        ps2link terminates the running program, resets the IOP and re-executes
        itself from RAM — no human needed at the console. The bridge watches
        the probe go down and come back, and only reports ok=true when ps2link
        is listening again (the whole call takes ≈ 5–40 s). Call it after a
        ps2_run whose program parks, to close the loop run → reset → run, or
        to recover a wedged console. It only works while ps2link is alive:
        if the last program exited, the console is on its browser and this
        fails with an honest error — ps2link must be reloaded on the console.
        """
        return core.reset_console(cfg)

    # ------------------------------------------------------------ REST routes

    async def health(request: Request):
        return JSONResponse(core.status(cfg))

    async def sync(request: Request):
        length = request.headers.get("content-length")
        if length and int(length) > cfg.sync_max_bytes:
            return JSONResponse(
                {"ok": False, "error": f"payload too large (> {cfg.sync_max_bytes} bytes)"},
                status_code=413,
            )
        data = await request.body()
        try:
            result = await run_in_threadpool(core.sync_sources, cfg, data)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return JSONResponse(result)

    def build_ep(request: Request):
        clean = _b(request.query_params.get("clean"))
        return JSONResponse(core.build(cfg, clean=clean))

    def artifacts_list(request: Request):
        return JSONResponse({"ok": True, "artifacts": core.list_artifacts(cfg)})

    def artifacts_download(request: Request):
        name = request.path_params.get("path", "")
        if not name:
            return JSONResponse({"ok": False, "error": "missing path"}, status_code=400)
        try:
            path = core.resolve_artifact(cfg, name)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        return FileResponse(path, filename=path.name)

    def push_ep(request: Request):
        artifact = request.query_params.get("artifact") or None
        return JSONResponse(core.push(cfg, artifact))

    def log_ep(request: Request):
        raw = request.query_params.get("seconds")
        seconds = int(raw) if raw and raw.isdigit() else None
        return JSONResponse(core.console_log(cfg, seconds))

    def run_ep(request: Request):
        artifact = request.query_params.get("artifact") or None
        raw = request.query_params.get("seconds")
        seconds = int(raw) if raw and raw.isdigit() else None
        return JSONResponse(core.run_on_console(cfg, artifact, seconds))

    def reset_ep(request: Request):
        return JSONResponse(core.reset_console(cfg))

    def symbolize_ep(request: Request):
        artifact = request.query_params.get("artifact") or None
        addrs = request.query_params.getlist("addr")
        return JSONResponse(core.symbolize(cfg, artifact, addrs))

    # ------------------------------------------------------------ assembly

    mcp_app = mcp.http_app(path="/mcp")

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/sync", sync, methods=["POST"]),
        Route("/build", build_ep, methods=["POST"]),
        Route("/artifacts", artifacts_list, methods=["GET"]),
        Route("/artifacts/{path:path}", artifacts_download, methods=["GET"]),
        Route("/push", push_ep, methods=["POST"]),
        Route("/log", log_ep, methods=["POST"]),
        Route("/run", run_ep, methods=["POST"]),
        Route("/reset", reset_ep, methods=["POST"]),
        Route("/symbolize", symbolize_ep, methods=["POST"]),
        # Catch-all: everything else belongs to the MCP streamable-HTTP app
        # (endpoint: POST /mcp). Must come last.
        Mount("/", app=mcp_app),
    ]

    return Starlette(
        routes=routes,
        middleware=[Middleware(ApiKeyMiddleware, api_key=cfg.api_key)],
        # Required for Streamable HTTP: without passing the MCP app lifespan,
        # the FastMCP session manager never initializes.
        lifespan=mcp_app.lifespan,
    )


def main() -> None:
    cfg = Config.from_env()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)
