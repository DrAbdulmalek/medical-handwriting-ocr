# 🚀 MedOCR Mobile — Build & Distribution System

## 📦 ما يُنتج النظام

| النوع | الامتداد | الاستخدام | الحجم |
|-------|----------|-----------|-------|
| **Debug APK** | `.apk` | اختبار داخلي | ~6 MB |
| **Release APK** | `.apk` | توزيع مباشر | ~5 MB |
| **App Bundle** | `.aab` | Google Play Store | ~4 MB |
| **IPA** | `.ipa` | iOS App Store | ~8 MB |

---

## 🏗️ كيف يُبنى التطبيق

```
React TypeScript
     │
     ▼ (Vite Build)
HTML + CSS + JS (dist/)
     │
     ▼ (Capacitor Sync)
Android Project (android/)
     │
     ▼ (Gradle Build)
APK / AAB (Native App)
```

---

## 📥 للمستخدم: كيف ينزل التطبيق

### الطريقة 1: Google Play Store (موصى بها)

```
1. افتح Google Play Store
2. ابحث عن "MedOCR" أو "الماسح الضوئي الطبي"
3. اضغط "تثبيت"
4. افتح التطبيق → أدخل API Key → ابدأ العمل
```

### الطريقة 2: APK مباشر (للاختبار)

```
1. افتح الرابط: https://firebaseappdistribution.googleapis.com/...
2. اضغط "Download APK"
3. في Android: Settings → Security → Allow Unknown Sources
4. افتح APK → تثبيت
5. افتح التطبيق
```

### الطريقة 3: PWA (بدون تثبيت)

```
1. افتح المتصفح على: https://medocr.com/mobile
2. اضغط "Add to Home Screen" (أو ⋮ → Install)
3. أيقونة تظهر على الشاشة الرئيسية
4. يعمل كتطبيق عادي!
```

---

## 🔧 للمطور: سكريبتات البناء

### 1. بناء APK للاختبار (Debug)

```bash
cd mobile
./build.sh debug
```

**النتيجة:**
```
dist-mobile/
├── MedOCR-v1.0.0-debug.apk    ← للتثبيت المباشر
└── build-info.json
```

**التثبيت على جهاز:**
```bash
adb install dist-mobile/MedOCR-v1.0.0-debug.apk
# أو أرسل APK عبر WhatsApp/Email
```

### 2. بناء APK للإنتاج (Release)

```bash
./build.sh release
```

**يتطلب:**
- Keystore (مفتاح توقيع رقمي)
- كلمة مرور للمفتاح

**النتيجة:**
```
dist-mobile/
├── MedOCR-v1.0.0-release.apk   ← موقع رقمياً
└── build-info.json
```

### 3. بناء App Bundle (لـ Google Play)

```bash
./build.sh aab
```

**النتيجة:**
```
dist-mobile/
├── MedOCR-v1.0.0.aab          ← لرفع على Play Console
└── build-info.json
```

---

## 📤 التوزيع

### Firebase App Distribution (الأسهل)

```bash
# 1. اضبط Firebase
npm install -g firebase-tools
firebase login

# 2. أنشئ مشروع في https://console.firebase.google.com
# 3. أضف Android app (package: com.medicalocr.app)
# 4. حمل google-services.json إلى android/app/

# 5. وزّع التطبيق
./firebase-distribute.sh debug testers
```

**النتيجة:**
- المختبرين يستلمون email برابط تحميل
- لا يحتاج Google Play Console
- مجاني حتى 200 tester

### Google Play Store (الرسمي)

```bash
# 1. أنشئ حساب مطور ($25 مرة واحدة)
#    https://play.google.com/console

# 2. اضبط Service Account
#    Play Console → Setup → API Access → Create Service Account
#    حمل المفتاح: play-store-service-account.json

# 3. رفع التطبيق
./play-store-upload.sh internal

# 4. اذهب لـ Play Console → Review & Publish
```

**المراحل:**
| Track | الغرض | المستخدمين |
|-------|-------|-----------|
| Internal | اختبار فريقك | حتى 100 |
| Alpha | اختبار مغلق | دعوة فقط |
| Beta | اختبار عام | أي شخص يمكنه الانضمام |
| Production | الإصدار النهائي | الجميع |

