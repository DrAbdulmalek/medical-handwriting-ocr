# 📱 MedOCR Mobile — دليل التثبيت الكامل

## 🎯 نظرة عامة

نظام متكامل للتعرف الضوئي على الخط اليدوي الطبي للجوال مع:
- ✅ عمل كامل offline (SQLite محلي)
- ✅ مزامنة ثنائية الاتجاه مع السيرفر
- ✅ تصحيح كلمة بكلمة مع اقتراحات ذكية
- ✅ دعم PDF والصور والكاميرا
- ✅ قواميس طبية عربية (مع التوكن)
- ✅ العمل على الكمبيوتر والجوال بالتناوب

---

## 📁 الملفات المطلوبة

### 1. Backend (Python/FastAPI)

| الملف | المسار | الوصف |
|-------|--------|-------|
| `mobile.py` | `backend/app/routers/mobile.py` | **Router جديد** — endpoints المزامنة |
| `corrections_updated.py` | `backend/app/routers/corrections.py` | **تحديث** — دعم X-Device-ID |
| `mobile_migration.sql` | قاعدة البيانات | **Migration** — جداول المزامنة |

### 2. Mobile (React/Capacitor)

| الملف | المسار | الوصف |
|-------|--------|-------|
| `sqlite.ts` | `mobile/src/db/sqlite.ts` | قاعدة بيانات SQLite محلية |
| `mobileAuth.ts` | `mobile/src/api/mobileAuth.ts` | مصادقة API Key |
| `sync.ts` | `mobile/src/api/sync.ts` | محرك المزامنة |
| `dictionaryCache.ts` | `mobile/src/api/dictionaryCache.ts` | قاموس offline |
| `suggestions.ts` | `mobile/src/api/suggestions.ts` | اقتراحات ذكية |
| `PdfUploader.tsx` | `mobile/src/components/PdfUploader.tsx` | رفع ومعالجة الملفات |
| `WordEditor.tsx` | `mobile/src/components/WordEditor.tsx` | محرر الكلمات |
| `SyncPanel.tsx` | `mobile/src/components/SyncPanel.tsx` | لوحة المزامنة |
| `App.tsx` | `mobile/src/App.tsx` | التطبيق الرئيسي |

---

## 🔧 خطوات التثبيت

### المرحلة 1: تحديث Backend

#### 1.1 تطبيق Migration على قاعدة البيانات

```bash
# الدخول لقاعدة البيانات
psql -U ocr_user -d medical_ocr

# تطبيق Migration
\i mobile_migration.sql

# التحقق
\dt
# يجب أن ترى: mobile_sync_logs, mobile_orphan_corrections
```

#### 1.2 إضافة Router الجديد

```bash
# نسخ mobile.py
cp mobile.py backend/app/routers/mobile.py

# تحديث corrections.py
cp corrections_updated.py backend/app/routers/corrections.py
```

#### 1.3 تسجيل Router في main.py

افتح `backend/app/main.py` وأضف:

```python
from app.routers import mobile as mobile_router

# ... في قسم Routers ...
app.include_router(mobile_router.router, tags=["mobile"])
```

#### 1.4 إعادة تشغيل السيرفر

```bash
cd backend
uvicorn app.main:app --reload
```

#### 1.5 التحقق من Endpoints

افتح المتصفح:
- `http://localhost:8000/docs` → يجب أن ترى قسم "mobile"
- `POST /api/mobile/sync/push`
- `POST /api/mobile/sync/pull`
- `GET /api/mobile/sync/status/{device_id}`
- `GET /api/mobile/documents/{id}/regions`

---

### المرحلة 2: إعداد Mobile App

#### 2.1 نسخ ملفات الجوال

```bash
# إنشاء مجلد الجوال
mkdir -p medical-handwriting-ocr/mobile
cp -r mobile-app/* medical-handwriting-ocr/mobile/
cd medical-handwriting-ocr/mobile
```

#### 2.2 تثبيت Dependencies

```bash
npm install
```

#### 2.3 إعداد Capacitor

```bash
# تثبيت Capacitor CLI
npm install -g @capacitor/cli

# إضافة المنصات
npx cap add android
npx cap add ios
```

#### 2.4 إعداد API Key

أنشئ ملف `.env`:

```env
VITE_API_URL=https://your-server.com
VITE_API_KEY=your_api_key_here
```

> ⚠️ **تنبيه أمني**: هذا التوكن يجب تخزينه بأمان. في الإنتاج، استخدم OAuth2 أو JWT بدلاً من التوكن الثابت.

