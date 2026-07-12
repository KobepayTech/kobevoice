# Kobevoice Lite Engine

A real, **GPU-free** text-to-speech worker that implements the stateless
synthesis contract the [cloud control-plane](../cloud/README.md) proxies to.

It shells out to the offline `espeak-ng` binary — no model downloads, no GPU —
so the whole SaaS runs end-to-end on any box. In production, point
`KOBEVOICE_CLOUD_ENGINE_URL` at the full Qwen/Whisper engine instead; give that
engine a `/synthesize` route with the same contract and nothing in the
control-plane changes.

## Contract

```
POST /synthesize
{ "text": "Hello", "voice": "female", "language": "en", "speed": 175 }
→ 200  audio/wav  (raw bytes)

GET /health   → { status, espeak, voices }
GET /voices   → { voices: [...] }
```

## Run

```bash
# Debian/Ubuntu
sudo apt-get install -y espeak-ng
# macOS
# brew install espeak-ng

uvicorn backend.engine_lite.main:app --port 8000
```

Then start the control-plane with `KOBEVOICE_CLOUD_ENGINE_URL=http://127.0.0.1:8000`.
