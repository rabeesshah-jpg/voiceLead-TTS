#!/usr/bin/env bash
# Synthesize with voice_id and save WAV.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:7788}"
VOICE_ID="${1:-}"
TEXT="${2:-Hello from my custom voice profile.}"
OUT="${3:-/tmp/voice_profile_tts.wav}"

if [[ -z "$VOICE_ID" ]]; then
  echo "Usage: $0 <voice_id> [text] [output.wav]" >&2
  exit 1
fi

START=$(date +%s%3N)
curl -sS -X POST "${BASE_URL}/v1/tts" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"${TEXT}\",\"voice_id\":\"${VOICE_ID}\",\"lang\":\"en\",\"response_format\":\"wav\"}" \
  --output "$OUT"
END=$(date +%s%3N)
MS=$((END - START))

ls -lh "$OUT"
echo "latency_ms=${MS} bytes=$(wc -c <"$OUT") path=$OUT"
