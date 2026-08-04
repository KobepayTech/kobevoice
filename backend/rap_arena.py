from __future__ import annotations

import io
import json
import secrets
import sqlite3
import string
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Iterator

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.main import ROOT, cfg

APP_TITLE = "Kobe Rap Arena"
DATA_ROOT = ROOT / "data" / "rap_arena"
BEAT_ROOT = DATA_ROOT / "beats"
RECORDING_ROOT = DATA_ROOT / "recordings"
DATA_ROOT.mkdir(parents=True, exist_ok=True)
BEAT_ROOT.mkdir(parents=True, exist_ok=True)
RECORDING_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_AUDIO = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg"}
ALLOWED_VIDEO = {
    "video/webm": ".webm",
    "video/mp4": ".mp4",
    "application/octet-stream": ".webm",
}
TERMS_VERSION = "rap-arena-2026-08-04"


class CreateBattle(BaseModel):
    host_stage_name: str = Field(min_length=2, max_length=40)
    title: str = Field(min_length=3, max_length=100)
    language: str = "en"
    verse_seconds: int = Field(default=45, ge=15, le=120)
    contestant_copy_price: int = Field(default=5000, ge=0, le=1_000_000)
    public_copy_price: int = Field(default=10000, ge=0, le=1_000_000)
    currency: str = "TZS"
    consent_to_record_and_sell: bool


class JoinBattle(BaseModel):
    stage_name: str = Field(min_length=2, max_length=40)
    consent_to_record_and_sell: bool


class PlayerAction(BaseModel):
    player_id: str


class SelectBeat(PlayerAction):
    beat_id: str


class Vote(BaseModel):
    voter_key: str = Field(min_length=8, max_length=100)
    player_id: str


class CreateOrder(BaseModel):
    buyer_name: str = Field(min_length=2, max_length=100)
    buyer_email: str = Field(min_length=5, max_length=200)
    edition: str = Field(pattern="^(contestant|public)$")
    player_id: str | None = None


class MarkPaid(BaseModel):
    payment_reference: str = Field(min_length=2, max_length=120)


class RapStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS rap_beats (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    bpm INTEGER,
                    style TEXT,
                    source TEXT NOT NULL,
                    generation_prompt TEXT,
                    file_path TEXT NOT NULL,
                    rights_confirmed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rap_battles (
                    code TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    host_player_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    verse_seconds INTEGER NOT NULL,
                    contestant_copy_price INTEGER NOT NULL,
                    public_copy_price INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    beat_id TEXT,
                    state TEXT NOT NULL,
                    terms_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE TABLE IF NOT EXISTS rap_players (
                    id TEXT PRIMARY KEY,
                    battle_code TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    consented_at TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(battle_code, slot),
                    UNIQUE(battle_code, stage_name)
                );
                CREATE TABLE IF NOT EXISTS rap_verses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    battle_code TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    duration_seconds REAL,
                    effects_manifest TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(battle_code, player_id, round_number)
                );
                CREATE TABLE IF NOT EXISTS rap_votes (
                    battle_code TEXT NOT NULL,
                    voter_key TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(battle_code, voter_key)
                );
                CREATE TABLE IF NOT EXISTS rap_orders (
                    id TEXT PRIMARY KEY,
                    battle_code TEXT NOT NULL,
                    buyer_name TEXT NOT NULL,
                    buyer_email TEXT NOT NULL,
                    edition TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payment_reference TEXT,
                    download_token TEXT,
                    created_at TEXT NOT NULL,
                    paid_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_rap_players_battle ON rap_players(battle_code);
                CREATE INDEX IF NOT EXISTS idx_rap_verses_battle ON rap_verses(battle_code);
                CREATE INDEX IF NOT EXISTS idx_rap_orders_battle ON rap_orders(battle_code);
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_battle(self, data: CreateBattle) -> dict[str, str]:
        if not data.consent_to_record_and_sell:
            raise ValueError("Recording and marketplace consent is required")
        alphabet = string.ascii_uppercase + string.digits
        with self.connection() as db:
            while True:
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                if db.execute("SELECT 1 FROM rap_battles WHERE code=?", (code,)).fetchone() is None:
                    break
            player_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """INSERT INTO rap_battles(
                    code,title,host_player_id,language,verse_seconds,
                    contestant_copy_price,public_copy_price,currency,state,
                    terms_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    code,
                    data.title.strip(),
                    player_id,
                    data.language if data.language in {"en", "sw"} else "en",
                    data.verse_seconds,
                    data.contestant_copy_price,
                    data.public_copy_price,
                    data.currency.upper(),
                    "lobby",
                    TERMS_VERSION,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO rap_players(id,battle_code,stage_name,slot,consented_at) VALUES(?,?,?,?,?)",
                (player_id, code, data.host_stage_name.strip(), 1, now),
            )
        return {"battle_code": code, "player_id": player_id}

    def battle(self, code: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute("SELECT * FROM rap_battles WHERE code=?", (code,)).fetchone()
        if row is None:
            raise ValueError("Battle not found")
        return row

    def player(self, code: str, player_id: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM rap_players WHERE battle_code=? AND id=?",
                (code, player_id),
            ).fetchone()
        if row is None:
            raise ValueError("Player not found in battle")
        return row

    def join(self, code: str, data: JoinBattle) -> str:
        if not data.consent_to_record_and_sell:
            raise ValueError("Recording and marketplace consent is required")
        player_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            battle = db.execute("SELECT state FROM rap_battles WHERE code=?", (code,)).fetchone()
            if battle is None:
                raise ValueError("Battle not found")
            if battle["state"] != "lobby":
                raise ValueError("This battle has already started")
            count = db.execute(
                "SELECT COUNT(*) AS count FROM rap_players WHERE battle_code=?", (code,)
            ).fetchone()["count"]
            if int(count) >= 2:
                raise ValueError("This battle already has two contestants")
            try:
                db.execute(
                    "INSERT INTO rap_players(id,battle_code,stage_name,slot,consented_at) VALUES(?,?,?,?,?)",
                    (player_id, code, data.stage_name.strip(), 2, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("That stage name is already used") from exc
        return player_id

    def add_beat(
        self,
        title: str,
        bpm: int | None,
        style: str,
        source: str,
        prompt: str | None,
        file_path: Path,
    ) -> str:
        beat_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute(
                "INSERT INTO rap_beats(id,title,bpm,style,source,generation_prompt,file_path,rights_confirmed,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    beat_id,
                    title.strip(),
                    bpm,
                    style.strip(),
                    source,
                    prompt,
                    str(file_path),
                    1,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return beat_id

    def beats(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT id,title,bpm,style,source,generation_prompt,created_at FROM rap_beats ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def beat(self, beat_id: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute("SELECT * FROM rap_beats WHERE id=?", (beat_id,)).fetchone()
        if row is None:
            raise ValueError("Beat not found")
        return row

    def select_beat(self, code: str, player_id: str, beat_id: str) -> None:
        battle = self.battle(code)
        if battle["host_player_id"] != player_id:
            raise ValueError("Only the host can select the beat")
        self.beat(beat_id)
        with self.connection() as db:
            db.execute("UPDATE rap_battles SET beat_id=? WHERE code=?", (beat_id, code))

    def start(self, code: str, player_id: str) -> None:
        battle = self.battle(code)
        if battle["host_player_id"] != player_id:
            raise ValueError("Only the host can start the battle")
        with self.connection() as db:
            players = db.execute(
                "SELECT COUNT(*) AS count FROM rap_players WHERE battle_code=?", (code,)
            ).fetchone()["count"]
            if int(players) != 2:
                raise ValueError("Two contestants are required")
            if not battle["beat_id"]:
                raise ValueError("Select a beat first")
            db.execute("UPDATE rap_battles SET state='recording' WHERE code=?", (code,))

    def add_verse(
        self,
        code: str,
        player_id: str,
        round_number: int,
        file_path: Path,
        mime_type: str,
        duration: float | None,
        effects_manifest: dict[str, Any],
    ) -> int:
        battle = self.battle(code)
        if battle["state"] not in {"recording", "review"}:
            raise ValueError("Battle is not recording")
        self.player(code, player_id)
        with self.connection() as db:
            try:
                cursor = db.execute(
                    """INSERT INTO rap_verses(
                        battle_code,player_id,round_number,file_path,mime_type,
                        duration_seconds,effects_manifest,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        code,
                        player_id,
                        round_number,
                        str(file_path),
                        mime_type,
                        duration,
                        json.dumps(effects_manifest),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This verse was already uploaded") from exc
            count = db.execute(
                "SELECT COUNT(*) AS count FROM rap_verses WHERE battle_code=?", (code,)
            ).fetchone()["count"]
            if int(count) >= 2:
                db.execute("UPDATE rap_battles SET state='review' WHERE code=?", (code,))
            return int(cursor.lastrowid)

    def vote(self, code: str, voter_key: str, player_id: str) -> None:
        battle = self.battle(code)
        if battle["state"] not in {"review", "published"}:
            raise ValueError("Voting opens after both verses are uploaded")
        self.player(code, player_id)
        with self.connection() as db:
            try:
                db.execute(
                    "INSERT INTO rap_votes(battle_code,voter_key,player_id,created_at) VALUES(?,?,?,?)",
                    (code, voter_key, player_id, datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This device already voted") from exc

    def publish(self, code: str, player_id: str) -> None:
        battle = self.battle(code)
        if battle["host_player_id"] != player_id:
            raise ValueError("Only the host can publish")
        if battle["state"] != "review":
            raise ValueError("Both recorded verses are required before publishing")
        with self.connection() as db:
            db.execute(
                "UPDATE rap_battles SET state='published',published_at=? WHERE code=?",
                (datetime.now(timezone.utc).isoformat(), code),
            )

    def snapshot(self, code: str) -> dict[str, Any]:
        battle = dict(self.battle(code))
        with self.connection() as db:
            players = [dict(row) for row in db.execute(
                """SELECT p.id,p.stage_name,p.slot,p.score,COUNT(v.voter_key) AS votes
                FROM rap_players p
                LEFT JOIN rap_votes v ON v.player_id=p.id AND v.battle_code=p.battle_code
                WHERE p.battle_code=? GROUP BY p.id ORDER BY p.slot""",
                (code,),
            ).fetchall()]
            verses = [dict(row) for row in db.execute(
                "SELECT id,player_id,round_number,duration_seconds,created_at FROM rap_verses WHERE battle_code=? ORDER BY round_number,id",
                (code,),
            ).fetchall()]
        beat = None
        if battle.get("beat_id"):
            row = self.beat(str(battle["beat_id"]))
            beat = {"id": row["id"], "title": row["title"], "bpm": row["bpm"], "style": row["style"], "source": row["source"]}
        return {"battle": battle, "players": players, "verses": verses, "beat": beat}

    def verse_file(self, verse_id: int) -> tuple[Path, str]:
        with self.connection() as db:
            row = db.execute("SELECT file_path,mime_type FROM rap_verses WHERE id=?", (verse_id,)).fetchone()
        if row is None:
            raise ValueError("Verse not found")
        return Path(row["file_path"]), row["mime_type"]

    def create_order(self, code: str, data: CreateOrder) -> dict[str, Any]:
        battle = self.battle(code)
        if battle["state"] != "published":
            raise ValueError("This battle is not for sale yet")
        if data.edition == "contestant":
            if not data.player_id:
                raise ValueError("Contestant purchase requires a player id")
            self.player(code, data.player_id)
            amount = int(battle["contestant_copy_price"])
        else:
            amount = int(battle["public_copy_price"])
        order_id = str(uuid.uuid4())
        with self.connection() as db:
            db.execute(
                "INSERT INTO rap_orders(id,battle_code,buyer_name,buyer_email,edition,amount,currency,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    order_id,
                    code,
                    data.buyer_name.strip(),
                    data.buyer_email.strip().lower(),
                    data.edition,
                    amount,
                    battle["currency"],
                    "pending",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return {"order_id": order_id, "amount": amount, "currency": battle["currency"], "status": "pending"}

    def mark_paid(self, order_id: str, payment_reference: str) -> dict[str, str]:
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            row = db.execute("SELECT status FROM rap_orders WHERE id=?", (order_id,)).fetchone()
            if row is None:
                raise ValueError("Order not found")
            if row["status"] == "paid":
                existing = db.execute("SELECT download_token FROM rap_orders WHERE id=?", (order_id,)).fetchone()
                return {"status": "paid", "download_token": existing["download_token"]}
            db.execute(
                "UPDATE rap_orders SET status='paid',payment_reference=?,download_token=?,paid_at=? WHERE id=?",
                (payment_reference, token, datetime.now(timezone.utc).isoformat(), order_id),
            )
        return {"status": "paid", "download_token": token}

    def download_order(self, token: str) -> sqlite3.Row:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM rap_orders WHERE download_token=? AND status='paid'", (token,)
            ).fetchone()
        if row is None:
            raise ValueError("Invalid or unpaid download")
        return row

    def catalog(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT b.code,b.title,b.public_copy_price,b.currency,b.published_at,
                GROUP_CONCAT(p.stage_name,' vs ') AS contestants
                FROM rap_battles b JOIN rap_players p ON p.battle_code=b.code
                WHERE b.state='published' GROUP BY b.code ORDER BY b.published_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]


store = RapStore(cfg.database_path)
app = FastAPI(title=APP_TITLE, version="0.1.0")


def clean_code(code: str) -> str:
    code = code.upper().strip()
    if len(code) != 6 or not code.isalnum():
        raise HTTPException(400, "Invalid battle code")
    return code


@app.post("/api/rap/battles")
async def create_battle(data: CreateBattle) -> dict[str, str]:
    try:
        return store.create_battle(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rap/battles/{code}/join")
async def join_battle(code: str, data: JoinBattle) -> dict[str, str]:
    try:
        return {"player_id": store.join(clean_code(code), data)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/rap/battles/{code}")
async def battle_state(code: str) -> dict[str, Any]:
    try:
        return store.snapshot(clean_code(code))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/rap/beats")
async def beats() -> dict[str, Any]:
    return {"items": store.beats()}


@app.post("/api/rap/beats/upload")
async def upload_beat(
    beat: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    style: Annotated[str, Form()] = "rap",
    bpm: Annotated[int | None, Form()] = None,
    generation_prompt: Annotated[str | None, Form()] = None,
    rights_confirmed: Annotated[bool, Form()] = False,
) -> dict[str, str]:
    if not rights_confirmed:
        raise HTTPException(400, "Confirm that this beat is original, generated, or properly licensed")
    mime = beat.content_type or "application/octet-stream"
    suffix = ALLOWED_AUDIO.get(mime)
    if not suffix:
        raise HTTPException(415, "Use MP3, WAV, or OGG audio")
    payload = await beat.read()
    if not payload or len(payload) > 50 * 1024 * 1024:
        raise HTTPException(400, "Beat must be between 1 byte and 50 MB")
    path = BEAT_ROOT / f"{uuid.uuid4()}{suffix}"
    path.write_bytes(payload)
    beat_id = store.add_beat(title, bpm, style, "ace-step-or-upload", generation_prompt, path)
    return {"beat_id": beat_id}


@app.get("/api/rap/beats/{beat_id}/audio")
async def beat_audio(beat_id: str) -> FileResponse:
    try:
        beat = store.beat(beat_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    path = Path(beat["file_path"])
    if not path.exists():
        raise HTTPException(404, "Beat audio file is missing")
    return FileResponse(path)


@app.post("/api/rap/battles/{code}/select-beat")
async def select_beat(code: str, data: SelectBeat) -> dict[str, str]:
    try:
        store.select_beat(clean_code(code), data.player_id, data.beat_id)
        return {"status": "selected"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rap/battles/{code}/start")
async def start_battle(code: str, data: PlayerAction) -> dict[str, str]:
    try:
        store.start(clean_code(code), data.player_id)
        return {"status": "recording"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rap/battles/{code}/verses")
async def upload_verse(
    code: str,
    video: Annotated[UploadFile, File()],
    player_id: Annotated[str, Form()],
    round_number: Annotated[int, Form()] = 1,
    duration_seconds: Annotated[float | None, Form()] = None,
    effects_manifest: Annotated[str, Form()] = "{}",
) -> dict[str, int]:
    code = clean_code(code)
    mime = video.content_type or "application/octet-stream"
    suffix = ALLOWED_VIDEO.get(mime)
    if not suffix:
        raise HTTPException(415, "Use WebM or MP4 video")
    payload = await video.read()
    if not payload or len(payload) > 250 * 1024 * 1024:
        raise HTTPException(400, "Recording must be between 1 byte and 250 MB")
    try:
        manifest = json.loads(effects_manifest)
        if not isinstance(manifest, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, "Invalid effects manifest") from exc
    battle_dir = RECORDING_ROOT / code
    battle_dir.mkdir(parents=True, exist_ok=True)
    path = battle_dir / f"{player_id}-round-{round_number}-{uuid.uuid4()}{suffix}"
    path.write_bytes(payload)
    try:
        verse_id = store.add_verse(code, player_id, round_number, path, mime, duration_seconds, manifest)
        return {"verse_id": verse_id}
    except ValueError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/rap/verses/{verse_id}/video")
async def verse_video(verse_id: int) -> FileResponse:
    try:
        path, mime = store.verse_file(verse_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.exists():
        raise HTTPException(404, "Recording file is missing")
    return FileResponse(path, media_type=mime)


@app.post("/api/rap/battles/{code}/vote")
async def vote(code: str, data: Vote) -> dict[str, str]:
    try:
        store.vote(clean_code(code), data.voter_key, data.player_id)
        return {"status": "recorded"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rap/battles/{code}/publish")
async def publish(code: str, data: PlayerAction) -> dict[str, str]:
    try:
        store.publish(clean_code(code), data.player_id)
        return {"status": "published"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/rap/catalog")
async def catalog() -> dict[str, Any]:
    return {"items": store.catalog()}


@app.post("/api/rap/battles/{code}/orders")
async def create_order(code: str, data: CreateOrder) -> dict[str, Any]:
    try:
        return store.create_order(clean_code(code), data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/rap/orders/{order_id}/mark-paid")
async def mark_paid(
    order_id: str,
    data: MarkPaid,
    x_rap_admin_token: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    expected = secrets.compare_digest(
        x_rap_admin_token or "",
        getattr(cfg, "rap_admin_token", None) or "change-me-before-production",
    )
    if not expected:
        raise HTTPException(401, "Invalid admin token")
    try:
        return store.mark_paid(order_id, data.payment_reference)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/rap/download/{token}")
async def download(token: str) -> StreamingResponse:
    try:
        order = store.download_order(token)
        snapshot = store.snapshot(order["battle_code"])
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "battle.json",
            json.dumps(
                {
                    "battle": snapshot["battle"],
                    "players": snapshot["players"],
                    "beat": snapshot["beat"],
                    "edition": order["edition"],
                    "license": "Personal viewing copy. No impersonation, resale, or commercial reuse without separate written permission.",
                },
                indent=2,
            ),
        )
        for verse in snapshot["verses"]:
            path, _ = store.verse_file(int(verse["id"]))
            if path.exists():
                archive.write(path, f"videos/{path.name}")
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="kobe-rap-battle-{order["battle_code"]}.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


app.mount("/", StaticFiles(directory=ROOT / "rap_frontend", html=True), name="rap-arena")
