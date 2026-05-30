#!/bin/bash
# ============================================================
# MedOCR Mobile — Google Play Store Upload
# Usage: ./play-store-upload.sh [track]
# Tracks: internal, alpha, beta, production
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

TRACK="${1:-internal}"  # Default to internal testing
DIST_DIR="$(cd "$(dirname "$0")" && pwd)/dist-mobile"

# Check Google Play CLI (fastlane supply)
if ! command -v fastlane &> /dev/null; then
    echo -e "${YELLOW}⚠ Fastlane not found. Installing...${NC}"
    sudo gem install fastlane -NV
fi

# Find AAB
AAB_FILE=$(ls "$DIST_DIR"/*.aab 2>/dev/null | head -n1)

if [ -z "$AAB_FILE" ] || [ ! -f "$AAB_FILE" ]; then
    echo -e "${RED}✗ No AAB file found${NC}"
    echo -e "${YELLOW}  Run: ./build.sh aab${NC}"
    exit 1
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Google Play Store Upload${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${BLUE}File:${NC} $(basename "$AAB_FILE")"
echo -e "${BLUE}Track:${NC} $TRACK"
echo -e "${BLUE}Size:${NC} $(du -h "$AAB_FILE" | cut -f1)"
echo ""

# Check service account key
if [ ! -f "play-store-service-account.json" ]; then
    echo -e "${YELLOW}⚠ Service account key not found${NC}"
    echo -e "${BLUE}  1. Go to https://play.google.com/console${NC}"
    echo -e "${BLUE}  2. Setup → API Access → Create Service Account${NC}"
    echo -e "${BLUE}  3. Download JSON key as play-store-service-account.json${NC}"
    echo ""
    read -p "Continue after creating service account? (y/n): " confirm
    if [ "$confirm" != "y" ]; then
        exit 1
    fi
fi

echo -e "${YELLOW}Uploading to Google Play ($TRACK track)...${NC}"

fastlane supply \
    --aab "$AAB_FILE" \
    --track "$TRACK" \
    --json_key "play-store-service-account.json" \
    --package_name "com.medicalocr.app" \
    --release_status draft \
    --skip_upload_metadata \
    --skip_upload_images \
    --skip_upload_screenshots

echo ""
echo -e "${GREEN}✓ Upload complete!${NC}"
echo -e "${BLUE}  Go to https://play.google.com/console${NC}"
echo -e "${BLUE}  Review and publish the release${NC}"
