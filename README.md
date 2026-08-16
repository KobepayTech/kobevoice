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

## Voice cloning: self-hosted Chatterbox

For cloned agent voices without sending a reference clip to a third party, `agent/src/chatterbox_tts.py` is a LiveKit TTS plugin wrapping [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI, **MIT** — commercial use permitted outright).

```bash
cd agent
uv pip install --python .venv/bin/python -e ".[chatterbox]"
KOBEVOICE_TTS=chatterbox CHATTERBOX_VOICE_SAMPLE=./voices/agent.wav \
  .venv/bin/python src/agent.py dev
```

Cloning takes a reference clip of roughly ten seconds. There is no LiveKit plugin for Chatterbox upstream, so this one is ours.

### Measured behaviour, not vendor claims

Verified locally on this codebase:

| Property | Result |
|---|---|
| Audio out of the plugin | Clean speech, 24 kHz mono, correct 16-bit PCM |
| Model load | ~18 s (once, at worker start via `prewarm()`) |
| **CPU real-time factor** | **~12–24x slower than realtime** |

**That RTF is the headline: Chatterbox needs a GPU.** At RTF 12 a two-second reply takes twenty-four seconds to synthesize — dead air on a phone call. `CHATTERBOX_DEVICE` therefore defaults to `cuda` and never silently falls back to CPU.

### Two upstream constraints the plugin works around

**No streaming API.** `generate()` returns one complete waveform, so time-to-first-audio equals full synthesis time. `build_tts()` wraps the model in LiveKit's `StreamAdapter` with a sentence tokenizer, so the agent starts speaking after the first sentence rather than the last. Use `build_tts()`, not `ChatterboxTTS` directly.

**Synchronous and compute-bound.** Calling it inline would block the event loop and stall every other call on the worker, so generation runs in a thread executor.

### A packaging trap worth knowing

Chatterbox's watermarker (`perth`) imports `pkg_resources`, which setuptools removed in v81. On a modern venv the model fails to load with a misleading `TypeError: 'NoneType' object is not callable`. The `chatterbox` extra pins `setuptools<81` to prevent it.

### Still unverified

Everything above was measured on CPU. Latency on a GPU, and cloning quality from a real reference clip, remain untested — I had no GPU and no reference sample. Those are the two things to check before committing Chatterbox to production.

Swapping to OpenAI Realtime is a documented one-line change in `src/agent.py` (install `livekit-agents[openai]`, replace the `llm=` argument); Anthropic and others are available the same way.

## Before outbound dialling goes live

Outbound calling — especially for sales and collections — is regulated in most jurisdictions (in the US, the TCPA and state analogues), and a growing number of places require disclosing that the caller is an AI. Call recording consent rules vary by state and country too.

This shapes the schema, so it's cheaper to design in now than retrofit: campaigns need consent records, per-number do-not-call state, calling-window rules by time zone, and a recording-consent flag per jurisdiction. Worth a conversation with whoever owns compliance before the first dial.

## History

This repo previously held a Pipecat-based prototype (Deepgram → Claude → Cartesia over WebRTC). It's superseded by the LiveKit stack but preserved in git history at commit `53a8351` if any of it is worth revisiting.
