"""Core operations: source sync, build, artifacts, and ps2client (push/log/run).

Design notes (honest ones):
- All ps2client calls go through subprocess wrappers with hard timeouts.
  The PS2 is real hardware on a real LAN: it can be off, hung or rebooting,
  and the bridge must never block forever on it.
- Builds and console operations are serialized by a lock: the toolchain is
  CPU-bound and there is exactly one console.
- ps2link protocol (verified against the ps2client/ps2link/ps2sdk sources, 2026-10):
  `execee` is a fire-and-forget UDP command. The console PULLS the ELF from
  this host through the TCP request channel — hence the `host:` path prefix
  (without it the console looks for a file on ITS side and fails with
  "could not execute"). The loaded program runs as a THREAD of ps2link
  (ee/cmdHandler.c: pkoExecEE → SifLoadElf + CreateThread/StartThread), so
  ps2link stays resident underneath while it runs. What kills the console is
  the program EXITING: ps2sdk's crt0 turns `main` returning into the kernel's
  Exit() (ee/startup/src/crt0.c), which resets the console to its browser.
  `reset` (PKO_RESET → pkoReset) is the way back: ps2link terminates the
  user thread, resets the IOP and re-executes itself from RAM (IRXs embedded
  in its ELF, network config cached in .data) — the primitive behind
  reset_console() and the park + reset debug loop. While the console pulls,
  the program's stdout and ps2link's messages stream back to the SAME
  ps2client process on its UDP console socket during the idle-timeout window
  (-t; activity extends it). The protocol has a single netconsole UDP port,
  so two ps2clients at once are illegal (the second crashes the client) —
  all console steps are strictly sequential: probe → netdump → execee/reset.
- ps2client quirks we defend against (all observed on real hardware):
  SIGSEGV when a connection is refused, silent rc=0 give-ups, and TCP
  connects that block forever against a console with no ps2link. A short
  pre-flight TCP probe turns all of those into honest, fast errors.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from . import VERSION
from .config import Config

# One console, one toolchain: serialize the heavy operations.
_op_lock = threading.Lock()

ARTIFACT_EXTENSIONS = (".elf", ".iso")


def _run(cmd: list[str], timeout: int, cwd: str | None = None) -> tuple[int, str]:
    """Run a command, returning (exit_code, combined_output).

    On timeout the process is killed and the partial output collected so far
    is preserved (retrying communicate() after kill retrieves the buffer).
    """
    try:
        p = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
    except FileNotFoundError:
        return 127, f"[ps2dev-bridge] command not found: {cmd[0]}"
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        return 124, (out or "") + f"\n[ps2dev-bridge] timed out after {timeout}s: {' '.join(cmd)}"


def truncate(text: str, max_chars: int) -> str:
    """Keep head and tail of long logs, marked, so agents never lose the ends."""
    if len(text) <= max_chars:
        return text
    head = max_chars // 5
    tail = max_chars - head
    omitted = len(text) - max_chars
    return f"{text[:head]}\n[ps2dev-bridge] ... {omitted} characters truncated ...\n{text[-tail:]}"


# ---------------------------------------------------------------- sync


def sync_sources(cfg: Config, data: bytes) -> dict:
    """Extract a tar archive (gz/bz2/xz/plain) into the workspace.

    Security: refuses absolute paths, `..` traversal and any links/devices —
    only regular files and directories are allowed.
    """
    if len(data) > cfg.sync_max_bytes:
        raise ValueError(f"payload too large: {len(data)} bytes (max {cfg.sync_max_bytes})")
    workspace = Path(cfg.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    count = 0
    with tempfile.NamedTemporaryFile(suffix=".tar") as tmp:
        tmp.write(data)
        tmp.flush()
        with tarfile.open(tmp.name, mode="r:*") as tar:
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError(f"refusing unsafe path in archive: {member.name!r}")
                if member.issym() or member.islnk():
                    raise ValueError(f"refusing link in archive: {member.name!r}")
                if not (member.isfile() or member.isdir()):
                    continue
                try:
                    tar.extract(member, path=workspace, filter="data")
                except TypeError:
                    # Python < 3.12: no filter kwarg; the manual checks above apply.
                    tar.extract(member, path=workspace)
                count += 1
    return {"ok": True, "files": count, "workspace": str(workspace)}


# ---------------------------------------------------------------- build


def build(cfg: Config, clean: bool = False) -> dict:
    """Run `make` in the workspace (optionally `make clean` first)."""
    workspace = Path(cfg.workspace)
    if not (workspace / "Makefile").exists():
        return {
            "ok": False,
            "error": f"no Makefile in workspace {workspace} — POST /sync the project sources first",
        }
    with _op_lock:
        parts: list[str] = []
        if clean:
            _, clean_out = _run(["make", "clean", "-C", str(workspace)], timeout=cfg.build_timeout)
            parts.append(clean_out)
        rc, out = _run(["make", "-C", str(workspace)], timeout=cfg.build_timeout)
        parts.append(out)
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "log": truncate("\n".join(parts), cfg.max_log_chars),
    }


# ---------------------------------------------------------------- artifacts


def list_artifacts(cfg: Config) -> list[dict]:
    workspace = Path(cfg.workspace)
    items: list[dict] = []
    if workspace.exists():
        for p in sorted(workspace.rglob("*")):
            if p.is_file() and p.suffix.lower() in ARTIFACT_EXTENSIONS:
                st = p.stat()
                items.append({
                    "path": str(p.relative_to(workspace)),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })
    return items


def resolve_artifact(cfg: Config, name: str | None) -> Path:
    """Resolve an artifact by workspace-relative path, or the newest .elf if omitted."""
    workspace = Path(cfg.workspace)
    if name:
        p = (workspace / name).resolve()
        ws = workspace.resolve()
        if p != ws and ws not in p.parents:
            raise ValueError(f"path escapes the workspace: {name!r}")
        if not p.is_file():
            raise ValueError(f"file not found in workspace: {name!r}")
        return p
    candidates = [p for p in workspace.rglob("*.elf") if p.is_file()]
    if not candidates:
        raise ValueError("no .elf artifact in workspace — build first, or pass an explicit path")
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------- console (ps2client)


def _ps2client(cfg: Config, args: list[str], timeout: int) -> tuple[int, str]:
    cmd = [cfg.ps2client_bin]
    if cfg.ps2_host:
        cmd += ["-h", cfg.ps2_host]
    if timeout:
        cmd += ["-t", str(timeout)]
    # Hard cap a bit above ps2client's own idle timeout (-t) so IT exits first.
    # ps2client resolves `host:` paths relative to its own working directory
    # (its fix_pathname strips the leading slash), so pin cwd to "/" — that
    # makes `host:/work/foo.elf` always mean the real workspace file.
    return _run(cmd + args, timeout=timeout + 5, cwd="/")


def _probe_ps2link(cfg: Config) -> tuple[bool, str]:
    """Pre-flight TCP probe of ps2link's request port (3 s, no ps2client).

    Why this exists (observed on real hardware): ps2client SIGSEGVs when the
    connection is refused, and blocks indefinitely on a console whose network
    stack answers ping but has no ps2link listening. The probe turns both
    situations into an honest, actionable error before ps2client is spawned.
    """
    if not cfg.ps2_host:
        return False, "PS2_HOST is not configured on the bridge"
    try:
        with socket.create_connection((cfg.ps2_host, cfg.ps2link_port), timeout=3):
            return True, ""
    except OSError as e:
        return False, (
            f"cannot reach ps2link at {cfg.ps2_host}:{cfg.ps2link_port} ({e}) — "
            "is the console on and ps2link loaded? (ps2link stays alive while a "
            "program runs, but when that program EXITS, the console resets to "
            "its browser and ps2link dies — reload it on the console before the "
            "next run)"
        )


def _wait_for_ps2link(cfg: Config, want: bool, budget: int) -> bool:
    """Poll the probe until it matches `want` or `budget` seconds pass.

    Used by reset_console() to witness the console going down (old ps2link
    dying) and coming back (ps2link re-executed from RAM). A budget <= 0
    polls zero times and returns False.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        ok, _ = _probe_ps2link(cfg)
        if ok is want:
            return True
        time.sleep(1)
    return False


