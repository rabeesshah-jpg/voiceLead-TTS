"""Optional Supertone cloud API for reference-audio cloning + TTS."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

SUPERTONE_API_BASE = os.getenv("SUPERTONE_API_BASE", "https://supertoneapi.com").rstrip("/")
SUPERTONE_TTS_MODEL = os.getenv("SUPERTONE_TTS_MODEL", "supertonic_api_3")


class SupertoneApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def api_key_configured() -> bool:
    return bool(os.getenv("SUPERTONE_API_KEY", "").strip())


def _headers() -> dict[str, str]:
    key = os.getenv("SUPERTONE_API_KEY", "").strip()
    if not key:
        raise SupertoneApiError("SUPERTONE_API_KEY is not configured")
    return {"x-sup-api-key": key}


def clone_voice_from_audio(
    wav_path: Path,
    *,
    name: str,
    description: str = "",
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST /v1/custom-voices/cloned-voice — returns API JSON with voice_id."""
    url = f"{SUPERTONE_API_BASE}/v1/custom-voices/cloned-voice"
    logger.info(
        "supertone_clone_request name=%r file=%s size=%d",
        name,
        wav_path.name,
        wav_path.stat().st_size,
    )
    with wav_path.open("rb") as f:
        files = {"files": (wav_path.name, f, "audio/wav")}
        data: dict[str, str] = {"name": name[:100]}
        if description:
            data["description"] = description[:500]
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, headers=_headers(), files=files, data=data)
    if r.status_code >= 400:
        logger.error(
            "supertone_clone_failed status=%d body=%s",
            r.status_code,
            r.text[:500],
        )
        raise SupertoneApiError(
            f"Supertone clone failed (HTTP {r.status_code}): {r.text[:300]}",
            status_code=r.status_code,
            body=r.text,
        )
    payload = r.json()
    cloud_id = payload.get("voice_id") or payload.get("id")
    logger.info("supertone_clone_ready cloud_voice_id=%s", cloud_id)
    return payload


def synthesize_speech(
    *,
    provider_voice_id: str,
    text: str,
    language: str = "en",
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> tuple[bytes, str]:
    """POST /v1/text-to-speech/{voice_id} — returns (audio_bytes, content_type)."""
    url = f"{SUPERTONE_API_BASE}/v1/text-to-speech/{provider_voice_id}"
    body = {
        "text": text,
        "language": language,
        "model": model or SUPERTONE_TTS_MODEL,
        "output_format": "wav",
    }
    logger.info(
        "supertone_tts_request provider_voice_id=%s text_len=%d lang=%s model=%s",
        provider_voice_id,
        len(text),
        language,
        body["model"],
    )
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=body)
    if r.status_code >= 400:
        raise SupertoneApiError(
            f"Supertone TTS failed (HTTP {r.status_code}): {r.text[:300]}",
            status_code=r.status_code,
            body=r.text,
        )
    ctype = r.headers.get("content-type", "audio/wav")
    logger.info(
        "supertone_tts_done provider_voice_id=%s bytes=%d content_type=%s",
        provider_voice_id,
        len(r.content),
        ctype,
    )
    return r.content, ctype
