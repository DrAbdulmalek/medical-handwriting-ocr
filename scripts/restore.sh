#!/usr/bin/env bash
# ==============================================================================
# Medical Handwriting OCR - Restore Script
# ==============================================================================
# Restores data from a previously created backup.
#
# Usage:
#   ./restore.sh                                    # Interactive: lists backups
#   ./restore.sh --backup-id 2024-01-15_03-00-00    # Restore specific backup
#   ./restore.sh --component db                      # Restore only database
#   ./restore.sh --backup-id 2024-01-15_03-00-00 --component models
#
# Components: db, minio, models, config, all (default: all)
#
# Exit codes:
#   0 = success
#   1 = partial restore
#   2 = total failure
#
# IMPORTANT: This script performs DESTRUCTIVE operations. It will prompt
# before overwriting any data unless running in a CI/non-interactive mode.
# ==============================================================================

set -euo pipefail

# ── Globals & Defaults ───────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Backup directory
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"

# Database connection (all overridable)
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-medical_ocr}"
DB_USER="${DB_USER:-ocr_user}"
DB_PASSWORD="${DB_PASSWORD:-ocr_password_123}"

# MinIO configuration
MINIO_ALIAS="${MINIO_ALIAS:-minio-local}"
MINIO_BUCKET="${MINIO_BUCKET:-ocr-crops}"

# Target directories
MODELS_DIR="${MODELS_DIR:-${PROJECT_ROOT}/models}"
ENV_FILE="${ENV_FILE:-${PROJECT_ROOT}/.env}"
CONFIG_FILE="${CONFIG_FILE:-${PROJECT_ROOT}/backend/app/config.py}"

# Runtime options
BACKUP_ID=""
COMPONENT="all"
FORCE=false
LOG_FILE="${BACKUP_DIR}/restore.log"
BACKUP_PATH=""

# Track success/failure per component
declare -A COMPONENT_STATUS=()
EXIT_CODE=0

# ── Helper Functions ──────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --backup-id ID         Backup timestamp to restore (e.g. 2024-01-15_03-00-00)
  --component COMP       Component to restore: db|minio|models|config|all (default: all)
  --force                Skip confirmation prompts (use in CI/automation)
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
  0 = all components restored successfully
  1 = partial restore (some components failed)
  2 = total failure
EOF
}

log() {
    local level="$1"; shift
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S %Z')"
    local msg="[${ts}] [${level}] $*"
    echo "$msg"
    mkdir -p "$(dirname "${LOG_FILE}")" 2>/dev/null || true
    echo "$msg" >> "${LOG_FILE}"
}

log_info()  { log "INFO"  "$@"; }
log_warn()  { log "WARN"  "$@"; }
log_error() { log "ERROR" "$@"; }

mark_success() { COMPONENT_STATUS["$1"]="ok"; }
mark_failure() { COMPONENT_STATUS["$1"]="failed"; EXIT_CODE=1; }

# Interactive confirmation before destructive operation
confirm() {
    local message="$1"
    if [[ "${FORCE}" == true || ! -t 0 ]]; then
        # Non-interactive or --force: proceed
        return 0
    fi
    echo -n "${message} [y/N] " >&2
    local answer
    read -r answer
    [[ "${answer}" =~ ^[Yy]$ ]]
}

# ── List Available Backups ───────────────────────────────────────────────────

list_backups() {
    echo "Available backups in ${BACKUP_DIR}:"
    echo "─────────────────────────────────────────────────────────────────────────────"
    printf "%-25s %-12s %s\n" "BACKUP ID" "SIZE" "STATUS"
    echo "─────────────────────────────────────────────────────────────────────────────"

    local found=0
    for backup_dir in $(ls -1d "${BACKUP_DIR}"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_* 2>/dev/null | sort -r); do
        local dir_name
        dir_name="$(basename "${backup_dir}")"
        local size
        size="$(du -sh "${backup_dir}" 2>/dev/null | cut -f1)"

        # Check if backup has a summary
        local status="partial"
        if [[ -f "${backup_dir}/backup_summary.txt" ]]; then
            # Check all components succeeded
            if grep -q "failed" "${backup_dir}/backup_summary.txt" 2>/dev/null; then
                status="partial"
            else
                status="complete"
            fi
        elif [[ -f "${backup_dir}/db_${DB_NAME}.dump" ]]; then
            status="complete"
        fi

        printf "%-25s %-12s %s\n" "${dir_name}" "${size}" "${status}"
        found=1
    done

    echo "─────────────────────────────────────────────────────────────────────────────"
    if [[ "${found}" -eq 0 ]]; then
        echo "No backups found."
        exit 2
    fi
}

