"""
Supertonic TTS HTTP client — copy this file into your LiveKit/agent project.

Set environment variable:
    TTS_BASE_URL=http://172.16.2.158:7788

Or pass base_url when creating the client.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

# RunPod external: http://103.196.86.102:10908  (internal server listens on 7788)
DEFAULT_BASE_URL = os.getenv("TTS_BASE_URL", "http://127.0.0.1:7788")
DEFAULT_MODEL = os.getenv("TTS_MODEL", "supertonic-3")
DEFAULT_VOICE = os.getenv("TTS_VOICE", "M1")
DEFAULT_LANG = os.getenv("TTS_LANG", "en")
DEFAULT_TIMEOUT = float(os.getenv("TTS_TIMEOUT", "60"))
DEFAULT_VOICE_ID = os.getenv("AGENT_TTS_VOICE_ID", os.getenv("TTS_VOICE_ID", "")).strip() or None


class TTSClient:
    """HTTP client for Supertonic serve API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        model: str = DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        lang: str = DEFAULT_LANG,
        timeout: float = DEFAULT_TIMEOUT,
        voice_id: Optional[str] = DEFAULT_VOICE_ID,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.lang = lang
        self.timeout = timeout
        self.voice_id = voice_id

    def health(self) -> dict[str, Any]:
        """GET /v1/health"""
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}/v1/health")
            r.raise_for_status()
            return r.json()

    def list_voices(self) -> dict[str, Any]:
        """GET /v1/styles (built-in + imported Supertonic styles)."""
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}/v1/styles")
            r.raise_for_status()
            return r.json()

    def list_voice_profiles(self) -> dict[str, Any]:
        """GET /v1/voices (uploaded voice profiles)."""
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}/v1/voices")
            r.raise_for_status()
            return r.json()

    def upload_voice_profile(
        self,
        file_path: str,
        *,
        display_name: str,
        consent_confirmed: bool = True,
    ) -> dict[str, Any]:
        """POST /v1/voices — upload Voice Builder JSON or reference audio."""
        if not consent_confirmed:
            raise ValueError("consent_confirmed must be true")
        with httpx.Client(timeout=self.timeout) as client:
            with open(file_path, "rb") as f:
                r = client.post(
                    f"{self.base_url}/v1/voices",
                    files={"file": (os.path.basename(file_path), f)},
                    data={
                        "display_name": display_name,
                        "consent_confirmed": "true",
                    },
                )
            r.raise_for_status()
            return r.json()

    def delete_voice_profile(self, voice_id: str) -> dict[str, Any]:
        """DELETE /v1/voices/{voice_id}"""
        with httpx.Client(timeout=self.timeout) as client:
            r = client.delete(f"{self.base_url}/v1/voices/{voice_id}")
            r.raise_for_status()
            return r.json()

    def synthesize(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        voice_id: Optional[str] = None,
        lang: Optional[str] = None,
        speed: float = 1.05,
        steps: int = 8,
        response_format: str = "wav",
    ) -> bytes:
        """
        POST /v1/tts — returns raw audio bytes (WAV by default).

        Use for LiveKit: decode WAV and push frames to the audio track.
        Pass voice_id for a custom profile, or voice for built-in (M1, F1, …).
        """
        payload: dict[str, Any] = {
            "text": text,
            "lang": lang or self.lang,
            "speed": speed,
            "steps": steps,
            "response_format": response_format,
        }
        resolved_voice_id = voice_id or self.voice_id
        if resolved_voice_id:
            payload["voice_id"] = resolved_voice_id
        else:
            payload["voice"] = voice or self.voice
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.base_url}/v1/tts",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.content

    def synthesize_openai(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        voice_id: Optional[str] = None,
        lang: Optional[str] = None,
        speed: float = 1.05,
        response_format: str = "wav",
    ) -> bytes:
        """
        POST /v1/audio/speech — OpenAI-compatible endpoint.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "input": text,
            "response_format": response_format,
            "speed": speed,
        }
        resolved_voice_id = voice_id or self.voice_id
        if resolved_voice_id:
            payload["voice_id"] = resolved_voice_id
        else:
            payload["voice"] = voice or self.voice
        if lang or self.lang:
            payload["lang"] = lang or self.lang
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.base_url}/v1/audio/speech",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.content

    async def synthesize_async(
        self,
        text: str,
        *,
        voice: Optional[str] = None,
        voice_id: Optional[str] = None,
        lang: Optional[str] = None,
        speed: float = 1.05,
        steps: int = 8,
        response_format: str = "wav",
        use_openai_route: bool = True,
    ) -> bytes:
        """Async version for LiveKit agents."""
        resolved_voice_id = voice_id or self.voice_id
        if use_openai_route:
            payload: dict[str, Any] = {
                "model": self.model,
                "input": text,
                "response_format": response_format,
                "speed": speed,
            }
            if resolved_voice_id:
                payload["voice_id"] = resolved_voice_id
            else:
                payload["voice"] = voice or self.voice
            if lang or self.lang:
                payload["lang"] = lang or self.lang
            url = f"{self.base_url}/v1/audio/speech"
        else:
            payload = {
                "text": text,
                "lang": lang or self.lang,
                "speed": speed,
                "steps": steps,
                "response_format": response_format,
            }
            if resolved_voice_id:
                payload["voice_id"] = resolved_voice_id
            else:
                payload["voice"] = voice or self.voice
            url = f"{self.base_url}/v1/tts"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            return r.content


# --- Convenience helpers ---

def tts_speak(text: str, base_url: str = DEFAULT_BASE_URL) -> bytes:
    """One-shot sync call."""
    return TTSClient(base_url=base_url).synthesize_openai(text)


async def tts_speak_async(text: str, base_url: str = DEFAULT_BASE_URL) -> bytes:
    """One-shot async call for agents."""
    return await TTSClient(base_url=base_url).synthesize_async(text)


if __name__ == "__main__":
    import sys

    url = os.getenv("TTS_BASE_URL", "http://127.0.0.1:7788")
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello from my project."
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/tts_from_project.wav"

    client = TTSClient(base_url=url)
    print("Health:", client.health())
    wav = client.synthesize_openai(text)
    with open(out, "wb") as f:
        f.write(wav)
    print(f"Saved {len(wav)} bytes -> {out}")
