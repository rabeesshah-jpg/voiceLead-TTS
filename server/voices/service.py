"""Voice profile business logic."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import UploadFile

from . import supertone_api
from .adapter import VoiceCloneAdapter, get_adapter
from .audio_util import ALLOWED_EXTENSIONS, AudioValidationError, normalize_and_save_wav
from .config import VoiceConfig
from .schemas import (
    OPEN_SOURCE_MODE_MESSAGE,
    VoiceCloneResponse,
    VoiceProfileMetadata,
    VoiceProfileResponse,
    metadata_to_response,
)
from .store import VoiceProfileStore

if TYPE_CHECKING:
    from supertonic.server.app import ServerState

logger = logging.getLogger(__name__)

REFERENCE_AUDIO_CLONE_MESSAGE = (
    "Reference audio cloning is not available in open-source mode. "
    "Upload a Voice Builder JSON file using POST /v1/voices."
)
VOICE_BUILDER_JSON_ONLY_MESSAGE = (
    "Open-source mode accepts Voice Builder JSON only. "
    "Use POST /v1/voices with a .json file exported from Supertone Voice Builder."
)


class VoiceProfileError(ValueError):
    """User-facing validation error."""


class ReferenceAudioCloneNotSupported(VoiceProfileError):
    """Raised when reference audio clone is requested without cloud support."""


class VoiceProfileService:
    def __init__(self, config: VoiceConfig, adapter: Optional[VoiceCloneAdapter] = None) -> None:
        self.config = config
        self.store = VoiceProfileStore(config.storage_dir)
        self.adapter = adapter or get_adapter()

    def startup(self, state: "ServerState") -> None:
        self.store.load()
        profiles = self.store.list_profiles()
        ready_count = 0
        skipped_count = 0
        for meta in profiles:
            if meta.status != "ready" or meta.tts_backend != "supertonic_local":
                skipped_count += 1
                logger.info(
                    "startup_skip_profile voice_id=%s status=%s backend=%s kind=%s",
                    meta.voice_id,
                    meta.status,
                    meta.tts_backend,
                    meta.profile_kind,
                )
                continue
            if not meta.engine_voice_name or not meta.style_json_path:
                skipped_count += 1
                logger.warning(
                    "startup_skip_profile voice_id=%s reason=missing_engine_or_style",
                    meta.voice_id,
                )
                continue
            style_path = Path(meta.style_json_path)
            if not style_path.exists():
                skipped_count += 1
                logger.warning("startup_skip_profile voice_id=%s reason=missing_style_json", meta.voice_id)
                continue
            result = self.adapter.process_voice_builder_json(
                state,
                voice_id=meta.voice_id,
                style_json_path=style_path,
                builtin_names=list(state.tts.voice_style_names) if state.tts else [],
            )
            if result.status != "ready":
                skipped_count += 1
                logger.warning(
                    "startup_skip_profile voice_id=%s reason=reregister_failed msg=%s",
                    meta.voice_id,
                    result.message,
                )
            else:
                ready_count += 1
                logger.info(
                    "startup_loaded_profile voice_id=%s provider_voice_id=%s name=%r",
                    meta.voice_id,
                    result.provider_voice_id,
                    meta.display_name,
                )
        logger.info(
            "startup_voice_profiles total=%d ready_loaded=%d skipped=%d storage=%s",
            len(profiles),
            ready_count,
            skipped_count,
            self.config.storage_dir,
        )

    def get_profile_for_tts(self, voice_id: str) -> VoiceProfileMetadata:
        meta = self.store.get(voice_id)
        if meta is None:
            raise VoiceProfileError(f"Unknown voice_id {voice_id!r}")
        if meta.status != "ready":
            raise VoiceProfileError(
                meta.status_message
                or f"Voice profile {voice_id!r} is not ready for synthesis (status={meta.status})."
            )
        return meta

    def resolve_engine_voice(self, voice_id: str) -> str:
        meta = self.get_profile_for_tts(voice_id)
        if meta.tts_backend == "supertone_cloud":
            raise VoiceProfileError(
                f"Voice profile {voice_id!r} uses cloud TTS backend; "
                "synthesis must route through Supertone API handler."
            )
        if not meta.engine_voice_name:
            raise VoiceProfileError(
                f"Voice profile {voice_id!r} has no engine_voice_name registered."
            )
        return meta.engine_voice_name

    def to_clone_response(self, saved: VoiceProfileMetadata) -> VoiceCloneResponse:
        provider = saved.provider_voice_id or saved.engine_voice_name
        if saved.status == "ready":
            message = saved.status_message or "Voice profile created"
        else:
            message = saved.status_message or f"Voice profile status: {saved.status}"
        return VoiceCloneResponse(
            status=saved.status,
            voice_id=saved.voice_id,
            provider_voice_id=provider,
            name=saved.display_name,
            message=message,
            tts_backend=saved.tts_backend,
            metadata=saved,
        )

    async def create_profile(
        self,
        state: "ServerState",
        *,
        upload: UploadFile,
        display_name: str,
        consent_confirmed: bool,
    ) -> VoiceProfileResponse:
        meta = await self._ingest_voice_builder_json(
            state,
            upload=upload,
            display_name=display_name,
            consent_confirmed=consent_confirmed,
        )
        return metadata_to_response(meta)

    async def clone_profile(
        self,
        state: "ServerState",
        *,
        upload: UploadFile,
        name: str,
        consent_confirmed: bool = True,
    ) -> VoiceCloneResponse:
        if not self.config.enable_voice_cloning:
            raise VoiceProfileError("Voice cloning is disabled (ENABLE_VOICE_CLONING=false).")
        if not consent_confirmed:
            raise VoiceProfileError(
                "consent_confirmed must be true. Only upload voices you own or have consent to use."
            )

        display_name = (name or "").strip()
        if not display_name:
            raise VoiceProfileError("name is required.")

        original_name = upload.filename or "upload"
        suffix = Path(original_name).suffix.lower()

        if suffix == ".json":
            raise VoiceProfileError(
                "Voice Builder JSON must be uploaded via POST /v1/voices, not /v1/voices/clone."
            )

        if suffix not in ALLOWED_EXTENSIONS:
            raise VoiceProfileError(
                f"Unsupported file type {suffix!r}. Allowed audio: "
                f"{', '.join(sorted(ALLOWED_EXTENSIONS))}."
            )

        if not supertone_api.api_key_configured():
            raise ReferenceAudioCloneNotSupported(REFERENCE_AUDIO_CLONE_MESSAGE)

        meta = await self._ingest_reference_audio(
            state,
            upload=upload,
            display_name=display_name,
            original_name=original_name,
            suffix=suffix,
        )
        response = self.to_clone_response(meta)
        if meta.status != "ready":
            raise VoiceProfileError(response.message)
        return response

    async def _ingest_reference_audio(
        self,
        state: "ServerState",
        *,
        upload: UploadFile,
        display_name: str,
        original_name: str,
        suffix: str,
    ) -> VoiceProfileMetadata:
        """Cloud reference-audio clone path (requires SUPERTONE_API_KEY)."""
        voice_id = self.store.new_voice_id()
        profile_dir = self.store.profile_dir(voice_id)
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "clone_request_received voice_id=%s name=%r filename=%s",
            voice_id,
            display_name,
            original_name,
        )

        raw_path = profile_dir / f"upload{suffix}"
        data = await upload.read()
        if not data:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError("Uploaded file is empty.")

        raw_path.write_bytes(data)
        base_meta = VoiceProfileMetadata(
            voice_id=voice_id,
            display_name=display_name,
            original_filename=original_name,
            created_at=self.store.now_iso(),
            consent_confirmed=True,
            status="processing",
            profile_kind="reference_audio",
            cloning_supported=True,
        )
        return self._finalize_reference_audio(state, base_meta, raw_path)

    def _finalize_reference_audio(
        self,
        state: "ServerState",
        base_meta: VoiceProfileMetadata,
        raw_path: Path,
    ) -> VoiceProfileMetadata:
        wav_path = raw_path.parent / "reference.wav"
        try:
            duration, sr = normalize_and_save_wav(
                raw_path,
                wav_path,
                target_sample_rate=self.config.target_sample_rate,
                min_seconds=self.config.min_voice_sample_seconds,
                max_seconds=self.config.max_voice_sample_seconds,
            )
        except AudioValidationError as e:
            shutil.rmtree(raw_path.parent, ignore_errors=True)
            raise VoiceProfileError(str(e)) from e
        finally:
            if raw_path.exists() and raw_path != wav_path:
                raw_path.unlink(missing_ok=True)

        base_meta.stored_audio_path = str(wav_path)
        base_meta.duration_seconds = duration
        base_meta.sample_rate = sr

        result = self.adapter.process_reference_audio(
            state,
            voice_id=base_meta.voice_id,
            reference_wav_path=wav_path,
            display_name=base_meta.display_name,
        )
        if result.status != "ready":
            shutil.rmtree(raw_path.parent, ignore_errors=True)
            raise VoiceProfileError(result.message or "Reference audio cloning failed.")
        return self._apply_adapter_result(base_meta, result)

    async def _ingest_voice_builder_json(
        self,
        state: "ServerState",
        *,
        upload: UploadFile,
        display_name: str,
        consent_confirmed: bool,
    ) -> VoiceProfileMetadata:
        if not self.config.enable_voice_cloning:
            raise VoiceProfileError("Voice cloning is disabled (ENABLE_VOICE_CLONING=false).")
        if not consent_confirmed:
            raise VoiceProfileError(
                "consent_confirmed must be true. Only upload voices you own or have consent to use."
            )

        display_name = (display_name or "").strip()
        if not display_name:
            raise VoiceProfileError("display_name or name is required.")

        original_name = upload.filename or "upload"
        suffix = Path(original_name).suffix.lower()
        if suffix != ".json":
            raise VoiceProfileError(
                f"Voice upload requires a .json Voice Builder file. {VOICE_BUILDER_JSON_ONLY_MESSAGE}"
            )

        voice_id = self.store.new_voice_id()
        profile_dir = self.store.profile_dir(voice_id)
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "voice_json_upload voice_id=%s name=%r filename=%s",
            voice_id,
            display_name,
            original_name,
        )

        data = await upload.read()
        if not data:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError("Uploaded file is empty.")
        if len(data) > self.config.max_voice_json_bytes:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError(
                f"Voice Builder JSON exceeds max size of {self.config.max_voice_json_bytes} bytes."
            )

        logger.info("voice_json_upload_size voice_id=%s bytes=%d", voice_id, len(data))

        raw_path = profile_dir / "style.json"
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError(f"Invalid Voice Builder JSON: {e}") from e

        from supertonic.utils import validate_voice_style_format

        if not validate_voice_style_format(payload):
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError(
                "Invalid Voice Builder JSON: requires style_ttl and style_dp fields."
            )

        with raw_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f)

        base_meta = VoiceProfileMetadata(
            voice_id=voice_id,
            display_name=display_name,
            original_filename=original_name,
            created_at=self.store.now_iso(),
            consent_confirmed=True,
            status="processing",
            profile_kind="voice_builder_json",
            style_json_path=str(raw_path),
            cloning_supported=True,
        )

        result = self.adapter.process_voice_builder_json(
            state,
            voice_id=voice_id,
            style_json_path=raw_path,
            builtin_names=list(state.tts.voice_style_names) if state.tts else [],
        )
        if result.status != "ready":
            shutil.rmtree(profile_dir, ignore_errors=True)
            raise VoiceProfileError(result.message or "Voice registration failed.")

        saved = self._apply_adapter_result(base_meta, result)
        logger.info(
            "voice_json_ready voice_id=%s provider_voice_id=%s engine_voice=%s path=%s",
            saved.voice_id,
            saved.provider_voice_id,
            saved.engine_voice_name,
            profile_dir,
        )
        return saved

    def _apply_adapter_result(
        self,
        base_meta: VoiceProfileMetadata,
        result,
    ) -> VoiceProfileMetadata:
        base_meta.status = result.status  # type: ignore[assignment]
        base_meta.engine_voice_name = result.engine_voice_name
        base_meta.provider_voice_id = result.provider_voice_id
        base_meta.tts_backend = result.tts_backend  # type: ignore[assignment]
        base_meta.status_message = result.message
        base_meta.cloning_supported = result.cloning_supported
        if result.style_json_path:
            base_meta.style_json_path = result.style_json_path
        return self.store.upsert(base_meta)

    def get_profile(self, voice_id: str) -> VoiceProfileMetadata:
        meta = self.store.get(voice_id)
        if meta is None:
            raise VoiceProfileError(f"Unknown voice_id {voice_id!r}")
        return meta

    def delete_profile(self, state: "ServerState", voice_id: str) -> VoiceProfileMetadata:
        meta = self.store.delete(voice_id)
        if meta is None:
            raise VoiceProfileError(f"Unknown voice_id {voice_id!r}")
        if meta.engine_voice_name:
            self.adapter.unregister(state, meta.engine_voice_name)
        return meta

    def capability_message(self) -> str:
        if supertone_api.api_key_configured():
            return (
                "Reference audio cloning uses Supertone cloud API (SUPERTONE_API_KEY). "
                "Voice Builder JSON works for fully local supertonic_local TTS."
            )
        return OPEN_SOURCE_MODE_MESSAGE