# ── Select Backup ─────────────────────────────────────────────────────────────

select_backup() {
    if [[ -z "${BACKUP_ID}" ]]; then
        # Interactive mode: show list and prompt
        list_backups
        echo ""
        echo -n "Enter backup ID to restore (or Ctrl+C to cancel): "
        read -r BACKUP_ID
    fi

    BACKUP_PATH="${BACKUP_DIR}/${BACKUP_ID}"

    if [[ ! -d "${BACKUP_PATH}" ]]; then
        log_error "Backup directory not found: ${BACKUP_PATH}"
        exit 2
    fi

    log_info "Selected backup: ${BACKUP_PATH}"
}

# ── Restore Database ─────────────────────────────────────────────────────────

restore_database() {
    local dump_file="${BACKUP_PATH}/db_${DB_NAME}.dump"
    local checksum_file="${BACKUP_PATH}/db_${DB_NAME}.dump.md5"

    log_info "Restoring PostgreSQL database '${DB_NAME}'..."

    if [[ ! -f "${dump_file}" ]]; then
        log_warn "Database dump not found in backup: ${dump_file}"
        log_warn "Skipping database restore"
        return 0
    fi

    # Verify checksum first
    if [[ -f "${checksum_file}" ]]; then
        if ! md5sum -c "${checksum_file}" &>/dev/null; then
            log_error "Database dump checksum FAILED — file may be corrupted"
            if ! confirm "Database dump integrity check failed. Proceed anyway?"; then
                mark_failure "database"
                return 1
            fi
        else
            log_info "Database dump checksum: OK"
        fi
    else
        log_warn "No checksum file found — skipping integrity verification"
    fi

    # Warn about destructive operation
    if ! confirm "WARNING: This will DROP and RECREATE the database '${DB_NAME}'. Continue?"; then
        log_info "Database restore cancelled by user"
        mark_failure "database"
        return 1
    fi

    # Ensure pg_restore is available
    if ! command -v pg_restore &>/dev/null; then
        log_error "pg_restore not found — cannot restore database"
        mark_failure "database"
        return 1
    fi

    export PGPASSWORD="${DB_PASSWORD}"

    # Drop existing connections and database, then recreate
    log_info "Dropping existing database '${DB_NAME}'..."
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${DB_NAME}' AND pid <> pg_backend_pid();" \
        &>>"${LOG_FILE}" 2>/dev/null || true

    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "DROP DATABASE IF EXISTS ${DB_NAME};" \
        &>>"${LOG_FILE}" 2>/dev/null || {
        log_error "Failed to drop database '${DB_NAME}'"
        unset PGPASSWORD
        mark_failure "database"
        return 1
    }

    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" \
        &>>"${LOG_FILE}" || {
        log_error "Failed to create database '${DB_NAME}'"
        unset PGPASSWORD
        mark_failure "database"
        return 1
    }

    # Restore from dump
    log_info "Restoring database from ${dump_file}..."
    if pg_restore -Fc -j "$(nproc 2>/dev/null || echo 2)" \
            -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" \
            -d "${DB_NAME}" \
            --no-owner --no-privileges \
            --verbose \
            "${dump_file}" &>>"${LOG_FILE}"; then
        log_info "Database restore complete"
        unset PGPASSWORD
        mark_success "database"
    else
        local rc=$?
        log_error "Database restore FAILED (exit code: ${rc})"
        unset PGPASSWORD
        mark_failure "database"
    fi
}

# ── Restore MinIO ──────────────────────────────────────────────────────────────

