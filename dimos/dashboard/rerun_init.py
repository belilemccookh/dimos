"""Rerun initialization helpers (dashboard-owned).

Design:
- Main process starts the server/viewer (CLI/integration layer).
- Worker processes connect to that server and emit rr.log calls from taps.
"""

from __future__ import annotations

import atexit
import importlib
import threading
from uuid import uuid4
from typing import Literal

from dimos.core.global_config import GlobalConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

RERUN_GRPC_PORT = 9876
RERUN_WEB_PORT = 9090
DEFAULT_RERUN_ADDR = f"rerun+http://127.0.0.1:{RERUN_GRPC_PORT}/proxy"

_server_started = False
_connected = False
_lock = threading.Lock()


ViewerMode = Literal["rerun-web", "rerun-native", "rerun-grpc-only"]


def init_rerun_if_enabled(global_config: GlobalConfig) -> GlobalConfig:
    """Start Rerun server/viewer in the main process and return updated config."""
    if not global_config.rerun_enabled:
        return global_config
    if not global_config.viewer_backend.startswith("rerun"):
        return global_config

    recording_id = global_config.rerun_recording_id or str(uuid4())
    server_addr = init_rerun_server(
        viewer_mode=global_config.viewer_backend, recording_id=recording_id
    )
    global_config = global_config.model_copy(
        update={"rerun_server_addr": server_addr, "rerun_recording_id": recording_id}
    )
    connect_rerun(server_addr=server_addr, recording_id=recording_id)
    return global_config


def init_rerun_server(viewer_mode: str = "rerun-web", recording_id: str | None = None) -> str:
    """Start gRPC server and (optionally) a viewer. Must be called from main process."""
    global _server_started
    with _lock:
        if _server_started:
            return DEFAULT_RERUN_ADDR

        rr = importlib.import_module("rerun")
        if recording_id is not None:
            rr.init("dimos", recording_id=recording_id)
        else:
            rr.init("dimos")

        if viewer_mode == "rerun-native":
            rr.spawn(port=RERUN_GRPC_PORT, connect=True)
            logger.info("Rerun: spawned native viewer", port=RERUN_GRPC_PORT)
        elif viewer_mode == "rerun-web":
            server_uri = rr.serve_grpc(grpc_port=RERUN_GRPC_PORT)
            rr.serve_web_viewer(
                web_port=RERUN_WEB_PORT, open_browser=False, connect_to=server_uri
            )
            logger.info(
                "Rerun: web viewer started",
                web_port=RERUN_WEB_PORT,
                url=f"http://localhost:{RERUN_WEB_PORT}",
            )
        else:
            rr.serve_grpc(grpc_port=RERUN_GRPC_PORT)
            logger.info("Rerun: gRPC server only", port=RERUN_GRPC_PORT)

        _server_started = True
        atexit.register(shutdown_rerun)
        return DEFAULT_RERUN_ADDR


def connect_rerun(server_addr: str | None = None, recording_id: str | None = None) -> None:
    """Connect this process to an existing Rerun server (worker-safe)."""
    global _connected
    addr = server_addr or DEFAULT_RERUN_ADDR
    with _lock:
        if _connected:
            return
        rr = importlib.import_module("rerun")
        # Avoid re-initializing if we already have the correct global recording.
        try:
            current = rr.get_recording_id()
        except Exception:
            current = None
        if recording_id is not None and current != recording_id:
            rr.init("dimos", recording_id=recording_id)
        elif recording_id is None and current is None:
            rr.init("dimos")
        rr.connect_grpc(addr)
        _connected = True
        logger.info("Rerun: connected to server", addr=addr)


def shutdown_rerun() -> None:
    """Best-effort disconnect."""
    global _server_started, _connected
    with _lock:
        if not (_server_started or _connected):
            return
        try:
            rr = importlib.import_module("rerun")
            rr.disconnect()
        except Exception:
            pass
        _server_started = False
        _connected = False


