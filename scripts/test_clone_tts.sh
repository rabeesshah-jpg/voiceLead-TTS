#!/usr/bin/env bash
# Open-source flow: upload Voice Builder JSON -> TTS with voice_id.
# Reference audio clone is expected to return HTTP 422.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:7788}"
SAMPLE="${1:-}"

echo "=== Capabilities ==="
curl -sS "${BASE_URL}/v1/voices/capabilities" | python3 -m json.tool

if [[ -z "$SAMPLE" ]]; then
  SAMPLE="${SUPERTONIC_STYLE_JSON:-$HOME/.cache/supertonic3/voice_styles/M1.json}"
fi

if [[ ! -f "$SAMPLE" ]]; then
  echo "Usage: $0 [voice_builder.json]" >&2
  echo "No sample file found at: $SAMPLE" >&2
  exit 1
fi

SUFFIX="${SAMPLE##*.}"
if [[ "${SUFFIX,,}" != "json" ]]; then
  echo "=== Reference audio clone (expect 422 in open-source mode) ==="
  HTTP_CODE=$(curl -sS -o /tmp/clone_error.json -w "%{http_code}" -X POST "${BASE_URL}/v1/voices/clone" \
    -F "file=@${SAMPLE}" \
    -F "name=Clone Test Voice" \
    -F "consent_confirmed=true")
  cat /tmp/clone_error.json | python3 -m json.tool
  if [[ "$HTTP_CODE" != "422" ]]; then
    echo "Expected HTTP 422 for reference audio clone, got $HTTP_CODE" >&2
    exit 1
  fi
  echo "Reference audio clone correctly rejected (HTTP 422)."
  exit 0
fi

echo ""
echo "=== Upload Voice Builder JSON: $SAMPLE ==="
UPLOAD_JSON=$(curl -sS -X POST "${BASE_URL}/v1/voices" \
  -F "file=@${SAMPLE}" \
  -F "display_name=Clone Test Voice" \
  -F "consent_confirmed=true")
echo "$UPLOAD_JSON" | python3 -m json.tool

VOICE_ID=$(echo "$UPLOAD_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('voice_id',''))")
STATUS=$(echo "$UPLOAD_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))")

if [[ "$STATUS" != "ready" || -z "$VOICE_ID" ]]; then
  echo "Upload did not return ready voice_id." >&2
  exit 1
fi

TEXT="${CLONE_TEST_TEXT:-Hello, this is a custom voice test.}"

echo ""
echo "=== TTS custom voice_id=$VOICE_ID ==="
START=$(date +%s%3N)
curl -sS -D /tmp/clone_tts_headers.txt -X POST "${BASE_URL}/v1/tts" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"${TEXT}\",\"voice_id\":\"${VOICE_ID}\",\"lang\":\"en\",\"response_format\":\"wav\"}" \
  --output /tmp/cloned_test.wav
END=$(date +%s%3N)
echo "latency_ms=$((END - START)) bytes=$(wc -c </tmp/cloned_test.wav)"
grep -i "x-tts-voice" /tmp/clone_tts_headers.txt || true
ls -lh /tmp/cloned_test.wav

echo ""
echo "=== TTS preset M1 ==="
START=$(date +%s%3N)
curl -sS -D /tmp/preset_tts_headers.txt -X POST "${BASE_URL}/v1/tts" \
  -H "Content-Type: application/json" \
  -d "{\"text\":\"${TEXT}\",\"voice\":\"M1\",\"lang\":\"en\",\"response_format\":\"wav\"}" \
  --output /tmp/preset_test.wav
END=$(date +%s%3N)
echo "latency_ms=$((END - START)) bytes=$(wc -c </tmp/preset_test.wav)"
grep -i "x-tts-voice" /tmp/preset_tts_headers.txt || true
ls -lh /tmp/preset_test.wav

echo ""
echo "Done. Compare /tmp/cloned_test.wav vs /tmp/preset_test.wav"
