# Kobevoice Cloud — SaaS control-plane

A lightweight FastAPI service that turns the local-first Kobevoice engine into a
multi-user SaaS. It is **deliberately separate** from the GPU/ML backend so it
can scale, deploy, and be tested independently (no `torch` dependency).

## What it provides

| Area | Endpoints |
|------|-----------|
| **Auth** (email/password + JWT) | `POST /api/auth/register`, `/login`, `/change-password`, `GET /api/auth/me` |
| **Billing** (Stripe) | `GET /api/billing/plans`, `/subscription`, `POST /api/billing/checkout`, `/portal`, `/webhook` |
| **Usage & quotas** | `GET /api/usage` |
| **API keys** (headless/agent access) | `GET/POST /api/api-keys`, `DELETE /api/api-keys/{id}` |
| **Voice gateway** (metered) | `POST /api/voice/speak` — auth + quota, then proxies to the engine |
| **Admin backoffice** | `GET /api/admin/stats`, `/users`, `POST /api/admin/users/{id}/disable|enable` |

## Roles

- **Public users** self-serve: register, land on the **Free** plan, optionally
  upgrade to **Cloud**/**Pro** via Stripe, and consume the voice engine under
  per-month quotas.
- **Admin** (the internal Kobevoice "studio" operator) is seeded from
  `KOBEVOICE_CLOUD_ADMIN_*` and can view stats and manage accounts.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/cloud/requirements.txt
cp backend/cloud/.env.example .env   # edit secrets
uvicorn backend.cloud.main:app --reload --port 9000
# Swagger UI: http://localhost:9000/docs
```

The control-plane proxies generations to the voice engine at
`KOBEVOICE_CLOUD_ENGINE_URL` (the existing `uvicorn backend.main:app`).

## Test

```bash
pip install pytest
pytest backend/cloud/tests -q
```

## Plans

Plans are seeded from `seed.py` (mirrors `landing/src/lib/pricing.ts`): `free`,
`cloud`, `pro`. Limits (`-1` = unlimited) are enforced in `quotas.py`. Wire real
Stripe prices via `KOBEVOICE_CLOUD_STRIPE_PRICE_*`; billing auto-disables when no
Stripe key is set so the rest of the API still runs.
