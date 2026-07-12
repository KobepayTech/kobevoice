# Kobevoice SaaS — Quickstart

Kobevoice ships two things:

1. **The local-first app** (desktop/web) — the original, runs entirely on your
   machine.
2. **Kobevoice Cloud** — a multi-user SaaS: accounts, plans & billing, per-user
   generation history, and a metered voice API. This guide covers the SaaS.

## Architecture

```
 Browser ──► Cloud control-plane ──► Voice engine (stateless /synthesize)
   │           backend/cloud            backend/engine_lite  (or Qwen/Whisper)
   │           • auth (JWT)
   │           • plans + Stripe billing
   │           • per-month quotas
   │           • per-user audio + history   ◄── the cloud owns all tenant data
   └── dashboard (served by the cloud)
```

The engine is **stateless** — it only turns text into audio. Everything that
makes the product multi-tenant (users, subscriptions, quotas, stored audio)
lives in the control-plane, so you can scale or swap the engine freely.

## Run the whole stack (Docker)

```bash
docker compose -f docker-compose.cloud.yml up --build
# Dashboard: http://localhost:9000   (sign in, generate, upgrade, manage keys)
```

Billing runs in **dev-mode** so upgrades work without Stripe. Admin login uses
`KOBEVOICE_CLOUD_ADMIN_EMAIL` / `_PASSWORD` from the compose file.

## Run locally (no Docker)

```bash
# 1. Voice engine (real, GPU-free TTS)
sudo apt-get install -y espeak-ng        # or: brew install espeak-ng
python -m venv .venv && source .venv/bin/activate
pip install -r backend/cloud/requirements.txt
uvicorn backend.engine_lite.main:app --port 8000 &

# 2. Cloud control-plane
export KOBEVOICE_CLOUD_ENGINE_URL=http://127.0.0.1:8000
export KOBEVOICE_CLOUD_BILLING_DEV_MODE=true
uvicorn backend.cloud.main:app --port 9000
# → http://localhost:9000
```

## Connect the web studio app

The browser build gates behind cloud login and sends the user's token with every
request:

```bash
cd web
VITE_CLOUD_URL=http://localhost:9000 bun run dev
```

## Going to production

| Dev default | Production |
|-------------|------------|
| `engine_lite` (espeak-ng) | The Qwen/Whisper engine exposing the same `/synthesize` contract |
| SQLite | Postgres via `KOBEVOICE_CLOUD_DATABASE_URL` |
| `BILLING_DEV_MODE=true` | Real Stripe: set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_CLOUD`, `STRIPE_PRICE_PRO`; point a Stripe webhook at `/api/billing/webhook` |
| Local `AUDIO_DIR` | Object storage (S3/R2) |
| Dev `JWT_SECRET` | A long random secret |

See [`backend/cloud/README.md`](../backend/cloud/README.md) and
[`backend/engine_lite/README.md`](../backend/engine_lite/README.md) for details.
