#!/usr/bin/env bash
# Upload a Voice Builder JSON (or WAV reference audio) and print voice_id.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# External RunPod: TTS_BASE_URL=http://103.196.86.102:10908
# Pod internal:     TTS_BASE_URL=http://127.0.0.1:7788
BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:7788}"
FILE="${1:-}"

if [[ -z "$FILE" ]]; then
  CACHE_STYLE="${SUPERTONIC_STYLE_JSON:-$HOME/.cache/supertonic3/voice_styles/M1.json}"
  if [[ -f "$CACHE_STYLE" ]]; then
    FILE="$CACHE_STYLE"
    echo "Using bundled style JSON for integration test: $FILE"
  else
    echo "Usage: $0 <voice_builder.json>" >&2
    exit 1
  fi
fi

DISPLAY_NAME="${2:-Test Custom Voice}"

curl -sS -X POST "${BASE_URL}/v1/voices" \
  -F "file=@${FILE}" \
  -F "display_name=${DISPLAY_NAME}" \
  -F "consent_confirmed=true" | python3 -m json.tool
