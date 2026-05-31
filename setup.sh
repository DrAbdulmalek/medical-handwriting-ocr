#!/bin/bash
# =============================================================================
# Medical Handwriting OCR - One-Click Setup Script
# =============================================================================
# This script automates the entire setup process:
# 1. Checks system requirements
# 2. Installs Docker & Docker Compose if missing
# 3. Creates environment configuration
# 4. Builds and starts all services
# 5. Runs database migrations
# 6. Verifies installation
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
PROJECT_NAME="medical-ocr"
MIN_DOCKER_VERSION="20.10.0"
MIN_COMPOSE_VERSION="2.0.0"
REQUIRED_PORTS=(8000 9000 9001 5432 6379 3000 9090)

# Logging functions
log_info() { echo -e "${BLUE}ℹ️  ${NC}$1"; }
log_success() { echo -e "${GREEN}✅ ${NC}$1"; }
log_warning() { echo -e "${YELLOW}⚠️  ${NC}$1"; }
log_error() { echo -e "${RED}❌ ${NC}$1"; }
log_step() { echo -e "\n${BOLD}${CYAN}▶ $1${NC}"; }
log_progress() { echo -e "${CYAN}  → ${NC}$1"; }

# Progress spinner
spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    while [ -d /proc/$pid ]; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

# =============================================================================
# STEP 0: Banner
# =============================================================================
show_banner() {
    clear
    echo -e "${CYAN}"
    cat << "EOF"
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║   ███╗   ███╗███████╗██████╗ ██╗ ██████╗ █████╗ ██╗         ██████╗  ██████╗ ██████╗ 
║   ████╗ ████║██╔════╝██╔══██╗██║██╔════╝██╔══██╗██║         ██╔══██╗██╔═══██╗██╔══██╗
║   ██╔████╔██║█████╗  ██║  ██║██║██║     ███████║██║         ██████╔╝██║   ██║██████╔╝
║   ██║╚██╔╝██║██╔══╝  ██║  ██║██║██║     ██╔══██║██║         ██╔══██╗██║   ██║██╔═══╝ 
║   ██║ ╚═╝ ██║███████╗██████╔╝██║╚██████╗██║  ██║███████╗    ██║  ██║╚██████╔╝██║     
║   ╚═╝     ╚═╝╚══════╝╚═════╝ ╚═╝ ╚═════╝╚═╝  ╚═╝╚══════╝    ╚═╝  ╚═╝ ╚═════╝ ╚═╝     
║                                                                              ║
║                    Medical Handwriting OCR - Setup                           ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    echo -e "${BOLD}Version:${NC} 4.0.0 (Production-Ready)"
    echo -e "${BOLD}Date:${NC} $(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "${BOLD}OS:${NC} $(uname -s) $(uname -r)"
    echo ""
}

# =============================================================================
# STEP 1: System Requirements Check
# =============================================================================
check_requirements() {
    log_step "STEP 1/8: Checking System Requirements"

    # Check OS
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    else
        log_error "Unsupported OS: $OSTYPE"
        exit 1
    fi
    log_progress "Operating System: $OS ✓"

    # Check architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" != "x86_64" && "$ARCH" != "arm64" ]]; then
        log_warning "Architecture $ARCH may not be fully supported"
    fi
    log_progress "Architecture: $ARCH ✓"

    # Check memory
    if [[ "$OS" == "linux" ]]; then
        TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
    else
        TOTAL_MEM=$(sysctl -n hw.memsize | awk '{print int($1/1024/1024/1024)}')
    fi

    if [[ $TOTAL_MEM -lt 8 ]]; then
        log_warning "Recommended memory is 8GB+, found ${TOTAL_MEM}GB"
    else
        log_progress "Memory: ${TOTAL_MEM}GB ✓"
    fi

    # Check disk space
    DISK_AVAIL=$(df -BG . | awk 'NR==2 {print $4}' | sed 's/G//')
    if [[ $DISK_AVAIL -lt 20 ]]; then
        log_warning "Recommended disk space is 20GB+, found ${DISK_AVAIL}GB"
    else
        log_progress "Disk Space: ${DISK_AVAIL}GB available ✓"
    fi

    log_success "System requirements check complete"
}

