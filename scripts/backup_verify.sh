#!/usr/bin/env bash
# ==============================================================================
# Medical Handwriting OCR - Backup Verification Script
# ==============================================================================
# Verifies the integrity of a backup by:
#   1. Checking md5sum checksums for all backup components
#   2. Testing database dump is restorable (to a temporary database)
#   3. Verifying MinIO backup contents structure
#   4. Generating a verification report
#
# Usage:
#   ./backup_verify.sh                              # Verify latest backup
#   ./backup_verify.sh --backup-id 2024-01-15_03-00-00
#   ./backup_verify.sh --backup-id 2024-01-15_03-00-00 --no-db-restore-test
#
# Exit codes:
#   0 = all verifications passed
#   1 = some verifications failed (partial)
#   2 = critical verifications failed
#
# ==============================================================================

set -euo pipefail

# ── Globals & Defaults ───────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
REPORT_FILE=""

# Database connection
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-medical_ocr}"
DB_USER="${DB_USER:-ocr_user}"
DB_PASSWORD="${DB_PASSWORD:-ocr_password_123}"

# Temporary database for restore testing
VERIFY_DB_NAME="${VERIFY_DB_NAME:-_verify_${DB_NAME}_$(date +%s)}"

# Runtime options
BACKUP_ID=""
SKIP_DB_RESTORE_TEST=false
MINIO_ALIAS="${MINIO_ALIAS:-minio-local}"
MINIO_BUCKET="${MINIO_BUCKET:-ocr-crops}"

# Verification results
declare -A CHECK_STATUS=()    # "check_name" -> "pass" | "fail" | "warn" | "skip"
declare -a CHECK_DETAILS=()   # human-readable details
EXIT_CODE=0

LOG_FILE="${BACKUP_DIR}/verify.log"

# ── Helper Functions ──────────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --backup-id ID              Backup to verify (default: latest)
  --no-db-restore-test        Skip database restore-to-temp test
  --backup-dir DIR            Override backup directory (default: ${BACKUP_DIR})
  --db-host HOST              Database host (default: ${DB_HOST})
  --db-port PORT              Database port (default: ${DB_PORT})
  --db-name NAME              Database name (default: ${DB_NAME})
  --db-user USER              Database user (default: ${DB_USER})
  --db-pass PASSWORD          Database password
  --verify-db-name NAME       Temporary database name for restore test
  -h, --help                  Show this help message

Exit codes:
  0 = all verifications passed
  1 = partial (non-critical checks failed)
  2 = critical verifications failed
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

record_check() {
    local name="$1"
    local status="$2"  # pass, fail, warn, skip
    local detail="$3"
    CHECK_STATUS["${name}"]="${status}"
    CHECK_DETAILS+=("${name}: [${status}] ${detail}")

    case "${status}" in
        fail) EXIT_CODE=2 ;;
        warn) [[ "${EXIT_CODE}" -lt 1 ]] && EXIT_CODE=1 ;;
    esac
}

# ── Select Backup ─────────────────────────────────────────────────────────────

select_backup() {
    local backup_path=""

    if [[ -n "${BACKUP_ID}" ]]; then
        backup_path="${BACKUP_DIR}/${BACKUP_ID}"
    else
        # Default: pick the latest backup
        backup_path="$(ls -1d "${BACKUP_DIR}"/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]_* 2>/dev/null | sort -r | head -1)"
    fi

    if [[ ! -d "${backup_path}" ]]; then
        log_error "Backup not found: ${backup_path:-<none specified>}"
        exit 2
    fi

    BACKUP_PATH="${backup_path}"
    BACKUP_ID="$(basename "${BACKUP_PATH}")"
    REPORT_FILE="${BACKUP_PATH}/verification_report.txt"
    log_info "Verifying backup: ${BACKUP_PATH}"
}

# ── Check 1: Backup Structure ────────────────────────────────────────────────

