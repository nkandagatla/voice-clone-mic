"""Sanitized conversion and configuration errors."""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlparse


class ConversionError(Exception):
    def __init__(self, code: str, message: str, fatal: bool = False, pause: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.fatal = fatal
        self.pause = pause


MAX_REQUEST_BYTES = 16000 * 2 * 5


def require_little_endian(byteorder: str = sys.byteorder) -> None:
    if byteorder != "little":
        raise ConversionError(
            "config",
            "This app requires a little-endian CPU to decode 16-bit PCM.",
        )


def validate_request_pcm(pcm: bytes) -> None:
    if not pcm or len(pcm) % 2 != 0 or len(pcm) > MAX_REQUEST_BYTES:
        raise ConversionError(
            "rejected",
            "Segment exceeded the size limit or was empty and was not sent.",
        )


def validate_api_base(base: str) -> str:
    raw = base.strip().rstrip("/")
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    allowed = host == "elevenlabs.io" or host.endswith(".elevenlabs.io")
    if parsed.scheme != "https" or not allowed:
        raise ConversionError(
            "config",
            "API base URL must be https and an elevenlabs.io host.",
        )
    return raw


def sanitize(text: str, secret: str | None) -> str:
    cleaned = text.replace("\x00", " ")
    if secret:
        cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = re.sub(
        r"(?i)(xi-api-key['\"]?\s*[:=]\s*)\S+",
        r"\1[redacted]",
        cleaned,
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:180]


def error_from_status(status: int, body: bytes, secret: str | None) -> ConversionError:
    detail = _detail(body, secret)
    if status in (401, 403):
        if status == 401:
            text = "Authentication failed (HTTP 401). Check the API key. Conversions stay muted."
        else:
            text = (
                "Forbidden (HTTP 403). The API key cannot use this voice or feature. "
                "Conversions stay muted."
            )
        return ConversionError(str(status), text + detail, fatal=True)
    if status == 422:
        return ConversionError(
            "422",
            "Request rejected (HTTP 422). Check the voice id, model, and PCM format." + detail,
        )
    if status == 429:
        return ConversionError(
            "429",
            "Rate limited (HTTP 429). Conversions are paused until unmute. "
            "This segment was not retried." + detail,
            pause=True,
        )
    if 300 <= status < 400:
        return ConversionError(
            "redirect",
            f"Unexpected redirect (HTTP {status}). It was not followed and this segment was not retried.",
        )
    if status >= 500:
        return ConversionError(
            "5xx",
            f"ElevenLabs server error (HTTP {status}). This segment was not retried." + detail,
        )
    return ConversionError(
        str(status),
        f"Unexpected response (HTTP {status}). This segment was not retried." + detail,
    )


def _detail(body: bytes, secret: str | None) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    message = text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
        if isinstance(detail, list):
            parts: list[str] = []
            for item in detail:
                if isinstance(item, dict) and "msg" in item:
                    parts.append(str(item["msg"]))
                else:
                    parts.append(str(item))
            message = "; ".join(parts)
        elif isinstance(detail, dict):
            message = str(detail.get("message") or detail.get("msg") or "")
        else:
            message = str(detail)
    safe = sanitize(message, secret)
    if not safe:
        return ""
    return f" Details: {safe}"
