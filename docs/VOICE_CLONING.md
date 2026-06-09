# Voice profiles and custom voices (open-source Supertonic)

This server adds a **voice profile** layer on top of open-source Supertonic TTS.
Preset voices (`M1`, `F1`, …) and `POST /v1/tts` remain backward-compatible.

## Architecture

```
Local app / frontend  →  Local backend  →  RunPod TTS server
```

The frontend must **not** call RunPod directly. The local backend proxies voice
upload and TTS requests.

## Ports and URLs

| Context | URL |
|---------|-----|
| RunPod external (agent/backend) | `http://103.196.86.102:10908` |
| Pod internal (server bind) | `0.0.0.0:7788` |

RunPod maps **internal 7788 → external 10908**. Do **not** use port `10907`.

Agent `.env`:

```bash
TTS_BASE_URL=http://103.196.86.102:10908
AGENT_TTS_VOICE_ID=<voice_id>   # optional default for TTS
```

## Open-source mode (no SUPERTONE_API_KEY)

| Feature | Supported? |
|---------|------------|
| Built-in voices (`M1`, `F1`, …) | Yes |
| Voice Builder JSON (`style_ttl` + `style_dp`) | Yes → `status=ready` |
| Raw reference audio (`.webm`, `.wav`, …) | **No** — `POST /v1/voices/clone` returns HTTP **422** |
| Celebrity / public-figure impersonation | **Not supported** |

Supertonic uses precomputed **style vectors**, not runtime reference-audio
conditioning. The working custom-voice path:

1. Create a voice in [Supertone Voice Builder](https://supertonic.supertone.ai/voice-builder).
2. Download the Supertonic 3 JSON export.
3. Upload via `POST /v1/voices`.
4. Synthesize with `voice_id` in `POST /v1/tts`.

## Environment variables

```bash
# Server (RunPod pod)
TTS_HOST=0.0.0.0
TTS_PORT=7788
VOICE_PROFILE_STORAGE_DIR=data/voices
ENABLE_VOICE_CLONING=true
MAX_VOICE_JSON_BYTES=2097152          # 2 MiB default
TTS_API_KEY=                          # optional; protects voice + TTS endpoints
TTS_WARMUP=1

# Agent (local backend)
TTS_BASE_URL=http://103.196.86.102:10908
```

## API

### Capabilities

```bash
curl -s http://103.196.86.102:10908/v1/voices/capabilities | python3 -m json.tool
```

Returns `supports_reference_audio_cloning: false` in open-source mode, plus
`supported_upload_formats_for_voice_builder`, `supported_tts_formats`, and
`supported_languages`.

### Upload Voice Builder JSON (production path)

```bash
curl -X POST http://103.196.86.102:10908/v1/voices \
  -F "file=@my_voice.json" \
  -F "display_name=My Voice" \
  -F "consent_confirmed=true"
```

Requirements:

- `.json` extension only
- Valid Voice Builder JSON with `style_ttl` and `style_dp`
- `consent_confirmed=true` (required)
- `display_name` or `name` (required)

Response when ready:

```json
{
  "voice_id": "<uuid>",
  "status": "ready",
  "provider_voice_id": "vp_<uuid>",
  "engine_voice_name": "vp_<uuid>",
  "name": "My Voice",
  "message": "Voice profile created and ready for local TTS."
}
```

Invalid JSON never returns `ready` — the profile is not persisted.

### Reference audio clone (not available in open-source mode)

```bash
curl -s -X POST http://103.196.86.102:10908/v1/voices/clone \
  -F "file=@recording.webm" \
  -F "name=My Voice" \
  -F "consent_confirmed=true"
```

Expected in open-source mode:

```json
{
  "error": {
    "message": "Reference audio cloning is not available in open-source mode. Upload a Voice Builder JSON file using POST /v1/voices.",
    "code": "reference_audio_clone_not_supported"
  }
}
```

HTTP **422**. No `stored_only` profile is created.

### List voices

```bash
curl -s http://103.196.86.102:10908/v1/voices | python3 -m json.tool
```

Each entry includes `tts_usable` (true only for `status=ready` local profiles),
`source_type`, `provider_voice_id`, and timestamps.

### TTS

Preset (unchanged):

```bash
curl -X POST http://103.196.86.102:10908/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello","voice":"M1","lang":"en","response_format":"wav"}' \
  -o preset.wav
```

Custom voice:

```bash
curl -X POST http://103.196.86.102:10908/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello","voice_id":"<voice_id>","lang":"en","response_format":"wav"}' \
  -D - -o custom.wav
```

Response headers include `X-TTS-Voice-Id`, `X-TTS-Voice-Source`, and
`X-TTS-Gen-Ms`. Unknown `voice_id` → 404; non-ready → 400. No silent fallback
to `M1` when `voice_id` is provided.

## Verification commands

Run from any machine that can reach the RunPod external URL:

```bash
BASE=http://103.196.86.102:10908

# Health
curl -s $BASE/health | python3 -m json.tool

# Capabilities (open-source message)
curl -s $BASE/v1/voices/capabilities | python3 -m json.tool

# List voices (check tts_usable)
curl -s $BASE/v1/voices | python3 -m json.tool

# Upload Voice Builder JSON
curl -s -X POST $BASE/v1/voices \
  -F "file=@my_voice.json" \
  -F "display_name=Test Voice" \
  -F "consent_confirmed=true" | python3 -m json.tool

# TTS with returned voice_id
curl -s -D - -X POST $BASE/v1/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello","voice_id":"<voice_id>","lang":"en","response_format":"wav"}' \
  -o /tmp/custom.wav

# Reference audio clone must fail with 422
curl -s -w "\nHTTP %{http_code}\n" -X POST $BASE/v1/voices/clone \
  -F "file=@recording.webm" \
  -F "name=Test" \
  -F "consent_confirmed=true" | python3 -m json.tool
```

Pod-internal equivalents use `http://127.0.0.1:7788` instead of `$BASE`.

## Test scripts

```bash
export TTS_BASE_URL=http://103.196.86.102:10908   # or http://127.0.0.1:7788 on pod

./scripts/test_voice_upload.sh path/to/voice.json
./scripts/test_voice_tts.sh <voice_id>
```

## Storage

Profiles persist under `data/voices/` (gitignored, not served publicly):

```
data/voices/index.json
data/voices/<voice_id>/metadata.json
data/voices/<voice_id>/style.json
```

Ready Voice Builder profiles are re-registered into Supertonic on startup.
`stored_only` and `failed` profiles are skipped.

## Latency

- Model loads once at startup; warmup enabled by default.
- Voice Builder JSON registers style once at upload; synthesis reuses cached ONNX sessions.
- Supertonic outputs **44100 Hz** WAV.
