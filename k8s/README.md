# Medical Handwriting OCR — Kubernetes Manifests

Production-ready Kubernetes manifests for the Medical Handwriting OCR service, built with Kustomize for environment-based overlays.

## Architecture

```
                         ┌──────────────────────────────────────────┐
                         │           Ingress (Nginx)                │
                         │         Port 80 / 443 (TLS)             │
                         └──────────────┬───────────────────────────┘
                                        │
                         ┌──────────────┴───────────────────────────┐
                         │         Nginx Reverse Proxy              │
                         │    Rate Limiting / Security Headers      │
                         └──────┬───────────────────┬───────────────┘
                                │                   │
                    ┌───────────┴───┐       ┌───────┴─────────┐
                    │  Backend      │       │  Backend        │
                    │  (Stable)     │       │  (Canary 20%)   │
                    │  FastAPI:8000 │       │  FastAPI:8000   │
                    │  3 replicas   │       │  1 replica      │
                    │  HPA 3-10     │       │                 │
                    └───────┬───────┘       └─────────────────┘
                            │
                 ┌──────────┼──────────┐
                 │          │          │
          ┌──────┴──┐  ┌───┴─────┐  ┌──┴──────┐
          │PostgreSQL│  │ Redis 7 │  │ MinIO   │
          │  :5432   │  │ :6379   │  │ :9000   │
          │  50Gi PVC│  │ 10Gi PVC│  │ 100Gi   │
          └─────────┘  └────┬────┘  └─────────┘
                            │
                    ┌───────┴────────┐
                    │ Celery Workers  │
                    │ Async OCR tasks │
                    │ 2 replicas      │
                    │ HPA 2-8         │
                    └────────────────┘

                    ┌────────────────┐
                    │ Training Job    │
                    │ GPU (nvidia)    │
                    │ 200Gi PVC       │
                    └────────────────┘
```

## Directory Structure

```
k8s/
├── base/
│   ├── namespace.yaml              # medical-ocr namespace
│   ├── configmap.yaml              # Application config (env vars)
│   ├── postgres-deployment.yaml    # PostgreSQL 15 + Secret + PVC + Service
│   ├── redis-deployment.yaml       # Redis 7 + ConfigMap + Secret + PVC + Service
│   ├── minio-deployment.yaml       # MinIO + Secret + PVC + InitContainer (buckets)
│   ├── backend-deployment.yaml     # FastAPI backend + HPA + PDB + Service
│   ├── celery-deployment.yaml      # Celery workers + Celery beat + HPA
│   ├── nginx-deployment.yaml       # Nginx reverse proxy + ConfigMap + Ingress + Service
│   ├── training-job.yaml           # GPU training Job + PVC + InitContainers
│   └── kustomization.yaml          # Base kustomize overlay
├── canary/
│   ├── kustomization.yaml          # Canary overlay (inherits base)
│   └── backend-canary.yaml         # Canary deployment + Service + Ingress (20% weight)
└── README.md                       # This file
```

## Quick Start

### Prerequisites

- Kubernetes 1.28+
- kustomize v5+
- kubectl
- An NVIDIA GPU node pool (for training jobs)
- cert-manager (for TLS certificates)
- NGINX Ingress Controller

### Deploy Base (Stable)

```bash
# Apply namespace and config first
kubectl apply -k k8s/base/ --dry-run=client -o yaml | kubectl apply -f -

# Full base deployment
kubectl apply -k k8s/base/
```

### Deploy Canary

```bash
# Deploy canary (inherits all base resources, adds canary variant)
kubectl apply -k k8s/canary/
```

### Deploy with ArgoCD

```bash
# Base application
argocd app create medical-ocr \
  --repo https://github.com/your-org/medical-handwriting-ocr.git \
  --path k8s/base \
  --dest-namespace medical-ocr \
  --dest-server https://kubernetes.default.svc \
  --sync-policy automated \
  --self-heal

# Canary application
argocd app create medical-ocr-canary \
  --repo https://github.com/your-org/medical-handwriting-ocr.git \
  --path k8s/canary \
  --dest-namespace medical-ocr \
  --dest-server https://kubernetes.default.svc \
  --sync-policy automated \
  --self-heal
```

## Secrets Management

All secrets use base64-encoded placeholders. **Replace before deploying to production.**

