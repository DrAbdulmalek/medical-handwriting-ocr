.PHONY: help setup up down build test lint train export migrate clean logs \
        shell db shell-celery test-integration test-slow test-medical coverage

# ─────────────────────────────────────────────────────────────
# Default
# ─────────────────────────────────────────────────────────────

help: ## Show this help message
	@echo "Medical Handwriting OCR — Development Commands"
	@echo "==============================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ─────────────────────────────────────────────────────────────
# Setup & Infrastructure
# ─────────────────────────────────────────────────────────────

setup: ## Run initial setup script (Docker, dirs, .env)
	@echo ">>> Running initial setup..."
	bash setup.sh

up: ## Start all services (docker-compose up)
	@echo ">>> Starting services..."
	cd docker && docker compose up -d
	@echo ">>> Services started. API: http://localhost:8000  MinIO: http://localhost:9001"

up-full: ## Start full stack with Celery, Redis, Nginx
	@echo ">>> Starting full stack..."
	cd docker && docker compose -f docker-compose.full.yml up -d
	@echo ">>> Full stack started. API: http://localhost  MinIO: http://localhost:9001"

down: ## Stop all services
	@echo ">>> Stopping services..."
	cd docker && docker compose down
	@echo ">>> Services stopped."

down-full: ## Stop full stack
	cd docker && docker compose -f docker-compose.full.yml down

build: ## Rebuild Docker images
	cd docker && docker compose build --no-cache

# ─────────────────────────────────────────────────────────────
# Testing
# ─────────────────────────────────────────────────────────────

test: ## Run unit tests (skip slow & integration)
	@echo ">>> Running unit tests..."
	cd backend && python -m pytest ../tests/ \
		-m "not integration and not slow" \
		--tb=short \
		-v

test-integration: ## Run integration tests only
	@echo ">>> Running integration tests..."
	cd backend && python -m pytest ../tests/ \
		-m "integration" \
		--tb=short \
		-v

test-slow: ## Run slow tests (model downloads, full pipeline)
	@echo ">>> Running slow tests..."
	cd backend && python -m pytest ../tests/ \
		-m "slow" \
		--tb=short \
		-v

test-medical: ## Run medical-specific tests
	@echo ">>> Running medical domain tests..."
	cd backend && python -m pytest ../tests/ \
		-m "medical" \
		--tb=short \
		-v

test-all: ## Run ALL tests (including slow & integration)
	@echo ">>> Running all tests..."
	cd backend && python -m pytest ../tests/ \
		--tb=short \
		-v

coverage: ## Run tests with coverage report
	@echo ">>> Running tests with coverage..."
	cd backend && python -m pytest ../tests/ \
		-m "not integration and not slow" \
		--cov=app \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=60 \
		-v
	@echo ">>> Coverage report: backend/htmlcov/index.html"

# ─────────────────────────────────────────────────────────────
# Code Quality
# ─────────────────────────────────────────────────────────────

lint: ## Run Python linting (flake8 + isort check + black check)
	@echo ">>> Checking import sorting..."
	cd backend && isort --check-only --diff app/
	@echo ">>> Checking code formatting..."
	cd backend && black --check --line-length 88 app/
	@echo ">>> Running flake8..."
	cd backend && flake8 app/ \
		--max-line-length=88 \
		--extend-ignore=E203,W503 \
		--exclude=__pycache__,.cache \
		--statistics
	@echo ">>> All checks passed."

lint-fix: ## Auto-fix linting issues
	@echo ">>> Auto-fixing imports and formatting..."
	cd backend && isort --apply app/
	cd backend && black --line-length 88 app/
	@echo ">>> Done. Run 'make lint' to verify."

typecheck: ## Run mypy type checking (if installed)
	@echo ">>> Running mypy..."
	cd backend && python -m mypy app/ --ignore-missing-imports || true

# ─────────────────────────────────────────────────────────────
# Training & Data
# ─────────────────────────────────────────────────────────────

train: ## Run the fine-tuning pipeline for TrOCR
	@echo ">>> Starting TrOCR fine-tuning..."
	cd backend && python -m training.finetune_trocr \
		--output-dir ./models/latest \
		--epochs 10 \
		--batch-size 8

evaluate: ## Evaluate model performance
	@echo ">>> Evaluating model..."
	cd backend && python -m training.evaluate \
		--model-path ./models/latest

export: ## Export approved corrections as training dataset
	@echo ">>> Exporting dataset from approved corrections..."
	cd backend && python -m training.export_dataset \
		--output ./data/exported_dataset \
		--status gold_standard \
		--format jsonl
	@echo ">>> Dataset exported to ./data/exported_dataset/"

# ─────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────

migrate: ## Run Alembic database migrations
	@echo ">>> Running database migrations..."
	cd backend && alembic upgrade head
	@echo ">>> Migrations complete."

migrate-create: ## Create a new Alembic migration (usage: make migrate-create msg="add users table")
	@echo ">>> Creating migration..."
	cd backend && alembic revision --autogenerate -m "$(msg)"

rollback: ## Rollback last migration
	@echo ">>> Rolling back last migration..."
	cd backend && alembic downgrade -1
	@echo ">>> Rollback complete."

# ─────────────────────────────────────────────────────────────
# Development Utilities
# ─────────────────────────────────────────────────────────────

shell: ## Open Python shell with app context
	cd backend && python -c "from app.config import settings; from app.database import engine; print(f'DB: {settings.DATABASE_URL}'); import code; code.interact(local=locals())"

db: ## Open psql shell connected to database
	cd docker && docker compose exec postgres psql -U ocr_user -d medical_ocr

shell-celery: ## Inspect Celery worker (interactive shell)
	cd docker && docker compose -f docker-compose.full.yml exec celery celery -A app.tasks worker --inspect

logs: ## Tail logs from all services
	cd docker && docker compose logs -f --tail=100

logs-backend: ## Tail backend logs only
	cd docker && docker compose logs -f --tail=100 backend

logs-celery: ## Tail Celery worker logs
	cd docker && docker compose -f docker-compose.full.yml logs -f --tail=100 celery

# ─────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────

clean: ## Remove caches, temp files, and build artifacts
	@echo ">>> Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".DS_Store" -delete 2>/dev/null || true
	rm -rf backend/.cache
	rm -rf backend/htmlcov
	rm -rf backend/.coverage
	rm -rf coverage.xml
	rm -rf uploads/*
	rm -rf crops/*
	@echo ">>> Cleanup complete."

clean-docker: ## Remove all Docker volumes and images for this project
	@echo ">>> WARNING: This will remove ALL project Docker data!"
	@echo -n "Are you sure? [y/N] " && read ans && [ "$$ans" = "y" ] || exit 1
	cd docker && docker compose down -v --rmi local
	@echo ">>> Docker cleanup complete."

# ─────────────────────────────────────────────────────────────
# Environment
# ─────────────────────────────────────────────────────────────

venv: ## Create a Python virtual environment
	@echo ">>> Creating virtual environment..."
	python3.10 -m venv .venv
	@echo ">>> Activate with: source .venv/bin/activate"
	@echo ">>> Then run: pip install -r backend/requirements.txt"

install: ## Install Python dependencies (into venv if active)
	pip install --upgrade pip
	pip install -r backend/requirements.txt
	pip install pytest pytest-cov pytest-timeout pytest-asyncio httpx factory-boy \
		flake8 flake8-bugbear flake8-isort black isort ruff
