"""Optimized Supertonic TTS server with GPU support for RunPod."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .gpu_setup import apply_provider_config, probe_runtime, session_providers
from .instrumentation import (
    apply_latency_headers,
    finish_request,
    get_current_request,
    mark_body_read_done,
    patch_synthesis_routes,
    run_warmup,
    start_request,
)
from .voices import VoiceConfig, VoiceProfileService
from .voices.integration import VoiceIdMiddleware, patch_voice_resolution
from .voices.routes import register_voice_routes

logger = logging.getLogger(__name__)
_SERVER_START = time.time()
_WARMUP_MS: Optional[float] = None


def _configure_logging() -> None:
    level = os.getenv("TTS_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


class ApiKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: Optional[str]) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if not self.api_key:
            return await call_next(request)
        if request.url.path in ("/health", "/v1/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        token = request.headers.get("x-api-key", "")
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        if token != self.api_key:
            return JSONResponse(status_code=401, content={"error": {"message": "unauthorized", "code": "unauthorized"}})
        return await call_next(request)


class TTSRequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "POST" or request.url.path not in ("/v1/tts", "/v1/audio/speech"):
            return await call_next(request)

        body = await request.body()
        text = ""
        voice = voice_id = lang = None
        steps = speed = None
        try:
            import json

            payload = json.loads(body.decode("utf-8") if body else "{}")
            text = payload.get("text") or payload.get("input") or ""
            voice = payload.get("voice")
            voice_id = payload.get("voice_id") or request.headers.get("x-tts-voice-id")
            lang = payload.get("lang")
            steps = payload.get("steps")
            speed = payload.get("speed")
        except Exception:
            pass

        state = getattr(request.app.state, "server_state", None)
        providers = session_providers(state.tts) if state and state.tts else None
        timing = start_request(
            text,
            endpoint=request.url.path,
            voice=voice,
            voice_id=voice_id,
            lang=lang,
            steps=steps,
            speed=speed,
            onnx_providers=providers,
        )
        mark_body_read_done()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)
        response = await call_next(request)

        pending = get_current_request() or timing
        finished = finish_request(
            response_bytes=pending.response_bytes,
            status_code=response.status_code,
            audio_duration_s=pending.audio_duration_s,
        )
        if finished is not None:
            apply_latency_headers(response, finished)

        return response


def create_optimized_app() -> FastAPI:
    _configure_logging()
    providers = apply_provider_config()
    patch_synthesis_routes()

    from supertonic import __version__
    from supertonic.pipeline import TTS
    from supertonic.server.app import ServerState, create_app

    model = os.getenv("TTS_MODEL", "supertonic-3")
    api_key = os.getenv("TTS_API_KEY", "").strip() or None

    logger.info("Loading TTS model %r with providers %s", model, providers)
    load_start = time.perf_counter()
    tts = TTS(model=model)
    logger.info(
        "Model loaded in %.0fms; active providers: %s",
        (time.perf_counter() - load_start) * 1000,
        session_providers(tts),
    )

    global _WARMUP_MS
    if os.getenv("TTS_WARMUP", "1") not in ("0", "false", "False"):
        _WARMUP_MS = run_warmup(
            tts,
            voice=os.getenv("TTS_WARMUP_VOICE", "M1"),
            lang=os.getenv("TTS_WARMUP_LANG", "en"),
            steps=int(os.getenv("TTS_WARMUP_STEPS", "5")),
        ) * 1000.0

    voice_config = VoiceConfig.from_env()
    voice_service = VoiceProfileService(voice_config)
    patch_voice_resolution(voice_service)
    voice_service.startup(state := ServerState(model=model, tts=tts))
    state.is_ready = True

    cors = os.getenv("TTS_CORS", "").strip()
    cors_origins = [o.strip() for o in cors.split(",") if o.strip()] if cors else None
    base = create_app(state=state, cors_origins=cors_origins)

    register_voice_routes(base, voice_service)
    base.add_middleware(TTSRequestContextMiddleware)
    base.add_middleware(ApiKeyMiddleware, api_key=api_key)
    base.add_middleware(VoiceIdMiddleware, service=voice_service, config=voice_config)

    @base.get("/health")
    def health_detailed():
        runtime = probe_runtime()
        active = session_providers(state.tts) if state.tts else []
        loaded = state.is_ready and state.tts is not None
        return {
            "status": "ok" if loaded else "loading",
            "model_loaded": loaded,
            "cuda_available": runtime.get("cuda_available", False),
            "cuda_device_name": runtime.get("cuda_device_name"),
            "onnx_providers_available": runtime.get("onnx_providers_available", []),
            "onnx_providers_configured": providers,
            "onnx_providers_active": active,
            "model": state.model,
            "sample_rate": state.tts.sample_rate if state.tts else None,
            "version": __version__,
            "uptime_s": round(time.time() - _SERVER_START, 1),
            "warmup_ms": _WARMUP_MS,
            "streaming_supported": False,
            "auth_enabled": api_key is not None,
            "voice_cloning": {
                "enabled": voice_config.enable_voice_cloning,
                "profiles_ready": sum(
                    1 for p in voice_service.store.list_profiles() if p.status == "ready"
                ),
                "profiles_total": len(voice_service.store.list_profiles()),
                "supports_reference_audio_cloning": voice_service.adapter.supports_reference_audio_cloning(),
                "supports_voice_builder_json": voice_service.adapter.supports_voice_builder_json(),
                "open_source_mode": not voice_service.adapter.supports_reference_audio_cloning(),
                "upload_endpoint": "/v1/voices",
                "default_voice_id": voice_config.default_voice_id or None,
            },
        }

    base.state.voice_service = voice_service  # type: ignore[attr-defined]
    return base
