# kobevoice

A real-time voice agent built on [Pipecat](https://github.com/pipecat-ai/pipecat) — speak to it, it speaks back, and it handles interruptions.

Current stack: **Deepgram** (speech-to-text) → **Claude** (reasoning) → **Cartesia** (text-to-speech), over WebRTC.

## How it works

One pipeline moves audio through six stages:

```
transport.input()  →  STT  →  user aggregator  →  LLM  →  TTS  →  transport.output()  →  assistant aggregator
```

Two details worth knowing:

- **The aggregator pair owns conversation state.** The user side turns transcripts into context messages; the assistant side records what was *actually spoken*. If a caller interrupts mid-reply, the stored message is truncated to match what they heard — so the model's memory matches the caller's.
- **VAD drives turn-taking.** Silero voice-activity detection tells the pipeline when the caller stopped talking, and lets barge-in cut off playback the moment they start again.

## Setup

Requires Python 3.11 or 3.12.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r pyproject.toml
cp .env.example .env      # then fill in the three API keys
```

Keys come from [Anthropic](https://console.anthropic.com/settings/keys), [Deepgram](https://console.deepgram.com/), and [Cartesia](https://play.cartesia.ai/keys).

## Run

```bash
.venv/bin/python bot.py -t webrtc
```

Open **http://localhost:7860** and allow microphone access. The agent greets you first.

Other transports are wired up the same way — `-t daily`, `-t twilio`. Run `bot.py --help` for the full list.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | *required* | |
| `DEEPGRAM_API_KEY` | *required* | |
| `KOBEVOICE_LLM_MODEL` | `claude-opus-5` | See the latency note below |
| `KOBEVOICE_TTS` | `cartesia` | `cartesia` or `fish` |
| `CARTESIA_API_KEY` | required when `KOBEVOICE_TTS=cartesia` | |
| `CARTESIA_VOICE_ID` | a default Cartesia voice | Browse voices in the Cartesia playground |
| `FISH_API_KEY` | required when `KOBEVOICE_TTS=fish` | Hosted Fish Audio API |
| `FISH_VOICE_ID` | required when `KOBEVOICE_TTS=fish` | Fish Audio voice / reference model ID |

### Switching TTS provider

```bash
KOBEVOICE_TTS=fish .venv/bin/python bot.py -t webrtc
```

Both providers stream PCM over a websocket, so they're interchangeable in the pipeline — nothing else in `bot.py` changes.

## A note on Fish Audio: two different things

These are easy to conflate, and the difference matters:

**Fish Audio hosted API** (`api.fish.audio`) — a commercial SaaS. This is what `KOBEVOICE_TTS=fish` uses, and what Pipecat supports out of the box. You bring an API key; no GPU required. Normal commercial terms apply.

**[fish-speech](https://github.com/fishaudio/fish-speech)** (self-hosted open weights) — the S2 Pro model you can run yourself. Two things to know before adopting it:

1. **Licence.** The weights and code are released under the **Fish Audio Research License**, which permits research and non-commercial use only: *"Any use of the Fish Audio Materials or Derivative Works for a Commercial Purpose requires a separate written license agreement from Fish Audio."* Redistribution also requires displaying "Built with Fish Audio." If kobevoice is a commercial product, self-hosting needs a commercial licence from Fish Audio first.
2. **Not wired up.** Pipecat's `FishAudioTTSService` hardcodes `wss://api.fish.audio/v1/tts/live`, so it will not talk to a local fish-speech server without a subclass that repoints that URL (or a custom service written against fish-speech's own API server). Self-hosting also needs a GPU — the quoted ~100 ms time-to-first-audio is measured on an H200.

The upside of self-hosting is real — no per-character cost, data stays in your infrastructure, and voice cloning from a short reference sample. It's a licensing decision before it's an engineering one.

**On model choice:** perceived latency in a voice agent is dominated by time-to-first-token. `claude-opus-5` gives the best answer quality; if replies feel sluggish in conversation, `claude-sonnet-5` is the first thing to try. Change it with `KOBEVOICE_LLM_MODEL` — no code edit needed.

The system prompt lives in `bot.py` as `SYSTEM_PROMPT`. It's written for speech: no markdown, no bullets, short answers. Keep that constraint if you edit it — anything the model writes gets read aloud verbatim.

## Adding tools

Pipecat handles function calling through the same `LLMContext` the aggregators manage. Register a function schema on the context and the LLM service will call it mid-conversation — useful for lookups, bookings, or transfers.

## Status

The pipeline is verified to assemble and the server boots and serves its client UI. It has not yet been run end-to-end against live provider APIs — that needs real keys.