def push(cfg: Config, artifact: str | None = None) -> dict:
    """Send an ELF to the PS2: `ps2client execee host:<path>`.

    The console PULLS the file from this host through the TCP request channel
    during ps2client's idle window — so this call blocks for roughly
    push_timeout seconds even when everything works. ps2client's rc=0 only
    means the command was sent and the window served: the console screen (or
    a follow-up ps2_run/ps2_log) is the real evidence of execution.
    """
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    probe_ok, probe_err = _probe_ps2link(cfg)
    if not probe_ok:
        return {"ok": False, "error": probe_err}
    try:
        artifact_path = resolve_artifact(cfg, artifact)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with _op_lock:
        # Best effort: ask ps2link to echo stdout/exceptions to the network
        # console so execution problems show up in the window / ps2_log.
        _ps2client(cfg, ["netdump"], timeout=15)
        rc, out = _ps2client(cfg, ["execee", f"host:{artifact_path}"], timeout=cfg.push_timeout)
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "artifact": str(artifact_path.relative_to(Path(cfg.workspace))),
        "log": truncate(out, cfg.max_log_chars),
        "note": (
            "the ELF runs as a thread under ps2link, which stays resident. When "
            "the program EXITS (returns from main), the console resets to its "
            "browser and ps2link dies — park the program (sleep-forever loop) "
            "and use ps2_reset to close the loop."
        ),
    }


