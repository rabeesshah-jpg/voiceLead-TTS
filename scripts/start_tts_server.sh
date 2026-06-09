#!/usr/bin/env bash
# Start GPU-enabled Supertonic TTS server (do NOT use bare `supertonic serve`).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

SP="$ROOT/.venv/lib/python3.11/site-packages"
export LD_LIBRARY_PATH="$SP/nvidia/cudnn/lib:$SP/nvidia/cublas/lib:${LD_LIBRARY_PATH:-}"
export SUPERTONIC_ONNX_PROVIDERS="${SUPERTONIC_ONNX_PROVIDERS:-CUDAExecutionProvider,CPUExecutionProvider}"
export TTS_HOST="${TTS_HOST:-0.0.0.0}"
export TTS_PORT="${TTS_PORT:-7788}"
export TTS_WARMUP="${TTS_WARMUP:-1}"
export TTS_WARMUP_STEPS="${TTS_WARMUP_STEPS:-5}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Note: system ffmpeg not found; voice uploads use bundled imageio-ffmpeg for webm/mp3."
fi

echo "Starting GPU TTS server on ${TTS_HOST}:${TTS_PORT}"
exec python3 -m server.main
