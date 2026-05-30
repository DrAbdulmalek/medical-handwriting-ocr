#!/bin/bash
# =============================================================================
# Medical Handwriting OCR - Clean Uninstall Script
# =============================================================================
# Safely removes all Docker containers, volumes, images, networks,
# and local data created during installation.
#
# Usage:
#   ./uninstall.sh           # Interactive mode (prompts for confirmation)
#   ./uninstall.sh --force   # Non-interactive (for CI/CD)
#   ./uninstall.sh --dry-run # Show what would be removed without removing
#
# WARNING: This script is DESTRUCTIVE and IRREVERSIBLE.
#          Always backup your data before running.
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Parse arguments
FORCE=false
DRY_RUN=false
KEEP_DATA=false

for arg in "$@"; do
    case "$arg" in
        --force|-f)  FORCE=true ;;
        --dry-run|-n) DRY_RUN=true ;;
        --keep-data) KEEP_DATA=true ;;
        --help|-h)
            echo "Usage: ./uninstall.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --force, -f      Skip confirmation prompts"
            echo "  --dry-run, -n    Show what would be removed (no changes)"
            echo "  --keep-data      Keep database backups and exported data"
            echo "  --help, -h       Show this help message"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
    esac
done

# Logging
log_info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success()  { echo -e "${GREEN}[OK]${NC} $1"; }
log_warning()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error()    { echo -e "${RED}[ERROR]${NC} $1"; }
log_dry()      { echo -e "${CYAN}[DRY-RUN]${NC} Would: $1"; }

confirm() {
    if [ "$FORCE" = true ]; then
        return 0
    fi
    echo -ne "${YELLOW}$1 [y/N]: ${NC}"
    read -r response
    [[ "$response" =~ ^[Yy]$ ]]
}

# Banner
echo -e "${RED}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                    ⚠️  UNINSTALL WARNING  ⚠️                      ║"
echo "║                                                                  ║"
echo "║   This will permanently remove:                                  ║"
echo "║     - All Docker containers (PostgreSQL, MinIO, Redis, Backend)  ║"
echo "║     - All Docker volumes (database data, images, cache)          ║"
echo "║     - All Docker images built for this project                   ║"
echo "║     - All Docker networks                                       ║"
echo "║     - Local data directories (uploads, crops, models)             ║"
echo "║     - Python caches and virtual environments                    ║"
echo "║     - Generated configuration files (.env, logs)                  ║"
echo "║                                                                  ║"
echo "║   ⚠️  THIS ACTION CANNOT BE UNDONE! ⚠️                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}${BOLD}=== DRY RUN MODE — No changes will be made ===${NC}"
    echo ""
fi

# Keep track of what we'd remove
REMOVED_CONTAINERS=()
REMOVED_VOLUMES=()
REMOVED_IMAGES=()
REMOVED_NETWORKS=()
REMOVED_DIRS=()
REMOVED_FILES=()

# =============================================================================
# STEP 1: Stop Docker Compose services
# =============================================================================
echo -e "${BOLD}Step 1: Stopping Docker services${NC}"
echo ""

# Stop full stack
if [ -f "docker/docker-compose.full.yml" ]; then
    if [ "$DRY_RUN" = true ]; then
        log_dry "docker compose -f docker/docker-compose.full.yml down"
    else
        if docker compose -f docker/docker-compose.full.yml down --remove-orphans 2>/dev/null; then
            log_success "Full stack stopped"
        else
            log_warning "Full stack was not running"
        fi
    fi
fi

# Stop basic stack
if [ -f "docker/docker-compose.yml" ]; then
    if [ "$DRY_RUN" = true ]; then
        log_dry "docker compose -f docker/docker-compose.yml down"
    else
        if docker compose -f docker/docker-compose.yml down --remove-orphans 2>/dev/null; then
            log_success "Basic stack stopped"
        else
            log_warning "Basic stack was not running"
        fi
    fi
fi

# Stop test stack
if [ -f "docker/docker-compose.test.yml" ]; then
    if [ "$DRY_RUN" = true ]; then
        log_dry "docker compose -f docker/docker-compose.test.yml down"
    else
        if docker compose -f docker/docker-compose.test.yml down --remove-orphans 2>/dev/null; then
            log_success "Test stack stopped"
        else
            log_warning "Test stack was not running"
        fi
    fi
fi

# Stop monitoring stack
if [ -f "docker/docker-compose.monitoring.yml" ]; then
    if [ "$DRY_RUN" = true ]; then
        log_dry "docker compose -f docker/docker-compose.monitoring.yml down"
    else
        if docker compose -f docker/docker-compose.monitoring.yml down --remove-orphans 2>/dev/null; then
            log_success "Monitoring stack stopped"
        else
            log_warning "Monitoring stack was not running"
        fi
    fi
