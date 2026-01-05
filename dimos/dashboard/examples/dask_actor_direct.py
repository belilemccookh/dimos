# Copyright 2025 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Standalone Dask actor example that replays YAML logs into Rerun."""

from __future__ import annotations

import dataclasses
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import pickle
import threading
import time
from typing import Any
import webbrowser

from distributed import Client
import rerun as rr  # pip install rerun-sdk
import rerun.blueprint as rrb
import yaml


# ------------------------ Minimal dashboard plumbing ----------------------- #
@dataclasses.dataclass
class RerunInfo:
    logging_id: str = os.environ.get("RERUN_ID", "dask_actor_demo")
    grpc_port: int = int(os.environ.get("RERUN_GRPC_PORT", "9876"))
    server_memory_limit: str = os.environ.get("RERUN_SERVER_MEMORY_LIMIT", "25%")
    url: str | None = None

    def __post_init__(self) -> None:
        if self.url is None:
            self.url = f"rerun+http://127.0.0.1:{self.grpc_port}/proxy"


info = RerunInfo()


class DashboardActor:
    """Tiny inline copy of the Dashboard module that can run as a Dask actor."""

    def __init__(self) -> None:
        pass

    def start(self) -> str:
        rr.init(info.logging_id, spawn=False, recording_id=info.logging_id)
        default_blueprint = rrb.Blueprint(
            rrb.Tabs(
                rrb.Spatial3DView(
                    name="Spatial3D",
                    origin="/",
                    line_grid=rrb.LineGrid3D(spacing=1.0, stroke_width=1.0),
                ),
                rrb.TextDocumentView(name="Logs", origin="/logs"),
            )
        )
        rr.send_blueprint(default_blueprint)
        rr.serve_grpc(
            grpc_port=info.grpc_port,
            default_blueprint=default_blueprint,
            server_memory_limit=info.server_memory_limit,
        )

        #
        # Manual control of Rerun viewer (simple html server)
        #
        host = "127.0.0.1"
        port = 4000
        html = f"""<body>
            <style>body {{ margin: 0; border: 0; }}\ncanvas {{ width: 100vw !important; height: 100vh !important; }}</style>
            <script type="module">
                import {{ WebViewer }} from "https://esm.sh/@rerun-io/web-viewer@0.27.2";
                const viewer = new WebViewer();
                viewer.start("{info.url}", document.body);
            </script>
        </body>"""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html.encode("utf-8"))
                elif self.path == "/health":
                    body = json.dumps({"status": "ok", "rerun_url": info.url}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = HTTPServer((host, port), Handler)
        thread = threading.Thread(target=server.serve_forever, name="dashboard-server", daemon=True)
        thread.start()


# ------------------------- Data replay as an actor ------------------------- #
DEFAULT_REPLAY_PATHS = {
    "lidar": str(Path(__file__).with_name(f"example_data_{'lidar'}.yaml")),
    "color_image": str(Path(__file__).with_name(f"example_data_{'color_image'}.yaml")),
}


class DataReplayActor:
    """Reads YAML messages and publishes them to Rerun from a Dask worker."""

    def __init__(self):
        self._threads: list[threading.Thread] = []

    def start(self) -> bool:
        for output_name, path in DEFAULT_REPLAY_PATHS.items():
            thread = threading.Thread(
                target=self._publish_stream,
                args=(output_name, path),
                name=f"{output_name}-replay",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()
            time.sleep(0.1)
        return True

    def _publish_stream(self, output_name: str, path: str) -> None:
        stream = rr.RecordingStream(
            info.logging_id,
            recording_id=info.logging_id,
        )
        stream.connect_grpc(info.url)
        while True:
            any_sent = False
            for _i, msg in enumerate(self._iter_messages(path)):
                try:
                    if isinstance(msg, tuple) and len(msg) == 2:
                        log_path, payload = msg
                    else:
                        log_path, payload = self._to_rerun_payload(msg, output_name)
                    print("logging " + log_path)
                    stream.log(log_path, payload, strict=True)
                    any_sent = True
                except Exception as error:
                    print(f"[DataReplayActor] error while publishing {output_name}: {error}")
            if not any_sent:
                break

    def _iter_messages(self, path: str):
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"[DataReplayActor] missing replay file: {file_path}")

        with file_path.open("r", encoding="utf-8") as f:
            for doc in yaml.safe_load_all(f):
                if doc is None:
                    continue
                items = doc if isinstance(doc, list) else [doc]
                for item in items:
                    if isinstance(item, (bytes, bytearray)):
                        try:
                            yield pickle.loads(item)
                        except Exception as error:
                            print(f"[DataReplayActor] failed to unpickle entry: {error}")
                    else:
                        yield item

    def _to_rerun_payload(self, msg: Any, output_name: str) -> tuple[str, Any]:
        path = f"/{output_name}"
        if hasattr(msg, "to_rerun"):
            payload = msg.to_rerun()  # type: ignore[call-arg]
        elif isinstance(msg, dict):
            path = msg.get("path", path)
            kind = msg.get("kind", "text")
            if kind == "points3d":
                positions = msg.get("positions") or msg.get("points") or []
                payload = rr.Points3D(positions=positions)
            else:
                payload = rr.TextLog(str(msg.get("payload", msg)))
        else:
            payload = rr.TextLog(str(msg))
        return path, payload


# ------------------------------ Entrypoint --------------------------------- #
def main() -> None:
    rerun_info = RerunInfo()

    client = Client(
        n_workers=1,
        threads_per_worker=4,
    )
    dashboard = client.submit(DashboardActor, actor=True).result()
    dashboard.start().result()

    replayer = client.submit(DataReplayActor, actor=True).result()
    replayer.start().result()

    print(f"Dashboard running at {rerun_info.url} (Rerun gRPC on port {rerun_info.grpc_port})")
    print("Press Ctrl+C to stop...")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()


if __name__ == "__main__":
    main()
