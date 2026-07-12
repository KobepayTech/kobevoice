"""Kobevoice Lite Engine — a real, GPU-free TTS worker.

Implements the stateless synthesis contract the cloud control-plane proxies to:

    POST /synthesize  {text, voice?, language?, speed?}  ->  audio/wav bytes

It uses the offline `espeak-ng` binary, so it produces actual speech audio on
any machine with no models to download and no GPU. In production, swap
``KOBEVOICE_CLOUD_ENGINE_URL`` to point at the full Qwen/Whisper engine (which
exposes the same ``/synthesize`` contract via an adapter) — the control-plane
code does not change.

Run:
    uvicorn backend.engine_lite.main:app --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

app = FastAPI(title="Kobevoice Lite Engine", version="0.1.0")

_ESPEAK = shutil.which("espeak-ng") or shutil.which("espeak")

# A tiny curated preset map so callers can request named voices. espeak-ng
# voice identifiers; extend freely.
VOICES = {
    "default": "en",
    "en-us": "en-us",
    "en-gb": "en-gb",
    "female": "en+f3",
    "male": "en+m3",
    "whisper": "en+whisper",
}


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    voice: str | None = None  # a key in VOICES, or a raw espeak voice id
    language: str | None = None
    speed: int = Field(default=175, ge=80, le=450)  # words per minute


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _ESPEAK else "degraded",
        "service": "kobevoice-lite-engine",
        "espeak": bool(_ESPEAK),
        "voices": list(VOICES),
    }


@app.get("/voices")
def voices() -> dict:
    return {"voices": list(VOICES)}


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    if not _ESPEAK:
        raise HTTPException(
            status_code=503,
            detail="espeak-ng is not installed on the engine host",
        )

    voice = VOICES.get((req.voice or "").lower(), req.voice) or req.language or "en"
    args = [_ESPEAK, "-v", voice, "-s", str(req.speed), "--stdout", req.text]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0 or not stdout:
        logger.error("espeak-ng failed: %s", stderr.decode(errors="ignore"))
        raise HTTPException(status_code=500, detail="Synthesis failed")

    return Response(
        content=stdout,
        media_type="audio/wav",
        headers={"X-Engine": "kobevoice-lite", "X-Voice": voice},
    )