restore_minio() {
    local minio_backup_dir="${BACKUP_PATH}/minio"
    local checksum_file="${BACKUP_PATH}/minio_manifest.md5"

    log_info "Restoring MinIO bucket '${MINIO_ALIAS}/${MINIO_BUCKET}'..."

    if [[ ! -d "${minio_backup_dir}" ]]; then
        log_warn "MinIO backup not found in backup: ${minio_backup_dir}"
        log_warn "Skipping MinIO restore"
        return 0
    fi

    # Verify checksums if available
    if [[ -f "${checksum_file}" ]]; then
        local errors
        errors="$(cd "${BACKUP_PATH}" && md5sum -c minio_manifest.md5 2>&1 | grep -c 'FAILED' || true)"
        if [[ "${errors}" -gt 0 ]]; then
            log_warn "MinIO backup checksums: ${errors} files with FAILED checksums"
            if ! confirm "Some MinIO backup files have integrity issues. Proceed?"; then
                mark_failure "minio"
                return 1
            fi
        else
            log_info "MinIO backup checksums: OK"
        fi
    fi

    # Check for files to restore
    local file_count
    file_count="$(find "${minio_backup_dir}" -type f | wc -l)"
    if [[ "${file_count}" -eq 0 ]]; then
        log_warn "MinIO backup directory is empty — nothing to restore"
        mark_success "minio"
        return 0
    fi

    if ! command -v mc &>/dev/null; then
        log_error "mc (MinIO Client) not found — cannot restore MinIO data"
        mark_failure "minio"
        return 1
    fi

    if ! confirm "WARNING: This will OVERWRITE files in bucket '${MINIO_BUCKET}'. Continue?"; then
        log_info "MinIO restore cancelled by user"
        mark_failure "minio"
        return 1
    fi

    # Ensure bucket exists
    mc mb "${MINIO_ALIAS}/${MINIO_BUCKET}" --ignore-existing &>>"${LOG_FILE}" || {
        log_error "Failed to ensure bucket '${MINIO_BUCKET}' exists"
        mark_failure "minio"
        return 1
    }

    # Mirror backup files back to MinIO
    if mc mirror "${minio_backup_dir}/" "${MINIO_ALIAS}/${MINIO_BUCKET}" \
            --overwrite &>>"${LOG_FILE}"; then
        log_info "MinIO restore complete: ${file_count} files restored"
        mark_success "minio"
    else
        log_error "MinIO restore FAILED"
        mark_failure "minio"
    fi
}

# ── Restore Models ───────────────────────────────────────────────────────────

restore_models() {
    local models_backup_dir="${BACKUP_PATH}/models"
    local checksum_file="${BACKUP_PATH}/models_manifest.md5"

    log_info "Restoring model files to ${MODELS_DIR}..."

    if [[ ! -d "${models_backup_dir}" ]]; then
        log_warn "Models backup not found in backup: ${models_backup_dir}"
        log_warn "Skipping model restore"
        return 0
    fi

    # Verify checksums if available
    if [[ -f "${checksum_file}" ]]; then
        local errors
        errors="$(cd "${BACKUP_PATH}" && md5sum -c models_manifest.md5 2>&1 | grep -c 'FAILED' || true)"
        if [[ "${errors}" -gt 0 ]]; then
            log_error "Models backup checksums: ${errors} files FAILED"
            mark_failure "models"
            return 1
        else
            log_info "Models backup checksums: OK"
        fi
    fi

    local file_count
    file_count="$(find "${models_backup_dir}" -type f | wc -l)"
    if [[ "${file_count}" -eq 0 ]]; then
        log_warn "Models backup directory is empty — nothing to restore"
        mark_success "models"
        return 0
    fi

    if ! confirm "WARNING: This will OVERWRITE files in ${MODELS_DIR}. Continue?"; then
        log_info "Model restore cancelled by user"
        mark_failure "models"
        return 1
    fi

    mkdir -p "${MODELS_DIR}"

    if rsync -a --checksum "${models_backup_dir}/" "${MODELS_DIR}/" &>>"${LOG_FILE}"; then
        log_info "Model restore complete: ${file_count} files restored"
        mark_success "models"
    else
        log_error "Model restore FAILED"
        mark_failure "models"
    fi
}

# ── Restore Config ───────────────────────────────────────────────────────────

restore_config() {
    local config_backup_dir="${BACKUP_PATH}/config"

    log_info "Restoring configuration files..."

    if [[ ! -d "${config_backup_dir}" ]]; then
        log_warn "Config backup not found in backup: ${config_backup_dir}"
        log_warn "Skipping config restore"
        return 0
    fi

    if ! confirm "WARNING: This will OVERWRITE configuration files (.env, config.py, etc.). Continue?"; then
        log_info "Config restore cancelled by user"
        mark_failure "config"
        return 1
    fi

    local restored=0

    # Restore .env (decrypt if encrypted)
    if [[ -f "${config_backup_dir}/.env.gpg" ]]; then
        if command -v gpg &>/dev/null; then
            gpg --batch --yes --decrypt \
                --output "${ENV_FILE}" \
                "${config_backup_dir}/.env.gpg" 2>>"${LOG_FILE}" && {
                log_info "Decrypted and restored .env from .env.gpg"
                ((restored++)) || true
            } || {
                log_warn "Failed to decrypt .env.gpg — skipping"
            }
        else
            log_warn "gpg not available — cannot decrypt .env.gpg"
        fi
    elif [[ -f "${config_backup_dir}/.env" ]]; then
        cp "${config_backup_dir}/.env" "${ENV_FILE}"
        log_info "Restored .env"
        ((restored++)) || true
    fi

    # Restore config.py
    if [[ -f "${config_backup_dir}/config.py" ]]; then
        cp "${config_backup_dir}/config.py" "${CONFIG_FILE}"
        log_info "Restored config.py"
        ((restored++)) || true
    fi

    # Restore docker-compose files
    for compose_file in docker-compose.yml docker-compose.full.yml docker-compose.monitoring.yml; do
        if [[ -f "${config_backup_dir}/${compose_file}" ]]; then
            cp "${config_backup_dir}/${compose_file}" "${PROJECT_ROOT}/docker/${compose_file}"
            log_info "Restored ${compose_file}"
            ((restored++)) || true
        fi
    done

    # Restore k8s manifests
    if [[ -d "${config_backup_dir}/k8s" ]]; then
        cp -r "${config_backup_dir}/k8s/"* "${PROJECT_ROOT}/k8s/" 2>/dev/null || true
        log_info "Restored k8s manifests"
        ((restored++)) || true
    fi

    # Restore terraform files
    if [[ -d "${config_backup_dir}/terraform" ]]; then
        cp -r "${config_backup_dir}/terraform/"* "${PROJECT_ROOT}/terraform/" 2>/dev/null || true
        log_info "Restored terraform files"
        ((restored++)) || true
    fi

    log_info "Config restore complete: ${restored} file(s) restored"
    mark_success "config"
}

