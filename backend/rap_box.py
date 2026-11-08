from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import Response
from starlette.background import BackgroundTask
from starlette.middleware.base import BaseHTTPMiddleware

from backend.rap_arena import app

app.title = "Rap Box"
app.description = (
    "Competitive voice and video rap battles with original beats, live visual "
    "effects, audience voting, recorded replay sales, and protected downloads."
)


class RapBoxBrandingMiddleware(BaseHTTPMiddleware):
    """Apply the Rap Box brand while legacy internal routes remain stable."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response

        body = b""
        iterator: AsyncIterator[bytes] = response.body_iterator  # type: ignore[assignment]
        async for chunk in iterator:
            body += chunk

        body = (
            body.replace(b"Kobe Rap Arena", b"Rap Box")
            .replace(b"KOBE RAP ARENA", b"RAP BOX")
            .replace(b"KobeRapArena", b"RapBox")
        )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        background = response.background
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type="text/html",
            background=background if isinstance(background, BackgroundTask) else None,
        )


app.add_middleware(RapBoxBrandingMiddleware)
