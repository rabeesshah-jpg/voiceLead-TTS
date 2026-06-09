"""Configure ONNX Runtime GPU providers before Supertonic loads models."""

from __future__ import annotations

import logging
import os
import site
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _site_packages() -> Path:
    paths = site.getsitepackages()
    if paths:
        return Path(paths[0])
    return Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def configure_cuda_library_path() -> list[str]:
    sp = _site_packages()
    lib_dirs: list[str] = []
    for sub in ("nvidia/cudnn/lib", "nvidia/cublas/lib", "nvidia/cuda_runtime/lib"):
        candidate = sp / sub
        if candidate.is_dir():
            lib_dirs.append(str(candidate))
    if not lib_dirs:
        return []
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    parts = [p for p in existing.split(":") if p]
    for lib_dir in lib_dirs:
        if lib_dir not in parts:
            parts.insert(0, lib_dir)
    os.environ["LD_LIBRARY_PATH"] = ":".join(parts)
    return lib_dirs


def resolve_onnx_providers() -> list[str]:
    env = os.getenv("SUPERTONIC_ONNX_PROVIDERS", "").strip()
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    try:
        import onnxruntime as ort
    except ImportError:
        return ["CPUExecutionProvider"]
    if "CUDAExecutionProvider" in ort.get_available_providers():
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def apply_provider_config() -> list[str]:
    configure_cuda_library_path()
    providers = resolve_onnx_providers()
    import supertonic.config as cfg
    cfg.DEFAULT_ONNX_PROVIDERS[:] = providers
    logger.info("Configured ONNX providers: %s", providers)
    return providers


def probe_runtime() -> dict[str, Any]:
    info: dict[str, Any] = {
        "cuda_available": False,
        "cuda_device_name": None,
        "onnx_providers_available": [],
        "gpu_memory_mb": None,
    }
    try:
        import onnxruntime as ort
        info["onnx_providers_available"] = ort.get_available_providers()
    except ImportError:
        return info
    try:
        out = os.popen(
            "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null"
        ).read().strip()
        if out:
            info["cuda_available"] = True
            parts = [p.strip() for p in out.split(",")]
            info["cuda_device_name"] = parts[0]
            if len(parts) >= 3:
                info["gpu_memory_mb"] = {"used": float(parts[1]), "total": float(parts[2])}
    except OSError:
        pass
    return info


def session_providers(tts: Any) -> list[str]:
    try:
        return list(tts.model.dp_ort.get_providers())
    except Exception:
        return []
