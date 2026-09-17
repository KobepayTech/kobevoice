# Voicebox, vendored into Kobe Voice

Source: https://github.com/jamiepine/voicebox — MIT ("Voicebox Contributors").
The upstream `LICENSE` is retained beside this file, as MIT requires.

## What was left out, and why

The upstream repository is ~55 MB, of which ~42 MB is binary artwork rather
than code. These paths were excluded:

| Path | Size | Reason |
|---|---|---|
| `tauri/assets/` | 28 MB | Voicebox app icons and export images — another product's branding |
| `landing/public/` | 14 MB | Marketing site assets, including a 4.5 MB demo video |
| `app/src/assets/voicebox-logo.png` | 4.1 MB | Voicebox logo |

**All source code is present** — nothing functional was dropped. If you ever
need the artwork (e.g. to run the desktop app with its real icons), it is one
`git clone` away upstream.

## What is actually useful here

`backend/` is the part worth reading: 120 Python files, ~25k lines implementing
a multi-backend voice service.

- `backend/backends/` — adapters for Chatterbox, **Chatterbox Turbo**, Kokoro,
  MLX (Apple Silicon), Qwen, Hume, LuxTTS. The Turbo adapter is the interesting
  one: Turbo is built for low-latency voice agents and is exactly what our own
  plugin would want if CPU/GPU latency proves marginal.
- `backend/services/profiles.py` and the `profiles` route — voice-profile
  management (create a cloned voice, store it, reuse it). Our agent builder
  currently stores a single `voice_sample_uri` per agent; this is the
  better-developed version of that idea.
- `backend/routes/transcription.py` — Whisper STT wiring.

## What it does not solve

Voicebox's Chatterbox adapters **do not stream** either — they call `generate()`
and return a finished waveform, same as ours. So it is not a fix for the
time-to-first-audio problem; our `StreamAdapter` sentence-chunking in
`agent/src/chatterbox_tts.py` remains the mitigation.

## Relationship to the rest of this repo

Voicebox is a **desktop voice studio**, not a telephony component. It is not
wired into the call pipeline and nothing imports from it. It is here as a
reference implementation and a source of code to lift — principally the Turbo
adapter and voice-profile handling.
