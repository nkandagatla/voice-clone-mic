"""Account and voice reads. These calls do not synthesize speech."""

from __future__ import annotations

import json

import httpx

from voice_clone_mic.errors import ConversionError, error_from_status, validate_api_base


class ElevenLabsAccount:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None, timeout: float = 15.0) -> None:
        self.base_url = validate_api_base(base_url)
        self.transport = transport
        self.timeout = timeout

    def verify(self, api_key: str) -> str:
        payload = self._get("/v1/user", api_key)
        subscription = payload.get("subscription")
        if isinstance(subscription, dict):
            tier = subscription.get("tier")
            if isinstance(tier, str) and tier.strip():
                return tier.strip()
        return "connected"

    def list_voices(self, api_key: str) -> list[dict[str, str]]:
        payload = self._get("/v1/voices", api_key)
        voices = payload.get("voices")
        listed: list[dict[str, str]] = []
        if not isinstance(voices, list):
            return listed
        for item in voices:
            if not isinstance(item, dict):
                continue
            voice_id = item.get("voice_id")
            name = item.get("name")
            if isinstance(voice_id, str) and voice_id and isinstance(name, str) and name.strip():
                listed.append({"voice_id": voice_id, "name": name})
        return listed

    def _get(self, path: str, api_key: str) -> dict:
        headers = {"xi-api-key": api_key, "Accept": "application/json"}
        try:
            with httpx.Client(
                base_url=self.base_url,
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
            ) as client:
                response = client.get(path, headers=headers)
        except httpx.TimeoutException:
            raise ConversionError(
                "timeout",
                "The ElevenLabs request timed out. Nothing was spoken.",
            ) from None
        except httpx.HTTPError:
            raise ConversionError(
                "network",
                "The ElevenLabs request failed. Nothing was spoken.",
            ) from None
        if response.status_code == 401 and _mentions_permission(response.content):
            raise ConversionError(
                "401",
                "ElevenLabs refused this key for account access (HTTP 401). "
                "The response says a permission is missing. "
                "Reading the account uses GET /v1/user and needs that permission. "
                "No speech was requested.",
                fatal=True,
            )
        if response.status_code != 200:
            raise error_from_status(response.status_code, response.content, api_key)
        try:
            payload = response.json()
        except json.JSONDecodeError:
            raise ConversionError("response", "ElevenLabs returned a response that was not JSON.") from None
        if not isinstance(payload, dict):
            raise ConversionError("response", "ElevenLabs returned an unexpected response.")
        return payload


def _mentions_permission(body: bytes) -> bool:
    try:
        text = body.decode("utf-8", "replace").lower()
    except Exception:
        return False
    return "missing_permissions" in text or "permission" in text
