"""Audio validation and normalization for uploaded voice samples."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

SOUNDFILE_EXTENSIONS = {".wav", ".flac", ".ogg"}
FFMPEG_EXTENSIONS = {".mp3", ".m4a", ".webm", ".mp4", ".aac"}
ALLOWED_EXTENSIONS = SOUNDFILE_EXTENSIONS | FFMPEG_EXTENSIONS


class AudioValidationError(ValueError):
    """Raised when an uploaded audio file fails validation."""


def _resolve_ffmpeg() -> str:
    """System ffmpeg, else bundled binary from imageio-ffmpeg (browser webm/mp3)."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return bundled
    except ImportError:
        pass
    raise AudioValidationError(
        f"Format requires ffmpeg. Upload WAV/FLAC/OGG, install system ffmpeg, "
        f"or pip install imageio-ffmpeg."
    )


def _read_with_ffmpeg(path: Path, target_sample_rate: int) -> tuple[np.ndarray, int]:
    ffmpeg_bin = _resolve_ffmpeg()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                str(path),
                "-ar",
                str(target_sample_rate),
                "-ac",
                "1",
                str(tmp_path),
            ],
            check=True,
            capture_output=True,
        )
        data, sr = sf.read(tmp_path, dtype="float32", always_2d=False)
        return np.asarray(data, dtype=np.float32), int(sr)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise AudioValidationError(f"ffmpeg failed to decode audio: {stderr}") from e
    finally:
        tmp_path.unlink(missing_ok=True)


def load_audio(path: Path, target_sample_rate: int) -> tuple[np.ndarray, int, float]:
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported format {suffix!r}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    if suffix in FFMPEG_EXTENSIONS:
        data, sr = _read_with_ffmpeg(path, target_sample_rate)
    else:
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        data = np.asarray(data, dtype=np.float32)
        if data.ndim > 1:
            data = data.mean(axis=1)

    if data.size == 0:
        raise AudioValidationError("Audio file is empty.")

    duration = float(data.shape[0]) / float(sr)
    if sr != target_sample_rate:
        # Linear resample without adding scipy dependency.
        x_old = np.linspace(0.0, 1.0, num=data.shape[0], endpoint=False)
        new_len = max(1, int(round(duration * target_sample_rate)))
        x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
        data = np.interp(x_new, x_old, data).astype(np.float32)
        sr = target_sample_rate

    return data, sr, duration


def normalize_and_save_wav(
    source: Path,
    destination: Path,
    *,
    target_sample_rate: int,
    min_seconds: float,
    max_seconds: float,
) -> tuple[float, int]:
    data, sr, duration = load_audio(source, target_sample_rate)
    if duration < min_seconds:
        raise AudioValidationError(
            f"Audio too short ({duration:.1f}s). Minimum is {min_seconds:.0f}s."
        )
    if duration > max_seconds:
        raise AudioValidationError(
            f"Audio too long ({duration:.1f}s). Maximum is {max_seconds:.0f}s."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, data, sr, subtype="PCM_16")
    return duration, sr