fi

echo ""

# =============================================================================
# STEP 2: Remove Docker containers
# =============================================================================
echo -e "${BOLD}Step 2: Removing project containers${NC}"
echo ""

CONTAINERS=(
    "ocr_postgres"
    "ocr_minio"
    "ocr_redis"
    "ocr_backend"
    "ocr_celery"
    "ocr_celery_beat"
    "ocr_nginx"
    "ocr_training_gpu"
    "ocr_test_postgres"
    "ocr_test_minio"
    "ocr_test_redis"
    "ocr_test_runner"
    "ocr_test_clamav"
    "ocr_prometheus"
    "ocr_grafana"
    "ocr_alertmanager"
)

for container in "${CONTAINERS[@]}"; do
    if docker ps -a --format "{{.Names}}" | grep -q "^${container}$"; then
        if [ "$DRY_RUN" = true ]; then
            log_dry "Remove container: $container"
            REMOVED_CONTAINERS+=("$container")
        else
            if docker rm -f "$container" 2>/dev/null; then
                log_success "Removed container: $container"
                REMOVED_CONTAINERS+=("$container")
            fi
        fi
    fi
done

echo ""

# =============================================================================
# STEP 3: Remove Docker volumes
# =============================================================================
echo -e "${BOLD}Step 3: Removing Docker volumes${NC}"
echo ""

if ! confirm "Remove all Docker volumes (database data, MinIO storage, cache)?"; then
    log_warning "Skipping volume removal"
else
    VOLUMES=(
        "repo-check_postgres_data"
        "repo-check_minio_data"
        "repo-check_model_cache"
        "repo-check_redis_data"
        "repo-check_nginx_logs"
        "repo-check_training_output"
        "medical-ocr_postgres_data"
        "medical-ocr_minio_data"
        "medical-ocr_model_cache"
        "medical-ocr_redis_data"
        "medical-ocr_nginx_logs"
        "medical-ocr_training_output"
    )

    for vol in "${VOLUMES[@]}"; do
        if docker volume ls --format "{{.Name}}" | grep -q "${vol}"; then
            if [ "$DRY_RUN" = true ]; then
                log_dry "Remove volume: $vol"
                REMOVED_VOLUMES+=("$vol")
            else
                if docker volume rm "$vol" 2>/dev/null; then
                    log_success "Removed volume: $vol"
                    REMOVED_VOLUMES+=("$vol")
                fi
            fi
        fi
    done

    # Also remove any project volumes matching patterns
    while IFS= read -r vol; do
        if [ -n "$vol" ]; then
            if [ "$DRY_RUN" = true ]; then
                log_dry "Remove matched volume: $vol"
                REMOVED_VOLUMES+=("$vol")
            else
                if docker volume rm "$vol" 2>/dev/null; then
                    log_success "Removed volume: $vol"
                    REMOVED_VOLUMES+=("$vol")
                fi
            fi
        fi
    done < <(docker volume ls --format "{{.Name}}" | grep -E "(ocr|medical)" 2>/dev/null || true)
fi

echo ""

# =============================================================================
# STEP 4: Remove Docker images
# =============================================================================
echo -e "${BOLD}Step 4: Removing project Docker images${NC}"
echo ""

if ! confirm "Remove all project Docker images?"; then
    log_warning "Skipping image removal"
else
    IMAGES=(
        "repo-check-backend"
        "repo-check-celery"
        "medical-ocr-backend"
        "medical-ocr-celery"
    )

    for img in "${IMAGES[@]}"; do
        if docker images --format "{{.Repository}}:{{.Tag}}" | grep -q "$img"; then
            if [ "$DRY_RUN" = true ]; then
                log_dry "Remove image: $img"
                REMOVED_IMAGES+=("$img")
            else
                if docker rmi "$img" 2>/dev/null; then
                    log_success "Removed image: $img"
                    REMOVED_IMAGES+=("$img")
                fi
            fi
        fi
    done

    # Remove dangling images from this project
    while IFS= read -r img; do
        if [ -n "$img" ]; then
            if [ "$DRY_RUN" = true ]; then
                log_dry "Remove dangling image: $img"
            else
                docker rmi "$img" 2>/dev/null && log_success "Removed dangling image: $img"
            fi
        fi
    done < <(docker images --filter "dangling=true" --format "{{.ID}}" 2>/dev/null || true)
fi

echo ""

