from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from ingest.auth import authorization_header, authorize, redact
from ingest.config import _load_env_files

JSON_MAX_BYTES = 4_500_000
LOGGER = logging.getLogger("titanskies.cron")


def write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > JSON_MAX_BYTES:
        body = json.dumps({"ok": False, "error": "response too large"}).encode("utf-8")
        status = 500
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if getattr(handler, "command", "GET") != "HEAD":
        handler.wfile.write(body)


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    header = authorization_header(getattr(handler, "headers", None))
    secret = os.environ.get("CRON_SECRET", "")
    if authorize(header, secret):
        return True
    if not secret.strip():
        LOGGER.error("cron auth failed: CRON_SECRET is unset")
    elif not header:
        LOGGER.error("cron auth failed: missing Authorization")
    else:
        LOGGER.error("cron auth failed: bearer mismatch")
    return False


def handle_cron_head(handler: BaseHTTPRequestHandler) -> None:
    _load_env_files()
    if not _authorized(handler):
        write_json(handler, 401, {"ok": False, "error": "Unauthorized"})
        return
    write_json(handler, 200, {"ok": True})


def handle_cron_get(handler: BaseHTTPRequestHandler, run: Callable[[Any], dict[str, Any]]) -> None:
    _load_env_files()
    if not _authorized(handler):
        write_json(handler, 401, {"ok": False, "error": "Unauthorized"})
        return
    try:
        from ingest.config import Settings
        result = run(Settings.from_env())
        if not isinstance(result, dict):
            raise TypeError("cron result must be an object")
    except Exception as exc:
        LOGGER.error("%s", redact(str(exc)))
        write_json(handler, 500, {"ok": False, "error": "internal error"})
        return
    write_json(handler, 200 if result.get("ok") else 500, result)


class CronHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:  # noqa: N802
        handle_cron_head(self)

    def _method_not_allowed(self) -> None:
        write_json(self, 405, {"ok": False, "error": "Method not allowed"})

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code in {401}:
            write_json(self, 401, {"ok": False, "error": "Unauthorized"})
            return
        if code in {404, 405, 501}:
            write_json(self, 405, {"ok": False, "error": "Method not allowed"})
            return
        write_json(self, 500, {"ok": False, "error": "internal error"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return
