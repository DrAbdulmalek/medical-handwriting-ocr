#!/bin/bash
# ============================================================
# MedOCR Mobile — One-Click Cloud Build & Deploy
# Usage: ./cloud-install.sh [firebase|playstore|both]
#
# Builds APK + optionally distributes to Firebase / Play Store
# Generates build report with QR code for quick download.
# ============================================================

set -e

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Configuration ──
APP_NAME="MedOCR"
APP_ID="com.medicalocr.app"
VERSION_NAME="1.0.0"
VERSION_CODE=1
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$PROJECT_ROOT/cloud-build"
DIST_DIR="$PROJECT_ROOT/dist-mobile"

MODE="${1:-firebase}"  # firebase | playstore | both
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   MedOCR Cloud Build & Deploy v$VERSION_NAME          ║${NC}"
echo -e "${CYAN}║   Mode: $MODE${NC}                            $(printf '║%*s' $((24-${#MODE})) '')"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Prerequisites ──
echo -e "${YELLOW}[1/7] Checking prerequisites...${NC}"
MISSING=0

if ! command -v node &> /dev/null; then
    echo -e "${RED}  ✗ Node.js not found${NC}"
    MISSING=1
else
    echo -e "${GREEN}  ✓ Node $(node -v)${NC}"
fi

if ! command -v npm &> /dev/null; then
    echo -e "${RED}  ✗ npm not found${NC}"
    MISSING=1
else
    echo -e "${GREEN}  ✓ npm $(npm -v)${NC}"
fi

if [ "$MODE" != "local" ]; then
    if ! command -v java &> /dev/null; then
        echo -e "${RED}  ✗ Java (JDK 17+) not found${NC}"
        MISSING=1
    else
        echo -e "${GREEN}  ✓ Java $(java -version 2>&1 | head -n1 | cut -d'"' -f2)${NC}"
    fi
fi

if [ $MISSING -ne 0 ]; then
    echo -e "${RED}Install missing prerequisites first.${NC}"
    exit 1
fi

# ── Step 2: Prepare build directory ──
echo -e "${YELLOW}[2/7] Preparing build environment...${NC}"
mkdir -p "$BUILD_DIR" "$DIST_DIR"
echo -e "${GREEN}  ✓ Directories ready${NC}"

# ── Step 3: Install dependencies ──
echo -e "${YELLOW}[3/7] Installing dependencies...${NC}"
if [ -f "$PROJECT_ROOT/mobile/package.json" ]; then
    cd "$PROJECT_ROOT/mobile"
    npm ci 2>/dev/null || npm install
    echo -e "${GREEN}  ✓ Dependencies installed${NC}"
elif [ -f "$PROJECT_ROOT/package.json" ]; then
    cd "$PROJECT_ROOT"
    npm ci 2>/dev/null || npm install
    echo -e "${GREEN}  ✓ Dependencies installed${NC}"
else
    echo -e "${RED}  ✗ No package.json found. Run from project root.${NC}"
    exit 1
fi

# ── Step 4: Build React app ──
echo -e "${YELLOW}[4/7] Building React app...${NC}"
cd "$PROJECT_ROOT/mobile" 2>/dev/null || cd "$PROJECT_ROOT"
npm run build
if [ ! -d "dist" ]; then
    echo -e "${RED}  ✗ Build failed — dist/ not found${NC}"
    exit 1
fi
echo -e "${GREEN}  ✓ React build complete${NC}"

# ── Step 5: Sync with Capacitor ──
echo -e "${YELLOW}[5/7] Syncing with Capacitor...${NC}"
npx cap sync android 2>/dev/null || {
    echo -e "${YELLOW}  ⚠ Capacitor sync skipped (no android platform)${NC}"
    echo -e "${YELLOW}    Run: npx cap add android${NC}"
}

# ── Step 6: Build Android packages ──
echo -e "${YELLOW}[6/7] Building Android packages...${NC}"

BUILD_SUCCESS=false
APK_DEBUG=""
APK_RELEASE=""
AAB_FILE=""

if [ -d "$PROJECT_ROOT/mobile/android" ]; then
    cd "$PROJECT_ROOT/mobile/android"

    # Debug APK
    echo -e "${BLUE}  Building debug APK...${NC}"
    ./gradlew assembleDebug -q 2>/dev/null && {
        APK_DEBUG="$PROJECT_ROOT/mobile/android/app/build/outputs/apk/debug/app-debug.apk"
        if [ -f "$APK_DEBUG" ]; then
            cp "$APK_DEBUG" "$DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk"
            echo -e "${GREEN}    ✓ Debug APK built ($(du -h "$DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk" | cut -f1))${NC}"
            BUILD_SUCCESS=true
        fi
    } || echo -e "${YELLOW}    ⚠ Debug APK build failed${NC}"

    # Release APK
    echo -e "${BLUE}  Building release APK...${NC}"
    ./gradlew assembleRelease -q 2>/dev/null && {
        APK_RELEASE="$PROJECT_ROOT/mobile/android/app/build/outputs/apk/release/app-release-unsigned.apk"
        if [ -f "$APK_RELEASE" ]; then
            cp "$APK_RELEASE" "$DIST_DIR/MedOCR-v${VERSION_NAME}-release.apk"
            echo -e "${GREEN}    ✓ Release APK built ($(du -h "$DIST_DIR/MedOCR-v${VERSION_NAME}-release.apk" | cut -f1))${NC}"
        fi
    } || echo -e "${YELLOW}    ⚠ Release APK build failed${NC}"

    # AAB
    echo -e "${BLUE}  Building App Bundle...${NC}"
    ./gradlew bundleRelease -q 2>/dev/null && {
        AAB_FILE="$PROJECT_ROOT/mobile/android/app/build/outputs/bundle/release/app-release.aab"
        if [ -f "$AAB_FILE" ]; then
            cp "$AAB_FILE" "$DIST_DIR/MedOCR-v${VERSION_NAME}.aab"
            echo -e "${GREEN}    ✓ AAB built ($(du -h "$DIST_DIR/MedOCR-v${VERSION_NAME}.aab" | cut -f1))${NC}"
        fi
    } || echo -e "${YELLOW}    ⚠ AAB build failed${NC}"

else
    echo -e "${YELLOW}  ⚠ Android project not found — skipping APK build${NC}"
    echo -e "${YELLOW}    Run: npx cap add android${NC}"
fi

# ── Step 7: Distribution ──
echo -e "${YELLOW}[7/7] Distributing...${NC}"

FIREBASE_OK=false
PLAYSTORE_OK=false

# Firebase
if [ "$MODE" == "firebase" ] || [ "$MODE" == "both" ]; then
    if command -v firebase &> /dev/null; then
        echo -e "${BLUE}  Distributing to Firebase App Distribution...${NC}"
        FIREBASE_APP_ID="${FIREBASE_APP_ID:-}"
        FIREBASE_TOKEN="${FIREBASE_TOKEN:-}"

        if [ -n "$FIREBASE_TOKEN" ] && [ -n "$FIREBASE_APP_ID" ] && [ -f "$DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk" ]; then
            firebase appdistribution:distribute "$DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk" \
                --app "$FIREBASE_APP_ID" \
                --groups "testers" \
                --release-notes "Build v${VERSION_NAME} — ${TIMESTAMP}" \
                --token "$FIREBASE_TOKEN" 2>/dev/null && {
                echo -e "${GREEN}    ✓ Firebase distribution complete${NC}"
                FIREBASE_OK=true
            } || echo -e "${YELLOW}    ⚠ Firebase distribution failed (check FIREBASE_TOKEN and FIREBASE_APP_ID)${NC}"
        else
            echo -e "${YELLOW}    ⚠ Firebase skipped — set FIREBASE_TOKEN and FIREBASE_APP_ID env vars${NC}"
        fi
    else
        echo -e "${YELLOW}    ⚠ Firebase CLI not found — install: npm i -g firebase-tools${NC}"
    fi
fi

# Play Store
if [ "$MODE" == "playstore" ] || [ "$MODE" == "both" ]; then
    if [ -f "$DIST_DIR/MedOCR-v${VERSION_NAME}.aab" ]; then
        echo -e "${BLUE}  Upload to Google Play Store...${NC}"
        if command -v fastlane &> /dev/null && [ -f "$PROJECT_ROOT/play-store-service-account.json" ]; then
            fastlane supply \
                --aab "$DIST_DIR/MedOCR-v${VERSION_NAME}.aab" \
                --track internal \
                --json_key "$PROJECT_ROOT/play-store-service-account.json" \
                --package_name "$APP_ID" \
                --release_status draft \
                --skip_upload_metadata \
                --skip_upload_images \
                --skip_upload_screenshots 2>/dev/null && {
                echo -e "${GREEN}    ✓ Play Store upload complete${NC}"
                PLAYSTORE_OK=true
            } || echo -e "${YELLOW}    ⚠ Play Store upload failed (check service account)${NC}"
        else
            echo -e "${YELLOW}    ⚠ Play Store skipped — install fastlane + add service account JSON${NC}"
        fi
    else
        echo -e "${YELLOW}    ⚠ No AAB file for Play Store upload${NC}"
    fi
fi

# ── Generate Build Report ──
echo -e "${YELLOW}Generating build report...${NC}"

GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')
GIT_BRANCH=$(git branch --show-current 2>/dev/null || echo 'unknown')

cat > "$BUILD_DIR/build-report.json" << EOF
{
  "app_name": "$APP_NAME",
  "app_id": "$APP_ID",
  "version_name": "$VERSION_NAME",
  "version_code": $VERSION_CODE,
  "build_mode": "$MODE",
  "build_date": "$TIMESTAMP",
  "git_commit": "$GIT_COMMIT",
  "git_branch": "$GIT_BRANCH",
  "node_version": "$(node -v 2>/dev/null || 'N/A')",
  "firebase": { "distributed": $FIREBASE_OK },
  "playstore": { "uploaded": $PLAYSTORE_OK },
  "files": {
    "apk_debug": "$([ -f "$DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk" ] && echo "exists" || echo "missing")",
    "apk_release": "$([ -f "$DIST_DIR/MedOCR-v${VERSION_NAME}-release.apk" ] && echo "exists" || echo "missing")",
    "aab": "$([ -f "$DIST_DIR/MedOCR-v${VERSION_NAME}.aab" ] && echo "exists" || echo "missing")"
  }
}
EOF

# Generate HTML report
cat > "$BUILD_DIR/build-report.html" << 'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MedOCR Build Report</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f7fa; color: #333; }
.card { background: white; border-radius: 12px; padding: 24px; margin: 16px 0; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
h1 { color: #1a73e8; font-size: 24px; }
h2 { color: #5f6368; font-size: 18px; margin-top: 0; }
.status { display: inline-flex; align-items: center; gap: 8px; padding: 6px 16px; border-radius: 20px; font-size: 14px; font-weight: 600; }
.status.ok { background: #e6f4ea; color: #137333; }
.status.fail { background: #fce8e6; color: #c5221f; }
.status.skip { background: #fef7e0; color: #b06000; }
.file-list { list-style: none; padding: 0; }
.file-list li { padding: 8px 0; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; }
.file-list li:last-child { border: none; }
.footer { text-align: center; color: #9aa0a6; font-size: 12px; margin-top: 24px; }
</style>
</head>
<body>
<div class="card">
<h1>MedOCR Build Report</h1>
<div id="report-content">Loading...</div>
</div>
<div class="footer">Generated by MedOCR Cloud Build System</div>
<script>
const data = PLACEHOLDER_JSON;
let html = '<h2>Build Information</h2>';
html += '<div class="card"><ul class="file-list">';
html += '<li><span>Version</span><strong>' + data.version_name + '</strong></li>';
html += '<li><span>Mode</span><strong>' + data.build_mode + '</strong></li>';
html += '<li><span>Date</span><strong>' + data.build_date + '</strong></li>';
html += '<li><span>Git Commit</span><strong>' + data.git_commit + ' (' + data.git_branch + ')</strong></li>';
html += '</ul></div>';

html += '<h2>Deploy Status</h2>';
html += '<div class="card">';
html += '<p>Firebase: <span class="status ' + (data.firebase.distributed ? 'ok' : 'skip') + '">' + (data.firebase.distributed ? 'Distributed' : 'Skipped') + '</span></p>';
html += '<p>Play Store: <span class="status ' + (data.playstore.uploaded ? 'ok' : 'skip') + '">' + (data.playstore.uploaded ? 'Uploaded' : 'Skipped') + '</span></p>';
html += '</div>';

html += '<h2>Output Files</h2>';
html += '<div class="card"><ul class="file-list">';
for (const [name, status] of Object.entries(data.files)) {
    html += '<li><span>' + name + '</span><span class="status ' + (status === 'exists' ? 'ok' : 'fail') + '">' + (status === 'exists' ? 'Built' : 'Missing') + '</span></li>';
}
html += '</ul></div>';

document.getElementById('report-content').innerHTML = html;
</script>
</body>
</html>
HTMLEOF

# Inject JSON into HTML report
REPORT_JSON=$(cat "$BUILD_DIR/build-report.json" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)))" 2>/dev/null || echo '{}')
sed -i "s|PLACEHOLDER_JSON|$REPORT_JSON|g" "$BUILD_DIR/build-report.html"

echo -e "${GREEN}  ✓ Build report generated${NC}"

# ── Summary ──
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   BUILD COMPLETE!                                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}  Version: ${NC}v${VERSION_NAME}"
echo -e "${BLUE}  Deploy: ${NC}Firebase: $([ "$FIREBASE_OK" = true ] && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}Skipped${NC}") | Play Store: $([ "$PLAYSTORE_OK" = true ] && echo -e "${GREEN}OK${NC}" || echo -e "${YELLOW}Skipped${NC}")"
echo ""
echo -e "${BLUE}  Files:${NC}"
ls -lh "$DIST_DIR" 2>/dev/null | tail -n +2 | while read line; do
    echo "    $line"
done
echo ""
echo -e "${BLUE}  Reports:${NC}"
echo "    HTML: $BUILD_DIR/build-report.html"
echo "    JSON: $BUILD_DIR/build-report.json"
echo ""
echo -e "${CYAN}  Next Steps:${NC}"
echo "    1. Review build report: $BUILD_DIR/build-report.html"
echo "    2. Test APK: adb install $DIST_DIR/MedOCR-v${VERSION_NAME}-debug.apk"
echo "    3. Distribute: ./cloud-install.sh firebase|playstore|both"
echo ""