check_backup_structure() {
    log_info "Check: Backup structure..."
    local missing=()

    # Expected files/dirs in a complete backup
    [[ -d "${BACKUP_PATH}/config" ]]   || missing+=("config/")
    [[ -d "${BACKUP_PATH}/minio" ]]    || missing+=("minio/")
    [[ -d "${BACKUP_PATH}/models" ]]   || missing+=("models/")

    # At least one major component should be present
    if [[ ${#missing[@]} -eq 3 ]]; then
        record_check "backup_structure" "fail" "Missing all component directories"
    elif [[ ${#missing[@]} -gt 0 ]]; then
        record_check "backup_structure" "warn" "Missing directories: ${missing[*]}"
    else
        record_check "backup_structure" "pass" "All expected directories present"
    fi

    # Check for backup summary
    if [[ -f "${BACKUP_PATH}/backup_summary.txt" ]]; then
        record_check "backup_summary" "pass" "backup_summary.txt present"
    else
        record_check "backup_summary" "warn" "backup_summary.txt missing"
    fi
}

# ── Check 2: Database Dump Integrity ──────────────────────────────────────────

check_database_checksum() {
    log_info "Check: Database dump checksum..."
    local dump_file="${BACKUP_PATH}/db_${DB_NAME}.dump"
    local checksum_file="${BACKUP_PATH}/db_${DB_NAME}.dump.md5"

    if [[ ! -f "${dump_file}" ]]; then
        record_check "db_checksum" "skip" "No database dump found in backup"
        return 0
    fi

    local dump_size
    dump_size="$(du -h "${dump_file}" | cut -f1)"

    if [[ ! -f "${checksum_file}" ]]; then
        record_check "db_checksum" "warn" "Dump exists (${dump_size}) but no checksum file"
        return 0
    fi

    if md5sum -c "${checksum_file}" &>/dev/null; then
        record_check "db_checksum" "pass" "Database dump checksum OK (${dump_size})"
    else
        record_check "db_checksum" "fail" "Database dump checksum MISMATCH"
    fi
}

# ── Check 3: Database Restore Test ────────────────────────────────────────────

check_database_restore() {
    log_info "Check: Database restore to temporary database..."

    if [[ "${SKIP_DB_RESTORE_TEST}" == true ]]; then
        record_check "db_restore" "skip" "Skipped (--no-db-restore-test)"
        return 0
    fi

    local dump_file="${BACKUP_PATH}/db_${DB_NAME}.dump"

    if [[ ! -f "${dump_file}" ]]; then
        record_check "db_restore" "skip" "No database dump to test"
        return 0
    fi

    if ! command -v pg_restore &>/dev/null; then
        record_check "db_restore" "warn" "pg_restore not available — cannot test restore"
        return 0
    fi

    export PGPASSWORD="${DB_PASSWORD}"

    # Create temporary database
    log_info "Creating temporary database '${VERIFY_DB_NAME}' for restore test..."
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "CREATE DATABASE ${VERIFY_DB_NAME} OWNER ${DB_USER};" \
        &>>"${LOG_FILE}" 2>/dev/null || {
        log_warn "Could not create temporary database — skipping restore test"
        unset PGPASSWORD
        record_check "db_restore" "warn" "Cannot create temporary database for restore test"
        return 0
    }

    # Attempt restore
    local restore_ok=false
    if pg_restore -Fc \
            -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" \
            -d "${VERIFY_DB_NAME}" \
            --no-owner --no-privileges \
            "${dump_file}" &>>"${LOG_FILE}" 2>&1; then
        restore_ok=true
    fi

    # Count restored tables
    local table_count=0
    if [[ "${restore_ok}" == true ]]; then
        table_count="$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${VERIFY_DB_NAME}" \
            -t -A -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "0")"
    fi

    # Count restored rows (sum across all tables)
    local row_count=0
    if [[ "${restore_ok}" == true ]] && [[ "${table_count}" -gt 0 ]]; then
        row_count="$(psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${VERIFY_DB_NAME}" \
            -t -A -c "SELECT sum(n_live_tup) FROM pg_stat_user_tables;" 2>/dev/null || echo "0")"
    fi

    # Clean up: drop temporary database
    psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d postgres \
        -c "DROP DATABASE IF EXISTS ${VERIFY_DB_NAME};" \
        &>>"${LOG_FILE}" 2>/dev/null || true

    unset PGPASSWORD

    if [[ "${restore_ok}" == true ]]; then
        record_check "db_restore" "pass" "Restore successful: ${table_count} tables, ${row_count} rows"
    else
        record_check "db_restore" "fail" "pg_restore FAILED — dump may be corrupted"
    fi
}

# ── Check 4: MinIO Backup Integrity ──────────────────────────────────────────

check_minio_backup() {
    log_info "Check: MinIO backup integrity..."
    local minio_dir="${BACKUP_PATH}/minio"
    local manifest="${BACKUP_PATH}/minio_manifest.md5"

    if [[ ! -d "${minio_dir}" ]]; then
        record_check "minio_integrity" "skip" "MinIO backup not found"
        return 0
    fi

    local file_count
    file_count="$(find "${minio_dir}" -type f | wc -l)"
    local total_size
    total_size="$(du -sh "${minio_dir}" 2>/dev/null | cut -f1)"

    if [[ "${file_count}" -eq 0 ]]; then
        record_check "minio_integrity" "warn" "MinIO backup directory is empty"
        return 0
    fi

    if [[ ! -f "${manifest}" ]]; then
        record_check "minio_integrity" "warn" "${file_count} files (${total_size}), no checksum manifest"
        return 0
    fi

    # Verify all checksums
    local errors
    errors="$(cd "${BACKUP_PATH}" && md5sum -c minio_manifest.md5 2>&1 | grep -c 'FAILED' || true)"

    if [[ "${errors}" -eq 0 ]]; then
        record_check "minio_integrity" "pass" "All ${file_count} files verified (${total_size})"
    else
        record_check "minio_integrity" "fail" "${errors} of ${file_count} files FAILED checksum"
    fi
}

# ── Check 5: Model Files Integrity ──────────────────────────────────────────

check_models_backup() {
    log_info "Check: Model files integrity..."
    local models_dir="${BACKUP_PATH}/models"
    local manifest="${BACKUP_PATH}/models_manifest.md5"

    if [[ ! -d "${models_dir}" ]]; then
        record_check "models_integrity" "skip" "Models backup not found"
        return 0
    fi

    local file_count
    file_count="$(find "${models_dir}" -type f | wc -l)"
    local total_size
    total_size="$(du -sh "${models_dir}" 2>/dev/null | cut -f1)"

    if [[ "${file_count}" -eq 0 ]]; then
        record_check "models_integrity" "warn" "Models backup directory is empty"
        return 0
    fi

    if [[ ! -f "${manifest}" ]]; then
        record_check "models_integrity" "warn" "${file_count} files (${total_size}), no checksum manifest"
        return 0
    fi

    local errors
    errors="$(cd "${BACKUP_PATH}" && md5sum -c models_manifest.md5 2>&1 | grep -c 'FAILED' || true)"

    if [[ "${errors}" -eq 0 ]]; then
        record_check "models_integrity" "pass" "All ${file_count} model files verified (${total_size})"
    else
        record_check "models_integrity" "fail" "${errors} of ${file_count} model files FAILED checksum"
    fi
}

# ── Check 6: Config Backup ───────────────────────────────────────────────────

check_config_backup() {
    log_info "Check: Config backup..."
    local config_dir="${BACKUP_PATH}/config"

    if [[ ! -d "${config_dir}" ]]; then
        record_check "config_backup" "skip" "Config backup not found"
        return 0
    fi

    local files=()
    [[ -f "${config_dir}/.env" ]]          && files+=(".env")
    [[ -f "${config_dir}/.env.gpg" ]]     && files+=(".env.gpg")
    [[ -f "${config_dir}/config.py" ]]    && files+=("config.py")

    local compose_count=0
    for f in docker-compose.yml docker-compose.full.yml docker-compose.monitoring.yml; do
        [[ -f "${config_dir}/${f}" ]] && ((compose_count++)) || true
    done

    [[ -d "${config_dir}/k8s" ]]      && files+=("k8s/")
    [[ -d "${config_dir}/terraform" ]] && files+=("terraform/")

    local total
    total=$(( ${#files[@]} + compose_count ))

    if [[ "${total}" -eq 0 ]]; then
        record_check "config_backup" "warn" "Config directory exists but is empty"
    else
        record_check "config_backup" "pass" "${total} config item(s) present: ${files[*]} compose(${compose_count})"
    fi
}

# ── Generate Verification Report ──────────────────────────────────────────────

generate_report() {
    log_info "Generating verification report..."

    local passed=0 failed=0 warned=0 skipped=0
    for key in "${!CHECK_STATUS[@]}"; do
        case "${CHECK_STATUS[$key]}" in
            pass)  ((passed++))  || true ;;
            fail)  ((failed++))  || true ;;
            warn)  ((warned++))  || true ;;
            skip)  ((skipped++)) || true ;;
        esac
    done

    {
        echo "=============================================="
        echo " Backup Verification Report"
        echo "=============================================="
        echo "Backup ID : ${BACKUP_ID}"
        echo "Path      : ${BACKUP_PATH}"
        echo "Timestamp : $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo ""
        echo "Results Summary:"
        echo "  PASS   : ${passed}"
        echo "  FAIL   : ${failed}"
        echo "  WARN   : ${warned}"
        echo "  SKIP   : ${skipped}"
        echo ""
        echo "Detailed Results:"
        echo "──────────────────────────────────────────────"
        for detail in "${CHECK_DETAILS[@]}"; do
            echo "  ${detail}"
        done
        echo "──────────────────────────────────────────────"
        echo ""
        echo "Overall: "
        if [[ "${failed}" -gt 0 ]]; then
            echo "  FAILED — ${failed} critical check(s) failed"
        elif [[ "${warned}" -gt 0 ]]; then
            echo "  PASSED with warnings — ${warned} warning(s)"
        else
            echo "  PASSED — all checks successful"
        fi
        echo "=============================================="
    } | tee "${REPORT_FILE}"

    log_info "Report saved to ${REPORT_FILE}"
}

# ── Parse CLI Arguments ─────────────────────────────────────────────────────

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --backup-id)
                BACKUP_ID="$2"; shift 2
                ;;
            --no-db-restore-test)
                SKIP_DB_RESTORE_TEST=true
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
            --verify-db-name)
                VERIFY_DB_NAME="$2"; shift 2
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

# ── Cleanup ───────────────────────────────────────────────────────────────────

cleanup() {
    unset PGPASSWORD 2>/dev/null || true
    log_info "Verification script finished (exit code: ${EXIT_CODE})"
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
    parse_args "$@"
    trap cleanup EXIT

    log_info "=============================================="
    log_info "Medical Handwriting OCR - Backup Verification"
    log_info "=============================================="

    select_backup

    # Run all verification checks
    check_backup_structure
    check_database_checksum
    check_database_restore
    check_minio_backup
    check_models_backup
    check_config_backup

    # Generate report
    generate_report

    log_info "Verification complete (exit code: ${EXIT_CODE})"
    exit "${EXIT_CODE}"
}

main "$@"
