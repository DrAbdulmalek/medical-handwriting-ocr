# Docker Deployment Profiles

Medical Handwriting OCR supports three deployment profiles optimized for different use cases.

## Quick Start

```bash
# Lite — development/testing (CPU only)
docker compose -f docker/profiles/lite/docker-compose.yml up -d

# Standard — staging (GPU optional)
docker compose -f docker/profiles/standard/docker-compose.yml up -d

# GPU Production — full deployment
docker compose -f docker/profiles/gpu-production/docker-compose.yml --env-file .env.production up -d
```

## Profile Comparison

| Feature | Lite | Standard | GPU Production |
|---------|------|----------|----------------|
| **Purpose** | Development & testing | Staging environments | Production deployment |
| **Min CPU** | 2 cores | 4 cores | 8 cores |
| **Min RAM** | 4 GB | 8 GB | 16 GB |
| **GPU** | None | Optional | Required (T4+) |
| **OCR Engines** | Tesseract, EasyOCR | + PaddleOCR, TrOCR | All 5 engines |
| **Database** | SQLite | PostgreSQL | PostgreSQL |
| **Redis** | Yes | Yes | Yes (password) |
| **MinIO/S3** | No | No | Yes |
| **Monitoring** | No | No | Prometheus + Grafana |
| **Continuous Learning** | No | No | Yes |
| **Health Checks** | No | No | Yes |

## Environment Variables

For GPU Production, create `.env.production` from the template:

```bash
cp env.example.production .env.production
# Edit with your actual values
docker compose -f docker/profiles/gpu-production/docker-compose.yml --env-file .env.production up -d
```

## API Endpoints (All Profiles)

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /metrics` | Prometheus metrics |
| `POST /api/upload` | Upload document for OCR |
| `POST /api/correct` | Correct OCR output |
| `POST /api/suggestions` | Get correction suggestions |
| `GET /api/dictionaries/search` | Search medical dictionaries |
| `POST /api/umls/validate` | Validate medical terms |

## Performance Targets

| Metric | Lite | Standard | GPU Production |
|--------|------|----------|----------------|
| CER | < 10% | < 7% | < 5% |
| Latency/page | < 10s | < 5s | < 3s |
| Throughput | 2 pages/min | 5 pages/min | 10+ pages/min |