| Secret Name          | Keys                              | Description                     |
|----------------------|-----------------------------------|---------------------------------|
| `postgres-secret`    | `POSTGRES_PASSWORD`, `DB_PASSWORD` | Database credentials           |
| `redis-secret`       | `REDIS_PASSWORD`                   | Redis auth password             |
| `minio-secret`       | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | MinIO credentials  |
| `backend-secret`     | `DB_PASSWORD`, `REDIS_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `SECRET_KEY` | Backend secrets |

**Recommended:** Use an external secrets operator (e.g., [External Secrets Operator](https://external-secrets.io/), [HashiCorp Vault](https://www.vaultproject.io/), or [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/)).

## Resource Allocation

| Component       | CPU (req/lim)  | Memory (req/lim) | Replicas | HPA Range |
|-----------------|----------------|-------------------|----------|-----------|
| PostgreSQL      | 250m / 2 cores | 512Mi / 4Gi      | 1        | —         |
| Redis           | 100m / 1 core  | 256Mi / 2Gi      | 1        | —         |
| MinIO           | 250m / 2 cores | 512Mi / 4Gi      | 1        | —         |
| Backend         | 500m / 2 cores | 1Gi / 4Gi        | 3        | 3–10      |
| Celery Workers  | 1 core / 4 cores| 2Gi / 8Gi       | 2        | 2–8       |
| Nginx           | 100m / 1 core  | 128Mi / 512Mi    | 2        | —         |
| Training Job    | 4 cores / 8    | 16Gi / 32Gi      | 1        | —         |
|                 | 1x GPU (req/lim)|                  |          |           |

## Storage (PVCs)

| PVC Name              | Size   | Access Mode     | Mount Path          |
|-----------------------|--------|-----------------|---------------------|
| `postgres-data-pvc`   | 50Gi   | ReadWriteOnce   | `/var/lib/postgresql/data` |
| `redis-data-pvc`      | 10Gi   | ReadWriteOnce   | `/data`             |
| `minio-data-pvc`      | 100Gi  | ReadWriteOnce   | `/data`             |
| `model-cache-pvc`      | 20Gi   | ReadWriteMany   | `/models`           |
| `training-output-pvc`  | 200Gi  | ReadWriteOnce   | `/models/output`    |

## Canary Strategy

The canary deployment routes **20% of traffic** to the canary variant using the NGINX Ingress Controller's native canary annotations:

- **Stable**: `backend` deployment (3 replicas, `v1.0.0`)
- **Canary**: `backend-canary` deployment (1 replica, `v1.1.0`)
- **Traffic routing**: NGINX Ingress `canary-weight: "20"`
- **Fine-grained control**: Cookie-based routing via `canary-by-cookie: "canary"` (set `canary=true` cookie to route all your traffic)

### Canary Promotion

```bash
# Gradually increase canary weight
kubectl annotate ingress medical-ocr-canary-ingress \
  nginx.ingress.kubernetes.io/canary-weight-"50" --overwrite

# Full promotion — delete canary, update stable tag
kubectl delete -k k8s/canary/
# Update image tag in base/kustomization.yaml to the canary version
kubectl apply -k k8s/base/
```

## Health Checks

All pods implement the three-probe pattern:

| Probe      | Purpose                          | Failure Threshold |
|------------|----------------------------------|-------------------|
| `liveness`  | Restarts unresponsive containers | 3                 |
| `readiness` | Removes pod from service LB      | 3                 |
| `startup`   | Grace period for slow starts     | 20–30             |

### Backend Endpoints

- **Liveness**: `GET /health/live` — process is running
- **Readiness**: `GET /health/ready` — can accept traffic (DB, Redis, MinIO connected)

## Security Features

- **Non-root containers**: All containers run as non-root (`runAsNonRoot: true`)
- **Capability dropping**: `ALL` capabilities dropped
- **Network policies**: Add Calico/Cilium NetworkPolicies for zero-trust (not included)
- **RBAC**: Add restrictive RBAC per service account (not included)
- **TLS everywhere**: Ingress TLS + cert-manager integration
- **Rate limiting**: Nginx rate limits on API and upload endpoints
- **PDB**: PodDisruptionBudget ensures minimum availability during node drains

## Monitoring

All deployments expose Prometheus metrics:

| Component    | Endpoint                          | Port  |
|-------------|-----------------------------------|-------|
| Backend     | `/metrics`                        | 8000  |
| PostgreSQL  | `/metrics` (via exporter)         | 9187  |
| Nginx       | `/metrics` (stub_status module)   | 80    |
| Training    | `/metrics`                        | 8080  |

## Training Job

The training job:

1. Waits for MinIO to be available
2. Downloads the training dataset from MinIO
3. Trains the handwriting OCR model on a single NVIDIA GPU
4. Uploads model artifacts to the output PVC
5. Automatically cleans up after 24 hours (`ttlSecondsAfterFinished`)

```bash
# Trigger a training run
kubectl create -k k8s/base/ --selector app.kubernetes.io/component=training

# Monitor training logs
kubectl logs -n medical-ocr job/medical-ocr-training -f

# Check GPU allocation
kubectl describe job medical-ocr-training -n medical-ocr | rg -i "nvidia.com/gpu"
```

## Pre-commit Validation

```bash
# Validate manifests
kubectl apply -k k8s/base/ --dry-run=client

# Lint with kubeval
kubeval k8s/base/*.yaml

# Check with kube-score
kube-score score k8s/base/*.yaml
```
