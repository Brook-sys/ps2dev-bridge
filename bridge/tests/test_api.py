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