# ── Post-Restore Verification ─────────────────────────────────────────────────

verify_restore() {
    log_info "Running post-restore verification..."

    local all_ok=true

    # Verify database connectivity
    if [[ "${COMPONENT_STATUS[database]:-skipped}" == "ok" ]]; then
        if command -v psql &>/dev/null; then
            export PGPASSWORD="${DB_PASSWORD}"
            if psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
                    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" \
                    -t -A &>>"${LOG_FILE}"; then
                local table_count
                table_count="$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
                    -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "0")"
                log_info "Database restored: ${table_count} public tables accessible"
            else
                log_error "Database verification FAILED — cannot connect or query"
                all_ok=false
            fi
            unset PGPASSWORD
        fi
    fi

    # Verify model files exist
    if [[ "${COMPONENT_STATUS[models]:-skipped}" == "ok" ]]; then
        local model_count
        model_count="$(find "${MODELS_DIR}" -type f 2>/dev/null | wc -l)"
        if [[ "${model_count}" -gt 0 ]]; then
            log_info "Models verified: ${model_count} files present"
        else
            log_warn "Models directory is empty after restore"
            all_ok=false
        fi
    fi

    # Verify MinIO bucket
    if [[ "${COMPONENT_STATUS[minio]:-skipped}" == "ok" ]] && command -v mc &>/dev/null; then
        local obj_count
        obj_count="$(mc ls "${MINIO_ALIAS}/${MINIO_BUCKET}" 2>/dev/null | wc -l)" || true
        log_info "MinIO bucket '${MINIO_BUCKET}': ${obj_count} objects"
    fi

    if [[ "${all_ok}" == true ]]; then
        log_info "Post-restore verification passed"
    else
        log_warn "Post-restore verification: some issues detected — review logs"
    fi
}

# ── Parse CLI Arguments ─────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backup-id)
                BACKUP_ID="$2"; shift 2
                ;;
            --component)
                COMPONENT="$2"; shift 2
                ;;
            --force)
                FORCE=true
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

    # Validate component
    case "${COMPONENT}" in
        db|minio|models|config|all) ;;
        *)
            log_error "Invalid component: ${COMPONENT} (must be: db, minio, models, config, all)"
            exit 2
            ;;
    esac
}

# ── Cleanup Handler ───────────────────────────────────────────────────────────

cleanup() {
    unset PGPASSWORD 2>/dev/null || true
    log_info "Restore script finished (exit code: ${EXIT_CODE})"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"

    trap cleanup EXIT

    log_info "=========================================="
    log_info "Medical Handwriting OCR - Restore Started"
    log_info "=========================================="

    # Step 1: Select the backup to restore
    select_backup

    # Step 2: Restore requested components
    case "${COMPONENT}" in
        db)
            restore_database
            ;;
        minio)
            restore_minio
            ;;
        models)
            restore_models
            ;;
        config)
            restore_config
            ;;
        all)
            restore_database
            restore_minio
            restore_models
            restore_config
            ;;
    esac

    # Step 3: Post-restore verification
    verify_restore

    # Final report
    log_info "=========================================="
    log_info "Restore complete. Exit code: ${EXIT_CODE}"
    log_info ""
    log_info "Component Status:"
    for component in database minio models config; do
        local status="${COMPONENT_STATUS[${component}]:-skipped}"
        log_info "  ${component}: ${status}"
    done
    log_info "=========================================="

    exit "${EXIT_CODE}"
}

main "$@"
