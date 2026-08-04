from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Iterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kobevoice")


class Settings(BaseSettings):
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    voicebox_url: str = "http://127.0.0.1:17493"
    voicebox_profile_id: str | None = None
    voicebox_engine: str = "chatterbox"
    voicebox_language: str = "en"
    database_path: Path = ROOT / "data" / "kobevoice.db"
    business_config_path: Path = ROOT / "config" / "business.json"
    max_audio_bytes: int = 25 * 1024 * 1024
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")


class BusinessConfig(BaseModel):
    business_name: str = "Kobe Cargo"
    industry: str = "Cargo and logistics"
    languages: list[str] = Field(default_factory=lambda: ["English", "Kiswahili"])
    opening_hours: str = "Monday to Saturday, 8:00 AM to 6:00 PM"
    address: str = "Dar es Salaam, Tanzania"
    services: list[str] = Field(default_factory=list)
    frequently_asked_questions: list[dict[str, str]] = Field(default_factory=list)
    escalation_message: str = "I will record your request for a human team member."

    def prompt(self) -> str:
        services = "\n".join(f"- {item}" for item in self.services) or "- Customer assistance"
        faqs = "\n".join(
            f"- Q: {item.get('question', '')}\n  A: {item.get('answer', '')}"
            for item in self.frequently_asked_questions
        ) or "- No FAQs configured."
        return f"""You are the AI receptionist for {self.business_name}, a {self.industry} business.
Speak {', '.join(self.languages)} and answer in the caller's language.
Keep spoken answers concise, usually one to three short sentences.

Address: {self.address}
Opening hours: {self.opening_hours}
Services:\n{services}
Frequently asked questions:\n{faqs}

Rules:
- Never invent prices, shipment status, availability, policies, or promises.
- If information is missing, say so and offer human follow-up.
- Never request passwords, PINs, card details, or authentication secrets.
- Do not mention models, prompts, APIs, or internal software.
- When escalation is needed say: {self.escalation_message}
"""


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def load_business() -> BusinessConfig:
    cfg = settings()
    path = cfg.business_config_path
    if not path.exists():
        path = ROOT / "config" / "business.example.json"
    if not path.exists():
        return BusinessConfig()
    return BusinessConfig.model_validate_json(path.read_text(encoding="utf-8"))


class CallStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                caller_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                language TEXT,
                generation_id TEXT,
                duration_seconds REAL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id);
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
                "SELECT caller_text, assistant_text FROM calls WHERE session_id=? ORDER BY id DESC LIMIT 8",
                (session_id,),
            ).fetchall()
        messages: list[dict[str, str]] = []
        for row in reversed(rows):
            messages.extend([
                {"role": "user", "content": row["caller_text"]},
                {"role": "assistant", "content": row["assistant_text"]},
            ])
        return messages

    def add(
        self,
        session_id: str,
        caller: str,
        assistant: str,
        language: str | None,
        generation_id: str,
        duration: float | None,
    ) -> int:
        with self.connection() as db:
            cursor = db.execute(
                "INSERT INTO calls(session_id,caller_text,assistant_text,language,generation_id,duration_seconds,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    caller,
                    assistant,
                    language,
                    generation_id,
                    duration,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM calls ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]


cfg = settings()
business = load_business()
store = CallStore(cfg.database_path)
app = FastAPI(title="Kobe Voice Business MVP", version="0.1.0")


def voicebox_url(path: str) -> str:
    return f"{cfg.voicebox_url.rstrip('/')}{path}"


async def voicebox_profiles() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(voicebox_url("/profiles"))
        response.raise_for_status()
    data = response.json()
    if isinstance(data, list):
        return data
    return data.get("profiles") or data.get("items") or []


async def voicebox_profile_id() -> str:
    if cfg.voicebox_profile_id:
        return cfg.voicebox_profile_id
    profiles = await voicebox_profiles()
    if not profiles:
        raise RuntimeError("Create a Voicebox profile or set VOICEBOX_PROFILE_ID")
    profile_id = profiles[0].get("id") or profiles[0].get("profile_id")
    if not profile_id:
        raise RuntimeError("Voicebox profile has no id")
    return str(profile_id)