# =============================================================================
# STEP 5: Remove Docker networks
# =============================================================================
echo -e "${BOLD}Step 5: Removing Docker networks${NC}"
echo ""

NETWORKS=(
    "repo-check_default"
    "repo-check_ocr-network"
    "repo-check_gateway"
    "repo-check_test-network"
    "medical-ocr_default"
    "medical-ocr_ocr-network"
    "medical-ocr_gateway"
    "medical-ocr_test-network"
)

for net in "${NETWORKS[@]}"; do
    if docker network ls --format "{{.Name}}" | grep -q "${net}"; then
        if [ "$DRY_RUN" = true ]; then
            log_dry "Remove network: $net"
            REMOVED_NETWORKS+=("$net")
        else
            if docker network rm "$net" 2>/dev/null; then
                log_success "Removed network: $net"
                REMOVED_NETWORKS+=("$net")
            fi
        fi
    fi
done

echo ""

# =============================================================================
# STEP 6: Remove local directories
# =============================================================================
echo -e "${BOLD}Step 6: Removing local data directories${NC}"
echo ""

if [ "$KEEP_DATA" = true ]; then
    log_info "Keeping data directories (--keep-data flag)"
else
    DIRS=(
        "uploads"
        "crops"
        "postgres_data"
        "minio_data"
        "redis_data"
        ".venv"
        "backend/htmlcov"
        "backend/.cache"
        "backend/.coverage"
        "training/models"
        "training/.cache"
    )

    for dir in "${DIRS[@]}"; do
        if [ -d "$dir" ]; then
            if [ "$DRY_RUN" = true ]; then
                log_dry "Remove directory: $dir/"
                REMOVED_DIRS+=("$dir")
            else
                if rm -rf "$dir"; then
                    log_success "Removed directory: $dir/"
                    REMOVED_DIRS+=("$dir")
                fi
            fi
        fi
    done
fi

echo ""

# =============================================================================
# STEP 7: Remove generated files
# =============================================================================
echo -e "${BOLD}Step 7: Removing generated files${NC}"
echo ""

FILES=(
    ".env"
    "coverage.xml"
    ".pytest_cache"
)

for file in "${FILES[@]}"; do
    if [ -e "$file" ]; then
        if [ "$DRY_RUN" = true ]; then
            log_dry "Remove file: $file"
            REMOVED_FILES+=("$file")
        else
            if rm -f "$file"; then
                log_success "Removed file: $file"
                REMOVED_FILES+=("$file")
            fi
        fi
    fi
done

# Clean Python caches
if [ "$DRY_RUN" = true ]; then
    log_dry "Remove all __pycache__ directories"
else
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name ".DS_Store" -delete 2>/dev/null || true
    log_success "Python caches cleaned"
fi

echo ""

# =============================================================================
# STEP 8: Final Summary
# =============================================================================
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                    Uninstall Summary                            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}Mode: DRY RUN (nothing was actually removed)${NC}"
    echo ""
fi

echo -e "${BOLD}Containers removed:${NC}   ${#REMOVED_CONTAINERS[@]}"
echo -e "${BOLD}Volumes removed:${NC}      ${#REMOVED_VOLUMES[@]}"
echo -e "${BOLD}Images removed:${NC}       ${#REMOVED_IMAGES[@]}"
echo -e "${BOLD}Networks removed:${NC}     ${#REMOVED_NETWORKS[@]}"
echo -e "${BOLD}Directories removed:${NC}   ${#REMOVED_DIRS[@]}"
echo -e "${BOLD}Files removed:${NC}        ${#REMOVED_FILES[@]}"
echo ""

TOTAL=$(( ${#REMOVED_CONTAINERS[@]} + ${#REMOVED_VOLUMES[@]} + ${#REMOVED_IMAGES[@]} + ${#REMOVED_NETWORKS[@]} + ${#REMOVED_DIRS[@]} + ${#REMOVED_FILES[@]} ))

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}Total items that would be removed: $TOTAL${NC}"
else
    echo -e "${GREEN}Total items removed: $TOTAL${NC}"
fi

echo ""

if [ "$DRY_RUN" = false ]; then
    echo -e "${GREEN}${BOLD}Uninstall complete!${NC}"
    echo ""
    echo -e "${YELLOW}To reinstall:${NC}"
    echo -e "  git clone https://github.com/DrAbdulmalek/medical-handwriting-ocr.git"
    echo -e "  cd medical-handwriting-ocr"
    echo -e "  chmod +x setup.sh && ./setup.sh"
    echo ""
else
    echo -e "${CYAN}Run without --dry-run to actually remove these items.${NC}"
    echo ""
fi
