"""Pluggable voice-cloning adapters.

Supertonic does not perform runtime cloning from reference audio. It accepts
precomputed Voice Builder style JSON (``style_ttl`` + ``style_dp`` vectors).
Other engines can implement :class:`VoiceCloneAdapter` later.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from supertonic.server import styles_store
from supertonic.utils import validate_voice_style_format

from . import supertone_api

if TYPE_CHECKING:
    from supertonic.server.app import ServerState

logger = logging.getLogger(__name__)


@dataclass
class AdapterProcessResult:
    status: str
    engine_voice_name: Optional[str] = None
    provider_voice_id: Optional[str] = None
    tts_backend: str = "supertonic_local"
    style_json_path: Optional[str] = None
    embedding_path: Optional[str] = None
    message: Optional[str] = None
    cloning_supported: bool = False


class VoiceCloneAdapter(ABC):
    engine_name: str

    @abstractmethod
    def capability_notes(self) -> str: ...

    @abstractmethod
    def supports_reference_audio_cloning(self) -> bool: ...

    @abstractmethod
    def supports_voice_builder_json(self) -> bool: ...

    @abstractmethod
    def process_voice_builder_json(
        self,
        state: "ServerState",
        *,
        voice_id: str,
        style_json_path: Path,
        builtin_names: list[str],
    ) -> AdapterProcessResult: ...

    @abstractmethod
    def process_reference_audio(
        self,
        state: "ServerState",
        *,
        voice_id: str,
        reference_wav_path: Path,
    ) -> AdapterProcessResult: ...

    @abstractmethod
    def unregister(self, state: "ServerState", engine_voice_name: str) -> None: ...


class SupertonicVoiceAdapter(VoiceCloneAdapter):
    engine_name = "supertonic"

    def capability_notes(self) -> str:
        if supertone_api.api_key_configured():
            return (
                "Reference audio cloning uses Supertone cloud API (SUPERTONE_API_KEY). "
                "TTS for cloned voices routes through supertone_cloud backend. "
                "Voice Builder JSON still works for fully local supertonic_local TTS."
            )
        from .schemas import OPEN_SOURCE_MODE_MESSAGE

        return OPEN_SOURCE_MODE_MESSAGE

    def supports_reference_audio_cloning(self) -> bool:
        return supertone_api.api_key_configured()

    def supports_voice_builder_json(self) -> bool:
        return True

    def process_voice_builder_json(
        self,
        state: "ServerState",
        *,
        voice_id: str,
        style_json_path: Path,
        builtin_names: list[str],
    ) -> AdapterProcessResult:
        with style_json_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not validate_voice_style_format(payload):
            return AdapterProcessResult(
                status="failed",
                message="Invalid Voice Builder JSON: requires style_ttl and style_dp.",
            )

        engine_voice_name = f"vp_{voice_id.replace('-', '_')}"
        try:
            target = styles_store.save(
                state.custom_styles_dir,
                engine_voice_name,
                payload,
                builtin_names=builtin_names,
                overwrite=True,
            )
        except styles_store.InvalidStyleName as e:
            return AdapterProcessResult(status="failed", message=str(e))
        except styles_store.StyleNameConflict as e:
            return AdapterProcessResult(status="failed", message=str(e))
        except ValueError as e:
            return AdapterProcessResult(status="failed", message=str(e))

        state.custom_styles[target.stem] = target
        return AdapterProcessResult(
            status="ready",
            engine_voice_name=target.stem,
            provider_voice_id=target.stem,
            tts_backend="supertonic_local",
            style_json_path=str(style_json_path),
            cloning_supported=True,
            message="Voice profile created and ready for local TTS.",
        )

    def process_reference_audio(
        self,
        state: "ServerState",
        *,
        voice_id: str,
        reference_wav_path: Path,
        display_name: str = "Custom Voice",
    ) -> AdapterProcessResult:
        del state
        if supertone_api.api_key_configured():
            try:
                payload = supertone_api.clone_voice_from_audio(
                    reference_wav_path,
                    name=display_name,
                    description=f"voiceLead profile {voice_id}",
                )
            except supertone_api.SupertoneApiError as e:
                return AdapterProcessResult(
                    status="failed",
                    message=str(e),
                    cloning_supported=False,
                )
            cloud_id = payload.get("voice_id") or payload.get("id")
            if not cloud_id:
                return AdapterProcessResult(
                    status="failed",
                    message="Supertone clone succeeded but no voice_id in response.",
                )
            return AdapterProcessResult(
                status="ready",
                engine_voice_name=None,
                provider_voice_id=str(cloud_id),
                tts_backend="supertone_cloud",
                message="Voice profile created via Supertone API (cloud TTS backend).",
                cloning_supported=True,
            )
        return AdapterProcessResult(
            status="stored_only",
            message=(
                "Reference audio stored but local Supertonic cannot clone from raw audio. "
                "Set SUPERTONE_API_KEY for cloud cloning, or upload a Voice Builder JSON "
                "from https://supertonic.supertone.ai/voice-builder."
            ),
            cloning_supported=False,
        )

    def unregister(self, state: "ServerState", engine_voice_name: str) -> None:
        state.custom_styles.pop(engine_voice_name, None)
        path = state.custom_styles_dir / f"{engine_voice_name}.json"
        if path.exists() and path.is_file():
            path.unlink()


def get_adapter() -> VoiceCloneAdapter:
    # Future: select adapter from VOICE_CLONE_ENGINE env var.
    return SupertonicVoiceAdapter()
