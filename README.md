# Kobe Voice

An AI call-center platform: inbound and outbound phone calls handled by voice agents, with live transcripts, human transfer, and a supervisor dashboard.

Built on [LiveKit Agents](https://docs.livekit.io/agents/). Telephony arrives over SIP, so the platform is not tied to a single carrier.

## Repository layout

```
kobevoice/
├── agent/        Voice agent engine  (LiveKit Agents, Python)   ← current focus
├── dashboard/    Supervisor web UI   (LiveKit React starter)     — not started
└── outbound/     Outbound campaigns  (SIP dialer, transfer)      — not started
```

`agent/` is derived from [livekit-examples/agent-starter-python](https://github.com/livekit-examples/agent-starter-python) (MIT). The upstream copyright notice is retained in `agent/LICENSE.livekit`.

## What the starter already gives us

Worth knowing before building anything, because several call-center features are already solved upstream:

| Capability | Status |
|---|---|
| Turn detection | Semantic + acoustic end-of-turn model (not just silence) |
| Adaptive interruption | Distinguishes a real interruption from a backchannel like "mhm" — the agent keeps talking through the latter |
| Preemptive generation | LLM starts drafting before end-of-turn, cutting perceived latency |
| Expressive TTS | LLM emits inline delivery tags (emotion, pacing) that TTS renders and transcripts hide |
| Docker + CI + tests | Dockerfile, ruff, and three behavioural evals ship with it |

The [outbound-caller](https://github.com/livekit-examples/outbound-caller-python) example (also MIT) adds the call-center primitives we'll want in `outbound/`: `transfer_call` (SIP REFER to a human), `end_call`, and `detected_answering_machine` for voicemail — about 244 readable lines.

## Running the agent

Requires Python 3.10–3.14 and a LiveKit API key.

```bash
cd agent
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e .
cp .env.example .env.local     # fill in LIVEKIT_* credentials
.venv/bin/python src/agent.py dev
```

Tests are live behavioural evals — they call a real model, so they need credentials:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Model providers, and a note on self-hosting

The starter routes every model through **LiveKit Inference**, LiveKit's hosted gateway, rather than through direct provider plugins. Defaults are:

- **LLM** — `google/gemma-4-31b-it`
- **STT** — `assemblyai/universal-3-5-pro`
- **TTS** — `fishaudio/s2.1-pro`

Two consequences worth being deliberate about:

**Self-hosting is partial by default.** The LiveKit *server* self-hosts fine, but LiveKit *Inference* is a hosted service and needs a `LIVEKIT_API_KEY` — which is why the test suite above fails without one. For a fully self-hosted stack, swap the `inference.*` calls in `src/agent.py` for direct provider plugins (`livekit-plugins-openai`, `-anthropic`, `-deepgram`, and so on) pointed at your own endpoints.

**The Fish Audio licence question is resolved.** The starter's default TTS is Fish Audio S2.1 Pro delivered *through LiveKit's commercial gateway* — so we get that voice quality on normal commercial terms, with no GPU and none of the non-commercial research-licence constraints that apply to self-hosting the `fish-speech` weights directly.

Swapping to OpenAI Realtime is a documented one-line change in `src/agent.py` (install `livekit-agents[openai]`, replace the `llm=` argument); Anthropic and others are available the same way.

## Before outbound dialling goes live

Outbound calling — especially for sales and collections — is regulated in most jurisdictions (in the US, the TCPA and state analogues), and a growing number of places require disclosing that the caller is an AI. Call recording consent rules vary by state and country too.

This shapes the schema, so it's cheaper to design in now than retrofit: campaigns need consent records, per-number do-not-call state, calling-window rules by time zone, and a recording-consent flag per jurisdiction. Worth a conversation with whoever owns compliance before the first dial.

## History

This repo previously held a Pipecat-based prototype (Deepgram → Claude → Cartesia over WebRTC). It's superseded by the LiveKit stack but preserved in git history at commit `53a8351` if any of it is worth revisiting.
