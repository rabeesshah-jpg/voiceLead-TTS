"""HTTP routes for voice profile management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from supertonic.config import AVAILABLE_LANGUAGES
from supertonic.server.audio import SUPPORTED_FORMATS

from . import supertone_api
from .schemas import (
    VoiceCloneResponse,
    VoiceCloningCapability,
    VoiceProfileListResponse,
    VoiceProfileResponse,
    metadata_to_list_item,
    metadata_to_response,
)
from .service import ReferenceAudioCloneNotSupported, VoiceProfileError, VoiceProfileService

if TYPE_CHECKING:
    from supertonic.server.app import ServerState

logger = logging.getLogger(__name__)


def _state(request: Request) -> "ServerState":
    return request.app.state.server_state  # type: ignore[no-any-return]


def _error(status_code: int, message: str, code: str):
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": "invalid_request_error", "code": code}},
    )


def _parse_consent(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def register_voice_routes(app, service: VoiceProfileService) -> None:
    router = APIRouter()

    @router.get("/v1/voices/capabilities", response_model=VoiceCloningCapability)
    def cloning_capabilities():
        cloud = supertone_api.api_key_configured()
        message = service.capability_message()
        return VoiceCloningCapability(
            supports_reference_audio_cloning=cloud and service.adapter.supports_reference_audio_cloning(),
            supports_voice_builder_json=service.adapter.supports_voice_builder_json(),
            supports_voice_id_tts=True,
            clone_endpoint="/v1/voices/clone",
            upload_endpoint="/v1/voices",
            tts_endpoint="/v1/tts",
            tts_backend_local="supertonic_local",
            tts_backend_cloud="supertone_cloud" if cloud else None,
            supported_upload_formats_for_voice_builder=["json"],
            supported_tts_formats=list(SUPPORTED_FORMATS),
            supported_languages=list(AVAILABLE_LANGUAGES),
            message=message,
            notes=message,
        )

    @router.get("/v1/voices", response_model=VoiceProfileListResponse)
    def list_voices():
        items = [metadata_to_list_item(meta) for meta in service.store.list_profiles()]
        usable = sum(1 for item in items if item.tts_usable)
        return VoiceProfileListResponse(voices=items, total=len(items), tts_usable_count=usable)

    # Must be registered BEFORE /v1/voices/{voice_id}
    @router.post("/v1/voices/clone", response_model=VoiceCloneResponse)
    async def clone_voice(
        request: Request,
        file: UploadFile = File(...),
        name: str = Form(...),
        display_name: Optional[str] = Form(None),
        consent_confirmed: Optional[str] = Form(None),
    ):
        state = _state(request)
        if state.tts is None:
            return _error(503, "server not ready", "not_ready")

        profile_name = (display_name or name or "").strip()
        logger.info(
            "clone_endpoint_hit name=%r filename=%s content_type=%s",
            profile_name,
            file.filename,
            file.content_type,
        )
        if not _parse_consent(consent_confirmed):
            return _error(
                400,
                "consent_confirmed must be true. Only upload voices you own or have explicit consent to clone.",
                "consent_required",
            )
        try:
            return await service.clone_profile(
                state,
                upload=file,
                name=profile_name,
                consent_confirmed=True,
            )
        except ReferenceAudioCloneNotSupported as e:
            return _error(422, str(e), "reference_audio_clone_not_supported")
        except VoiceProfileError as e:
            msg = str(e)
            if "POST /v1/voices" in msg:
                return _error(422, msg, "reference_audio_clone_not_supported")
            return _error(400, msg, "invalid_voice_clone")

    @router.get("/v1/voices/{voice_id}", response_model=VoiceProfileResponse)
    def get_voice(voice_id: str):
        try:
            meta = service.get_profile(voice_id)
        except VoiceProfileError as e:
            return _error(404, str(e), "unknown_voice_id")
        return metadata_to_response(meta)

    @router.post("/v1/voices", response_model=VoiceProfileResponse)
    async def upload_voice(
        request: Request,
        file: UploadFile = File(...),
        display_name: Optional[str] = Form(None),
        name: Optional[str] = Form(None),
        consent_confirmed: str = Form(...),
    ):
        state = _state(request)
        if state.tts is None:
            return _error(503, "server not ready", "not_ready")
        if not _parse_consent(consent_confirmed):
            return _error(
                400,
                "consent_confirmed must be true. Only upload voices you own or have explicit consent to clone.",
                "consent_required",
            )
        profile_name = (display_name or name or "").strip()
        if not profile_name:
            return _error(400, "display_name or name is required.", "invalid_voice_upload")
        try:
            return await service.create_profile(
                state,
                upload=file,
                display_name=profile_name,
                consent_confirmed=True,
            )
        except VoiceProfileError as e:
            return _error(400, str(e), "invalid_voice_upload")

    @router.delete("/v1/voices/{voice_id}", response_model=VoiceProfileResponse)
    def delete_voice(voice_id: str, request: Request):
        state = _state(request)
        try:
            meta = service.delete_profile(state, voice_id)
        except VoiceProfileError as e:
            return _error(404, str(e), "unknown_voice_id")
        return metadata_to_response(meta)

    app.include_router(router)