# =============================================================================
# STEP 2: Docker Installation
# =============================================================================
install_docker() {
    log_step "STEP 2/8: Docker & Docker Compose"

    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        log_progress "Docker found: v$DOCKER_VERSION"

        if [[ $(printf '%s\n' "$MIN_DOCKER_VERSION" "$DOCKER_VERSION" | sort -V | head -n1) != "$MIN_DOCKER_VERSION" ]]; then
            log_warning "Docker version $DOCKER_VERSION is older than recommended $MIN_DOCKER_VERSION"
        fi
    else
        log_info "Docker not found. Installing..."

        if [[ "$OS" == "linux" ]]; then
            # Install Docker on Linux
            curl -fsSL https://get.docker.com -o get-docker.sh
            sh get-docker.sh
            sudo usermod -aG docker $USER
            rm get-docker.sh
        else
            # macOS
            log_error "Please install Docker Desktop manually from https://www.docker.com/products/docker-desktop"
            exit 1
        fi

        log_success "Docker installed successfully"
    fi

    # Check Docker Compose
    if command -v docker-compose &> /dev/null; then
        COMPOSE_VERSION=$(docker-compose --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        log_progress "Docker Compose found: v$COMPOSE_VERSION"
    elif docker compose version &> /dev/null; then
        log_progress "Docker Compose (plugin) found"
    else
        log_info "Installing Docker Compose..."

        if [[ "$OS" == "linux" ]]; then
            sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)"                 -o /usr/local/bin/docker-compose
            sudo chmod +x /usr/local/bin/docker-compose
        fi

        log_success "Docker Compose installed"
    fi

    # Start Docker service
    if [[ "$OS" == "linux" ]]; then
        sudo systemctl start docker 2>/dev/null || true
        sudo systemctl enable docker 2>/dev/null || true
    fi

    # Test Docker
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running. Please start Docker and try again."
        exit 1
    fi

    log_success "Docker is ready"
}

