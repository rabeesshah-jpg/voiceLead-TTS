"""
WhisperLiveKit STT HTTP client — copy into your LiveKit/agent project alongside tts_client.py.

Environment:
    STT_BASE_URL=http://172.16.2.158:8000
    STT_LANGUAGE=en
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

import httpx

DEFAULT_BASE_URL = os.getenv("STT_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")
DEFAULT_TIMEOUT = float(os.getenv("STT_TIMEOUT", "120"))


class STTClient:
    """HTTP client for WhisperLiveKit OpenAI-compatible STT API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        language: str = DEFAULT_LANGUAGE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            r = client.get(f"{self.base_url}/health")
            r.raise_for_status()
            return r.json()

    def transcribe(
        self,
        audio_path: Union[str, Path],
        *,
        language: Optional[str] = None,
        response_format: str = "json",
    ) -> dict[str, Any]:
        """
        POST /v1/audio/transcriptions — file upload (wav, mp3, etc.).
        Returns {"text": "..."} for response_format=json.
        """
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(path)

        data = {"language": language or self.language}
        if response_format:
            data["response_format"] = response_format

        with httpx.Client(timeout=self.timeout) as client:
            with path.open("rb") as f:
                r = client.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    files={"file": (path.name, f)},
                    data=data,
                )
            r.raise_for_status()
            if response_format == "json":
                return r.json()
            return {"text": r.text}

    async def transcribe_async(
        self,
        audio_path: Union[str, Path],
        *,
        language: Optional[str] = None,
        response_format: str = "json",
    ) -> dict[str, Any]:
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(path)

        data = {"language": language or self.language}
        if response_format:
            data["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            with path.open("rb") as f:
                r = await client.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    files={"file": (path.name, f)},
                    data=data,
                )
            r.raise_for_status()
            if response_format == "json":
                return r.json()
            return {"text": r.text}

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        *,
        language: Optional[str] = None,
    ) -> str:
        """Transcribe in-memory audio (e.g. from microphone buffer)."""
        data = {"language": language or self.language, "response_format": "json"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files={"file": (filename, audio_bytes)},
                data=data,
            )
            r.raise_for_status()
            return r.json()["text"]


def stt_transcribe(audio_path: Union[str, Path], base_url: str = DEFAULT_BASE_URL) -> str:
    return STTClient(base_url=base_url).transcribe(audio_path)["text"]


async def stt_transcribe_async(audio_path: Union[str, Path], base_url: str = DEFAULT_BASE_URL) -> str:
    result = await STTClient(base_url=base_url).transcribe_async(audio_path)
    return result["text"]


if __name__ == "__main__":
    import sys

    url = os.getenv("STT_BASE_URL", "http://127.0.0.1:8000")
    audio = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_stt.wav"

    client = STTClient(base_url=url)
    print("Health:", client.health())
    print("Text:", client.transcribe(audio)["text"])