def console_log(cfg: Config, seconds: int | None = None) -> dict:
    """Capture `seconds` of output from the PS2 network console (`ps2client listen`).

    listen is idle-based: it returns after `seconds` without traffic (activity
    resets the counter). It binds the single netconsole port, so it must be
    the only ps2client running — the operation lock takes care of that.
    """
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    probe_ok, probe_err = _probe_ps2link(cfg)
    if not probe_ok:
        return {"ok": False, "error": probe_err}
    secs = seconds if seconds and seconds > 0 else cfg.log_seconds
    with _op_lock:
        rc, out = _ps2client(cfg, ["listen"], timeout=secs)
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "log": truncate(out, cfg.max_log_chars),
    }


def run_on_console(
    cfg: Config,
    artifact: str | None = None,
    seconds: int | None = None,
) -> dict:
    """Push an ELF to the PS2 and capture the console output while it runs.

    One ps2client process does the whole job, strictly sequential (the
    protocol has a single netconsole UDP port — a concurrent listener makes
    the second ps2client crash, so the old listener+execee design is gone):
      1. probe ps2link (honest fast failure when the console is unreachable);
      2. netdump (best effort; exits immediately) so stdout/exceptions are
         echoed to the network console;
      3. `execee host:<path> -t <window>` — the console pulls the ELF through
         this process's TCP channel and the netconsole stream (program
         output, ps2link messages, exception dumps) is printed by the SAME
         process until the window goes idle.
    `seconds` is the idle capture window (default 15): activity extends it,
    so it never truncates a pull that is still making progress. The response
    takes ≈ pull time + runtime + seconds of idle tail.
    """
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    probe_ok, probe_err = _probe_ps2link(cfg)
    if not probe_ok:
        return {"ok": False, "error": probe_err}
    try:
        artifact_path = resolve_artifact(cfg, artifact)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    secs = seconds if seconds and seconds > 0 else cfg.log_seconds
    window = max(secs, 10)  # keep the file-pull phase comfortably inside -t
    with _op_lock:
        _ps2client(cfg, ["netdump"], timeout=15)
        rc, out = _ps2client(cfg, ["execee", f"host:{artifact_path}"], timeout=window)
    # Post-run probe: which console state did the program leave behind?
    alive, _ = _probe_ps2link(cfg)
    if alive:
        note = (
            "the program is still resident on the console (it parked or is "
            "long-running) — ps2link stayed alive underneath. Call ps2_reset "
            "to reboot the console back into a fresh ps2link before the next "
            "run."
        )
    else:
        note = (
            "the program exited: ps2sdk's crt0 turns main() returning into "
            "the kernel's Exit(), which resets the console to its browser — "
            "ps2link is gone and must be reloaded on the console (or make "
            "the program park and use ps2_reset instead)."
        )
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "artifact": str(artifact_path.relative_to(Path(cfg.workspace))),
        "log": truncate(out, cfg.max_log_chars),
        "ps2link_alive": alive,
        "note": note,
    }


