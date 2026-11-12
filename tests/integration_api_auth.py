"""Exercise the plugin router with Hermes' real WS authentication functions."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
previous_home = os.environ.get("HERMES_HOME")
try:
    with tempfile.TemporaryDirectory(prefix=".api-test-", dir=ROOT) as sandbox:
        os.environ["HERMES_HOME"] = sandbox
        from hermes_cli import web_server
        from plugin.dashboard.plugin_api import manager, router

        app = FastAPI()
        app.include_router(router, prefix="/api/plugins/selection-translate")
        core_state = web_server.app.state
        with patch.object(web_server, "_SESSION_TOKEN", "isolated-integration-only"), \
                patch.object(core_state, "bound_host", "127.0.0.1", create=True), \
                patch.object(core_state, "auth_required", False, create=True), \
                patch.object(manager, "_start_control_locked"):
            with TestClient(app, base_url="http://127.0.0.1:8899",
                            client=("127.0.0.1", 50000)) as client:
                assert client.get("/api/plugins/selection-translate/status").json()["enabled"] is False
                rejected = 0
                for suffix, headers in [
                    ("", {}),
                    ("?token=wrong", {}),
                    ("?token=isolated-integration-only", {"Origin": "https://example.invalid"}),
                ]:
                    try:
                        with client.websocket_connect("ws://127.0.0.1:8899/api/plugins/selection-translate/lifecycle" + suffix,
                                                      headers=headers):
                            raise AssertionError("unauthorized lifecycle connection accepted")
                    except WebSocketDisconnect as exc:
                        assert exc.code == 1008
                        rejected += 1
                with client.websocket_connect(
                        "ws://127.0.0.1:8899/api/plugins/selection-translate/lifecycle?token=isolated-integration-only") as socket:
                    # A round trip ensures attach_session has completed before inspection.
                    socket.send_text("lifetime-probe")
                assert not manager.sessions
                assert not manager.status()["enabled"]
            with TestClient(app, base_url="http://127.0.0.1:8899",
                            client=("198.51.100.10", 50000)) as remote:
                try:
                    with remote.websocket_connect(
                            "ws://127.0.0.1:8899/api/plugins/selection-translate/lifecycle?token=isolated-integration-only"):
                        raise AssertionError("remote lifecycle connection accepted")
                except WebSocketDisconnect as exc:
                    assert exc.code == 1008
            assert not manager.sessions
            print(json.dumps({"ok": True, "real_hermes_auth_used": True,
                              "unauthorized_cases_rejected": rejected,
                              "remote_peer_rejected": True,
                              "authenticated_lifecycle_connected_and_closed": True,
                              "worker_stayed_disabled": True}))
        logging.shutdown()
finally:
    if previous_home is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = previous_home
