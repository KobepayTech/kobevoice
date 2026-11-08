from __future__ import annotations

import random
import sqlite3
import string
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

ARENA_TITLE = "Kobe Voice Arena"
PROMPT_THEMES = [
    "funny everyday situations",
    "impossible inventions",
    "school and workplace comedy",
    "travel mistakes",
    "superpowers with silly disadvantages",
    "friendly debates about harmless topics",
]


class CreateRoom(BaseModel):
    host_name: str = Field(min_length=1, max_length=40)
    language: str = "en"


class JoinRoom(BaseModel):
    player_name: str = Field(min_length=1, max_length=40)


class PlayerAction(BaseModel):
    player_id: str


class TextAnswer(BaseModel):
    player_id: str
    text: str = Field(min_length=1, max_length=500)


class Vote(BaseModel):
    player_id: str
    answer_id: int


class ArenaStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS arena_rooms (
                code TEXT PRIMARY KEY,
                host_player_id TEXT NOT NULL,
                language TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS arena_players (
                id TEXT PRIMARY KEY,
                room_code TEXT NOT NULL,
                name TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                joined_at TEXT NOT NULL,
                UNIQUE(room_code, name)
            );
            CREATE TABLE IF NOT EXISTS arena_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_code TEXT NOT NULL,
                number INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                state TEXT NOT NULL,
                winner_answer_id INTEGER,
                host_audio_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS arena_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                player_id TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(round_id, player_id)
            );
            CREATE TABLE IF NOT EXISTS arena_votes (
                round_id INTEGER NOT NULL,
                voter_player_id TEXT NOT NULL,
                answer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(round_id, voter_player_id)
            );
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

    def room_exists(self, code: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM arena_rooms WHERE code=?", (code,)).fetchone() is not None

    def create_room(self, host_name: str, language: str) -> dict[str, str]:
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(random.choice(alphabet) for _ in range(6))
            if not self.room_exists(code):
                break
        player_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute(
                "INSERT INTO arena_rooms(code,host_player_id,language,state,created_at) VALUES(?,?,?,?,?)",
                (code, player_id, language, "lobby", now),
            )
            db.execute(
                "INSERT INTO arena_players(id,room_code,name,joined_at) VALUES(?,?,?,?)",
                (player_id, code, host_name.strip(), now),
            )
        return {"room_code": code, "player_id": player_id}

    def join(self, code: str, name: str) -> str:
        player_id = str(uuid.uuid4())
        with self.connection() as db:
            room = db.execute("SELECT state FROM arena_rooms WHERE code=?", (code,)).fetchone()
            if room is None:
                raise ValueError("Room not found")
            if room["state"] == "closed":
                raise ValueError("Room is closed")
            try:
                db.execute(
                    "INSERT INTO arena_players(id,room_code,name,joined_at) VALUES(?,?,?,?)",
                    (player_id, code, name.strip(), datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("That player name is already in the room") from exc
        return player_id

    def player(self, code: str, player_id: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM arena_players WHERE room_code=? AND id=?",
                (code, player_id),
            ).fetchone()
        if row is None:
            raise ValueError("Player not found in room")
        return row

    def room(self, code: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute("SELECT * FROM arena_rooms WHERE code=?", (code,)).fetchone()
        if row is None:
            raise ValueError("Room not found")
        return row

    def active_round(self, code: str) -> sqlite3.Row | None:
        with self.connection() as db:
            return db.execute(
                "SELECT * FROM arena_rounds WHERE room_code=? ORDER BY id DESC LIMIT 1",
                (code,),
            ).fetchone()

    def start_round(self, code: str, prompt: str, audio_id: str | None) -> int:
        with self.connection() as db:
            last = db.execute(
                "SELECT COALESCE(MAX(number),0) AS n FROM arena_rounds WHERE room_code=?",
                (code,),
            ).fetchone()
            number = int(last["n"]) + 1
            cursor = db.execute(
                "INSERT INTO arena_rounds(room_code,number,prompt,state,host_audio_id,created_at) VALUES(?,?,?,?,?,?)",
                (code, number, prompt, "answering", audio_id, datetime.now(timezone.utc).isoformat()),
            )
            db.execute("UPDATE arena_rooms SET state='playing' WHERE code=?", (code,))
            return int(cursor.lastrowid)

    def answer(self, round_id: int, player_id: str, text: str) -> int:
        with self.connection() as db:
            round_row = db.execute("SELECT state FROM arena_rounds WHERE id=?", (round_id,)).fetchone()
            if round_row is None or round_row["state"] != "answering":
                raise ValueError("This round is not accepting answers")
            try:
                cursor = db.execute(
                    "INSERT INTO arena_answers(round_id,player_id,answer_text,created_at) VALUES(?,?,?,?)",
                    (round_id, player_id, text.strip(), datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("You already answered this round") from exc
            return int(cursor.lastrowid)

    def open_voting(self, round_id: int) -> None:
        with self.connection() as db:
            db.execute("UPDATE arena_rounds SET state='voting' WHERE id=?", (round_id,))

    def vote(self, round_id: int, voter_id: str, answer_id: int) -> None:
        with self.connection() as db:
            answer = db.execute(
                "SELECT player_id FROM arena_answers WHERE id=? AND round_id=?",
                (answer_id, round_id),
            ).fetchone()
            if answer is None:
                raise ValueError("Answer not found")
            if answer["player_id"] == voter_id:
                raise ValueError("You cannot vote for yourself")
            try:
                db.execute(
                    "INSERT INTO arena_votes(round_id,voter_player_id,answer_id,created_at) VALUES(?,?,?,?)",
                    (round_id, voter_id, answer_id, datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("You already voted this round") from exc

    def finish_round(self, round_id: int) -> dict[str, Any] | None:
        with self.connection() as db:
            winner = db.execute(
                """
                SELECT a.id,a.player_id,a.answer_text,p.name,COUNT(v.voter_player_id) votes
                FROM arena_answers a
                JOIN arena_players p ON p.id=a.player_id
                LEFT JOIN arena_votes v ON v.answer_id=a.id
                WHERE a.round_id=?
                GROUP BY a.id
                ORDER BY votes DESC,a.id ASC
                LIMIT 1
                """,
                (round_id,),
            ).fetchone()
            if winner is None:
                db.execute("UPDATE arena_rounds SET state='finished' WHERE id=?", (round_id,))
                return None
            db.execute(
                "UPDATE arena_rounds SET state='finished',winner_answer_id=? WHERE id=?",
                (winner["id"], round_id),
            )
            db.execute("UPDATE arena_players SET score=score+1 WHERE id=?", (winner["player_id"],))
            return dict(winner)

    def snapshot(self, code: str) -> dict[str, Any]:
        room = self.room(code)
        round_row = self.active_round(code)
        with self.connection() as db:
            players = [dict(row) for row in db.execute(
                "SELECT id,name,score FROM arena_players WHERE room_code=? ORDER BY score DESC,joined_at",
                (code,),
            ).fetchall()]
            answers: list[dict[str, Any]] = []
            if round_row is not None and round_row["state"] in {"voting", "finished"}:
                answers = [dict(row) for row in db.execute(
                    """
                    SELECT a.id,a.player_id,a.answer_text,p.name,COUNT(v.voter_player_id) votes
                    FROM arena_answers a
                    JOIN arena_players p ON p.id=a.player_id
                    LEFT JOIN arena_votes v ON v.answer_id=a.id
                    WHERE a.round_id=?
                    GROUP BY a.id
                    ORDER BY a.id
                    """,
                    (round_row["id"],),
                ).fetchall()]
        return {
            "room": dict(room),
            "players": players,
            "round": dict(round_row) if round_row is not None else None,
            "answers": answers,
        }


store = ArenaStore(cfg.database_path)
app = FastAPI(title=ARENA_TITLE, version="0.1.0")


async def make_prompt(language: str) -> str:
    theme = random.choice(PROMPT_THEMES)
    instruction = f"""Create one short, family-friendly party-game prompt about {theme}.
It must invite a funny spoken answer, take under 15 seconds to answer, and have no single correct answer.
Return only the prompt. Language: {'Kiswahili' if language == 'sw' else 'English'}.
Avoid politics, sex, gambling, insults, dangerous challenges, and copyrighted characters."""
    payload = {
        "model": cfg.ollama_model,
        "messages": [{"role": "user", "content": instruction}],
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": 80},
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{cfg.ollama_url.rstrip('/')}/api/chat", json=payload)
        response.raise_for_status()
    prompt = str(response.json().get("message", {}).get("content", "")).strip().strip('"')
    if not prompt:
        raise RuntimeError("Could not create a round prompt")
    return prompt


@app.post("/api/arena/rooms")
async def create_room(data: CreateRoom) -> dict[str, str]:
    return store.create_room(data.host_name, data.language if data.language in {"en", "sw"} else "en")


@app.post("/api/arena/rooms/{code}/join")
async def join_room(code: str, data: JoinRoom) -> dict[str, str]:
    try:
        return {"player_id": store.join(code.upper(), data.player_name)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/arena/rooms/{code}")
async def room_state(code: str) -> dict[str, Any]:
    try:
        return store.snapshot(code.upper())
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/start-round")
async def start_round(code: str, action: PlayerAction) -> dict[str, Any]:
    code = code.upper()
    try:
        room = store.room(code)
        if room["host_player_id"] != action.player_id:
            raise ValueError("Only the host can start a round")
        active = store.active_round(code)
        if active is not None and active["state"] in {"answering", "voting"}:
            raise ValueError("Finish the current round first")
        prompt = await make_prompt(room["language"])
        audio_id = await synthesize(prompt, room["language"])
        round_id = store.start_round(code, prompt, audio_id)
        return {"round_id": round_id, "prompt": prompt, "audio_url": f"/api/arena/audio/{audio_id}"}
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/text-answer")
async def text_answer(code: str, data: TextAnswer) -> dict[str, int]:
    try:
        store.player(code.upper(), data.player_id)
        active = store.active_round(code.upper())
        if active is None:
            raise ValueError("No active round")
        return {"answer_id": store.answer(int(active["id"]), data.player_id, data.text)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/voice-answer")
async def voice_answer(
    code: str,
    audio: Annotated[UploadFile, File()],
    player_id: Annotated[str, Form()],
    language: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "No audio uploaded")
    try:
        store.player(code.upper(), player_id)
        active = store.active_round(code.upper())
        if active is None:
            raise ValueError("No active round")
        transcript = await transcribe(
            payload,
            Path(audio.filename or "answer.webm").name,
            audio.content_type or "audio/webm",
            language,
        )
        text = str(transcript["text"]).strip()
        answer_id = store.answer(int(active["id"]), player_id, text)
        return {"answer_id": answer_id, "text": text}
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/open-voting")
async def open_voting(code: str, action: PlayerAction) -> dict[str, str]:
    try:
        room = store.room(code.upper())
        if room["host_player_id"] != action.player_id:
            raise ValueError("Only the host can open voting")
        active = store.active_round(code.upper())
        if active is None:
            raise ValueError("No active round")
        store.open_voting(int(active["id"]))
        return {"status": "voting"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/vote")
async def vote(code: str, data: Vote) -> dict[str, str]:
    try:
        store.player(code.upper(), data.player_id)
        active = store.active_round(code.upper())
        if active is None or active["state"] != "voting":
            raise ValueError("Voting is not open")
        store.vote(int(active["id"]), data.player_id, data.answer_id)
        return {"status": "recorded"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/arena/rooms/{code}/finish-round")
async def finish_round(code: str, action: PlayerAction) -> dict[str, Any]:
    try:
        room = store.room(code.upper())
        if room["host_player_id"] != action.player_id:
            raise ValueError("Only the host can finish the round")
        active = store.active_round(code.upper())
        if active is None:
            raise ValueError("No active round")
        winner = store.finish_round(int(active["id"]))
        return {"winner": winner}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/arena/audio/{generation_id}")
async def arena_audio(generation_id: str) -> Response:
    return await generated_audio(generation_id)


app.mount("/", StaticFiles(directory=ROOT / "arena_frontend", html=True), name="arena")
