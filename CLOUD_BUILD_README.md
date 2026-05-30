# ☁️ MedOCR — Cloud Build System

## 🎯 نظرة عامة

نظام بناء سحابي كامل لبناء تطبيق MedOCR Mobile وتوزيعه تلقائياً. يدعم ثلاث طرق للبناء: محلي، Docker، وGitHub Actions CI/CD.

---

## 🚀 الطرق الثلاث للبناء

### 1. One-Click Local (أسرع)

```bash
# بناء فقط (بدون توزيع)
./cloud-install.sh local

# بناء + توزيع لـ Firebase
./cloud-install.sh firebase

# بناء + توزيع لـ Google Play
./cloud-install.sh playstore

# بناء + توزيع للاثنين معاً
./cloud-install.sh both
```

**المتطلبات:**
- Node.js 18+
- Java JDK 17+
- Android SDK (API 34)
- Capacitor CLI

### 2. Docker Build (معزول وموثوق)

```bash
# بناء في container
docker build -t medocr-builder -f Dockerfile.mobile .

# تشغيل البناء
docker run -v $(pwd)/dist-mobile:/app/dist-mobile medocr-builder

# أو باستخدام docker-compose
docker-compose -f docker-compose.mobile.yml up medocr-builder
```

### 3. GitHub Actions CI/CD (تلقائي)

```bash
# Push لـ main أو develop → يبني تلقائياً
git push origin main

# على كل push:
# 1. بناء React → Capacitor sync → APK debug + release + AAB
# 2. توزيع لـ Firebase App Distribution
# 3. رفع تقارير البناء كـ artifact
```

---

## 📦 مخرجات البناء

```
dist-mobile/
├── MedOCR-v1.0.0-debug.apk      ← للاختبار (6 MB)
├── MedOCR-v1.0.0-release.apk    ← للتوزيع المباشر (5 MB)
├── MedOCR-v1.0.0.aab            ← لـ Google Play (4 MB)
└── build-info.json              ← معلومات البناء

cloud-build/
├── build-report.html            ← تقرير بصري
└── build-report.json            ← تقرير JSON
```

---

## 🔧 إعداد المتغيرات البيئية

### Firebase

```bash
# 1. تثبيت Firebase CLI
npm install -g firebase-tools

# 2. تسجيل الدخول
firebase login:ci

# 3. نسخ التوكن
export FIREBASE_TOKEN="1//0d..."

# 4. إعداد App ID من Firebase Console
export FIREBASE_APP_ID="1:123456789:android:abc123"
```

### Google Play Store

```bash
# 1. إنشاء Service Account
# Play Console → Setup → API Access → Create Service Account
# 2. تحميل مفتاح JSON

# 3. في GitHub Secrets:
# PLAY_STORE_SERVICE_ACCOUNT_JSON = محتوى ملف JSON
```

### GitHub Secrets المطلوبة

| Secret | الوصف | مطلوب |
|--------|-------|-------|
| `FIREBASE_TOKEN` | Firebase CLI token | نعم (للتوزيع) |
| `FIREBASE_APP_ID` | Firebase App ID | نعم (للتوزيع) |
| `PLAY_STORE_SERVICE_ACCOUNT_JSON` | مفتاح Service Account | نعم (للـ Play Store) |

---

## 📊 سير عمل التطوير

```
المطور يكتب كود
       │
       ▼
git push origin main
       │
       ▼
GitHub Actions يبدأ تلقائياً
       │
       ├─→ بناء React (npm run build)
       ├─→ Capacitor sync (npx cap sync android)
       ├─→ APK debug + release + AAB
       ├─→ Firebase distribution
       └─→ تقارير البناء
       │
       ▼
المختبرون يستلمون email من Firebase
       │
       ▼
تحميل APK → اختبار → ملاحظات
       │
       ▼
إصدار رسمي:
git tag v1.1.0 && git push origin v1.1.0
       │
       ▼
يُرفع لـ Google Play Store تلقائياً
```

---

## 🛠️ Troubleshooting

| المشكلة | الحل |
|---------|------|
| `Node.js not found` | `nvm install 20` |
| `Java not found` | `sudo apt install openjdk-17-jdk` |
| `Android SDK not found` | `sdkmanager --install "platforms;android-34"` |
| `Firebase token expired` | `firebase login:ci` |
| `Play Store upload failed` | تحقق من صلاحيات Service Account |
| `Capacitor sync failed` | `npx cap add android` |
| `APK too large` | فعّل `minifyEnabled` و `shrinkResources` في build.gradle |

---

## 📄 الملفات المدرجة في هذا النظام

| الملف | الوصف |
|-------|-------|
| `cloud-install.sh` | سكريبت البناء بنقرة واحدة |
| `Dockerfile.mobile` | بيئة بناء معزولة في Docker |
| `docker-compose.mobile.yml` | تنسيق Docker Compose |
| `.github/workflows/cloud-build.yml` | CI/CD pipeline |
| `build.sh` | سكريبت البناء التفصيلي |
| `firebase-distribute.sh` | توزيع لـ Firebase |
| `play-store-upload.sh` | رفع لـ Google Play |
| `Fastfile` | Fastlane lanes |
| `CLOUD_BUILD_README.md` | هذا الملف |

---

## 📜 الرخصة

MIT License — نفس رخصة المشروع الأصلي.
