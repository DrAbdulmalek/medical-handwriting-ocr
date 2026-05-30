#!/usr/bin/env bash
# ==============================================================================
# Medical Handwriting OCR - Backup Script
# ==============================================================================
# Creates timestamped backups of:
#   1. PostgreSQL database (pg_dump, custom format, compressed)
#   2. MinIO bucket data (mc mirror)
#   3. Model files (rsync)
#   4. Config / env files (GPG-encrypted if available, otherwise copied)
#
# Usage:
#   ./backup.sh                    # Full backup with defaults
#   ./backup.sh --dry-run          # Show what would be done
#   BACKUP_DIR=/mnt/backups ./backup.sh
#
# Exit codes:
#   0 = success (all components backed up)
#   1 = partial (some components failed)
#   2 = total failure (no usable backup created)
#
# See docs/DISASTER_RECOVERY.md for full documentation.
# ==============================================================================

set -euo pipefail

# ── Globals & Defaults ───────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Backup destination (overridable via env / CLI)
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"

# Retention policies
BACKUP_RETENTION_DAILY="${BACKUP_RETENTION_DAILY:-7}"
BACKUP_RETENTION_WEEKLY="${BACKUP_RETENTION_WEEKLY:-4}"
BACKUP_RETENTION_MONTHLY="${BACKUP_RETENTION_MONTHLY:-6}"

# Database connection (all overridable)
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-medical_ocr}"
DB_USER="${DB_USER:-ocr_user}"
DB_PASSWORD="${DB_PASSWORD:-ocr_password_123}"

# MinIO configuration
MINIO_ALIAS="${MINIO_ALIAS:-minio-local}"
MINIO_BUCKET="${MINIO_BUCKET:-ocr-crops}"

# Source directories relative to PROJECT_ROOT
MODELS_DIR="${MODELS_DIR:-${PROJECT_ROOT}/models}"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"
CONFIG_FILE="${CONFIG_FILE:-${PROJECT_ROOT}/backend/app/config.py}"

# Runtime flags
DRY_RUN=false
LOG_FILE="${BACKUP_DIR}/backup.log"

# Track success/failure per component
declare -A COMPONENT_STATUS=()
EXIT_CODE=0

# ── Helper Functions ──────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --dry-run              Print actions without executing them
  --backup-dir DIR       Override backup directory (default: ${BACKUP_DIR})
  --db-host HOST          Database host (default: ${DB_HOST})
  --db-port PORT          Database port (default: ${DB_PORT})
  --db-name NAME          Database name (default: ${DB_NAME})
  --db-user USER          Database user (default: ${DB_USER})
  --db-pass PASSWORD      Database password
  --minio-alias ALIAS     MinIO mc alias (default: ${MINIO_ALIAS})
  --minio-bucket BUCKET   MinIO bucket name (default: ${MINIO_BUCKET})
  -h, --help              Show this help message

Environment variables (same names as long options) can also be used.

Exit codes:
  0 = all components backed up successfully
  1 = partial success (some components failed)
  2 = total failure
EOF
}

# Tee output to both stdout and the log file
log() {
    local level="$1"; shift
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
    local msg="[${ts}] [${level}] $*"
    echo "$msg"
    # Ensure log directory exists before writing
    mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
    echo "$msg" >> "${LOG_FILE}"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

# Mark a component as succeeded or failed
mark_success() { COMPONENT_STATUS["$1"]="ok"; }
mark_failure() { COMPONENT_STATUS["$1"]="failed"; EXIT_CODE=1; }

# ── Pre-flight Checks ────────────────────────────────────────────────────────

preflight() {
    log_info "Running pre-flight checks..."

    # Ensure required tools are available
    local missing=()
    for cmd in date md5sum; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing[*]}"
        exit 2
    fi

    # Warn about optional tools
    if ! command -v pg_dump &>/dev/null; then
        log_warn "pg_dump not found — database backup will be skipped"
    fi
    if ! command -v mc &>/dev/null; then
        log_warn "mc (MinIO Client) not found — MinIO backup will be skipped"
    fi
    if ! command -v gpg &>/dev/null; then
        log_warn "gpg not found — config backup will NOT be encrypted"
    fi

    # Create backup directory structure
    if [[ "${DRY_RUN}" == false ]]; then
        mkdir -p "${BACKUP_DIR}"
    fi

    log_info "Pre-flight checks complete"
}

