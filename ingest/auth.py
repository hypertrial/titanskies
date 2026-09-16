from __future__ import annotations

import hmac
import os
import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"((?:CRON_SECRET|BLOB_READ_WRITE_TOKEN)=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"(token=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|api%5fkey|api%5Fkey|access_token)=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"(MAP_KEY[=/])([^/\s&]+)", re.IGNORECASE),
    re.compile(r"(api/area/csv/)([^/\s&]+)", re.IGNORECASE),
    re.compile(r"((?:X-API-Key|Cookie|Set-Cookie):\s*)(\S+)", re.IGNORECASE),
)
MIN_CRON_SECRET_LENGTH = 16


def _header_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    return str(value).strip() if value else ""


def authorization_header(headers: Any, environ: dict[str, str] | None = None) -> str | None:
    candidates: list[Any] = []
    getter = getattr(headers, "get", None)
    if callable(getter):
        for key in ("Authorization", "authorization", "AUTHORIZATION"):
            candidates.append(getter(key))
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            candidates.extend(get_all("authorization") or get_all("Authorization") or [])
    items = getattr(headers, "items", None)
    if callable(items):
        try:
            candidates.extend(value for key, value in items() if str(key).lower() == "authorization")
        except (TypeError, ValueError, AttributeError):
            pass
    env = environ if environ is not None else os.environ
    candidates.append(env.get("HTTP_AUTHORIZATION"))
    for raw in candidates:
        text = _header_text(raw)
        if text:
            return text
    return None


def authorize(header_value: str | None, secret: str) -> bool:
    secret = _header_text(secret)
    header_value = _header_text(header_value)
    if len(secret) < MIN_CRON_SECRET_LENGTH or not header_value or not secret.isascii() or not header_value.isascii():
        return False
    expected = f"Bearer {secret}"
    if len(header_value) != len(expected):
        hmac.compare_digest(header_value, expected)
        return False
    return hmac.compare_digest(header_value, expected)


def redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted
