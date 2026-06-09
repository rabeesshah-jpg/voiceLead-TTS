"""Pydantic schemas for voice profile API."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

VoiceStatus = Literal["processing", "ready", "stored_only", "failed"]
ProfileKind = Literal["voice_builder_json", "reference_audio"]
SourceType = Literal["voice_builder_json", "reference_audio", "preset", "unknown"]
TtsBackend = Literal["supertonic_local", "supertone_cloud"]

OPEN_SOURCE_MODE_MESSAGE = (
    "Reference audio cloning requires SUPERTONE_API_KEY. "
    "Open-source mode supports Voice Builder JSON only."
)


class VoiceProfileMetadata(BaseModel):
    voice_id: str
    display_name: str
    original_filename: str
    stored_audio_path: Optional[str] = None
    style_json_path: Optional[str] = None
    embedding_path: Optional[str] = None
    engine_voice_name: Optional[str] = None
    provider_voice_id: Optional[str] = None
    tts_backend: TtsBackend = "supertonic_local"
    duration_seconds: Optional[float] = None
    sample_rate: Optional[int] = None
    created_at: str
    updated_at: Optional[str] = None
    consent_confirmed: bool
    consent_note: str = (
        "User confirmed they own this voice or have explicit consent to clone it."
    )
    status: VoiceStatus
    profile_kind: ProfileKind
    status_message: Optional[str] = None
    cloning_supported: bool = False


class VoiceProfileResponse(BaseModel):
    voice_id: str
    status: VoiceStatus
    provider_voice_id: Optional[str] = None
    engine_voice_name: Optional[str] = None
    name: str
    message: Optional[str] = None
    metadata: VoiceProfileMetadata


class VoiceCloneResponse(BaseModel):
    """Agent-facing clone response (POST /v1/voices/clone)."""

    status: VoiceStatus
    voice_id: str
    provider_voice_id: Optional[str] = None
    name: str
    message: str
    tts_backend: TtsBackend = "supertonic_local"
    metadata: Optional[VoiceProfileMetadata] = None


class VoiceListItem(BaseModel):
    voice_id: str
    provider_voice_id: Optional[str] = None
    engine_voice_name: Optional[str] = None
    name: str
    status: VoiceStatus
    source_type: SourceType
    created_at: str
    updated_at: Optional[str] = None
    tts_usable: bool = False


class VoiceProfileListResponse(BaseModel):
    voices: list[VoiceListItem]
    total: int
    tts_usable_count: int = 0


class VoiceCloningCapability(BaseModel):
    engine: str = "supertonic"
    supports_reference_audio_cloning: bool = False
    supports_voice_builder_json: bool = True
    supports_voice_id_tts: bool = True
    clone_endpoint: str = "/v1/voices/clone"
    upload_endpoint: str = "/v1/voices"
    tts_endpoint: str = "/v1/tts"
    tts_backend_local: str = "supertonic_local"
    tts_backend_cloud: Optional[str] = None
    supported_upload_formats_for_voice_builder: list[str] = Field(
        default_factory=lambda: ["json"]
    )
    supported_tts_formats: list[str] = Field(default_factory=lambda: ["wav", "flac", "ogg"])
    supported_languages: list[str] = Field(default_factory=list)
    message: str = OPEN_SOURCE_MODE_MESSAGE
    notes: str = OPEN_SOURCE_MODE_MESSAGE


def metadata_to_list_item(meta: VoiceProfileMetadata) -> VoiceListItem:
    source_type: SourceType
    if meta.profile_kind == "voice_builder_json":
        source_type = "voice_builder_json"
    elif meta.profile_kind == "reference_audio":
        source_type = "reference_audio"
    else:
        source_type = "unknown"

    tts_usable = (
        meta.status == "ready"
        and meta.tts_backend == "supertonic_local"
        and bool(meta.engine_voice_name)
    )
    return VoiceListItem(
        voice_id=meta.voice_id,
        provider_voice_id=meta.provider_voice_id or meta.engine_voice_name,
        engine_voice_name=meta.engine_voice_name,
        name=meta.display_name,
        status=meta.status,
        source_type=source_type,
        created_at=meta.created_at,
        updated_at=meta.updated_at or meta.created_at,
        tts_usable=tts_usable,
    )


def metadata_to_response(meta: VoiceProfileMetadata) -> VoiceProfileResponse:
    return VoiceProfileResponse(
        voice_id=meta.voice_id,
        status=meta.status,
        provider_voice_id=meta.provider_voice_id or meta.engine_voice_name,
        engine_voice_name=meta.engine_voice_name,
        name=meta.display_name,
        message=meta.status_message,
        metadata=meta,
    )