# ── Timestamped Backup Directory ──────────────────────────────────────────────

create_backup_dir() {
    BACKUP_TIMESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
    BACKUP_PATH="${BACKUP_DIR}/${BACKUP_TIMESTAMP}"

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY-RUN] Would create backup directory: ${BACKUP_PATH}"
        return 0
    fi

    mkdir -p "${BACKUP_PATH}"
    log_info "Backup directory: ${BACKUP_PATH}"
}

# ── Component 1: PostgreSQL Database ─────────────────────────────────────────

backup_database() {
    local dump_file="${BACKUP_PATH}/db_${DB_NAME}.dump"
    local checksum_file="${BACKUP_PATH}/db_${DB_NAME}.dump.md5"

    if ! command -v pg_dump &>/dev/null; then
        log_warn "Skipping database backup (pg_dump not available)"
        return 0
    fi

    log_info "Backing up PostgreSQL database '${DB_NAME}' on ${DB_HOST}:${DB_PORT}..."

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY-RUN] Would run: pg_dump -Fc -Z9 -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -d ${DB_NAME} > ${dump_file}"
        mark_success "database"
        return 0
    fi

    # Run pg_dump with custom (compressed) format
    export PGPASSWORD="${DB_PASSWORD}"
    if pg_dump -Fc -Z9 \
            -h "${DB_HOST}" -p "${DB_PORT}" \
            -U "${DB_USER}" -d "${DB_NAME}" \
            > "${dump_file}" 2>>"${LOG_FILE}"; then
        unset PGPASSWORD
        log_info "Database dump created: ${dump_file} ($(du -h "${dump_file}" | cut -f1))"

        # Generate integrity checksum
        md5sum "${dump_file}" > "${checksum_file}"
        log_info "Database checksum saved to ${checksum_file}"
        mark_success "database"
    else
        local rc=$?
        unset PGPASSWORD
        log_error "Database backup FAILED (pg_dump exit code: ${rc})"
        mark_failure "database"
    fi
}

# ── Component 2: MinIO Bucket ─────────────────────────────────────────────────

backup_minio() {
    local minio_backup_dir="${BACKUP_PATH}/minio"
    local checksum_file="${BACKUP_PATH}/minio_manifest.md5"

    if ! command -v mc &>/dev/null; then
        log_warn "Skipping MinIO backup (mc not available)"
        return 0
    fi

    log_info "Backing up MinIO bucket '${MINIO_ALIAS}/${MINIO_BUCKET}'..."

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY-RUN] Would mirror: mc mirror ${MINIO_ALIAS}/${MINIO_BUCKET} ${minio_backup_dir}"
        mark_success "minio"
        return 0
    fi

    # Check if the alias exists; if not, try a best-effort configure
    if ! mc alias list "${MINIO_ALIAS}" &>/dev/null; then
        log_warn "MinIO alias '${MINIO_ALIAS}' not configured. Attempting auto-configure..."
        # Attempt common local configuration (Docker compose defaults)
        mc alias set "${MINIO_ALIAS}" \
            "http://${MINIO_HOST:-localhost}:${MINIO_API_PORT:-9000}" \
            "${MINIO_ACCESS_KEY:-minioadmin}" \
            "${MINIO_SECRET_KEY:-minioadmin123}" \
            &>>"${LOG_FILE}" || {
            log_error "Could not configure MinIO alias '${MINIO_ALIAS}'. Skipping MinIO backup."
            mark_failure "minio"
            return 0
        }
    fi

    # Check bucket exists
    if ! mc ls "${MINIO_ALIAS}/${MINIO_BUCKET}" &>/dev/null; then
        log_warn "MinIO bucket '${MINIO_ALIAS}/${MINIO_BUCKET}' not found or empty. Skipping."
        mark_success "minio"
        return 0
    fi

    mkdir -p "${minio_backup_dir}"

    # Mirror bucket contents to backup directory
    if mc mirror "${MINIO_ALIAS}/${MINIO_BUCKET}" "${minio_backup_dir}" \
            --overwrite --remove &>>"${LOG_FILE}"; then

        # Count files backed up
        local file_count
        file_count="$(find "${minio_backup_dir}" -type f | wc -l)"
        local total_size
        total_size="$(du -sh "${minio_backup_dir}" 2>/dev/null | cut -f1)"

        log_info "MinIO backup complete: ${file_count} files, ${total_size}"

        # Generate checksum manifest for all files
        find "${minio_backup_dir}" -type f -exec md5sum {} + > "${checksum_file}"
        log_info "MinIO checksum manifest: ${checksum_file} ($file_count entries)"
        mark_success "minio"
    else
        log_error "MinIO backup FAILED"
        mark_failure "minio"
    fi
}

# ── Component 3: Model Files ───────────────────────────────────────────────────

backup_models() {
    local models_backup_dir="${BACKUP_PATH}/models"
    local checksum_file="${BACKUP_PATH}/models_manifest.md5"

    log_info "Backing up model files from ${MODELS_DIR}..."

    if [[ ! -d "${MODELS_DIR}" ]]; then
        log_warn "Models directory '${MODELS_DIR}' not found. Skipping model backup."
        mark_success "models"
        return 0
    fi

    # Check if directory is empty
    if [[ -z "$(find "${MODELS_DIR}" -type f 2>/dev/null)" ]]; then
        log_warn "Models directory is empty. Skipping model backup."
        mark_success "models"
        return 0
    fi

    if [[ "${DRY_RUN}" == true ]]; then
        local file_count
        file_count="$(find "${MODELS_DIR}" -type f | wc -l)"
        log_info "[DRY-RUN] Would copy ${file_count} model files from ${MODELS_DIR} to ${models_backup_dir}"
        mark_success "models"
        return 0
    fi

    if rsync -a --checksum "${MODELS_DIR}/" "${models_backup_dir}/" &>>"${LOG_FILE}"; then
        local file_count
        file_count="$(find "${models_backup_dir}" -type f | wc -l)"
        local total_size
        total_size="$(du -sh "${models_backup_dir}" 2>/dev/null | cut -f1)"
        log_info "Model backup complete: ${file_count} files, ${total_size}"

        # Generate checksum manifest
        find "${models_backup_dir}" -type f -exec md5sum {} + > "${checksum_file}"
        log_info "Models checksum manifest: ${checksum_file}"
        mark_success "models"
    else
        log_error "Model backup FAILED"
        mark_failure "models"
    fi
}

# ── Component 4: Config / Environment ────────────────────────────────────────

backup_config() {
    local config_backup_dir="${BACKUP_PATH}/config"

    log_info "Backing up configuration and environment files..."

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY-RUN] Would copy config files to ${config_backup_dir}"
        if command -v gpg &>/dev/null; then
            log_info "[DRY-RUN] Would encrypt sensitive files with GPG"
        fi
        mark_success "config"
        return 0
    fi

    mkdir -p "${config_backup_dir}"

    local any_copied=false

    # Copy .env file if it exists
    if [[ -f "${ENV_FILE}" ]]; then
        if command -v gpg &>/dev/null; then
            gpg --batch --yes --symmetric \
                --cipher-algo AES256 \
                --output "${config_backup_dir}/.env.gpg" \
                "${ENV_FILE}" 2>>"${LOG_FILE}" && {
                log_info "Encrypted .env -> ${config_backup_dir}/.env.gpg"
            } || {
                log_warn "GPG encryption of .env failed — copying plaintext"
                cp "${ENV_FILE}" "${config_backup_dir}/.env"
            }
        else
            cp "${ENV_FILE}" "${config_backup_dir}/.env"
            log_info "Copied .env (unencrypted — gpg not available)"
        fi
        any_copied=true
    else
        log_warn "No .env file found at ${ENV_FILE}"
    fi

    # Copy config.py
    if [[ -f "${CONFIG_FILE}" ]]; then
        cp "${CONFIG_FILE}" "${config_backup_dir}/config.py"
        log_info "Copied config.py"
        any_copied=true
    fi

    # Copy docker-compose files if present
    for compose_file in \
        "${PROJECT_ROOT}/docker/docker-compose.yml" \
        "${PROJECT_ROOT}/docker/docker-compose.full.yml" \
        "${PROJECT_ROOT}/docker/docker-compose.monitoring.yml"; do
        if [[ -f "${compose_file}" ]]; then
            cp "${compose_file}" "${config_backup_dir}/$(basename "${compose_file}")"
            log_info "Copied $(basename "${compose_file}")"
            any_copied=true
        fi
    done

    # Copy Kubernetes manifests if present
    if [[ -d "${PROJECT_ROOT}/k8s" ]]; then
        cp -r "${PROJECT_ROOT}/k8s" "${config_backup_dir}/k8s"
        log_info "Copied k8s/ manifests"
        any_copied=true
    fi

    # Copy terraform files if present
    if [[ -d "${PROJECT_ROOT}/terraform" ]]; then
        cp -r "${PROJECT_ROOT}/terraform" "${config_backup_dir}/terraform"
        log_info "Copied terraform/ files"
        any_copied=true
    fi

    # Generate checksum for all config files
    find "${config_backup_dir}" -type f -exec md5sum {} + > "${config_backup_dir}/config_manifest.md5"

    if [[ "${any_copied}" == true ]]; then
        mark_success "config"
    else
        log_warn "No configuration files found to back up"
        mark_success "config"
    fi
}