# ---------------------------------------------------------------- reset


def reset_console(cfg: Config) -> dict:
    """Reboot the console back into a fresh ps2link (`ps2client reset`).

    ps2link's PKO_RESET handler (pkoReset, ee/cmdHandler.c) terminates the
    user program's thread, resets the IOP and re-executes ps2link from RAM
    (ExecPS2 with its own startup; IRXs embedded in the ELF, network config
    cached in .data) — so the console re-enters ps2link with nobody touching
    the TV. `reset` is a fire-and-forget UDP command and the stock ps2client
    returns immediately after sending it (its main() forces timeout=0 for
    reset), so this function confirms the outcome itself: it first witnesses
    the probe go down (the old ps2link dying), then waits for it to answer
    again (ps2link back and listening).

    This closes the debug loop: end test programs with a park (sleep-forever
    loop) instead of returning — crt0 turns `main` returning into the
    kernel's Exit(), which resets the console to its browser — then the
    cycle is ps2_run → ps2_reset → ps2_run, fully unattended.
    """
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    probe_ok, probe_err = _probe_ps2link(cfg)
    if not probe_ok:
        return {"ok": False, "error": probe_err}
    with _op_lock:
        # -t is irrelevant here (ps2client forces 0 for reset); 10s is only
        # the hard cap in case the client misbehaves against a dying console.
        rc, out = _ps2client(cfg, ["reset"], timeout=10)
    log = truncate(out, cfg.max_log_chars)
    if rc != 0:
        return {
            "ok": False,
            "exit_code": rc,
            "log": log,
            "error": (
                f"ps2client reset failed (exit {rc}) — the reset command may "
                "not have been delivered; console state is unknown, probe it "
                "with ps2_status"
            ),
        }
    went_down = _wait_for_ps2link(cfg, want=False, budget=cfg.reset_down_wait)
    if not went_down:
        alive, _ = _probe_ps2link(cfg)
        return {
            "ok": False,
            "exit_code": rc,
            "log": log,
            "ps2link_alive": alive,
            "note": (
                "the reset command was sent but ps2link did not go down "
                f"within {cfg.reset_down_wait}s — it may not have taken effect "
                "(lost UDP packet, or a ps2link build without the reset "
                "handler). If a program is parked on the console it is still "
                "resident: retry ps2_reset or check the console screen."
            ),
        }
    came_back = _wait_for_ps2link(cfg, want=True, budget=cfg.reset_up_wait)
    if came_back:
        return {
            "ok": True,
            "exit_code": rc,
            "log": log,
            "ps2link_alive": True,
            "note": (
                "ps2link rebooted and is listening again — the console is "
                "ready for the next ps2_run."
            ),
        }
    alive, _ = _probe_ps2link(cfg)
    return {
        "ok": False,
        "exit_code": rc,
        "log": log,
        "ps2link_alive": alive,
        "note": (
            "the reset took the console down but ps2link did not answer "
            f"again within {cfg.reset_up_wait}s — it may still be rebooting "
            "(wait a few seconds and probe again) or the re-exec failed; "
            "check the console screen."
        ),
    }


# ---------------------------------------------------------------- status


def status(cfg: Config) -> dict:
    workspace = Path(cfg.workspace)
    disk_target = workspace if workspace.exists() else workspace.parent
    try:
        free = shutil.disk_usage(disk_target if str(disk_target) else "/").free
    except OSError:
        free = 0
    return {
        "ok": True,
        "version": VERSION,
        "workspace": str(workspace),
        "ps2_host": cfg.ps2_host,
        "ps2link_port": cfg.ps2link_port,
        "api_key_configured": bool(cfg.api_key),
        "tools": {
            "make": shutil.which("make") is not None,
            "ps2client": shutil.which(cfg.ps2client_bin) is not None,
            # The official ps2dev image ships the triplet-named cross-compiler
            # (verified in-image: mips64r5900el-ps2-elf-gcc); keep the classic
            # ee-gcc alias as a fallback for older toolchain layouts.
            "ee-gcc": (
                shutil.which("ee-gcc")
                or shutil.which("mips64r5900el-ps2-elf-gcc")
            )
            is not None,
        },
        "artifacts": list_artifacts(cfg),
        "disk_free_bytes": free,
    }
