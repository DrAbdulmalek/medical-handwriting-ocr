#!/bin/bash
# ============================================================
# MedOCR Mobile — Firebase App Distribution
# Usage: ./firebase-distribute.sh [debug|release] [tester-group]
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BUILD_TYPE="${1:-debug}"
TESTER_GROUP="${2:-testers}"  # Default tester group
DIST_DIR="$(cd "$(dirname "$0")" && pwd)/dist-mobile"

# Check Firebase CLI
if ! command -v firebase &> /dev/null; then
    echo -e "${YELLOW}⚠ Firebase CLI not found. Installing...${NC}"
    npm install -g firebase-tools
fi

# Check login
if ! firebase projects:list &> /dev/null; then
    echo -e "${YELLOW}⚠ Not logged in to Firebase${NC}"
    echo -e "${BLUE}  Run: firebase login${NC}"
    exit 1
fi

# Find APK/AAB
if [ "$BUILD_TYPE" == "debug" ]; then
    FILE=$(ls "$DIST_DIR"/*debug.apk 2>/dev/null | head -n1)
    RELEASE_NOTES="Debug build for internal testing"
elif [ "$BUILD_TYPE" == "release" ]; then
    FILE=$(ls "$DIST_DIR"/*release.apk 2>/dev/null | head -n1)
    RELEASE_NOTES="Release candidate for testing"
else
    FILE=$(ls "$DIST_DIR"/*.aab 2>/dev/null | head -n1)
    RELEASE_NOTES="App Bundle for Google Play"
fi

if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo -e "${RED}✗ No $BUILD_TYPE file found in $DIST_DIR${NC}"
    echo -e "${YELLOW}  Run: ./build.sh $BUILD_TYPE${NC}"
    exit 1
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Firebase App Distribution${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${BLUE}File:${NC} $(basename "$FILE")"
echo -e "${BLUE}Size:${NC} $(du -h "$FILE" | cut -f1)"
echo -e "${BLUE}Testers:${NC} $TESTER_GROUP"
echo ""

# Get app ID from google-services.json (if exists)
APP_ID=""
if [ -f "android/app/google-services.json" ]; then
    APP_ID=$(grep -o '"mobilesdk_app_id": "[^"]*"' android/app/google-services.json | head -n1 | cut -d'"' -f4)
fi

if [ -z "$APP_ID" ]; then
    echo -e "${YELLOW}⚠ Firebase app ID not found${NC}"
    echo -e "${BLUE}  1. Go to https://console.firebase.google.com${NC}"
    echo -e "${BLUE}  2. Create project / add Android app${NC}"
    echo -e "${BLUE}  3. Download google-services.json to android/app/${NC}"
    echo ""
    read -p "Enter Firebase App ID (1:1234567890:android:abc123): " APP_ID
fi

echo -e "${YELLOW}Uploading to Firebase...${NC}"

firebase appdistribution:distribute "$FILE" \
    --app "$APP_ID" \
    --groups "$TESTER_GROUP" \
    --release-notes "$RELEASE_NOTES" \
    --testers-file "testers.txt" 2>/dev/null || \
firebase appdistribution:distribute "$FILE" \
    --app "$APP_ID" \
    --groups "$TESTER_GROUP" \
    --release-notes "$RELEASE_NOTES"

echo ""
echo -e "${GREEN}✓ Distribution complete!${NC}"
echo -e "${BLUE}  Testers will receive email with download link${NC}"
echo -e "${BLUE}  Check: https://console.firebase.google.com → App Distribution${NC}"
