# Docker Deployment Guide

**Status:** In Development for v0.2.0
**Requested By:** Reddit community ([thread](https://reddit.com/r/LocalLLaMA/...))

## Overview

Docker support makes Kobevoice easier to deploy, especially for:

- **Consistent Environments**: Same setup across dev/staging/prod
- **GPU Passthrough**: Easy NVIDIA/AMD GPU access
- **Server Deployments**: Run on headless Linux servers
- **Multi-User Setups**: Isolate instances per user/team
- **Cloud Platforms**: Deploy to AWS, GCP, Azure, DigitalOcean

## Quick Start

### Using Pre-Built Images (Recommended)

```bash
# CPU-only version
docker run -p 8000:8000 -v kobevoice-data:/app/data \
  ghcr.io/kobepaytech/kobevoice:latest

# NVIDIA GPU version
docker run --gpus all -p 8000:8000 -v kobevoice-data:/app/data \
  ghcr.io/kobepaytech/kobevoice:latest-cuda

# AMD GPU version (experimental)
docker run --device=/dev/kfd --device=/dev/dri -p 8000:8000 \
  -v kobevoice-data:/app/data \
  ghcr.io/kobepaytech/kobevoice:latest-rocm
```

Then open: `http://localhost:8000`

### Using Docker Compose (Easiest)

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  kobevoice:
    image: ghcr.io/kobepaytech/kobevoice:latest-cuda
    ports:
      - "8000:8000"
    volumes:
      - kobevoice-data:/app/data
      - huggingface-cache:/root/.cache/huggingface
    environment:
      - GPU_MEMORY_FRACTION=0.8  # Use 80% of GPU memory
      - TTS_MODE=local
      - WHISPER_MODE=local
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  kobevoice-data:
  huggingface-cache:
```

Run:
```bash
docker compose up -d
```

## Building From Source

### Basic Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy application
COPY backend/ /app/backend/
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir git+https://github.com/QwenLM/Qwen3-TTS.git

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Run server
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:
```bash
docker build -t kobevoice .
docker run -p 8000:8000 -v $(pwd)/data:/app/data kobevoice
```

### Multi-Stage Build (Optimized)

Smaller image size by separating build and runtime:

```dockerfile
# Dockerfile.optimized
# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    git build-essential && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir --target=/build/packages \
    -r requirements.txt

RUN pip install --no-cache-dir --target=/build/packages \
    git+https://github.com/QwenLM/Qwen3-TTS.git

# Stage 2: Runtime
FROM python:3.11-slim

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages/

# Copy application code
COPY backend/ /app/backend/

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build:
```bash
docker build -f Dockerfile.optimized -t kobevoice:slim .
```

## GPU Support

### NVIDIA GPUs (CUDA)

**Dockerfile:**
```dockerfile
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

# Install Python
RUN apt-get update && apt-get install -y \
    python3.11 python3-pip git ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install PyTorch with CUDA support
COPY backend/requirements.txt .
RUN pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install other dependencies
RUN pip3 install -r requirements.txt
RUN pip3 install git+https://github.com/QwenLM/Qwen3-TTS.git

COPY backend/ /app/backend/

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Run with GPU:**
```bash
docker run --gpus all -p 8000:8000 \
  -v kobevoice-data:/app/data \
  kobevoice:cuda
```

**Docker Compose with GPU:**
```yaml
services:
  kobevoice:
    image: kobevoice:cuda
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
```

### AMD GPUs (ROCm) - Experimental

**Dockerfile:**
```dockerfile
FROM rocm/dev-ubuntu-22.04:6.0

# Install Python
RUN apt-get update && apt-get install -y \
    python3.11 python3-pip git ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install PyTorch with ROCm support
COPY backend/requirements.txt .
RUN pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/rocm6.0

# Install other dependencies
RUN pip3 install -r requirements.txt
RUN pip3 install git+https://github.com/QwenLM/Qwen3-TTS.git

# Set ROCm environment variables
ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV ROCM_PATH=/opt/rocm

COPY backend/ /app/backend/

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Run with AMD GPU:**
```bash
docker run --device=/dev/kfd --device=/dev/dri \
  --group-add video --ipc=host --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  -p 8000:8000 -v kobevoice-data:/app/data \
  kobevoice:rocm
```

**Note:** ROCm support varies by GPU model. Works best on Linux. See [AMD ROCm docs](https://rocm.docs.amd.com) for compatibility.

## Volume Mounts

### Essential Volumes

```bash
docker run -v kobevoice-data:/app/data \           # Profiles, generations, history
           -v huggingface-cache:/root/.cache/huggingface \  # Downloaded models
           -p 8000:8000 kobevoice
```

### Development Volume Mounts

For development with hot-reload:

```bash
docker run -v $(pwd)/backend:/app/backend \       # Live code changes
           -v kobevoice-data:/app/data \
           -e RELOAD=true \
           -p 8000:8000 kobevoice
```

### Custom Model Storage

Use external model directory:

```bash
docker run -v /path/to/models:/models \
           -e MODELS_DIR=/models \
           -v kobevoice-data:/app/data \
           -p 8000:8000 kobevoice
```

## Environment Variables

Configure Kobevoice via environment variables:

```bash
docker run -e TTS_MODE=local \
           -e WHISPER_MODE=openai-api \
           -e OPENAI_API_KEY=sk-... \
           -e GPU_MEMORY_FRACTION=0.8 \
           -e LOG_LEVEL=info \
           -p 8000:8000 kobevoice
```

### Available Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_MODE` | `local` | TTS provider: `local`, `remote` |
| `TTS_REMOTE_URL` | - | URL for remote TTS server |
| `WHISPER_MODE` | `local` | Whisper provider: `local`, `openai-api`, `remote` |
| `WHISPER_REMOTE_URL` | - | URL for remote Whisper server |
| `OPENAI_API_KEY` | - | OpenAI API key (if using OpenAI Whisper) |
| `GPU_MEMORY_FRACTION` | `0.9` | Fraction of GPU memory to use (0.0-1.0) |
| `DATA_DIR` | `/app/data` | Directory for profiles/generations |
| `MODELS_DIR` | `/app/models` | Directory for local models |
| `LOG_LEVEL` | `info` | Logging level: `debug`, `info`, `warning`, `error` |
| `RELOAD` | `false` | Enable hot-reload for development |

## Complete Docker Compose Examples

### Production Deployment

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  kobevoice:
    image: ghcr.io/kobepaytech/kobevoice:latest-cuda
    container_name: kobevoice
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - kobevoice-data:/app/data
      - huggingface-cache:/root/.cache/huggingface
    environment:
      - TTS_MODE=local
      - WHISPER_MODE=local
      - GPU_MEMORY_FRACTION=0.8
      - LOG_LEVEL=info
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  kobevoice-data:
    driver: local
  huggingface-cache:
    driver: local
```

Run:
```bash
docker compose -f docker-compose.prod.yml up -d
```

### Development Setup

```yaml
# docker-compose.dev.yml
version: '3.8'

services:
  kobevoice:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app/backend:ro
      - kobevoice-data:/app/data
      - huggingface-cache:/root/.cache/huggingface
    environment:
      - RELOAD=true
      - LOG_LEVEL=debug
      - TTS_MODE=local
    command: uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

volumes:
  kobevoice-data:
  huggingface-cache:
```

### Multi-Service Stack

Full stack with reverse proxy and monitoring:

```yaml
# docker-compose.stack.yml
version: '3.8'

services:
  # Main Kobevoice app
  kobevoice:
    image: ghcr.io/kobepaytech/kobevoice:latest-cuda
    restart: unless-stopped
    volumes:
      - kobevoice-data:/app/data
      - huggingface-cache:/root/.cache/huggingface
    environment:
      - TTS_MODE=local
      - WHISPER_MODE=local
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  # Nginx reverse proxy
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - kobevoice

  # Prometheus monitoring (optional)
  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus

volumes:
  kobevoice-data:
  huggingface-cache:
  prometheus-data:
```

## Cloud Deployment

### AWS EC2

1. **Launch GPU Instance** (g4dn.xlarge or p3.2xlarge)
2. **Install Docker + nvidia-docker:**
   ```bash
   # Amazon Linux 2
   sudo yum install -y docker
   sudo systemctl start docker
   distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
   curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
   curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | \
     sudo tee /etc/apt/sources.list.d/nvidia-docker.list
   sudo apt-get update && sudo apt-get install -y nvidia-docker2
   sudo systemctl restart docker
   ```
3. **Deploy:**
   ```bash
   docker run --gpus all -d -p 80:8000 \
     -v kobevoice-data:/app/data \
     --restart unless-stopped \
     ghcr.io/kobepaytech/kobevoice:latest-cuda
   ```

### DigitalOcean

Use GPU Droplet + Docker:

```bash
# Create droplet via CLI
doctl compute droplet create kobevoice \
  --size gpu-h100x1-80gb \
  --image ubuntu-22-04-x64 \
  --region nyc3

# SSH and deploy
ssh root@<droplet-ip>
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
docker run --gpus all -d -p 80:8000 kobevoice:cuda
```

### Google Cloud Run (CPU-only)

```bash
# Build and push
docker build -t gcr.io/your-project/kobevoice .
docker push gcr.io/your-project/kobevoice

# Deploy to Cloud Run
gcloud run deploy kobevoice \
  --image gcr.io/your-project/kobevoice \
  --platform managed \
  --region us-central1 \
  --memory 4Gi \
  --cpu 2 \
  --port 8000
```

### Fly.io

Create `fly.toml`:
```toml
app = "kobevoice"

[build]
  image = "ghcr.io/kobepaytech/kobevoice:latest"

[[services]]
  http_checks = []
  internal_port = 8000
  protocol = "tcp"

  [[services.ports]]
    port = 80
    handlers = ["http"]

  [[services.ports]]
    port = 443
    handlers = ["tls", "http"]

[mounts]
  source = "kobevoice_data"
  destination = "/app/data"
```

Deploy:
```bash
fly launch
fly deploy
```

## Troubleshooting

### GPU Not Detected

**Check NVIDIA Docker:**
```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

If this fails, reinstall nvidia-docker2.

**Check AMD ROCm:**
```bash
docker run --rm --device=/dev/kfd --device=/dev/dri rocm/dev-ubuntu-22.04:6.0 rocminfo
```

### Permission Errors

Container can't write to volumes:
```bash
# Fix permissions
docker run --user $(id -u):$(id -g) -v $(pwd)/data:/app/data kobevoice
```

### Out of Memory

Reduce GPU memory usage:
```bash
docker run -e GPU_MEMORY_FRACTION=0.5 kobevoice
```

Or use CPU-only:
```bash
docker run -e DEVICE=cpu kobevoice
```

### Model Download Fails

Ensure HuggingFace cache is writable:
```bash
docker run -v huggingface-cache:/root/.cache/huggingface kobevoice
```

Or use host cache:
```bash
docker run -v ~/.cache/huggingface:/root/.cache/huggingface kobevoice
```

### Port Already in Use

Change host port:
```bash
docker run -p 8080:8000 kobevoice  # Use port 8080 instead
```

## Security Best Practices

### 1. Don't Run as Root

Create non-root user in Dockerfile:
```dockerfile
RUN useradd -m -u 1000 kobevoice
USER kobevoice
```

### 2. Use Secrets for API Keys

Don't put API keys in docker-compose.yml:

```bash
# Use Docker secrets
echo "sk-your-key" | docker secret create openai_key -

docker service create \
  --secret openai_key \
  -e OPENAI_API_KEY_FILE=/run/secrets/openai_key \
  kobevoice
```

### 3. Network Isolation

Use internal networks for multi-container setups:

```yaml
services:
  kobevoice:
    networks:
      - internal
  nginx:
    networks:
      - internal
      - external
    ports:
      - "80:80"

networks:
  internal:
    internal: true
  external:
```

### 4. Resource Limits

Prevent resource exhaustion:

```yaml
services:
  kobevoice:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
        reservations:
          cpus: '2'
          memory: 4G
```

## Performance Tuning

### GPU Memory Management

```bash
# Use 80% of GPU (default 90%)
docker run -e GPU_MEMORY_FRACTION=0.8 kobevoice

# Allow GPU memory growth (prevents OOM)
docker run -e TF_FORCE_GPU_ALLOW_GROWTH=true kobevoice
```

### Model Caching

Pre-download models to volume:

```bash
# Download models first
docker run --rm -v huggingface-cache:/root/.cache/huggingface \
  kobevoice python -c "
from transformers import WhisperProcessor, WhisperForConditionalGeneration
WhisperProcessor.from_pretrained('openai/whisper-base')
WhisperForConditionalGeneration.from_pretrained('openai/whisper-base')
"

# Then run normally
docker run -v huggingface-cache:/root/.cache/huggingface kobevoice
```

### Multi-Worker Setup

Use uvicorn workers for better throughput:

```dockerfile
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## Monitoring

### Health Checks

Built-in health endpoint:
```bash
curl http://localhost:8000/health
```

Docker health check:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### Prometheus Metrics

Add metrics exporter:
```python
# backend/main.py
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

Then scrape `/metrics` with Prometheus.

### Logs

View container logs:
```bash
docker logs -f kobevoice

# Or with compose
docker compose logs -f kobevoice
```

## Next Steps

- [ ] Publish official images to GitHub Container Registry
- [ ] Add Kubernetes Helm charts
- [ ] Create Docker Desktop extension
- [ ] Add automated vulnerability scanning
- [ ] Support ARM64 builds for Raspberry Pi / Apple Silicon

## Contributing

Help improve Docker support:
1. Test on different platforms (AMD GPU, ARM64, etc.)
2. Submit Dockerfile optimizations
3. Share deployment configurations
4. Report issues: [GitHub Issues](https://github.com/kobepaytech/kobevoice/issues)

## Resources

- [Docker Documentation](https://docs.docker.com)
- [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-docker)
- [AMD ROCm Docker](https://rocm.docs.amd.com/projects/install-on-linux/en/latest/how-to/docker.html)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)
