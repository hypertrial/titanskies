from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(token=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|api%5fkey|api%5Fkey|access_token)=)([^&\s]+)", re.IGNORECASE),
    re.compile(r"((?:X-API-Key|Cookie|Set-Cookie):\s*)(\S+)", re.IGNORECASE),
)
def redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted
