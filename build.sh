#!/bin/bash
# ============================================================
# MedOCR Mobile — Automated Build Script (APK + AAB)
# Usage: ./build.sh [debug|release|aab]
# ============================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="MedOCR"
APP_ID="com.medicalocr.app"
VERSION_NAME="1.0.0"
VERSION_CODE=1
KEYSTORE_FILE="medocr.keystore"
KEY_ALIAS="medocr"
BUILD_TYPE="${1:-debug}"  # Default to debug

# Paths
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
ANDROID_DIR="$PROJECT_ROOT/android"
DIST_DIR="$PROJECT_ROOT/dist-mobile"
APK_OUTPUT="$ANDROID_DIR/app/build/outputs/apk"
AAB_OUTPUT="$ANDROID_DIR/app/build/outputs/bundle"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  MedOCR Mobile Builder v$VERSION_NAME${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Step 1: Check prerequisites
echo -e "${YELLOW}[1/8] Checking prerequisites...${NC}"

if ! command -v node &> /dev/null; then
    echo -e "${RED}✗ Node.js not found. Install from https://nodejs.org${NC}"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo -e "${RED}✗ npm not found${NC}"
    exit 1
fi

if ! command -v java &> /dev/null; then
    echo -e "${RED}✗ Java not found. Install JDK 17+${NC}"
    exit 1
fi

NODE_VERSION=$(node -v | cut -d'v' -f2 | cut -d'.' -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
    echo -e "${RED}✗ Node.js 18+ required. Found: $(node -v)${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Node $(node -v)${NC}"
echo -e "${GREEN}✓ npm $(npm -v)${NC}"
echo -e "${GREEN}✓ Java $(java -version 2>&1 | head -n1 | cut -d'"' -f2)${NC}"

# Step 2: Install dependencies
echo -e "${YELLOW}[2/8] Installing dependencies...${NC}"
npm ci  # Clean install from package-lock.json
echo -e "${GREEN}✓ Dependencies installed${NC}"

# Step 3: Run tests
echo -e "${YELLOW}[3/8] Running tests...${NC}"
npm run test -- --run 2>/dev/null || echo -e "${YELLOW}⚠ Tests skipped or failed${NC}"

# Step 4: Build React app
echo -e "${YELLOW}[4/8] Building React app...${NC}"
npm run build
if [ ! -d "$PROJECT_ROOT/dist" ]; then
    echo -e "${RED}✗ Build failed - dist/ not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ React build complete${NC}"

# Step 5: Sync with Capacitor
echo -e "${YELLOW}[5/8] Syncing with Capacitor...${NC}"
npx cap sync android
if [ ! -d "$ANDROID_DIR" ]; then
    echo -e "${RED}✗ Android directory not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Capacitor sync complete${NC}"

# Step 6: Check keystore for release builds
if [ "$BUILD_TYPE" == "release" ] || [ "$BUILD_TYPE" == "aab" ]; then
    echo -e "${YELLOW}[6/8] Checking keystore...${NC}"
    if [ ! -f "$PROJECT_ROOT/$KEYSTORE_FILE" ]; then
        echo -e "${YELLOW}⚠ Keystore not found. Creating new keystore...${NC}"
        echo -e "${YELLOW}⚠ You will be prompted for passwords${NC}"
        keytool -genkey -v             -keystore "$PROJECT_ROOT/$KEYSTORE_FILE"             -alias "$KEY_ALIAS"             -keyalg RSA             -keysize 2048             -validity 10000             -dname "CN=MedOCR, OU=Medical, O=DrAbdulmalek, L=Riyadh, ST=Riyadh, C=SA"
        echo -e "${GREEN}✓ Keystore created: $KEYSTORE_FILE${NC}"
    else
        echo -e "${GREEN}✓ Keystore found${NC}"
    fi
fi

# Step 7: Build Android package
echo -e "${YELLOW}[7/8] Building Android package ($BUILD_TYPE)...${NC}"

cd "$ANDROID_DIR"

if [ "$BUILD_TYPE" == "debug" ]; then
    ./gradlew assembleDebug

    # Copy APK to dist
    mkdir -p "$DIST_DIR"
    cp "$APK_OUTPUT/debug/app-debug.apk" "$DIST_DIR/MedOCR-v$VERSION_NAME-debug.apk"

    echo -e "${GREEN}✓ Debug APK built${NC}"
    echo -e "${BLUE}  → $DIST_DIR/MedOCR-v$VERSION_NAME-debug.apk${NC}"

elif [ "$BUILD_TYPE" == "release" ]; then
    # Build signed release APK
    ./gradlew assembleRelease         -Pandroid.injected.signing.store.file="$PROJECT_ROOT/$KEYSTORE_FILE"         -Pandroid.injected.signing.store.password="$KEYSTORE_PASSWORD"         -Pandroid.injected.signing.key.alias="$KEY_ALIAS"         -Pandroid.injected.signing.key.password="$KEY_PASSWORD" 2>/dev/null || {
        echo -e "${YELLOW}⚠ Automatic signing failed. Building unsigned APK...${NC}"
        ./gradlew assembleRelease

        # Manual signing
        UNSIGNED_APK="$APK_OUTPUT/release/app-release-unsigned.apk"
        SIGNED_APK="$DIST_DIR/MedOCR-v$VERSION_NAME-release.apk"

        mkdir -p "$DIST_DIR"

        echo -e "${YELLOW}⚠ Please sign manually:${NC}"
        echo -e "${BLUE}  jarsigner -verbose -sigalg SHA1withRSA -digestalg SHA1 \"${NC}"
        echo -e "${BLUE}    -keystore $KEYSTORE_FILE $UNSIGNED_APK $KEY_ALIAS${NC}"

        cp "$UNSIGNED_APK" "$DIST_DIR/MedOCR-v$VERSION_NAME-unsigned.apk"
        echo -e "${GREEN}✓ Unsigned APK: $DIST_DIR/MedOCR-v$VERSION_NAME-unsigned.apk${NC}"
        exit 0
    }

    mkdir -p "$DIST_DIR"
    cp "$APK_OUTPUT/release/app-release.apk" "$DIST_DIR/MedOCR-v$VERSION_NAME-release.apk"

    echo -e "${GREEN}✓ Release APK built${NC}"
    echo -e "${BLUE}  → $DIST_DIR/MedOCR-v$VERSION_NAME-release.apk${NC}"

elif [ "$BUILD_TYPE" == "aab" ]; then
    # Build App Bundle for Google Play
    ./gradlew bundleRelease         -Pandroid.injected.signing.store.file="$PROJECT_ROOT/$KEYSTORE_FILE"         -Pandroid.injected.signing.store.password="$KEYSTORE_PASSWORD"         -Pandroid.injected.signing.key.alias="$KEY_ALIAS"         -Pandroid.injected.signing.key.password="$KEY_PASSWORD" 2>/dev/null || {
        echo -e "${YELLOW}⚠ Automatic signing failed. Building unsigned AAB...${NC}"
        ./gradlew bundleRelease
    }

    mkdir -p "$DIST_DIR"
    cp "$AAB_OUTPUT/release/app-release.aab" "$DIST_DIR/MedOCR-v$VERSION_NAME.aab" 2>/dev/null ||     cp "$AAB_OUTPUT/release/app-release.aab" "$DIST_DIR/MedOCR-v$VERSION_NAME-unsigned.aab"

    echo -e "${GREEN}✓ App Bundle built${NC}"
    echo -e "${BLUE}  → $DIST_DIR/MedOCR-v$VERSION_NAME.aab${NC}"
    echo -e "${YELLOW}  Upload this to Google Play Console${NC}"
fi

# Step 8: Generate build info
echo -e "${YELLOW}[8/8] Generating build info...${NC}"

cat > "$DIST_DIR/build-info.json" << EOF
{
  "app_name": "$APP_NAME",
  "app_id": "$APP_ID",
  "version_name": "$VERSION_NAME",
  "version_code": $VERSION_CODE,
  "build_type": "$BUILD_TYPE",
  "build_date": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "git_commit": "$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')",
  "node_version": "$(node -v)",
  "capacitor_version": "$(npx cap --version 2>/dev/null || echo 'unknown')",
  "files": {
    "apk_debug": "MedOCR-v$VERSION_NAME-debug.apk",
    "apk_release": "MedOCR-v$VERSION_NAME-release.apk",
    "aab": "MedOCR-v$VERSION_NAME.aab"
  }
}
EOF

echo -e "${GREEN}✓ Build info generated${NC}"

# Summary
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Build Complete! 🎉${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Output Directory:${NC} $DIST_DIR"
echo ""
echo -e "${BLUE}Files:${NC}"
ls -lh "$DIST_DIR" 2>/dev/null || echo "  (check $DIST_DIR)"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"

if [ "$BUILD_TYPE" == "debug" ]; then
    echo -e "  1. Install on device: ${BLUE}adb install $DIST_DIR/MedOCR-v$VERSION_NAME-debug.apk${NC}"
    echo -e "  2. Or share APK via WhatsApp/Email"
    echo -e "  3. For Firebase: ${BLUE}./firebase-distribute.sh debug${NC}"
elif [ "$BUILD_TYPE" == "release" ]; then
    echo -e "  1. Test APK on device"
    echo -e "  2. Upload to Google Play: ${BLUE}./play-store-upload.sh${NC}"
    echo -e "  3. Or distribute via Firebase: ${BLUE}./firebase-distribute.sh release${NC}"
elif [ "$BUILD_TYPE" == "aab" ]; then
    echo -e "  1. Go to ${BLUE}https://play.google.com/console${NC}"
    echo -e "  2. Create new release → Upload AAB"
    echo -e "  3. Or use: ${BLUE}./play-store-upload.sh${NC}"
fi

echo ""
echo -e "${YELLOW}Build Info:${NC} $DIST_DIR/build-info.json"
echo ""