# ── Backup Verification ──────────────────────────────────────────────────────

verify_backup() {
    log_info "Verifying backup integrity..."

    local backup_ok=true

    # Verify database dump
    if [[ -f "${BACKUP_PATH}/db_${DB_NAME}.dump" ]]; then
        if md5sum -c "${BACKUP_PATH}/db_${DB_NAME}.dump.md5" &>/dev/null; then
            log_info "Database dump checksum: OK"
        else
            log_error "Database dump checksum: MISMATCH"
            backup_ok=false
        fi
    fi

    # Verify MinIO manifest
    if [[ -f "${BACKUP_PATH}/minio_manifest.md5" ]]; then
        local minio_errors
        # cd to backup path so relative paths in manifest work
        minio_errors="$(cd "${BACKUP_PATH}" && md5sum -c minio_manifest.md5 2>&1 | grep -c 'FAILED' || true)"
        if [[ "${minio_errors}" -eq 0 ]]; then
            log_info "MinIO backup checksums: OK"
        else
            log_error "MinIO backup checksums: ${minio_errors} FAILED"
            backup_ok=false
        fi
    fi

    # Verify models manifest
    if [[ -f "${BACKUP_PATH}/models_manifest.md5" ]]; then
        local model_errors
        model_errors="$(cd "${BACKUP_PATH}" && md5sum -c models_manifest.md5 2>&1 | grep -c 'FAILED' || true)"
        if [[ "${model_errors}" -eq 0 ]]; then
            log_info "Models backup checksums: OK"
        else
            log_error "Models backup checksums: ${model_errors} FAILED"
            backup_ok=false
        fi
    fi

    if [[ "${backup_ok}" == true ]]; then
        log_info "All backup integrity checks passed"
    else
        log_warn "Some backup integrity checks failed — review logs"
    fi
}

# ── Backup Rotation ───────────────────────────────────────────────────────────

rotate_backups() {
    log_info "Applying retention policy (daily=${BACKUP_RETENTION_DAILY}, weekly=${BACKUP_RETENTION_WEEKLY}, monthly=${BACKUP_RETENTION_MONTHLY})..."

    if [[ "${DRY_RUN}" == true ]]; then
        log_info "[DRY-RUN] Would rotate old backups"
        return 0
    fi

    if [[ ! -d "${BACKUP_DIR}" ]]; then
        return 0
    fi

    local deleted_count=0
    local now
    now="$(date +%s)"

    # Iterate over backup directories (sorted by name = chronological)
    for backup_dir in $(ls -1d "${BACKUP_DIR}"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_* 2>/dev/null | sort -r); do
        local dir_name
        dir_name="$(basename "${backup_dir}")"
        local dir_date
        dir_date="${dir_name%%_*}"  # YYYY-MM-DD

        # Parse date components
        local year month day
        year="${dir_date:0:4}"
        month="${dir_date:5:2}"
        day="${dir_date:8:2}"

        # Calculate age in days
        local dir_epoch
        dir_epoch="$(date -d "${dir_date}" +%s 2>/dev/null || echo "0")"
        local age_days=$(( (now - dir_epoch) / 86400 ))

        # Skip the very latest backup (never delete it)
        if [[ "${backup_dir}" == "$(ls -1d "${BACKUP_DIR}"/[0-9]* 2>/dev/null | sort -r | head -1)" ]]; then
            continue
        fi

        # Retention logic: keep if within daily, weekly, or monthly windows
        local keep=false

        # Within daily retention
        if [[ "${age_days}" -le "${BACKUP_RETENTION_DAILY}" ]]; then
            keep=true
        fi

        # Weekly retention: keep if it's a Sunday (or Monday) and within weekly window
        if [[ "${age_days}" -le $((BACKUP_RETENTION_WEEKLY * 7)) ]]; then
            local day_of_week
            day_of_week="$(date -d "${dir_date}" +%u 2>/dev/null || echo "0")"
            # %u: 1=Monday ... 7=Sunday — keep Sunday backups as weekly markers
            if [[ "${day_of_week}" -eq 7 ]]; then
                keep=true
            fi
        fi

        # Monthly retention: keep first-of-month backups within monthly window
        if [[ "${age_days}" -le $((BACKUP_RETENTION_MONTHLY * 30)) ]]; then
            if [[ "${day}" == "01" || "${day}" == "1" ]]; then
                keep=true
            fi
        fi

        # Delete if not kept
        if [[ "${keep}" == false ]]; then
            log_info "Removing old backup: ${dir_name} (${age_days} days old)"
            rm -rf "${backup_dir}"
            ((deleted_count++)) || true
        fi
    done

    log_info "Rotation complete: removed ${deleted_count} old backup(s)"
}