# =============================================================================
# STEP 3: Port Availability Check
# =============================================================================
check_ports() {
    log_step "STEP 3/8: Checking Port Availability"

    PORTS_IN_USE=()

    for port in "${REQUIRED_PORTS[@]}"; do
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1 ||            netstat -tuln 2>/dev/null | grep -q ":$port " ||            ss -tuln 2>/dev/null | grep -q ":$port "; then
            PORTS_IN_USE+=("$port")
            log_warning "Port $port is already in use"
        else
            log_progress "Port $port is available ✓"
        fi
    done

    if [[ ${#PORTS_IN_USE[@]} -gt 0 ]]; then
        echo ""
        log_warning "The following ports are in use: ${PORTS_IN_USE[*]}"
        echo -e "${YELLOW}Options:${NC}"
        echo "  1) Stop existing services using these ports"
        echo "  2) Modify docker-compose.full.yml to use different ports"
        echo "  3) Continue anyway (may cause conflicts)"
        echo ""
        read -p "Continue? [y/N]: " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    log_success "Port check complete"
}

# =============================================================================
# STEP 4: Environment Configuration
# =============================================================================
setup_environment() {
    log_step "STEP 4/8: Environment Configuration"

    ENV_FILE=".env"

    if [[ -f "$ENV_FILE" ]]; then
        log_progress "Found existing .env file"
        read -p "Overwrite existing configuration? [y/N]: " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Using existing .env file"
            return
        fi
    fi

    log_info "Creating environment configuration..."

    # Generate random secrets
    JWT_SECRET=$(openssl rand -hex 32 2>/dev/null || head -c 64 /dev/urandom | xxd -p | tr -d '\n')
    DB_PASSWORD=$(openssl rand -base64 24 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -d '=+/')
    MINIO_SECRET=$(openssl rand -base64 24 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -d '=+/')

    cat > "$ENV_FILE" << EOF
# =============================================================================
# Medical Handwriting OCR - Environment Configuration
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
# =============================================================================

# ============================================================
# CORE SERVICES
# ============================================================

# Database (PostgreSQL)
DB_USER=ocr_user
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=medical_ocr
DB_PORT=5432
DATABASE_URL=postgresql://ocr_user:${DB_PASSWORD}@postgres:5432/medical_ocr

# MinIO (Object Storage)
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=${MINIO_SECRET}
MINIO_BUCKET=ocr-crops
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001

# Redis (Cache & Queue)
REDIS_PORT=6379
REDIS_URL=redis://redis:6379/0

# ============================================================
# SECURITY
# ============================================================

JWT_SECRET=${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

API_RATE_LIMIT=1000

# ============================================================
# OPTIONAL: DICTIONARY INTEGRATION
# ============================================================

# Arabic Dictionaries (GitHub Token)
# Get from: https://github.com/settings/tokens
# Required scopes: repo
# DICTIONARY_REPO_TOKEN=ghp_your_token_here

# UMLS/SNOMED (Medical Terminology)
# Get from: https://uts.nlm.nih.gov/uts/signup-login
# UMLS_API_KEY=your_umls_api_key

# ============================================================
# OPTIONAL: EXTERNAL SERVICES
# ============================================================

# Sentry (Error Tracking)
# SENTRY_DSN=https://xxx@yyy.ingest.sentry.io/zzz

# Slack (Notifications)
# SLACK_WEBHOOK=https://hooks.slack.com/services/xxx/yyy/zzz

# ============================================================
# MODEL CONFIGURATION
# ============================================================

MODEL_PATH=/models/production
TRAINING_OUTPUT_DIR=/models/training
PADDLEOCR_LANG=ar,en

# ============================================================
# FEATURE FLAGS
# ============================================================

ENABLE_DICTIONARIES=false
ENABLE_UMLS=false
ENABLE_SMART_SUGGESTIONS=true
ENABLE_CONTINUAL_LEARNING=true
ENABLE_METRICS=true

# ============================================================
# DEVELOPMENT
# ============================================================

DEBUG=false
LOG_LEVEL=INFO
ENVIRONMENT=production

# API Ports
API_PORT=8000
FRONTEND_PORT=80

# Monitoring Ports
PROMETHEUS_PORT=9090
GRAFANA_PORT=3000
GRAFANA_PASSWORD=admin123
EOF

    log_success "Environment file created: .env"
    log_info "Important: Review and customize .env before production use"

    # Ask about dictionary token
    echo ""
    read -p "Do you want to configure Arabic Dictionary access? [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${CYAN}Get your token from: https://github.com/settings/tokens${NC}"
        read -p "Enter GitHub Token (ghp_...): " DICT_TOKEN
        if [[ -n "$DICT_TOKEN" ]]; then
            sed -i '' "s|# DICTIONARY_REPO_TOKEN=.*|DICTIONARY_REPO_TOKEN=${DICT_TOKEN}|" "$ENV_FILE" 2>/dev/null ||             sed -i "s|# DICTIONARY_REPO_TOKEN=.*|DICTIONARY_REPO_TOKEN=${DICT_TOKEN}|" "$ENV_FILE"
            sed -i '' "s|ENABLE_DICTIONARIES=false|ENABLE_DICTIONARIES=true|" "$ENV_FILE" 2>/dev/null ||             sed -i "s|ENABLE_DICTIONARIES=false|ENABLE_DICTIONARIES=true|" "$ENV_FILE"
            log_success "Dictionary integration enabled"
        fi
    fi

    # Ask about UMLS (optional — requires NIH UMLS license)
    echo ""
    echo -e "${YELLOW}⚠ UMLS integration is OPTIONAL and requires a free NIH UMLS license.${NC}"
    echo -e "${YELLOW}  If you don't have a license, press N or Enter to skip.${NC}"
    echo -e "${YELLOW}  Signup: https://uts.nlm.nih.gov/uts/signup-login${NC}"
    read -p "Configure UMLS medical terminology? [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${CYAN}Enter your UMLS API Key (or press Enter to skip):${NC}"
        read -p "UMLS API Key: " UMLS_KEY
        if [[ -n "$UMLS_KEY" ]]; then
            sed -i '' "s|# UMLS_API_KEY=.*|UMLS_API_KEY=${UMLS_KEY}|" "$ENV_FILE" 2>/dev/null ||             sed -i "s|# UMLS_API_KEY=.*|UMLS_API_KEY=${UMLS_KEY}|" "$ENV_FILE"
            sed -i '' "s|ENABLE_UMLS=false|ENABLE_UMLS=true|" "$ENV_FILE" 2>/dev/null ||             sed -i "s|ENABLE_UMLS=false|ENABLE_UMLS=true|" "$ENV_FILE"
            log_success "UMLS integration enabled"
        else
            echo -e "${YELLOW}  No key entered — UMLS skipped (you can configure it later in .env)${NC}"
        fi
    else
        echo -e "${BLUE}  UMLS skipped — you can enable it later by setting UMLS_API_KEY in .env${NC}"
    fi
}

# =============================================================================
# STEP 5: Build & Start Services
# =============================================================================
build_and_start() {
    log_step "STEP 5/8: Building & Starting Services"

    cd docker

    log_progress "Pulling latest images..."
    docker-compose -f docker-compose.full.yml pull &
    spinner $!
    wait $!

    log_progress "Building custom images..."
    docker-compose -f docker-compose.full.yml build --parallel &
    spinner $!
    wait $!

    log_progress "Starting services..."
    docker-compose -f docker-compose.full.yml up -d

    log_success "All services started"

    # Wait for services to be healthy
    log_progress "Waiting for services to be healthy..."
    sleep 10

    cd ..
}

# =============================================================================
# STEP 6: Database Setup
# =============================================================================
setup_database() {
    log_step "STEP 6/8: Database Setup"

    log_progress "Running database migrations..."

    # Wait for PostgreSQL
    MAX_RETRIES=30
    RETRY_COUNT=0

    while ! docker exec ocr_postgres pg_isready -U ocr_user -d medical_ocr >/dev/null 2>&1; do
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [[ $RETRY_COUNT -ge $MAX_RETRIES ]]; then
            log_error "PostgreSQL failed to start after ${MAX_RETRIES} attempts"
            exit 1
        fi
        log_progress "Waiting for PostgreSQL... ($RETRY_COUNT/$MAX_RETRIES)"
        sleep 2
    done

    log_success "PostgreSQL is ready"

    # Run migrations
    log_progress "Applying database schema..."
    docker exec -i ocr_postgres psql -U ocr_user -d medical_ocr < docker/init.sql

    log_success "Database initialized"
}

# =============================================================================
# STEP 7: Verification
# =============================================================================
verify_installation() {
    log_step "STEP 7/8: Verification"

    SERVICES=(
        "ocr_postgres:PostgreSQL"
        "ocr_minio:MinIO"
        "ocr_redis:Redis"
        "ocr_backend:Backend API"
    )

    ALL_HEALTHY=true

    for service_info in "${SERVICES[@]}"; do
        IFS=':' read -r container name <<< "$service_info"

        if docker ps --format "{{.Names}}" | grep -q "^${container}$"; then
            log_progress "$name is running ✓"
        else
            log_error "$name is not running"
            ALL_HEALTHY=false
        fi
    done

    # Test API health
    log_progress "Testing API endpoint..."
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        log_progress "API health check passed ✓"
    else
        log_warning "API health check failed (may still be starting)"
    fi

    if [[ "$ALL_HEALTHY" == true ]]; then
        log_success "All services verified"
    else
        log_warning "Some services need attention. Check logs with: make logs"
    fi
}

# =============================================================================
# STEP 8: Final Instructions
# =============================================================================
show_final_instructions() {
    log_step "STEP 8/8: Setup Complete!"

    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}║                    🎉 SETUP COMPLETE! 🎉                         ║${NC}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${BOLD}📱 Access Points:${NC}"
    echo -e "  ${CYAN}Web Application:${NC}     http://localhost:8000"
    echo -e "  ${CYAN}API Documentation:${NC}   http://localhost:8000/docs"
    echo -e "  ${CYAN}MinIO Console:${NC}       http://localhost:9001"
    echo -e "  ${CYAN}Grafana Dashboard:${NC}   http://localhost:3000 (admin/admin123)"
    echo -e "  ${CYAN}Prometheus:${NC}          http://localhost:9090"
    echo ""

    echo -e "${BOLD}🔧 Useful Commands:${NC}"
    echo -e "  ${YELLOW}make logs${NC}          - View all service logs"
    echo -e "  ${YELLOW}make stop${NC}          - Stop all services"
    echo -e "  ${YELLOW}make restart${NC}       - Restart all services"
    echo -e "  ${YELLOW}make test${NC}          - Run tests"
    echo -e "  ${YELLOW}make backup${NC}        - Backup database"
    echo -e "  ${YELLOW}make train${NC}         - Run training pipeline"
    echo ""

    echo -e "${BOLD}📁 Important Files:${NC}"
    echo -e "  ${CYAN}.env${NC}              - Environment configuration"
    echo -e "  ${CYAN}docker/${NC}          - Docker configurations"
    echo -e "  ${CYAN}docs/${NC}            - Documentation"
    echo -e "  ${CYAN}data/${NC}            - Data directory (created on first run)"
    echo ""

    echo -e "${BOLD}🚀 Quick Start:${NC}"
    echo -e "  1. Open ${CYAN}http://localhost:8000${NC} in your browser"
    echo -e "  2. Upload a medical document image"
    echo -e "  3. Review OCR results and apply corrections"
    echo -e "  4. Watch accuracy improve over time!"
    echo ""

    echo -e "${BOLD}📖 Documentation:${NC}"
    echo -e "  - Full docs: ${CYAN}docs/docs/${NC}"
    echo -e "  - API ref:  ${CYAN}http://localhost:8000/redoc${NC}"
    echo -e "  - README:   ${CYAN}README.md${NC}"
    echo ""

    echo -e "${YELLOW}⚠️  Note:${NC} First-time model download may take 5-10 minutes"
    echo -e "${YELLOW}⚠️  Note:${NC} GPU support requires NVIDIA Docker runtime"
    echo ""

    echo -e "${GREEN}Happy OCR-ing! 🩺${NC}"
    echo ""
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

cleanup_on_error() {
    echo ""
    log_error "Setup failed! Cleaning up..."
    docker-compose -f docker/docker-compose.full.yml down >/dev/null 2>&1 || true
    exit 1
}

trap cleanup_on_error ERR

# =============================================================================
# MAIN EXECUTION
# =============================================================================
main() {
    show_banner

    log_info "Starting Medical OCR setup..."
    log_info "This may take 10-15 minutes depending on your system"
    echo ""

    check_requirements
    install_docker
    check_ports
    setup_environment
    build_and_start
    setup_database
    verify_installation
    show_final_instructions
}

# Run main function
main "$@"
