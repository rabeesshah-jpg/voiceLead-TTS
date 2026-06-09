"""Integrate voice_id resolution into Supertonic TTS routes."""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .config import VoiceConfig
from .service import VoiceProfileError, VoiceProfileService
from . import supertone_api

logger = logging.getLogger(__name__)


def patch_voice_resolution(service: VoiceProfileService) -> None:
    from supertonic.server import routes as route_mod

    if getattr(route_mod, "_voice_id_patched", False):
        return

    orig_resolve = route_mod._resolve_voice

    def _resolve_voice(state, voice_name: str):
        try:
            return orig_resolve(state, voice_name)
        except route_mod.UnknownVoice:
            pass

        if voice_name.startswith("vp_"):
            custom_path = state.custom_styles.get(voice_name)
            if custom_path is not None and state.tts is not None:
                return state.tts.get_voice_style_from_path(custom_path)
        raise route_mod.UnknownVoice(voice_name)

    route_mod._resolve_voice = _resolve_voice
    route_mod._voice_id_patched = True
    route_mod._voice_profile_service = service


class VoiceIdMiddleware:
    """Resolve voice_id for TTS; cloud profiles synthesize via Supertone API."""

    def __init__(self, app, service: VoiceProfileService, config: Optional[VoiceConfig] = None):
        self.app = app
        self.service = service
        self.config = config or service.config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        if method != "POST" or path not in ("/v1/tts", "/v1/audio/speech"):
            await self.app(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)

        voice_id: Optional[str] = None
        voice_source = "preset"
        try:
            payload = json.loads(body.decode("utf-8") if body else "{}")
            text = payload.get("text") or payload.get("input") or ""
            lang = payload.get("lang") or "en"
            preset_voice = payload.get("voice")
            voice_id = payload.get("voice_id")

            if not voice_id and not preset_voice and self.config.default_voice_id:
                voice_id = self.config.default_voice_id

            logger.info(
                "tts_voice_resolve text_len=%d voice_id=%s preset_voice=%s lang=%s",
                len(text),
                voice_id,
                preset_voice,
                lang,
            )

            if voice_id:
                try:
                    meta = self.service.get_profile_for_tts(str(voice_id))
                except VoiceProfileError as e:
                    code = "unknown_voice_id" if "Unknown" in str(e) else "voice_not_ready"
                    status = 404 if "Unknown" in str(e) else 400
                    await _send_json_error(send, status, str(e), code)
                    return

                if meta.tts_backend == "supertone_cloud":
                    if not meta.provider_voice_id:
                        await _send_json_error(
                            send, 500, "Cloud voice profile missing provider_voice_id", "invalid_profile"
                        )
                        return
                    voice_source = "cloned_cloud"
                    logger.info(
                        "tts_using_cloned_voice voice_id=%s provider_voice_id=%s backend=supertone_cloud",
                        voice_id,
                        meta.provider_voice_id,
                    )
                    start = time.perf_counter()
                    try:
                        audio, ctype = supertone_api.synthesize_speech(
                            provider_voice_id=meta.provider_voice_id,
                            text=text,
                            language=lang,
                        )
                    except supertone_api.SupertoneApiError as e:
                        await _send_json_error(send, 502, str(e), "cloud_tts_failed")
                        return
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    logger.info(
                        "tts_cloned_complete voice_id=%s bytes=%d content_type=%s total_ms=%.1f",
                        voice_id,
                        len(audio),
                        ctype,
                        elapsed_ms,
                    )
                    await _send_audio(
                        send,
                        audio,
                        content_type=ctype,
                        extra_headers=[
                            (b"x-tts-voice-id", str(voice_id).encode("utf-8")),
                            (b"x-tts-voice-source", voice_source.encode("utf-8")),
                            (b"x-tts-provider-voice-id", meta.provider_voice_id.encode("utf-8")),
                            (b"x-tts-server-ms", f"{elapsed_ms:.1f}".encode("ascii")),
                        ],
                    )
                    return

                engine_voice = meta.engine_voice_name
                if not engine_voice:
                    await _send_json_error(
                        send, 500, f"Profile {voice_id!r} is ready but not registered locally.", "invalid_profile"
                    )
                    return
                voice_source = "cloned_local"
                logger.info(
                    "tts_using_cloned_voice voice_id=%s provider_voice_id=%s engine_voice=%s backend=supertonic_local",
                    voice_id,
                    meta.provider_voice_id or engine_voice,
                    engine_voice,
                )
                payload["voice"] = engine_voice
                payload.pop("voice_id", None)
                body = json.dumps(payload).encode("utf-8")
                scope = dict(scope)
                headers = [
                    (k, v)
                    for k, v in scope.get("headers", [])
                    if k.lower() not in (
                        b"content-length",
                        b"x-tts-voice-id",
                        b"x-tts-voice-source",
                        b"x-tts-provider-voice-id",
                    )
                ]
                headers.append((b"content-length", str(len(body)).encode("ascii")))
                headers.append((b"x-tts-voice-id", str(voice_id).encode("utf-8")))
                headers.append((b"x-tts-voice-source", voice_source.encode("utf-8")))
                headers.append((b"x-tts-provider-voice-id", (meta.provider_voice_id or engine_voice).encode("utf-8")))
                scope["headers"] = headers
            else:
                voice_source = "preset"
                logger.info(
                    "tts_using_preset_voice voice=%s",
                    preset_voice or "M1(default)",
                )
                scope = dict(scope)
                headers = list(scope.get("headers", []))
                headers.append((b"x-tts-voice-source", voice_source.encode("utf-8")))
                scope["headers"] = headers

        except json.JSONDecodeError:
            pass

        async def receive_replay():
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, receive_replay, send)


async def _send_json_error(send, status: int, message: str, code: str) -> None:
    payload = json.dumps(
        {"error": {"message": message, "type": "invalid_request_error", "code": code}}
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def _send_audio(
    send,
    audio: bytes,
    *,
    content_type: str,
    extra_headers: list[tuple[bytes, bytes]],
) -> None:
    headers = [
        (b"content-type", content_type.encode("ascii")),
        (b"content-length", str(len(audio)).encode("ascii")),
        *extra_headers,
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": audio})
