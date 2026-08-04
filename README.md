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

## Kobe Voice Quest game prototype

The repository also contains a local voice-controlled adventure called **Kobe Voice Quest: The Hidden City of Bahari**. Players speak or type actions in English or Kiswahili. Ollama narrates the consequences, Voicebox speaks the response, and SQLite remembers the story session.

Start it after completing the normal setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-game.ps1
```

Open `http://127.0.0.1:8081`.

Game endpoints:

- `POST /api/game/voice-turn`
- `POST /api/game/text-turn`
- `GET /api/game/history/{session_id}`
- `GET /api/game/audio/{generation_id}`

## Kobe Voice Arena public party game

**Kobe Voice Arena** is a faster public game designed for parties, schools, events, livestreams, and social sharing. A host creates a six-character room code. Players join from their phones, receive a short funny prompt, submit a spoken or typed answer, and vote for the funniest response. Winners earn scoreboard points.

Start Arena after the normal setup:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-arena.ps1
```

Open `http://127.0.0.1:8082` on the host computer. Other players on the same network can open `http://HOST_COMPUTER_IP:8082` and join using the room code.

Arena features in the current prototype:

- Public room creation and joining
- English or Kiswahili AI-generated prompts
- Voice or typed answers
- Local Whisper transcription
- Audience voting
- Scoreboard and multiple rounds
- SQLite room, player, answer, and vote storage
- Browser polling, ready to be replaced by LiveKit real-time events

Arena endpoints include:

- `POST /api/arena/rooms`
- `POST /api/arena/rooms/{code}/join`
- `POST /api/arena/rooms/{code}/start-round`
- `POST /api/arena/rooms/{code}/voice-answer`
- `POST /api/arena/rooms/{code}/text-answer`
- `POST /api/arena/rooms/{code}/open-voting`
- `POST /api/arena/rooms/{code}/vote`
- `POST /api/arena/rooms/{code}/finish-round`

These prototypes prove that the same local voice platform can power AI characters, public party games, interactive stories, language-learning games, quizzes, mystery games, and later multiplayer voice rooms through LiveKit.

## Configuration

`setup.ps1` creates `.env` and `config/business.json` from the examples. Set an explicit `VOICEBOX_PROFILE_ID` for predictable production behavior; otherwise the first Voicebox profile is used.

Important business endpoints:

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

Replace push-to-talk and browser polling with continuous real-time audio and events through self-hosted LiveKit. Then connect Asterisk/SIP or a GSM gateway for the business agent and add internet-ready moderated public rooms for Arena.

Only clone voices with explicit permission. Tell callers they are speaking with an AI and when calls are recorded. Public rooms need reporting, blocking, profanity filtering, rate limits, and age-appropriate moderation before an internet launch.
