#!/bin/bash

echo "Medical Handwriting OCR - Setup Script"
echo "=========================================="

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "Docker not found. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "Docker Compose not found. Please install Docker Compose."
    exit 1
fi

echo "Docker found"

# Create directories
echo "Creating directories..."
mkdir -p uploads crops postgres_data minio_data

# Copy environment file
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cp .env.example .env
fi

# Start infrastructure
echo "Starting PostgreSQL and MinIO..."
cd docker
docker-compose up -d postgres minio

echo "Waiting for services to be ready..."
sleep 10

# Check health
echo "Checking service health..."
docker-compose ps

# Get MinIO console URL
echo ""
echo "MinIO Console: http://localhost:9001"
echo "   Username: minioadmin"
echo "   Password: minioadmin123"

echo ""
echo "Next steps:"
echo "   1. Build backend: cd backend && docker build -t ocr-backend ."
echo "   2. Start full stack: docker-compose up -d"
echo "   3. Open frontend: http://localhost:8000 (or open frontend/index.html directly)"
echo ""
echo "API Documentation will be at: http://localhost:8000/docs"
