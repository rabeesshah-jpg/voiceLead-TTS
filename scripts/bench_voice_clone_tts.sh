#!/usr/bin/env bash
# Benchmark default voice vs custom voice_id.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:7788}"
VOICE_ID="${1:-}"
TEXT="${2:-Benchmark sentence for voice cloning latency.}"
RUNS="${3:-3}"

bench() {
  local label="$1"
  local payload="$2"
  local total=0
  local i
  for ((i = 1; i <= RUNS; i++)); do
    local out="/tmp/bench_${label}_${i}.wav"
    local start end ms size
    start=$(date +%s%3N)
    curl -sS -X POST "${BASE_URL}/v1/tts" \
      -H "Content-Type: application/json" \
      -d "$payload" \
      --output "$out"
    end=$(date +%s%3N)
    ms=$((end - start))
    size=$(wc -c <"$out")
    total=$((total + ms))
    echo "  run $i: ${ms}ms ${size} bytes"
  done
  echo "  avg_ms=$((total / RUNS))"
}

echo "=== Default voice (M1) ==="
bench default '{"text":"'"$TEXT"'","voice":"M1","lang":"en","response_format":"wav"}'

if [[ -n "$VOICE_ID" ]]; then
  echo "=== Custom voice_id ($VOICE_ID) ==="
  bench custom '{"text":"'"$TEXT"'","voice_id":"'"$VOICE_ID"'","lang":"en","response_format":"wav"}'
else
  echo "Skip custom voice: pass voice_id as first arg (upload via scripts/test_voice_upload.sh)" >&2
fi
