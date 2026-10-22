"""Core operations: source sync, build, artifacts, and ps2client (push/log/run).

Design notes (honest ones):
- All ps2client calls go through subprocess wrappers with hard timeouts.
  The PS2 is real hardware on a real LAN: it can be off, hung or rebooting,
  and the bridge must never block forever on it.
- Builds and console operations are serialized by a lock: the toolchain is
  CPU-bound and there is exactly one console.
- `run_on_console` mirrors the classic two-terminal ps2link workflow:
  start `ps2client listen` FIRST, then `execee` the ELF, then collect.
"""

from __future__ import annotations

import shutil
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
    return _run(cmd + args, timeout=timeout + 5)


def push(cfg: Config, artifact: str | None = None) -> dict:
    """Send an ELF to the PS2 with `ps2client execee` (upload + execute)."""
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    try:
        artifact_path = resolve_artifact(cfg, artifact)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    with _op_lock:
        # Best effort: ask ps2link to dump exceptions to the network console
        # so crashes show up in `listen` output.
        _ps2client(cfg, ["netdump"], timeout=15)
        rc, out = _ps2client(cfg, ["execee", str(artifact_path)], timeout=cfg.push_timeout)
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "artifact": str(artifact_path.relative_to(Path(cfg.workspace))),
        "log": truncate(out, cfg.max_log_chars),
    }


def console_log(cfg: Config, seconds: int | None = None) -> dict:
    """Capture `seconds` of output from the PS2 network console (`ps2client listen`)."""
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
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
    """Push an ELF and capture the console output while it runs.

    Mirrors the classic ps2link two-terminal workflow: start `listen` first
    (so nothing printed right after exec is missed), then `netdump` (best
    effort), then `execee`, then wait out the capture window.
    """
    if not cfg.ps2_host:
        return {"ok": False, "error": "PS2_HOST is not configured on the bridge"}
    try:
        artifact_path = resolve_artifact(cfg, artifact)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    secs = seconds if seconds and seconds > 0 else cfg.log_seconds
    with _op_lock:
        with tempfile.TemporaryFile(mode="w+b") as listener_out:
            listener = subprocess.Popen(
                [cfg.ps2client_bin, "-h", cfg.ps2_host, "-t", str(secs), "listen"],
                stdin=subprocess.DEVNULL,
                stdout=listener_out,
                stderr=subprocess.STDOUT,
            )
            try:
                _ps2client(cfg, ["netdump"], timeout=15)
                rc, out = _ps2client(cfg, ["execee", str(artifact_path)], timeout=cfg.push_timeout)
                time.sleep(secs)
            finally:
                try:
                    listener.terminate()
                except ProcessLookupError:
                    pass
                try:
                    listener.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    listener.kill()
                listener_out.seek(0)
                console = listener_out.read().decode(errors="replace")
    return {
        "ok": rc == 0,
        "exit_code": rc,
        "artifact": str(artifact_path.relative_to(Path(cfg.workspace))),
        "push_log": truncate(out, cfg.max_log_chars),
        "console_log": truncate(console, cfg.max_log_chars),
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
