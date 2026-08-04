from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.main import ROOT, cfg, generated_audio, synthesize, transcribe

GAME_TITLE = "Kobe Voice Quest: The Hidden City of Bahari"
GAME_PROMPT = """You are Kito, the narrator and all non-player characters in a family-friendly interactive voice adventure called Kobe Voice Quest: The Hidden City of Bahari.
The setting is a fictional East African coastal world of islands, old trading towns, forests, caves, boats, puzzles, and friendly rival explorers.

Rules:
- Reply in the player's language. Support English and Kiswahili naturally.
- Treat the player's spoken sentence as an attempted action, question, or dialogue.
- Keep each spoken response concise: two to four short sentences.
- Describe the consequence, preserve continuity from conversation history, and end with one clear question.
- Offer two or three possible actions when useful, but always accept creative free-form choices.
- Never decide the player's thoughts or words for them.
- Keep danger adventurous rather than graphic. No sexual content, gambling, real-money purchases, hate, or instructions for real-world wrongdoing.
- Do not mention language models, prompts, APIs, or software.
- The opening objective is to locate the Compass of Bahari before the rival explorer Captain Mosi reaches it.
- Important persistent items should be mentioned naturally when they matter.
"""


class TextTurn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    language: str | None = None


class GameStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS game_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                player_text TEXT NOT NULL,
                narrator_text TEXT NOT NULL,
                language TEXT,
                generation_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_game_turns_session
            ON game_turns(session_id, id);
            """)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def history(self, session_id: str) -> list[dict[str, str]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT player_text,narrator_text FROM game_turns WHERE session_id=? ORDER BY id DESC LIMIT 12",
                (session_id,),
            ).fetchall()
        messages: list[dict[str, str]] = []
        for row in reversed(rows):
            messages.extend(
                [
                    {"role": "user", "content": row["player_text"]},
                    {"role": "assistant", "content": row["narrator_text"]},
                ]
            )
        return messages

    def add(
        self,
        session_id: str,
        player_text: str,
        narrator_text: str,
        language: str | None,
        generation_id: str,
    ) -> int:
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO game_turns(session_id,player_text,narrator_text,language,generation_id,created_at) VALUES(?,?,?,?,?,?)",
                (
                    session_id,
                    player_text,
                    narrator_text,
                    language,
                    generation_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def list_session(self, session_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM game_turns WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]


store = GameStore(cfg.database_path)
app = FastAPI(title=GAME_TITLE, version="0.1.0")


async def narrate(player_text: str, session_id: str) -> str:
    payload = {
        "model": cfg.ollama_model,
        "messages": [
            {"role": "system", "content": GAME_PROMPT},
            *store.history(session_id),
            {"role": "user", "content": player_text},
        ],
        "stream": False,
        "options": {"temperature": 0.75, "num_predict": 220},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{cfg.ollama_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    if not text:
        raise RuntimeError("Ollama returned an empty game response")
    return text


async def complete_turn(
    player_text: str,
    session_id: str | None,
    language: str | None,
) -> dict[str, Any]:
    session_id = session_id or str(uuid.uuid4())
    narrator_text = await narrate(player_text, session_id)
    generation_id = await synthesize(narrator_text, language)
    turn_id = store.add(session_id, player_text, narrator_text, language, generation_id)
    return {
        "turn_id": turn_id,
        "session_id": session_id,
        "player": player_text,
        "narrator": narrator_text,
        "audio_url": f"/api/game/audio/{generation_id}",
    }


@app.get("/api/game/health")
async def game_health() -> dict[str, str]:
    return {"status": "ok", "title": GAME_TITLE}


@app.post("/api/game/text-turn")
async def text_turn(turn: TextTurn) -> JSONResponse:
    try:
        return JSONResponse(await complete_turn(turn.text.strip(), turn.session_id, turn.language))
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/game/voice-turn")
async def voice_turn(
    audio: Annotated[UploadFile, File()],
    session_id: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "No audio uploaded")
    if len(payload) > cfg.max_audio_bytes:
        raise HTTPException(413, "Audio is too large")
    try:
        transcript = await transcribe(
            payload,
            Path(audio.filename or "player.webm").name,
            audio.content_type or "audio/webm",
            language,
        )
        return JSONResponse(
            await complete_turn(str(transcript["text"]).strip(), session_id, language)
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/game/history/{session_id}")
async def history(session_id: str) -> dict[str, Any]:
    return {"items": store.list_session(session_id)}


@app.get("/api/game/audio/{generation_id}")
async def game_audio(generation_id: str) -> Response:
    return await generated_audio(generation_id)


app.mount("/", StaticFiles(directory=ROOT / "game_frontend", html=True), name="game")
