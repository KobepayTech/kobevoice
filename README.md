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
| `CARTESIA_API_KEY` | *required* | |
| `KOBEVOICE_LLM_MODEL` | `claude-opus-5` | See the latency note below |
| `CARTESIA_VOICE_ID` | a default Cartesia voice | Browse voices in the Cartesia playground |

**On model choice:** perceived latency in a voice agent is dominated by time-to-first-token. `claude-opus-5` gives the best answer quality; if replies feel sluggish in conversation, `claude-sonnet-5` is the first thing to try. Change it with `KOBEVOICE_LLM_MODEL` — no code edit needed.

The system prompt lives in `bot.py` as `SYSTEM_PROMPT`. It's written for speech: no markdown, no bullets, short answers. Keep that constraint if you edit it — anything the model writes gets read aloud verbatim.

## Adding tools

Pipecat handles function calling through the same `LLMContext` the aggregators manage. Register a function schema on the context and the LLM service will call it mid-conversation — useful for lookups, bookings, or transfers.

## Status

The pipeline is verified to assemble and the server boots and serves its client UI. It has not yet been run end-to-end against live provider APIs — that needs real keys.
