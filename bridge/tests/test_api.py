"""Service-level tests: auth, sync, artifacts, REST endpoints and the MCP
streamable-HTTP endpoint. No PS2 console and no real toolchain are needed —
console operations are exercised only for their failure paths.
"""

import io
import tarfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ps2bridge.api import create_app
from ps2bridge.config import Config

H = {"X-API-Key": "test-key"}


@pytest.fixture()
def cfg(tmp_path):
    return Config(
        api_key="test-key",
        host="127.0.0.1",
        port=17800,
        workspace=str(tmp_path / "work"),
        ps2_host=None,
        ps2link_port=18193,
        ps2client_bin="ps2client-not-present-in-tests",
        build_timeout=60,
        push_timeout=10,
        log_seconds=1,
        reset_down_wait=10,
        reset_up_wait=30,
        sync_max_bytes=1024 * 1024,
        max_log_chars=40000,
    )


@pytest.fixture()
def client(cfg):
    # Context manager so the FastMCP lifespan (session manager) runs.
    with TestClient(create_app(cfg)) as c:
        yield c


def _tar_bytes(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["api_key_configured"] is True
    assert body["ps2_host"] is None


def test_api_key_required_everywhere_else(client):
    assert client.get("/artifacts").status_code == 401
    assert client.get("/artifacts", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/artifacts", headers=H).status_code == 200
    assert client.post("/build", headers=H).status_code == 200


def test_sync_and_artifacts_roundtrip(client, cfg):
    payload = _tar_bytes({
        "Makefile": "all:\n\techo built\n",
        "src/main.c": "int main(){return 0;}\n",
    })
    r = client.post("/sync", content=payload, headers=H)
    assert r.status_code == 200
    assert r.json()["files"] == 2
    assert (Path(cfg.workspace) / "src" / "main.c").exists()

    # Fabricate an artifact, then list and download it.
    (Path(cfg.workspace) / "demo.elf").write_bytes(b"\x7fELF-fake")
    r = client.get("/artifacts", headers=H)
    assert r.status_code == 200
    assert [a["path"] for a in r.json()["artifacts"]] == ["demo.elf"]
    r = client.get("/artifacts/demo.elf", headers=H)
    assert r.status_code == 200
    assert r.content == b"\x7fELF-fake"


def test_sync_rejects_unsafe_paths(client):
    r = client.post("/sync", content=_tar_bytes({"../evil.txt": "x"}), headers=H)
    assert r.status_code == 400
    r = client.post("/sync", content=_tar_bytes({"/abs.txt": "x"}), headers=H)
    assert r.status_code == 400


def test_sync_rejects_oversized_payload(client):
    big = b"x" * (1024 * 1024 + 1)
    r = client.post("/sync", content=big, headers=H)
    assert r.status_code == 413


def test_download_rejects_workspace_escape(client):
    # URL-encoded so the HTTP client does not normalize the dot segments away.
    r = client.get("/artifacts/%2e%2e%2fetc%2fpasswd", headers=H)
    assert r.status_code == 404


def test_build_requires_makefile(client):
    r = client.post("/build", headers=H)
    body = r.json()
    assert body["ok"] is False
    assert "Makefile" in body["error"]


def test_console_ops_fail_cleanly_without_ps2_host(client):
    r = client.post("/push", headers=H)
    assert r.json()["error"] == "PS2_HOST is not configured on the bridge"
    r = client.post("/log", headers=H)
    assert r.json()["error"] == "PS2_HOST is not configured on the bridge"
    r = client.post("/run", headers=H)
    assert r.json()["error"] == "PS2_HOST is not configured on the bridge"
    r = client.post("/reset", headers=H)
    assert r.json()["error"] == "PS2_HOST is not configured on the bridge"


def test_reset_fails_cleanly_when_ps2link_unreachable(client, cfg):
    # Point at this test machine: port 18193 is closed, so the probe is
    # refused instantly and the honest error comes back without ps2client.
    cfg.ps2_host = "127.0.0.1"
    r = client.post("/reset", headers=H)
    body = r.json()
    assert body["ok"] is False
    assert "cannot reach ps2link" in body["error"]


def test_reset_happy_path_witnesses_down_and_back(client, cfg, monkeypatch):
    from ps2bridge import core

    cfg.ps2_host = "192.168.15.20"
    seq = [True, True, False, False, True, True]  # pre, down-polls, up-polls
    state = {"i": 0}

    def fake_probe(c):
        i = min(state["i"], len(seq) - 1)
        state["i"] += 1
        return seq[i], ""

    monkeypatch.setattr(core, "_probe_ps2link", fake_probe)
    monkeypatch.setattr(core, "_ps2client", lambda c, args, timeout: (0, "sent"))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    r = client.post("/reset", headers=H)
    body = r.json()
    assert body["ok"] is True
    assert body["ps2link_alive"] is True
    assert body["exit_code"] == 0
    assert "ready for the next ps2_run" in body["note"]


def test_reset_reports_when_console_never_went_down(client, cfg, monkeypatch):
    from ps2bridge import core

    cfg.ps2_host = "192.168.15.20"
    cfg.reset_down_wait = 0  # do not burn the budget polling an alive console
    monkeypatch.setattr(core, "_probe_ps2link", lambda c: (True, ""))
    monkeypatch.setattr(core, "_ps2client", lambda c, args, timeout: (0, ""))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    r = client.post("/reset", headers=H)
    body = r.json()
    assert body["ok"] is False
    assert body["ps2link_alive"] is True
    assert "did not go down" in body["note"]


def test_reset_reports_when_ps2link_never_came_back(client, cfg, monkeypatch):
    from ps2bridge import core

    cfg.ps2_host = "192.168.15.20"
    cfg.reset_up_wait = 0
    # pre-probe alive, then everything after is dead.
    state = {"i": 0}

    def fake_probe(c):
        state["i"] += 1
        return (state["i"] == 1, "")

    monkeypatch.setattr(core, "_probe_ps2link", fake_probe)
    monkeypatch.setattr(core, "_ps2client", lambda c, args, timeout: (0, ""))
    monkeypatch.setattr(core.time, "sleep", lambda s: None)
    r = client.post("/reset", headers=H)
    body = r.json()
    assert body["ok"] is False
    assert body["ps2link_alive"] is False
    assert "did not answer" in body["note"]


def test_mcp_endpoint_initialize(client):
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        headers={**H, "Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 200
    assert "ps2dev-bridge" in r.text


# ------------------------------------------------------------- crash handling
# The exception block below is the real ps2link dump captured on hardware
# (2026-10-23, crash.elf: null-pointer write three call levels deep).

CRASH_LOG = """loadelf: fname host:/work/crash.elf secname all
Input ELF format filename = host:/work/crash.elf
1 00100000 00021008 ...
Loaded, host:/work/crash.elf
start address 0x100ae0
gp address 00000000
read/write allocate memory 4000

=== ps2dev-bridge CRASH test ===
call chain: main -> level1 -> level2 -> level3 -> boom
crash.c: about to write to address 0x0 (null pointer)


           EE Exception handler: TLB store exception

      Cause 7000800C  BadVAddr 00000000  Status 70030C13  EPC 0011DA4C

zero: 00000000000000000000000000000000   t8: 00000000FFFFFFFF0000000080021940
  at: 00000000000000000000000070030C13   t9: 00000000000000000000000000019600
  v0: 00000000000000000000000000000000   s0: 00000000000000000000000000120000
  a1: 000000000000000000000000001273A0   s3: 00000000000000000000000000000000
  t5: 00000000FFFFFFFF00000000A0000000   s7: 00000000000000000000000000000000
  gp: 00000000000000000000000000128FF0
  sp: 00000000000000000000000001FFED30
  fp: 000000000000000000000000000DC9A0
  ra: 0000000000000000000000000011DA4C

"""


def test_parse_crash_real_log():
    from ps2bridge import core

    c = core.parse_crash(CRASH_LOG)
    assert c == {
        "cpu": "EE",
        "type": "TLB store",
        "cause": "0x7000800C",
        "badvaddr": "0x00000000",
        "status": "0x70030C13",
        "epc": "0x0011DA4C",
        "ra": "0x0011DA4C",
        "sp": "0x01FFED30",
    }


def test_parse_crash_none_on_normal_log():
    from ps2bridge import core

    assert core.parse_crash("parking now — call ps2_reset to reboot me\n") is None
    assert core.parse_crash("") is None
    # An IOP-shaped dump still parses (fields + 8-hex register block).
    c = core.parse_crash(
        "         IOP Exception handler: TLB load/inst fetch exception\n"
        '               Module Name "udptty" Relative EPC 00001234\n'
        "      Cause 70008008  BadVAddr 00001000  Status 0000FF03  EPC 0000ABCD\n"
        "       zero: 00000000   at: 00000000   ra: 00001234\n"
    )
    assert c["cpu"] == "IOP"
    assert c["epc"] == "0x0000ABCD"
    assert c["ra"] == "0x00001234"


def test_run_detects_crash_and_demands_reset(client, cfg, monkeypatch):
    from ps2bridge import core

    cfg.ps2_host = "192.168.15.20"
    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)
    (Path(cfg.workspace) / "crash.elf").write_bytes(b"\x7fELF-fake")
    monkeypatch.setattr(core, "_probe_ps2link", lambda c: (True, ""))
    monkeypatch.setattr(core, "_ps2client", lambda c, args, timeout: (0, CRASH_LOG))
    r = client.post("/run?artifact=crash.elf", headers=H)
    body = r.json()
    assert body["ok"] is True
    assert body["crash"]["type"] == "TLB store"
    assert body["crash"]["epc"] == "0x0011DA4C"
    # No addr2line in the test env -> no symbols, but the run never fails.
    assert "symbols" not in body["crash"]
    assert "CRASHED" in body["note"]
    assert "ps2_reset" in body["note"]
    assert body["ps2link_alive"] is True


def test_run_without_crash_keeps_parked_note(client, cfg, monkeypatch):
    from ps2bridge import core

    cfg.ps2_host = "192.168.15.20"
    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)
    (Path(cfg.workspace) / "hello.elf").write_bytes(b"\x7fELF-fake")
    monkeypatch.setattr(core, "_probe_ps2link", lambda c: (True, ""))
    monkeypatch.setattr(
        core, "_ps2client", lambda c, args, timeout: (0, "parking now\n")
    )
    r = client.post("/run?artifact=hello.elf", headers=H)
    body = r.json()
    assert "crash" not in body
    assert "still resident" in body["note"]


def test_symbolize_rejects_bad_input(client):
    r = client.post("/symbolize", headers=H)
    assert r.json()["ok"] is False
    assert "no addr" in r.json()["error"]
    r = client.post("/symbolize?addr=11da4c", headers=H)  # no 0x prefix
    assert r.json()["ok"] is False
    assert "invalid address" in r.json()["error"]
    r = client.post("/symbolize?artifact=nope.elf&addr=0x11da4c", headers=H)
    assert r.json()["ok"] is False
    assert "not found" in r.json()["error"]


def test_symbolize_missing_binary_is_honest(client, cfg):
    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)
    (Path(cfg.workspace) / "crash.elf").write_bytes(b"\x7fELF-fake")
    r = client.post("/symbolize?artifact=crash.elf&addr=0x11da4c", headers=H)
    body = r.json()
    assert body["ok"] is False
    assert "addr2line not found" in body["error"]


def test_symbolize_maps_addresses_to_lines(client, cfg, monkeypatch):
    from ps2bridge import core

    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)
    (Path(cfg.workspace) / "crash.elf").write_bytes(b"\x7fELF-fake")
    monkeypatch.setattr(core.shutil, "which", lambda name: "/fake/addr2line")
    monkeypatch.setattr(
        core,
        "_run",
        lambda cmd, timeout, cwd=None: (0, "level3\n/work/crash.c:21\n??\n??\n"),
    )
    r = client.post(
        "/symbolize?artifact=crash.elf&addr=0x11da4c&addr=0x00111111", headers=H
    )
    body = r.json()
    assert body["ok"] is True
    assert body["artifact"] == "crash.elf"
    assert body["symbols"][0] == {
        "addr": "0x11da4c",
        "function": "level3",
        "location": "/work/crash.c:21",
    }
    assert body["symbols"][1]["location"] == "??"
