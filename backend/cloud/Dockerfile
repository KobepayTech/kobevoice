# Kobevoice Cloud control-plane — standalone, lightweight (no ML deps).
# Build from the repo root:
#   docker build -f backend/cloud/Dockerfile -t kobevoice-cloud .
#   docker run -p 9000:9000 --env-file .env kobevoice-cloud
FROM python:3.11-slim

WORKDIR /app

COPY backend/cloud/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy just the cloud package (control-plane is independent of the ML backend).
COPY backend/__init__.py ./backend/__init__.py
COPY backend/cloud ./backend/cloud

ENV KOBEVOICE_CLOUD_DATABASE_URL=sqlite:////data/cloud.db
VOLUME ["/data"]
EXPOSE 9000

CMD ["uvicorn", "backend.cloud.main:app", "--host", "0.0.0.0", "--port", "9000"]
