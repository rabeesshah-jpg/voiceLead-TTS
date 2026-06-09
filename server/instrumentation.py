"""Request-scoped TTS timing and startup warmup.

Server-side timings measure model + encoding + FastAPI handler overhead only.
Network/client latency is NOT visible here — clients should compare their
round-trip time against the X-TTS-* response headers this module sets.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("supertonic.tts.timing")
_current_request: ContextVar[Optional["RequestTiming"]] = ContextVar("tts_request_timing", default=None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ms(start: float, end: float) -> float:
    return (end - start) * 1000.0


@dataclass
class RequestTiming:
    request_id: str
    text: str
    received_at: float
    received_at_iso: str = field(default_factory=_now_iso)
    endpoint: str = "/v1/tts"
    voice: Optional[str] = None
    voice_id: Optional[str] = None
    lang: Optional[str] = None
    steps: Optional[int] = None
    speed: Optional[float] = None
    body_read_done_at: Optional[float] = None
    handler_enter_at: Optional[float] = None
    generation_start: Optional[float] = None
    generation_done: Optional[float] = None
    encoding_start: Optional[float] = None
    encoding_done: Optional[float] = None
    response_ready_at: Optional[float] = None
    response_bytes: int = 0
    audio_duration_s: Optional[float] = None
    gpu_mem_before: Optional[dict[str, float]] = None
    gpu_mem_after: Optional[dict[str, float]] = None
    onnx_providers: Optional[list[str]] = None
    status_code: int = 200

    @property
    def text_length(self) -> int:
        return len(self.text)

    def queue_wait_ms(self) -> Optional[float]:
        if self.body_read_done_at is None or self.generation_start is None:
            return None
        return _ms(self.body_read_done_at, self.generation_start)

    def generation_ms(self) -> Optional[float]:
        if self.generation_start is None or self.generation_done is None:
            return None
        return _ms(self.generation_start, self.generation_done)

    def encoding_ms(self) -> Optional[float]:
        if self.encoding_start is None or self.encoding_done is None:
            return None
        return _ms(self.encoding_start, self.encoding_done)

    def server_total_ms(self) -> float:
        end = self.response_ready_at or self.encoding_done or self.generation_done or time.perf_counter()
        return _ms(self.received_at, end)

    def first_byte_ready_ms(self) -> float:
        """Buffered TTS: first byte is sent only after full generation + encoding."""
        end = self.encoding_done or self.generation_done or time.perf_counter()
        return _ms(self.received_at, end)

    def overhead_ms(self) -> Optional[float]:
        gen = self.generation_ms() or 0.0
        enc = self.encoding_ms() or 0.0
        total = self.server_total_ms()
        overhead = total - gen - enc
        return max(0.0, overhead)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "received_at": self.received_at_iso,
            "text_length": self.text_length,
            "voice": self.voice,
            "voice_id": self.voice_id,
            "lang": self.lang,
            "steps": self.steps,
            "speed": self.speed,
            "queue_wait_ms": self.queue_wait_ms(),
            "generation_ms": self.generation_ms(),
            "encoding_ms": self.encoding_ms(),
            "overhead_ms": self.overhead_ms(),
            "server_total_ms": round(self.server_total_ms(), 1),
            "first_byte_ready_ms": round(self.first_byte_ready_ms(), 1),
            "response_bytes": self.response_bytes,
            "audio_duration_s": self.audio_duration_s,
            "status_code": self.status_code,
            "onnx_providers": self.onnx_providers,
            "gpu_mem_before": self.gpu_mem_before,
            "gpu_mem_after": self.gpu_mem_after,
            "network_note": (
                "Network/client latency is not measured server-side. "
                "Compare client round-trip minus X-TTS-Server-Ms header."
            ),
        }

    def log_received(self) -> None:
        logger.info(
            "tts_received request_id=%s endpoint=%s text_len=%d voice=%s voice_id=%s lang=%s steps=%s speed=%s ts=%s",
            self.request_id,
            self.endpoint,
            self.text_length,
            self.voice,
            self.voice_id,
            self.lang,
            self.steps,
            self.speed,
            self.received_at_iso,
        )

    def log_summary(self) -> None:
        payload = self.as_dict()
        logger.info(
            "tts_complete request_id=%s text_len=%d queue_ms=%s gen_ms=%s enc_ms=%s "
            "overhead_ms=%s server_total_ms=%.1f first_byte_ready_ms=%.1f bytes=%d audio_s=%s gpu_before=%s gpu_after=%s",
            self.request_id,
            self.text_length,
            f"{payload['queue_wait_ms']:.1f}" if payload["queue_wait_ms"] is not None else "n/a",
            f"{payload['generation_ms']:.1f}" if payload["generation_ms"] is not None else "n/a",
            f"{payload['encoding_ms']:.1f}" if payload["encoding_ms"] is not None else "n/a",
            f"{payload['overhead_ms']:.1f}" if payload["overhead_ms"] is not None else "n/a",
            payload["server_total_ms"],
            payload["first_byte_ready_ms"],
            self.response_bytes,
            f"{self.audio_duration_s:.3f}" if self.audio_duration_s is not None else "n/a",
            self.gpu_mem_before,
            self.gpu_mem_after,
        )
        if _json_logs_enabled():
            logger.info("tts_latency_json %s", json.dumps(payload, separators=(",", ":")))


def _json_logs_enabled() -> bool:
    import os
    return os.getenv("TTS_LATENCY_LOG_JSON", "").lower() in ("1", "true", "yes")


def _gpu_mem_snapshot() -> Optional[dict[str, float]]:
    try:
        import os

        out = os.popen(
            "nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null"
        ).read().strip()
        if not out:
            return None
        used, total = [float(x.strip()) for x in out.split(",")[:2]]
        return {"used_mb": used, "total_mb": total}
    except Exception:
        return None


def start_request(
    text: str,
    *,
    endpoint: str = "/v1/tts",
    voice: Optional[str] = None,
    voice_id: Optional[str] = None,
    lang: Optional[str] = None,
    steps: Optional[int] = None,
    speed: Optional[float] = None,
    onnx_providers: Optional[list[str]] = None,
) -> RequestTiming:
    timing = RequestTiming(
        request_id=str(uuid.uuid4())[:8],
        text=text,
        received_at=time.perf_counter(),
        endpoint=endpoint,
        voice=voice,
        voice_id=voice_id,
        lang=lang,
        steps=steps,
        speed=speed,
        gpu_mem_before=_gpu_mem_snapshot(),
        onnx_providers=onnx_providers,
    )
    _current_request.set(timing)
    timing.log_received()
    return timing


def mark_body_read_done() -> None:
    timing = _current_request.get()
    if timing and timing.body_read_done_at is None:
        timing.body_read_done_at = time.perf_counter()


def mark_handler_enter() -> None:
    timing = _current_request.get()
    if timing and timing.handler_enter_at is None:
        timing.handler_enter_at = time.perf_counter()


def get_current_request() -> Optional[RequestTiming]:
    return _current_request.get()


def apply_latency_headers(response: Any, timing: RequestTiming) -> None:
    """Attach server-side timing headers for client/network correlation."""
    headers = response.headers
    headers["X-Request-Id"] = timing.request_id
    headers["X-TTS-Server-Ms"] = f"{timing.server_total_ms():.1f}"
    headers["X-TTS-First-Byte-Ms"] = f"{timing.first_byte_ready_ms():.1f}"
    if timing.generation_ms() is not None:
        headers["X-TTS-Gen-Ms"] = f"{timing.generation_ms():.1f}"
    if timing.encoding_ms() is not None:
        headers["X-TTS-Enc-Ms"] = f"{timing.encoding_ms():.1f}"
    if timing.queue_wait_ms() is not None:
        headers["X-TTS-Queue-Ms"] = f"{timing.queue_wait_ms():.1f}"
    headers["X-TTS-Network-Note"] = "client_rtt_minus_X-TTS-Server-Ms"
    if timing.voice_id:
        headers["X-TTS-Voice-Id"] = timing.voice_id
        headers["X-TTS-Voice-Source"] = "cloned_local"
    elif timing.voice and str(timing.voice).startswith("vp_"):
        headers["X-TTS-Voice-Source"] = "cloned_local"
        headers["X-TTS-Provider-Voice-Id"] = str(timing.voice)
    else:
        headers["X-TTS-Voice-Source"] = "preset"
        if timing.voice:
            headers["X-TTS-Preset-Voice"] = str(timing.voice)


def finish_request(
    response_bytes: int = 0,
    *,
    status_code: int = 200,
    audio_duration_s: Optional[float] = None,
) -> Optional[RequestTiming]:
    timing = _current_request.get()
    if timing is None:
        return None
    timing.response_bytes = response_bytes
    timing.status_code = status_code
    timing.audio_duration_s = audio_duration_s
    timing.response_ready_at = time.perf_counter()
    timing.gpu_mem_after = _gpu_mem_snapshot()
    timing.log_summary()
    _current_request.set(None)
    return timing


def patch_synthesis_routes() -> None:
    import numpy as np
    from supertonic.server import routes as route_mod

    if getattr(route_mod, "_tts_instrumented", False):
        return

    orig_synth = route_mod._do_synthesize
    orig_encode = route_mod.encode_audio
    orig_response = route_mod._audio_response

    def _do_synthesize(*args, **kwargs):
        timing = get_current_request()
        if timing:
            mark_handler_enter()
            if timing.generation_start is None:
                timing.generation_start = time.perf_counter()
                logger.info(
                    "tts_generation_start request_id=%s ts=%s",
                    timing.request_id,
                    _now_iso(),
                )
        try:
            return orig_synth(*args, **kwargs)
        finally:
            if timing and timing.generation_done is None:
                timing.generation_done = time.perf_counter()
                gen_ms = timing.generation_ms()
                logger.info(
                    "tts_generation_done request_id=%s gen_ms=%s ts=%s",
                    timing.request_id,
                    f"{gen_ms:.1f}" if gen_ms is not None else "n/a",
                    _now_iso(),
                )

    def encode_audio(wav: np.ndarray, sample_rate: int, fmt: str) -> bytes:
        timing = get_current_request()
        if timing and timing.encoding_start is None:
            timing.encoding_start = time.perf_counter()
            logger.info(
                "tts_encoding_start request_id=%s ts=%s",
                timing.request_id,
                _now_iso(),
            )
        try:
            return orig_encode(wav, sample_rate, fmt)
        finally:
            if timing and timing.encoding_done is None:
                timing.encoding_done = time.perf_counter()
                enc_ms = timing.encoding_ms()
                logger.info(
                    "tts_encoding_done request_id=%s enc_ms=%s ts=%s",
                    timing.request_id,
                    f"{enc_ms:.1f}" if enc_ms is not None else "n/a",
                    _now_iso(),
                )

    def _audio_response(state, wav, fmt, duration_s):
        response = orig_response(state, wav, fmt, duration_s)
        timing = get_current_request()
        if timing:
            timing.response_bytes = len(response.body) if response.body else 0
            timing.audio_duration_s = float(duration_s)
            timing.response_ready_at = time.perf_counter()
        return response

    route_mod._do_synthesize = _do_synthesize
    route_mod.encode_audio = encode_audio
    route_mod._audio_response = _audio_response
    route_mod._tts_instrumented = True


def run_warmup(tts: Any, *, voice: str = "M1", lang: str = "en", steps: int = 5) -> float:
    style = tts.get_voice_style(voice)
    start = time.perf_counter()
    tts.synthesize("Hi.", voice_style=style, lang=lang, total_steps=steps)
    elapsed = time.perf_counter() - start
    logger.info("Warmup synthesis completed in %.3fs (steps=%d)", elapsed, steps)
    return elapsed
