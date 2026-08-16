# Kobe Voice

An AI call-center platform: inbound and outbound phone calls handled by voice agents, with live transcripts, human transfer, compliance gating, and a supervisor dashboard.

Built on [LiveKit Agents](https://docs.livekit.io/agents/). Telephony arrives over SIP, so the platform is not tied to a single carrier.

> **Status: no phone call has been placed or received yet.** Every component below is built and tested locally, but the telephony leg needs a SIP trunk and a purchased number — see [What is not done](#what-is-not-done).

## Layout

```
kobevoice/
├── api/         Control plane — FastAPI + Postgres (tenants, calls, compliance, KobeOS)
├── agent/       Voice agent worker — LiveKit Agents, Chatterbox TTS, telephony tools
├── dashboard/   Supervisor web UI — Next.js 15 + LiveKit React
└── docker-compose.yml
```

`agent/` and `dashboard/` derive from LiveKit's MIT starters; upstream notices are kept in each directory's `LICENSE.livekit`.

## Quick start

```bash
docker compose up --build          # postgres + api + agent + dashboard
```

Or per service:

```bash
# API — 22 tests
cd api && uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m uvicorn kobeos.main:app --app-dir src --port 8000

# Dashboard
cd dashboard && pnpm install && pnpm build && pnpm dev

# Agent
cd agent && .venv/bin/python src/agent.py dev
```

## Compliance is enforced in code, not policy

Outbound calling — especially collections — is regulated. Rather than leave that to process, the dialler calls `POST /compliance/check-dialable` before every attempt, and the gate **fails closed**: if the compliance service is unreachable, the call is refused. An outage costs calls; it does not generate violations.

Three checks, in order:

1. **Do-not-call** — per tenant, permanent. The table deliberately has no `is_active` flag, so the dialler cannot toggle a DNC entry off.
2. **Consent** — append-only records. A revocation anywhere in a number's history blocks it permanently; later consent does not resurrect it.
3. **Calling window** — 08:00–21:00 in the *called party's* local timezone, resolved from their contact record. An unresolvable timezone blocks the call rather than guessing.

Agents also expose an `add_to_do_not_call` tool, so a caller saying "stop calling me" is honoured mid-call rather than after the fact.

The 08:00–21:00 default reflects US TCPA hours. It is a starting point, not a jurisdiction survey — confirm with whoever owns compliance before dialling.

## Running with no per-use vendor billing

`KOBEVOICE_STACK=local` swaps every metered model service for software you run yourself:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up
docker compose exec ollama ollama pull qwen2.5:7b-instruct
```

| Layer | Hosted default | Local replacement | Licence |
|---|---|---|---|
| Media server | LiveKit Cloud | `livekit-server` | Apache 2.0 |
| STT | AssemblyAI via Inference | Whisper (speaches) | MIT |
| LLM | Gemma via Inference | Qwen 2.5 via Ollama | Apache 2.0 |
| TTS | Fish Audio via Inference | Chatterbox | MIT |
| VAD | — | Silero | MIT |
| Turn detection | LiveKit Inference | `livekit-local-inference` | Apache 2.0 code; models free but **LiveKit-Agents-only** |

Components mix freely — `KOBEVOICE_TTS=local` with a hosted LLM is valid while a GPU is being provisioned.

### What this does not remove

Being blunt, because "no vendor billing" is not the same as free:

**Telephony still costs money, and there is no free option.** A carrier charges for each phone number (typically ~$1–2/month) and for minutes. Numbers are regulated, carrier-allocated resources — no amount of self-hosting changes that. Self-hosting LiveKit means you bring your own SIP trunk; the trunk is still a paid account. **This is the one cost that cannot be engineered away**, and for a call center it is likely the largest line item.

**Hardware costs money.** Chatterbox and a 7B LLM both need a GPU. Measured here: Chatterbox on CPU runs at RTF 12–24 — unusable for live calls. That's either a machine you buy or an instance you rent. Self-hosting moves spend from per-minute vendor invoices to fixed capacity you must size and operate.

**Model licences still bind.** Everything defaulted to above is MIT or Apache 2.0, which permit commercial use outright. If you swap models, check first: Llama carries a 700M-MAU restriction, and `fish-speech` is non-commercial without a written agreement (see below). A permissive default was chosen deliberately.

The honest summary: the local stack eliminates **per-use AI billing** and keeps your audio on your own infrastructure. It does not make the product free to run.

## Text-to-speech

Selected by `KOBEVOICE_TTS`:

| Value | What it is | Licence | Notes |
|---|---|---|---|
| `inference` (default) | LiveKit's hosted gateway (Fish Audio S2.1 Pro) | Commercial SaaS | No GPU. Needs `LIVEKIT_API_KEY`. |
| `chatterbox` | Self-hosted [Chatterbox](https://github.com/resemble-ai/chatterbox) | **MIT** | Voice cloning from ~10s of reference audio. **Needs a GPU.** |

**Measured, not claimed:** Chatterbox on CPU runs at **RTF 12–24** — 31 seconds to synthesize 2.6 seconds of speech. That is dead air on a phone call, so `CHATTERBOX_DEVICE` defaults to `cuda` and never silently falls back to CPU.

Two upstream constraints are handled in `agent/src/chatterbox_tts.py`: the model has **no streaming API** (so it is wrapped in LiveKit's `StreamAdapter` to speak sentence-by-sentence rather than after the whole reply), and it is **synchronous** (so generation runs in a thread executor instead of blocking every other call on the worker).

**Packaging trap:** Chatterbox's `perth` watermarker imports `pkg_resources`, removed in setuptools 81. Without the `setuptools<81` pin the model fails with a misleading `'NoneType' object is not callable`.

### Considered and rejected

- **fish-speech** — Fish Audio Research License: non-commercial without a separate written agreement, and that covers derivative works, so modifying it changes nothing. Use the hosted gateway instead (which is what `inference` does).
- **[Miso TTS 8B](https://github.com/Shard-MW/misotts)** — MIT-with-attribution (only binding above 50M MAU or $10M/month revenue), 24 kHz, voice cloning via audio context, English only, no streaming. Same integration shape as Chatterbox but ~16x the parameters, so materially more GPU per concurrent call. Worth revisiting as a quality upgrade if GPU headroom allows.

## API surface

| Area | Endpoints |
|---|---|
| Tenancy | `POST /tenants` |
| Agent builder | `POST/GET /agents`, `GET /agents/{id}` |
| Calls | `GET /calls`, `GET/POST /calls/{id}/transcript`, `GET /stats` |
| Compliance | `POST /compliance/check-dialable`, `/compliance/dnc`, `/compliance/consent` |
| KobeOS | `/contacts`, `/orders`, `/payments` (model), `/reservations`, `/tickets` |

Every business table is tenant-scoped, and tenant isolation is covered by tests — including that fetching another tenant's agent by ID returns 404 rather than the record.

## What is not done

Being explicit, because the gap matters more than the code that exists:

- **No real phone call, inbound or outbound.** SIP trunk, phone-number provisioning, and inbound routing are unbuilt. This is the single biggest gap and the riskiest remaining integration.
- **Authentication is a trust-me header.** `X-Tenant-Slug` is unauthenticated — any caller can claim any tenant. **Replace before exposing this service anywhere.**
- **Outbound campaign runner.** The per-call tools exist (`agent/src/telephony.py`); the campaign loop that walks a contact list does not.
- **Call recording.** Modelled (`recordings`, with a required consent basis) but nothing writes audio to storage.
- **Dashboard is the stock LiveKit starter.** It builds and connects to a room; it does not yet render the call list, transcripts, or stats this API exposes.
- **No Alembic migrations.** `init_db()` creates tables for development only.
- **GPU latency for Chatterbox** and **cloning quality from a real reference clip** remain unmeasured.

## Verified

What was actually run, not assumed:

- API: **22 tests pass** — tenant isolation, DNC precedence, revoked-consent handling, timezone calling windows (including fail-closed on unknown zones), transcript ordering.
- Compliance gate end-to-end: agent → live API. No consent → blocked; DNC → blocked; API down → **blocked** (fail-closed).
- Chatterbox: real 24 kHz speech through LiveKit's `AudioEmitter`, correct 16-bit PCM.
- Dashboard: `pnpm build` succeeds (6 routes). Required one upstream fix — `motion` v12 rejects a widened `ease: string`, fixed by annotating `MotionProps`.
- Agent: imports clean on `livekit-agents` 1.6.10; torch stays unloaded unless Chatterbox is selected.
