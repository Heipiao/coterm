from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .app import HubAPIError, MockHubApp
from .models import new_id


def build_http_server(app: MockHubApp, host: str, port: int, logger: logging.Logger) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CotermMockHub/0.1"

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), fmt % args)

        def _dispatch(self, method: str) -> None:
            try:
                response = self._handle(method)
                self._send_json(HTTPStatus.OK, response)
            except HubAPIError as exc:
                self._send_json(
                    exc.status,
                    {
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "request_id": new_id("req"),
                        }
                    },
                )
            except Exception as exc:
                logger.exception("http request failed")
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "error": {
                            "code": "HUB_INTERNAL_ERROR",
                            "message": str(exc),
                            "request_id": new_id("req"),
                        }
                    },
                )

        def _handle(self, method: str) -> dict[str, Any]:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            if method == "POST" and path == "/api/v1/sessions":
                body = self._read_json()
                return app.create_session(
                    agent_type=str(body.get("agent_type", "claude_code")),
                    device_id=str(body.get("device_id", "mock-device")),
                )

            if method == "GET" and path.startswith("/api/v1/sessions/") and path.count("/") == 4:
                session_id = path.split("/")[-1]
                return app.get_session(session_id)

            if method == "POST" and path.startswith("/api/v1/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[4]
                body = self._read_json()
                return app.post_user_message(
                    session_id=session_id,
                    message_id=str(body.get("message_id", new_id("msg"))),
                    content=str(body["content"]),
                )

            if method == "GET" and path.startswith("/api/v1/sessions/") and path.endswith("/messages"):
                session_id = path.split("/")[4]
                limit = int(query.get("limit", ["20"])[0])
                before = query.get("before", [None])[0]
                return app.list_messages(session_id, limit=limit, before=before)

            if method == "GET" and path.startswith("/api/v1/sessions/") and path.endswith("/permissions"):
                session_id = path.split("/")[4]
                return app.list_permissions(session_id)

            if method == "POST" and path.startswith("/api/v1/permissions/") and path.endswith("/decision"):
                request_id = path.split("/")[4]
                body = self._read_json()
                return app.post_permission_decision(
                    request_id=request_id,
                    decision=str(body["decision"]),
                    resolved_by=str(body.get("reason", "mock-user")),
                )

            raise HubAPIError(404, "HUB_ROUTE_NOT_FOUND", f"unknown route: {path}")

        def _read_json(self) -> dict[str, Any]:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise HubAPIError(400, "HUB_INVALID_MESSAGE", "request body must be an object")
            return data

        def _send_json(self, status: int | HTTPStatus, body: dict[str, Any]) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return ThreadingHTTPServer((host, port), Handler)
