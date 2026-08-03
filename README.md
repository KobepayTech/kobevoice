# Kobe Voice Business MVP

A local-first English/Kiswahili AI receptionist. This first working slice is a browser push-to-talk application using local Voicebox for Whisper transcription and speech generation, local Ollama for reasoning, and SQLite for conversation history.

## Working flow

1. Hold **Hold to talk** in the browser.
2. Voicebox transcribes the recording locally.
3. Ollama answers from the configured business information.
4. Voicebox generates the spoken reply locally.
5. The browser plays the reply and stores the conversation.

No OpenAI, ElevenLabs, Deepgram, Twilio, or other cloud AI API is required.

## Included submodules

- `services/livekit-agents` — real-time rooms and telephony for the next milestone.
- `tools/voicebox` — local voice profiles, Whisper, and TTS.

Clone everything:

```powershell
git clone --recurse-submodules https://github.com/KobepayTech/kobevoice.git
cd kobevoice
```

## Windows setup

1. Install and open Voicebox. Create at least one voice profile.
2. Install Ollama and pull the model:

```powershell
ollama pull qwen3:8b
```

3. Configure and run Kobe Voice:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

4. Open `http://127.0.0.1:8080` and allow microphone access.

Voicebox should expose its local API at `http://127.0.0.1:17493/docs`. Ollama should be available at `http://127.0.0.1:11434`.

## Configuration

`setup.ps1` creates `.env` and `config/business.json` from the examples. Set an explicit `VOICEBOX_PROFILE_ID` for predictable production behavior; otherwise the first Voicebox profile is used.

Important endpoints:

- `GET /api/health`
- `POST /api/conversation`
- `GET /api/audio/{generation_id}`
- `GET /api/calls`
- `GET /docs`

## Docker

Run Ollama and Voicebox on the host, then:

```powershell
copy config\business.example.json config\business.json
docker compose up --build
```

## Next milestone

Replace push-to-talk with continuous real-time audio through self-hosted LiveKit, then connect Asterisk/SIP or a GSM gateway. The same local Ollama, Voicebox, business prompt, and call storage will be reused.

Only clone voices with explicit permission. Tell callers they are speaking with an AI and when calls are recorded. Never collect passwords, PINs, card details, or authentication secrets.
