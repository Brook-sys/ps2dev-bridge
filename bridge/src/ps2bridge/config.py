"""Configuration read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    api_key: str | None
    host: str
    port: int
    workspace: str
    ps2_host: str | None
    ps2client_bin: str
    build_timeout: int
    push_timeout: int
    log_seconds: int
    sync_max_bytes: int
    max_log_chars: int

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Config":
        env = os.environ if env is None else env
        return cls(
            api_key=env.get("PS2BRIDGE_API_KEY") or None,
            host=env.get("PS2BRIDGE_HOST", "0.0.0.0"),
            port=int(env.get("PS2BRIDGE_PORT", "17800")),
            workspace=env.get("PS2BRIDGE_WORKSPACE", "/work"),
            # ps2client itself also honors PS2HOSTNAME; keep both entry points.
            ps2_host=env.get("PS2_HOST") or env.get("PS2HOSTNAME") or None,
            ps2client_bin=env.get("PS2CLIENT_BIN", "ps2client"),
            build_timeout=int(env.get("PS2BRIDGE_BUILD_TIMEOUT", "900")),
            push_timeout=int(env.get("PS2BRIDGE_PUSH_TIMEOUT", "60")),
            log_seconds=int(env.get("PS2BRIDGE_LOG_SECONDS", "15")),
            sync_max_bytes=int(env.get("PS2BRIDGE_SYNC_MAX", str(512 * 1024 * 1024))),
            max_log_chars=int(env.get("PS2BRIDGE_MAX_LOG", "40000")),
        )
