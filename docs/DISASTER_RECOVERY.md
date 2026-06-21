# Disaster Recovery Plan — Medical Handwriting OCR

> **Version:** 1.0
> **Last Updated:** $(date +%Y-%m-%d)
> **Classification:** Internal — Operations

---

## Table of Contents

1. [Backup Strategy Overview](#1-backup-strategy-overview)
2. [RPO / RTO Targets](#2-rpo--rto-targets)
3. [What Gets Backed Up](#3-what-gets-backed-up)
4. [How to Run Backups](#4-how-to-run-backups)
   - 4.1 [Manual Backup](#41-manual-backup)
   - 4.2 [Automated Backups (Cron)](#42-automated-backups-cron)
   - 4.3 [Dry-Run](#43-dry-run)
5. [Backup Verification](#5-backup-verification)
6. [How to Restore from Backup](#6-how-to-restore-from-backup)
   - 6.1 [List Available Backups](#61-list-available-backups)
   - 6.2 [Restore All Components](#62-restore-all-components)
   - 6.3 [Restore a Specific Component](#63-restore-a-specific-component)
   - 6.4 [Post-Restore Checklist](#64-post-restore-checklist)
7. [Failure Scenarios & Recovery Procedures](#7-failure-scenarios--recovery-procedures)
   - 7.1 [Database Corruption](#71-database-corruption)
   - 7.2 [MinIO Data Loss](#72-minio-data-loss)
   - 7.3 [Model File Corruption](#73-model-file-corruption)
   - 7.4 [Full System Failure](#74-full-system-failure)
   - 7.5 [Kubernetes Cluster Recovery](#75-kubernetes-cluster-recovery)
8. [Testing the Backup / Restore Process](#8-testing-the-backup--restore-process)
9. [Cron Schedule Examples](#9-cron-schedule-examples)
10. [Escalation & Contact Information](#10-escalation--contact-information)
11. [Script Reference](#11-script-reference)

---

## 1. Backup Strategy Overview

The Medical Handwriting OCR system stores critical data in four locations:

| Component       | Storage Technology | Content                                      |
|-----------------|--------------------|----------------------------------------------|
| **Database**    | PostgreSQL 15      | OCR results, correction data, user sessions, API keys, audit logs |
| **Object Store** | MinIO             | Cropped prescription images, uploaded documents |
| **Model Files** | Filesystem        | Fine-tuned TrOCR / PaddleOCR model weights    |
| **Configuration** | Files            | `.env`, `config.py`, Docker Compose, K8s manifests, Terraform |

The backup script (`scripts/backup.sh`) captures all four components into a
timestamped directory under `backups/YYYY-MM-DD_HH-MM-SS/` with per-file
MD5 checksums for integrity verification.

---

## 2. RPO / RTO Targets

| Metric                         | Target        | Notes                                      |
|--------------------------------|---------------|--------------------------------------------|
| **Recovery Point Objective**   | 24 hours      | Maximum acceptable data loss               |
| **Recovery Time Objective**   | 4 hours       | Time to fully restore all components      |
| **Backup Frequency**          | Daily         | Automated via cron; can be run manually    |
| **Backup Retention**           | 7 daily, 4 weekly, 6 monthly | Automatic rotation built into `backup.sh` |

> **Note:** RPO assumes daily automated backups. If your deployment has higher
> data change rates, increase backup frequency accordingly.

---

## 3. What Gets Backed Up

### 3.1 PostgreSQL Database

- Tool: `pg_dump` (custom format, `-Fc`, compressed with `-Z9`)
- Contents: All tables, indexes, sequences, ownership, and comments
- Output: `db_medical_ocr.dump` (~size depends on data volume)
- Checksum: `db_medical_ocr.dump.md5`

### 3.2 MinIO Bucket (`ocr-crops`)

- Tool: `mc mirror` (MinIO Client)
- Contents: All objects in the configured bucket
- Output: `minio/` directory preserving object hierarchy
- Checksum: `minio_manifest.md5` (per-file MD5)

### 3.3 Model Files

- Tool: `rsync --checksum`
- Source: `models/` directory (fine-tuned model weights)
- Output: `models/` directory
- Checksum: `models_manifest.md5` (per-file MD5)

### 3.4 Configuration & Environment

- `.env` → GPG-encrypted (`AES256` symmetric) if GPG is available, otherwise plaintext copy
- `backend/app/config.py`
- `docker/docker-compose*.yml` files
- `k8s/` manifests
- `terraform/` files

---

## 4. How to Run Backups

### 4.1 Manual Backup

```bash
# Full backup with default settings
./scripts/backup.sh

# Override backup destination
BACKUP_DIR=/mnt/nfs/backups ./scripts/backup.sh

# Specify database credentials explicitly
./scripts/backup.sh \
    --db-host db.internal \
    --db-port 5432 \
    --db-name medical_ocr \
    --db-user ocr_user \
    --db-pass 's3cure_p@ss'
```

### 4.2 Automated Backups (Cron)

Add a cron job to the application server (or a dedicated backup runner):

```bash
# Edit crontab
crontab -e

# Daily backup at 03:00 AM
0 3 * * * /opt/mho-repo/scripts/backup.sh >> /opt/mho-repo/backups/cron_backup.log 2>&1
```

See [Section 9](#9-cron-schedule-examples) for more scheduling examples.

### 4.3 Dry-Run

Preview what the backup script would do without writing any files:

```bash
./scripts/backup.sh --dry-run
```

---

## 5. Backup Verification

Run the verification script to validate backup integrity:

```bash
# Verify the latest backup
./scripts/backup_verify.sh

# Verify a specific backup
./scripts/backup_verify.sh --backup-id 2024-06-15_03-00-00

# Skip the database restore test (faster, no temp DB created)
./scripts/backup_verify.sh --backup-id 2024-06-15_03-00-00 --no-db-restore-test
```

The verification performs:

| Check                    | Description                                                |
|--------------------------|------------------------------------------------------------|
| **Backup structure**     | All expected directories present                           |
| **DB dump checksum**     | MD5 of the `pg_dump` file                                  |
| **DB restore test**      | Restores to a temporary DB, counts tables and rows        |
| **MinIO integrity**      | MD5 verification of all mirrored objects                  |
| **Model files integrity** | MD5 verification of all model files                        |
| **Config backup**        | Presence of `.env`, `config.py`, and IaC manifests        |

The script generates a `verification_report.txt` inside the backup directory
and exits with:
- `0` = all checks passed
- `1` = warnings (non-critical)
- `2` = failures (critical)

---

## 6. How to Restore from Backup

### 6.1 List Available Backups

```bash
./scripts/restore.sh
```

Output:

```
Available backups in ./backups:
───────────────────────────────────────────────────────────
BACKUP ID                 SIZE        STATUS
───────────────────────────────────────────────────────────
2024-06-15_03-00-00      2.1G        complete
2024-06-14_03-00-00      2.0G        complete
2024-06-13_03-00-00       45M        partial
───────────────────────────────────────────────────────────
```

### 6.2 Restore All Components

```bash
# Interactive mode (prompts before destructive operations)
./scripts/restore.sh --backup-id 2024-06-15_03-00-00

# Non-interactive / CI mode (no prompts — use with caution)
./scripts/restore.sh --backup-id 2024-06-15_03-00-00 --force
```

### 6.3 Restore a Specific Component

```bash
# Database only
./scripts/restore.sh --backup-id 2024-06-15_03-00-00 --component db

# MinIO only
./scripts/restore.sh --backup-id 2024-06-15_03-00-00 --component minio

# Model files only
./scripts/restore.sh --backup-id 2024-06-15_03-00-00 --component models

# Configuration only
./scripts/restore.sh --backup-id 2024-06-15_03-00-00 --component config
```

### 6.4 Post-Restore Checklist

After any restore operation, verify the system:

1. **Database:** `psql -h localhost -U ocr_user -d medical_ocr -c "SELECT count(*) FROM ocr_results;"`
2. **MinIO:** `mc ls minio-local/ocr-crops/ --summarize`
3. **Backend:** `curl -s http://localhost:8000/health` (or your health endpoint)
4. **OCR Test:** Upload a test image and verify end-to-end OCR processing works
5. **Review logs:** Check `backend/logs/` and container logs for any errors

---

## 7. Failure Scenarios & Recovery Procedures

### 7.1 Database Corruption

**Symptoms:**
- Application errors: `relation does not exist`, `could not open file`, corruption warnings
- `pg_dump` fails or produces errors
- Inconsistent query results

**Recovery Steps:**

```bash
# 1. Stop the application to prevent further corruption
docker compose -f docker/docker-compose.yml stop backend celery-worker celery-beat

# 2. Identify a known-good backup
./scripts/restore.sh          # lists available backups

# 3. Verify the backup before restoring
./scripts/backup_verify.sh --backup-id YYYY-MM-DD_HH-MM-SS

# 4. Restore the database
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component db

# 5. Run pending migrations (if backup is older than current schema)
cd backend && alembic upgrade head

# 6. Restart the application
docker compose -f docker/docker-compose.yml up -d backend celery-worker celery-beat

# 7. Verify
curl -s http://localhost:8000/health
```

### 7.2 MinIO Data Loss

**Symptoms:**
- `mc ls` shows empty bucket or missing objects
- Application errors when trying to retrieve cropped images
- 404 errors from object storage

**Recovery Steps:**

```bash
# 1. Check MinIO health
docker compose -f docker/docker-compose.yml ps minio
mc admin info minio-local

# 2. If data is lost, restore from backup
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component minio

# 3. Verify restored objects
mc ls minio-local/ocr-crops/ --summarize

# 4. Check application can access objects
curl -s http://localhost:8000/api/uploads/ | python3 -m json.tool
```

### 7.3 Model File Corruption

**Symptoms:**
- OCR engine fails to load models
- Errors like `FileNotFoundError`, `corrupted file`, or `unexpected EOF`
- Degrading OCR accuracy or complete processing failure

**Recovery Steps:**

```bash
# 1. Identify the corrupted files
ls -la models/
md5sum models/*  # compare against backup manifest

# 2. Restore model files from backup
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component models

# 3. Verify model loads correctly
cd backend && python3 -c "
from app.ocr_engine import OCREngine
engine = OCREngine()
print('OCR engine loaded successfully')
"

# 4. If models came from training, you may need to re-export
#    See training/export_dataset.py and deployment_manager.py
```

### 7.4 Full System Failure

**Symptoms:**
- Server unreachable
- All services down
- Data directory destroyed or storage failure

**Recovery Steps:**

```bash
# 1. Provision a new server (or VM) with the same infrastructure
#    - Docker & Docker Compose installed
#    - Sufficient disk space
#    - Network access restored

# 2. Clone the repository (or restore from version control)
git clone <repo-url> /opt/mho-repo
cd /opt/mho-repo

# 3. Restore configuration first (needed by Docker Compose)
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component config --force

# 4. Start infrastructure services (PostgreSQL, MinIO, Redis)
docker compose -f docker/docker-compose.yml up -d postgres minio

# Wait for services to be healthy
docker compose -f docker/docker-compose.yml ps

# 5. Restore database
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component db --force

# 6. Restore MinIO data
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component minio --force

# 7. Restore model files
./scripts/restore.sh \
    --backup-id YYYY-MM-DD_HH-MM-SS \
    --component models --force

# 8. Start all services
docker compose -f docker/docker-compose.yml up -d

# 9. Run post-restore checklist (see Section 6.4)
```

### 7.5 Kubernetes Cluster Recovery

If the entire K8s cluster must be rebuilt:

```bash
# 1. Set up kubectl context for the new cluster
kubectl config use-context <new-cluster>

# 2. Apply namespace and base manifests
kubectl apply -k k8s/base

# 3. Wait for PostgreSQL to be ready
kubectl rollout status deployment/postgres -n medical-ocr

# 4. Restore database (exec into a temporary pod with pg_restore)
#    Option A: Copy the backup dump into the cluster
kubectl cp backups/YYYY-MM-DD_HH-MM-SS/db_medical_ocr.dump \
    medical-ocr/postgres-0:/tmp/db_medical_ocr.dump

kubectl exec -it postgres-0 -n medical-ocr -- bash -c "
    pg_restore -Fc -U ocr_user -d medical_ocr --no-owner --no-privileges \
        --clean --if-exists /tmp/db_medical_ocr.dump
"

# 5. Wait for MinIO to be ready
kubectl rollout status deployment/minio -n medical-ocr

# 6. Restore MinIO data (port-forward + mc)
kubectl port-forward -n medical-ocr svc/minio 9000:9000 &
MC_PID=$!

mc alias set k8s-minio http://localhost:9000 minioadmin ${MINIO_SECRET_KEY}
mc mb k8s-minio/ocr-crops --ignore-existing
mc mirror backups/YYYY-MM-DD_HH-MM-SS/minio/ k8s-minio/ocr-crops/

kill $MC_PID 2>/dev/null

# 7. Restart remaining deployments to pick up restored data
kubectl rollout restart deployment/backend deployment/celery-worker -n medical-ocr
kubectl rollout status deployment/backend -n medical-ocr

# 8. Verify
kubectl get pods -n medical-ocr
```

---

## 8. Testing the Backup / Restore Process

Regular testing of the backup and restore pipeline is essential. Perform a
full DR drill at least **quarterly**:

### Test Procedure

```bash
# 1. Run a fresh backup
./scripts/backup.sh

# Get the backup ID
BACKUP=$(ls -1d backups/[0-9]* | sort -r | head -1 | xargs basename)
echo "Testing backup: ${BACKUP}"

# 2. Verify the backup
./scripts/backup_verify.sh --backup-id "${BACKUP}"
echo "Exit code: $?"  # Should be 0

# 3. Test database restore (non-destructive: uses temp DB)
#    The verify script already does this. To test manually:
pg_restore -Fc -U ocr_user -h localhost -d _dr_test \
    "backups/${BACKUP}/db_medical_ocr.dump"

# 4. Document the results
echo "$(date): DR test passed for backup ${BACKUP}" >> backups/dr_test_log.txt
```

### Quarterly DR Drill Checklist

- [ ] Backup completes successfully (exit code 0)
- [ ] Verification passes all checks
- [ ] Database restore test succeeds (tables and rows present)
- [ ] MinIO backup contains expected objects
- [ ] Model files restore to working state
- [ ] Full end-to-end restore on a staging environment
- [ ] Application functions correctly after restore
- [ ] Document any issues found and remediation steps
- [ ] Update this document if procedures changed

---

## 9. Cron Schedule Examples

### Production (Recommended)

```cron
# m h  dom mon dow   command

# Daily full backup at 03:00 AM
0 3 * * * cd /opt/mho-repo && ./scripts/backup.sh >> backups/cron_backup.log 2>&1

# Weekly backup verification (Sunday 05:00 AM)
0 5 * * 0 cd /opt/mho-repo && ./scripts/backup_verify.sh >> backups/cron_verify.log 2>&1
```

### High-Frequency (Critical Deployments)

```cron
# Every 6 hours
0 */6 * * * cd /opt/mho-repo && ./scripts/backup.sh >> backups/cron_backup.log 2>&1

# Daily verification
0 4 * * * cd /opt/mho-repo && ./scripts/backup_verify.sh >> backups/cron_verify.log 2>&1
```

### Staging / Development

```cron
# Daily backup at midnight
0 0 * * * cd /opt/mho-repo && ./scripts/backup.sh >> backups/cron_backup.log 2>&1
```

### Offsite / Remote Backup (rsync to external storage)

```cron
# Daily: sync backup directory to remote NAS after backup completes
30 3 * * * rsync -a --delete /opt/mho-repo/backups/ backup-server:/mnt/backups/mho-repo/ >> /opt/mho-repo/backups/cron_rsync.log 2>&1
```

---

## 10. Escalation & Contact Information

| Role              | Name (Placeholder) | Contact (Placeholder)      | Availability   |
|-------------------|--------------------|-----------------------------|----------------|
| **Primary DBA**   | Jane Doe           | jane.doe@hospital.org       | 24/7 on-call   |
| **DevOps Lead**   | John Smith         | john.smith@hospital.org     | Business hours |
| **System Admin**  | Bob Lee             | bob.lee@hospital.org        | Business hours |
| **Vendor Support**| MinIO / PostgreSQL | vendor-support@example.com  | Per contract   |

### Escalation Matrix

| Severity | Description                              | Response Time | Action                        |
|----------|------------------------------------------|---------------|-------------------------------|
| **P1**   | Full data loss, system completely down   | 15 minutes    | Page on-call DBA + DevOps     |
| **P2**   | Partial data loss, single component down | 1 hour        | Page on-call DBA              |
| **P3**   | Backup failure, degradation              | 4 hours       | Create ticket, DevOps team    |
| **P4**   | Verification warning, non-critical       | 24 hours      | Next business day review      |

---

## 11. Script Reference

### `scripts/backup.sh`

Creates a timestamped backup of all system components.

| Parameter                 | Default               | Description                    |
|---------------------------|-----------------------|--------------------------------|
| `--dry-run`               | false                 | Preview actions without executing |
| `--backup-dir`            | `./backups`           | Backup destination directory    |
| `--db-host`               | `localhost`           | PostgreSQL host                 |
| `--db-port`               | `5432`                | PostgreSQL port                 |
| `--db-name`               | `medical_ocr`         | Database name                   |
| `--db-user`               | `ocr_user`            | Database user                   |
| `--db-pass`               | (from env)            | Database password               |
| `--minio-alias`           | `minio-local`         | MinIO Client alias              |
| `--minio-bucket`          | `ocr-crops`           | MinIO bucket name               |
| `BACKUP_RETENTION_DAILY`  | `7`                   | Daily backups to keep           |
| `BACKUP_RETENTION_WEEKLY` | `4`                   | Weekly backups to keep          |
| `BACKUP_RETENTION_MONTHLY`| `6`                   | Monthly backups to keep         |

**Exit codes:** `0` = success, `1` = partial, `2` = failed

### `scripts/restore.sh`

Restores data from a specified backup.

| Parameter        | Default   | Description                            |
|------------------|-----------|----------------------------------------|
| `--backup-id`    | (prompt)  | Backup timestamp to restore            |
| `--component`    | `all`     | Component: `db`, `minio`, `models`, `config`, `all` |
| `--force`        | false     | Skip confirmation prompts              |
| `--backup-dir`   | `./backups` | Backup source directory              |
| `--db-host`      | `localhost` | PostgreSQL host                      |
| `--db-port`      | `5432`    | PostgreSQL port                        |
| `--db-name`      | `medical_ocr` | Database name                      |
| `--db-user`      | `ocr_user` | Database user                         |
| `--db-pass`      | (from env) | Database password                     |
| `--minio-alias`  | `minio-local` | MinIO Client alias                  |
| `--minio-bucket` | `ocr-crops` | MinIO bucket name                    |

**Exit codes:** `0` = success, `1` = partial, `2` = failed

### `scripts/backup_verify.sh`

Verifies the integrity of an existing backup.

| Parameter             | Default       | Description                             |
|-----------------------|---------------|-----------------------------------------|
| `--backup-id`         | (latest)      | Backup to verify                        |
| `--no-db-restore-test`| false         | Skip the temporary DB restore test       |
| `--backup-dir`        | `./backups`   | Backup directory                        |
| `--db-host`           | `localhost`   | PostgreSQL host                          |
| `--db-port`           | `5432`        | PostgreSQL port                          |
| `--db-name`           | `medical_ocr` | Database name                            |
| `--db-user`           | `ocr_user`    | Database user                            |
| `--db-pass`           | (from env)    | Database password                        |
| `--verify-db-name`    | (auto)        | Temporary database name for restore test |

**Exit codes:** `0` = passed, `1` = warnings, `2` = failures

---

## Appendix: Environment Variables Quick Reference

All three scripts share these common environment variables:

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=medical_ocr
DB_USER=ocr_user
DB_PASSWORD=ocr_password_123

# MinIO
MINIO_ALIAS=minio-local
MINIO_BUCKET=ocr-crops
MINIO_HOST=localhost
MINIO_API_PORT=9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}

# Paths
BACKUP_DIR=./backups
MODELS_DIR=./models
ENV_FILE=./.env
CONFIG_FILE=./backend/app/config.py

# Retention
BACKUP_RETENTION_DAILY=7
BACKUP_RETENTION_WEEKLY=4
BACKUP_RETENTION_MONTHLY=6
```

These can be set in a `.env` file, exported in the shell, or passed via CLI flags.
