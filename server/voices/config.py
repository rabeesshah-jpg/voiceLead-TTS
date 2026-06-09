"""Environment configuration for voice profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class VoiceConfig:
    storage_dir: Path
    enable_voice_cloning: bool
    max_voice_sample_seconds: float
    min_voice_sample_seconds: float
    recommended_min_seconds: float
    default_voice_id: str
    allow_voice_fallback: bool
    max_voice_json_bytes: int
    target_sample_rate: int = 44100

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        return cls(
            storage_dir=Path(
                os.getenv("VOICE_PROFILE_STORAGE_DIR", "data/voices")
            ).expanduser(),
            enable_voice_cloning=_env_bool("ENABLE_VOICE_CLONING", True),
            max_voice_sample_seconds=float(os.getenv("MAX_VOICE_SAMPLE_SECONDS", "60")),
            min_voice_sample_seconds=float(os.getenv("MIN_VOICE_SAMPLE_SECONDS", "2")),
            recommended_min_seconds=float(os.getenv("RECOMMENDED_VOICE_SAMPLE_SECONDS", "10")),
            default_voice_id=os.getenv("DEFAULT_VOICE_ID", "").strip(),
            allow_voice_fallback=_env_bool("TTS_ALLOW_VOICE_FALLBACK", False),
            max_voice_json_bytes=int(os.getenv("MAX_VOICE_JSON_BYTES", str(2 * 1024 * 1024))),
        )
