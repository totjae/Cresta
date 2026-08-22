from __future__ import annotations

import re

MAX_BROKER_RESULT_CODE_LENGTH = 32
MAX_BROKER_RESULT_MESSAGE_LENGTH = 200

_SAFE_CODE = re.compile(r"[A-Za-z0-9_.:-]+")
_CONTROL_OR_SPACE = re.compile(r"[\x00-\x1f\x7f\s]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_CREDENTIAL_VALUE = re.compile(
    r"(?i)\b(?:app(?:lication)?[_ -]?key|secret(?:key)?|authorization|"
    r"access[_ -]?token|token)\b\s*[:=]\s*[^\s,;]+"
)
_OPAQUE_SECRET = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])")
_ACCOUNT_LIKE_NUMBER = re.compile(r"(?<!\d)\d{8,12}(?!\d)")
_BARE_SECRET = re.compile(r"(?i)^secret$")


def normalize_broker_result_code(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > MAX_BROKER_RESULT_CODE_LENGTH or not _SAFE_CODE.fullmatch(text):
        return None
    return text


def sanitize_broker_result_message(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = _CONTROL_OR_SPACE.sub(" ", value).strip()
    if not text:
        return None
    text = _BEARER.sub("[REDACTED]", text)
    text = _CREDENTIAL_VALUE.sub("[REDACTED]", text)
    text = _OPAQUE_SECRET.sub("[REDACTED]", text)
    text = _ACCOUNT_LIKE_NUMBER.sub("[REDACTED]", text)
    if _BARE_SECRET.fullmatch(text):
        text = "[REDACTED]"
    text = _CONTROL_OR_SPACE.sub(" ", text).strip()
    return text[:MAX_BROKER_RESULT_MESSAGE_LENGTH] or None
