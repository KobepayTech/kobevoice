# Kobevoice Lite Engine — GPU-free TTS worker (espeak-ng).
# Build from the repo root:
#   docker build -f backend/engine_lite/Dockerfile -t kobevoice-engine-lite .
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "pydantic>=2.7"

COPY backend/__init__.py ./backend/__init__.py
COPY backend/engine_lite ./backend/engine_lite

EXPOSE 8000
CMD ["uvicorn", "backend.engine_lite.main:app", "--host", "0.0.0.0", "--port", "8000"]