# ── Generate Summary ──────────────────────────────────────────────────────────

generate_summary() {
    local summary_file="${BACKUP_PATH}/backup_summary.txt"
    local total_size
    total_size="$(du -sh "${BACKUP_PATH}" 2>/dev/null | cut -f1)"

    {
        echo "========================================"
        echo "Backup Summary"
        echo "========================================"
        echo "Timestamp  : ${BACKUP_TIMESTAMP}"
        echo "Directory  : ${BACKUP_PATH}"
        echo "Total Size : ${total_size}"
        echo "Dry Run    : ${DRY_RUN}"
        echo ""
        echo "Component Status:"
        for component in database minio models config; do
            local status="${COMPONENT_STATUS[${component}]:-skipped}"
            printf "  %-10s : %s\n" "${component}" "${status}"
        done
        echo "========================================"
        echo ""
        echo "Files in backup:"
        find "${BACKUP_PATH}" -maxdepth 2 -type f | sort | while read -r f; do
            local fsize
            fsize="$(du -h "${f}" | cut -f1)"
            local relpath="${f#${BACKUP_PATH}/}"
            printf "  %-60s %s\n" "${relpath}" "${fsize}"
        done
    } > "${summary_file}"

    log_info "Summary written to ${summary_file}"
}

# ── Cleanup Handler ───────────────────────────────────────────────────────────

cleanup() {
    # Unset sensitive environment variables
    unset PGPASSWORD 2>/dev/null || true
    log_info "Backup script finished (exit code: ${EXIT_CODE})"
}

# ── Parse CLI Arguments ─────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --backup-dir)
                BACKUP_DIR="$2"; shift 2
                ;;
            --db-host)
                DB_HOST="$2"; shift 2
                ;;
            --db-port)
                DB_PORT="$2"; shift 2
                ;;
            --db-name)
                DB_NAME="$2"; shift 2
                ;;
            --db-user)
                DB_USER="$2"; shift 2
                ;;
            --db-pass)
                DB_PASSWORD="$2"; shift 2
                ;;
            --minio-alias)
                MINIO_ALIAS="$2"; shift 2
                ;;
            --minio-bucket)
                MINIO_BUCKET="$2"; shift 2
                ;;
            -h|--help)
                usage; exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                usage
                exit 2
                ;;
        esac
    done
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"

    # Install cleanup trap
    trap cleanup EXIT

    log_info "=========================================="
    log_info "Medical Handwriting OCR - Backup Started"
    log_info "=========================================="

    # Step 1: Pre-flight
    preflight

    # Step 2: Create backup directory
    create_backup_dir

    # Step 3: Run each backup component
    backup_database
    backup_minio
    backup_models
    backup_config

    # Step 4: Verify backup integrity (only on real runs)
    if [[ "${DRY_RUN}" == false ]]; then
        verify_backup
    fi

    # Step 5: Generate summary
    if [[ "${DRY_RUN}" == false ]]; then
        generate_summary
    fi

    # Step 6: Rotate old backups
    rotate_backups

    # Final report
    log_info "=========================================="
    log_info "Backup complete. Exit code: ${EXIT_CODE}"
    log_info "  0 = success, 1 = partial, 2 = failed"
    log_info "=========================================="

    exit "${EXIT_CODE}"
}

main "$@"
