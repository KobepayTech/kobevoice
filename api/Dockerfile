FROM python:3.11-slim

WORKDIR /app

# Install deps first so dependency layers cache across source edits.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . psycopg[binary]

EXPOSE 8000

# Run as a non-root user: the API is internet-facing in most deployments.
RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["uvicorn", "kobeos.main:app", "--host", "0.0.0.0", "--port", "8000"]