---

## ⚙️ CI/CD (GitHub Actions)

```yaml
# .github/workflows/mobile-build.yml
```

**يُبنى تلقائياً:**
- على كل push لـ `main` أو `develop`
- ينتج APK debug
- يرفع لـ Firebase App Distribution (اختياري)

**لتفعيل:**
```bash
# 1. اذهب لـ GitHub → Settings → Secrets
# 2. أضف:
#    - FIREBASE_TOKEN
#    - FIREBASE_APP_ID
# 3. push أي تغيير → يبني تلقائياً
```

---

## 🛠️ Fastlane (بديل متقدم)

```bash
# تثبيت Fastlane
sudo gem install fastlane

# بناء وتوزيع بنقرة واحدة
cd mobile
fastlane debug        # APK debug
fastlane release      # APK release
fastlane bundle       # AAB
fastlane firebase     # Firebase Distribution
fastlane play_internal # Play Store Internal
fastlane play_beta     # Play Store Beta
fastlane play_production # Play Store Production
```

---

## 📱 iOS (iPhone/iPad)

```bash
# 1. يتطلب Mac + Xcode
npx cap add ios
npx cap open ios

# 2. في Xcode:
#    - اختر Team (Apple Developer Account $99/سنة)
#    - اضغط Product → Archive
#    - Distribute App → App Store Connect

# 3. أو TestFlight (اختبار داخلي)
#    - لا يحتاج مراجعة Apple
#    - حتى 10000 tester
```

---

## 🔐 الأمان

### التوقيع الرقمي (Signing)

```bash
# إنشاء Keystore (مرة واحدة)
keytool -genkey -v   -keystore medocr.keystore   -alias medocr   -keyalg RSA   -validity 10000

# التوقيع
jarsigner -verbose   -keystore medocr.keystore   app-release-unsigned.apk medocr

# تحسين
zipalign -v 4 app-release-unsigned.apk MedOCR-release.apk
```

### متطلبات Play Store

| المتطلب | الحالة |
|---------|--------|
| Target SDK 34+ | ✅ |
| Privacy Policy | مطلوب |
| App Signing | مطلوب |
| Content Rating | مطلوب |
| Screenshots (5 صور) | مطلوب |
| Feature Graphic | مطلوب |

---

## 📊 مقارنة طرق التوزيع

| الطريقة | السعر | السرعة | الجمهور | الصعوبة |
|---------|-------|--------|---------|---------|
| **APK مباشر** | مجاني | فوري | محدود | سهل |
| **Firebase** | مجاني | فوري | 200 tester | سهل |
| **TestFlight** | $99/سنة | فوري | 10000 | متوسط |
| **Play Internal** | $25 | فوري | 100 | متوسط |
| **Play Beta** | $25 | أيام | غير محدود | متوسط |
| **Play Production** | $25 | أيام | الجميع | صعب |

---

## 🆘 Troubleshooting

### "Build failed: Keystore not found"
```bash
# الحل: إنشاء keystore جديد
keytool -genkey -v -keystore medocr.keystore -alias medocr -keyalg RSA -validity 10000
```

### "Firebase: App ID not found"
```bash
# الحل: إضافة app في Firebase Console
# 1. https://console.firebase.google.com
# 2. Project Settings → Your Apps → Add App
# 3. حمل google-services.json
```

### "Play Store: Upload failed"
```bash
# الحل: التحقق من Service Account
# 1. Play Console → Setup → API Access
# 2. Create Service Account
# 3. منح صلاحية "Release Manager"
# 4. حمل JSON key
```

### "APK too large"
```bash
# الحل: تفعيل shrinkResources
# في android/app/build.gradle:
android {
  buildTypes {
    release {
      minifyEnabled true
      shrinkResources true
      proguardFiles ...
    }
  }
}
```

---

## 📞 دعم

- GitHub Issues: https://github.com/DrAbdulmalek/medical-handwriting-ocr/issues
- Firebase Help: https://firebase.google.com/support
- Play Console Help: https://support.google.com/googleplay/android-developer