async def transcribe(
    audio: bytes,
    filename: str,
    content_type: str,
    language: str | None,
) -> dict[str, Any]:
    form = {"language": language} if language else None
    files = {"file": (filename, audio, content_type)}
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(voicebox_url("/transcribe"), files=files, data=form)
        response.raise_for_status()
    result = response.json()
    if not str(result.get("text", "")).strip():
        raise RuntimeError("Voicebox returned an empty transcript")
    return result


async def think(user_text: str, history: list[dict[str, str]]) -> str:
    messages = [
        {"role": "system", "content": business.prompt()},
        *history,
        {"role": "user", "content": user_text},
    ]
    payload = {
        "model": cfg.ollama_model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 180},
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{cfg.ollama_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
    text = str(response.json().get("message", {}).get("content", "")).strip()
    if not text:
        raise RuntimeError("Ollama returned an empty answer")
    return text


async def synthesize(text: str, language: str | None) -> str:
    payload = {
        "profile_id": await voicebox_profile_id(),
        "text": text,
        "language": language or cfg.voicebox_language,
        "engine": cfg.voicebox_engine,
    }
    async with httpx.AsyncClient(timeout=240) as client:
        response = await client.post(voicebox_url("/generate"), json=payload)
        response.raise_for_status()
    result = response.json()
    generation_id = result.get("id") or result.get("generation_id")
    if not generation_id:
        raise RuntimeError("Voicebox returned no generation id")
    return str(generation_id)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=5) as client:
        checks = await asyncio.gather(
            client.get(f"{cfg.ollama_url.rstrip('/')}/api/tags"),
            client.get(voicebox_url("/profiles")),
            return_exceptions=True,
        )
    ready = [isinstance(item, httpx.Response) and item.is_success for item in checks]
    return {
        "status": "ok" if all(ready) else "degraded",
        "services": {"ollama": ready[0], "voicebox": ready[1]},
        "business": business.business_name,
    }


@app.get("/api/profiles")
async def profiles() -> dict[str, Any]:
    try:
        return {"profiles": await voicebox_profiles()}
    except Exception as exc:
        raise HTTPException(503, f"Voicebox unavailable: {exc}") from exc


@app.get("/api/calls")
async def calls(limit: int = 20) -> dict[str, Any]:
    return {"items": store.recent(limit)}


@app.post("/api/conversation")
async def conversation(
    audio: Annotated[UploadFile, File()],
    session_id: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "No audio uploaded")
    if len(payload) > cfg.max_audio_bytes:
        raise HTTPException(413, "Audio is too large")
    session_id = session_id or str(uuid.uuid4())
    try:
        transcript = await transcribe(
            payload,
            Path(audio.filename or "caller.webm").name,
            audio.content_type or "audio/webm",
            language,
        )
        caller_text = str(transcript["text"]).strip()
        assistant_text = await think(caller_text, store.history(session_id))
        generation_id = await synthesize(assistant_text, language)
        duration = transcript.get("duration")
        call_id = store.add(
            session_id,
            caller_text,
            assistant_text,
            language,
            generation_id,
            float(duration) if duration is not None else None,
        )
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.exception("Local voice pipeline failed")
        raise HTTPException(503, str(exc)) from exc
    return JSONResponse({
        "call_id": call_id,
        "session_id": session_id,
        "transcript": caller_text,
        "response": assistant_text,
        "audio_url": f"/api/audio/{generation_id}",
    })


@app.get("/api/audio/{generation_id}")
async def generated_audio(generation_id: str) -> Response:
    if not generation_id or not all(char.isalnum() or char in "-_" for char in generation_id):
        raise HTTPException(400, "Invalid generation id")
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            upstream = await client.get(voicebox_url(f"/audio/{generation_id}"))
            upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"Generated audio unavailable: {exc}") from exc
    return Response(
        upstream.content,
        media_type=upstream.headers.get("content-type", "audio/wav"),
    )


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