#### 2.5 بناء التطبيق

```bash
npm run build
npx cap sync
```

#### 2.6 تشغيل على الجهاز

```bash
# Android
npx cap open android
# ثم اضغط Run في Android Studio

# iOS
npx cap open ios
# ثم اضغط Run في Xcode
```

---

## 🔐 نظام المصادقة

### تدفق API Key

```
┌────────────┐     ┌──────────────┐     ┌──────────────┐
│   User     │────▶│ Enter API    │────▶│ Save to      │
│            │     │ Key + URL    │     │ Preferences  │
└────────────┘     └──────────────┘     └──────────────┘
                                               │
                                               ▼
┌────────────┐     ┌──────────────┐     ┌──────────────┐
│   App      │◀────│ X-API-Key    │◀────│ Load on      │
│   Works    │     │ Header       │     │ Startup      │
│   Offline  │     │ Every Req    │     │              │
└────────────┘     └──────────────┘     └──────────────┘
```

### Headers المطلوبة

| Header | الوصف | مثال |
|--------|-------|------|
| `X-API-Key` | مفتاح API للمصادقة | `ghp_7TUh7wVG...` |
| `X-Device-ID` | معرف الجهاز للتتبع | `device_abc123` |

---

## 🔄 تدفق المزامنة

### Push (جوال → سيرفر)

```bash
POST /api/mobile/sync/push
Content-Type: application/json
X-API-Key: ghp_7TUh7wVG...

{
  "device_id": "device_abc123",
  "last_sync_token": "sync_xxx_1234567890000",
  "corrections": [
    {
      "local_region_id": "local_reg_001",
      "server_region_id": "550e8400-e29b-41d4-a716-446655440000",
      "corrected_text": "Paracetamol",
      "original_text": "Paracetam0l",
      "corrected_at": 1717200000000,
      "user_id": "doctor_ahmed"
    }
  ]
}
```

### Response

```json
{
  "sync_token": "sync_new_1717300000000",
  "accepted_count": 1,
  "rejected_count": 0,
  "rejected_items": [],
  "server_updates": [
    {
      "server_region_id": "550e8400...",
      "corrected_text": "Ibuprofen",
      "status": "corrected",
      "updated_at": 1717250000000
    }
  ],
  "has_more": false,
  "server_timestamp": 1717300000000
}
```

### Pull (سيرفر → جوال)

```bash
POST /api/mobile/sync/pull
Content-Type: application/json
X-API-Key: ghp_7TUh7wVG...

{
  "device_id": "device_abc123",
  "last_sync_token": "sync_xxx_1234567890000",
  "user_id": "doctor_ahmed",
  "limit": 100
}
```

---

## 📊 Endpoints الجديدة

| Endpoint | Method | الوصف |
|----------|--------|-------|
| `/api/mobile/sync/push` | POST | رفع تصحيحات من الجوال |
| `/api/mobile/sync/pull` | POST | سحب تحديثات من السيرفر |
| `/api/mobile/sync/status/{device_id}` | GET | حالة المزامنة للجهاز |
| `/api/mobile/sync/bulk-correct` | POST | تصحيح متعدد دفعة واحدة |
| `/api/mobile/documents/{id}/regions` | GET | جلب مناطق المستند للجوال |
| `/api/correct` | POST | تحديث (يدعم X-Device-ID) |
| `/api/pending` | GET | تحديث (يدعم since, user_id) |

---

## 🗄️ جداول قاعدة البيانات الجديدة

### mobile_sync_logs

| العمود | النوع | الوصف |
|--------|-------|-------|
| `id` | UUID | معرف فريد |
| `device_id` | VARCHAR | معرف الجهاز |
| `direction` | VARCHAR | push/pull |
| `accepted_count` | INTEGER | عدد المقبول |
| `rejected_count` | INTEGER | عدد المرفوض |
| `sync_token` | VARCHAR | توكن المزامنة |
| `created_at` | TIMESTAMP | وقت المزامنة |

### mobile_orphan_corrections

| العمود | النوع | الوصف |
|--------|-------|-------|
| `id` | UUID | معرف فريد |
| `device_id` | VARCHAR | معرف الجهاز |
| `local_region_id` | VARCHAR | معرف المحلي |
| `document_id` | UUID | معرف المستند |
| `predicted_text` | TEXT | النص الأصلي |
| `corrected_text` | TEXT | النص المصحح |
| `resolved` | BOOLEAN | هل تم الحل |

---

## 📱 SQLite Schema (في الجوال)

### documents
```sql
CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  file_name TEXT NOT NULL,
  file_type TEXT NOT NULL,
  file_data TEXT NOT NULL,
  page_count INTEGER,
  status TEXT DEFAULT 'pending',
  created_at INTEGER,
  updated_at INTEGER,
  server_doc_id TEXT,
  user_id TEXT
);
```

### regions
```sql
CREATE TABLE regions (
  id TEXT PRIMARY KEY,
  local_doc_id TEXT NOT NULL,
  page_number INTEGER,
  bbox TEXT,
  predicted_text TEXT,
  confidence REAL,
  corrected_text TEXT,
  status TEXT,
  sync_status TEXT DEFAULT 'pending',
  server_region_id TEXT
);
```

### sync_queue
```sql
CREATE TABLE sync_queue (
  id TEXT PRIMARY KEY,
  entity_type TEXT,
  entity_id TEXT,
  action TEXT,
  payload TEXT,
  status TEXT DEFAULT 'pending',
  retry_count INTEGER DEFAULT 0,
  created_at INTEGER
);
```

---

## 🚀 سيناريوهات الاستخدام

### السيناريو 1: تصحيح Offline ثم مزامنة

```
1. الطبيب يصور وصفة طبية بالكاميرا (offline)
2. التطبيق يحفظ الصورة في SQLite
3. الطبيب يصحح الكلمات واحدة بواحدة (offline)
4. التصحيحات تحفظ في SQLite + sync_queue
5. عند دخول المستشفى (WiFi) → التطبيق يزامن تلقائياً
6. التصحيحات ترفع للسيرفر → تتحدث PostgreSQL
7. السكرتيرة ترى التصحيحات على الكمبيوتر
```

### السيناريو 2: عمل مشترك بين الجوال والكمبيوتر

```
1. السكرتيرة ترفع PDF على الكمبيوتر
2. السيرفر يقسم PDF لكلمات (OCR)
3. الطبيب يفتح التطبيق → يزامن → يستلم الكلمات
4. الطبيب يصحح على الجوال (في الطريق)
5. يعود للمستشفى → يزامن → التصحيحات تظهر على الكمبيوتر
```

### السيناريو 3: قواميس طبية

```
1. التطبيق يبحث في القاموس المحلي (SQLite)
2. إذا لم يجد → يسأل السيرفر (يحتاج GitHub Token)
3. السيرفر يبحث في arabic-dictionaries-collection
4. النتائج ترجع للجوال + تُخزن محلياً للمرة القادمة
```

---

## ⚠️ ملاحظات أمنية

1. **API Key**: التوكن `ghp_7TUh7wVG...` يجب تدويره (rotate) بشكل دوري
2. **HTTPS**: يجب استخدام HTTPS في الإنتاج
3. **Rate Limiting**: السيرفر يحدد 100 طلب/دقيقة افتراضياً
4. **Device ID**: يُستخدم للتتبع فقط، لا يُخزن معلومات شخصية
5. **SQLite Encryption**: في الإنتاج، فعّل تشفير SQLite:
   ```typescript
   await sqlite.createConnection(DB_NAME, false, 'encryption', 1, false);
   ```

---

## 🔧 Troubleshooting

### المشكلة: المزامنة لا تعمل
```bash
# تحقق من:
1. السيرفر يعمل (curl http://localhost:8000/health)
2. API Key صحيح (X-API-Key header)
3. CORS مُفعل (ALLOWED_ORIGINS env)
4. قاعدة البيانات محدثة (migration applied)
```

### المشكلة: SQLite لا يعمل على Android
```bash
# في capacitor.config.ts:
plugins: {
  SQLite: {
    androidIsEncryption: false,
    androidBiometric: {
      biometricTitle: "Authentication",
      biometricSubTitle: "Access SQLite"
    }
  }
}
```

### المشكلة: الكاميرا لا تفتح
```bash
# Android permissions:
# في AndroidManifest.xml أضف:
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" />
```

---

## 📞 دعم

- GitHub Issues: https://github.com/DrAbdulmalek/medical-handwriting-ocr/issues
- API Docs: http://localhost:8000/docs (بعد تشغيل السيرفر)
- Mobile Docs: انظر `mobile/README.md`

---

## 📜 License

MIT License — نفس رخصة المشروع الأصلي.
