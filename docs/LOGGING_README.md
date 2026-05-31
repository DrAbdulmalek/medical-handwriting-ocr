# 📋 MedOCR Mobile — Logging System

## 🎯 نظرة عامة

نظام تسجيل شامل (Logging) لكل جلسة — يساعد في:
- ✅ تتبع الأخطاء ومعالجتها
- ✅ مراقبة أداء التطبيق
- ✅ تحليل استخدام المستخدمين
- ✅ تطوير وإصلاح التطبيق

---

## 📊 ما يُسجّل

### في كل جلسة (Session)

```
┌─────────────────────────────────────────────────────────┐
│  SESSION START                                           │
│  ├── Device Info (Android/iOS, model, OS version)       │
│  ├── Screen Size & Language                             │
│  ├── Battery Level                                       │
│  ├── App Version                                         │
│  └── User ID                                             │
│                                                          │
│  DURING SESSION                                          │
│  ├── Every screen navigation                             │
│  ├── Every button click                                  │
│  ├── Every API call (URL, status, duration)              │
│  ├── Every database operation                            │
│  ├── Every OCR process                                   │
│  ├── Every sync attempt                                  │
│  ├── Every error (with stack trace)                      │
│  ├── Memory usage (every 30s)                            │
│  └── Network status changes                              │
│                                                          │
│  SESSION END                                             │
│  ├── Duration                                            │
│  └── Total actions                                       │
└─────────────────────────────────────────────────────────┘
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MOBILE APP                            │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐   │
│  │  App.tsx   │───▶│  Logger    │───▶│  SQLite    │   │
│  │  (events)  │    │  (service) │    │  (local)   │   │
│  └────────────┘    └────────────┘    └────────────┘   │
│         │                   │                           │
│         │                   ▼                           │
│  ┌────────────┐    ┌────────────┐                      │
│  │  LogViewer │◀───│  Memory    │                      │
│  │  (UI)      │    │  (buffer)  │                      │
│  └────────────┘    └────────────┘                      │
│                           │                             │
│                           │ Online                      │
│                           ▼                             │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐   │
│  │  Server    │◀───│  POST      │◀───│  Batch     │   │
│  │  (analytics)    │  /logs     │    │  Upload    │   │
│  └────────────┘    └────────────┘    └────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 📁 الملفات

| الملف | المسار | الوصف |
|-------|--------|-------|
| `logger.ts` | `src/services/logger.ts` | **Core logger** — التسجيل والتخزين |
| `LogViewer.tsx` | `src/components/LogViewer.tsx` | **UI** — عرض السجلات |
| `log-viewer.css` | `src/styles/log-viewer.css` | **Styles** — تصميم عارض السجلات |
| `mobile_logs_endpoint.py` | `backend/` | **Server** — استقبال السجلات |
| `logs_migration.sql` | `database/` | **Migration** — جدول السجلات |

---

## 🔧 الاستخدام

### 1. التسجيل في الكود

```typescript
import { logger } from './services/logger';

// تسجيل عادي
logger.info('app', 'User logged in', { userId: 'doctor_ahmed' });

// تسجيل تحذير
logger.warn('network', 'Slow connection detected', { speed: '2g' });

// تسجيل خطأ
logger.error('ocr', 'OCR failed', error, { imageSize: '2MB' });

// تسجيل فادح (يرفع تلقائياً)
logger.fatal('db', 'Database corruption detected', error);

// قياس الأداء
const endTimer = logger.startTimer('ocr-process');
// ... do OCR ...
endTimer(); // يسجل: "Timer: ocr-process — 1250ms"

// استخدام الذاكرة
logger.logMemoryUsage('performance');
```

### 2. عرض السجلات

```typescript
// في أي مكون
import { useLogger } from './services/logger';

function MyComponent() {
  const { logs, stats } = useLogger();

  return (
    <div>
      <p>Errors: {stats.level_error}</p>
      <p>Warnings: {stats.level_warn}</p>
    </div>
  );
}
```

### 3. فلترة السجلات

```typescript
// جميع أخطاء الشبكة
const networkErrors = logger.getLogs({
  level: 'error',
  category: 'network',
  since: Date.now() - 3600000, // آخر ساعة
});

// بحث نصي
const searchResults = logger.getLogs({
  search: 'timeout',
  limit: 50,
});
```

### 4. تصدير السجلات

```typescript
// JSON (للمطورين)
const filename = await logger.exportLogs('json');
// → medocr-logs-1717200000000.json

// CSV (لـ Excel)
const filename = await logger.exportLogs('csv');
// → medocr-logs-1717200000000.csv

// Text (للقراءة)
const filename = await logger.exportLogs('txt');
// → medocr-logs-1717200000000.txt
```

### 5. رفع للسيرفر

```typescript
// رفع يدوي
await logger.uploadLogs(true);

// أو تلقائي (كل 5 دقائق)
// مُفعّل في الإعدادات
```

---

## 📱 Log Viewer UI

### الوصول

| الطريقة | الإجراء |
|---------|---------|
| زر التصحيح | 🐛 (أعلى اليمين في وضع التطوير) |
| من الإعدادات | ⚙️ → "عرض سجل الأحداث" |
| اختصار | اضغط مطولاً على الشعار |

### الميزات

```
┌─────────────────────────────────────────────┐
│  📋 سجل الأحداث                    [✕]    │
├─────────────────────────────────────────────┤
│  🔵 Debug 145 │ ℹ️ Info 892 │ ⚠️ Warn 23 │
│  ❌ Error 5  │ 💀 Fatal 0  │ 📊 Total 1065│
├─────────────────────────────────────────────┤
│  المستوى: [الكل ▼]  الفئة: [الكل ▼]       │
│  [🔍 بحث في السجلات...                    ] │
├─────────────────────────────────────────────┤
│  12:34:56  INFO   [sync]  Sync completed    │
│  12:34:55  DEBUG  [db]    Query executed    │
│  12:34:54  WARN   [net]   Slow connection    │
│  12:34:53  ERROR  [ocr]   OCR failed        │
│  ─────────────────────────────────────────   │
│  ID: log_xxx                                │
│  Session: sess_xxx                          │
│  Details: {imageSize: "2MB"}                │
│  Stack: Error: timeout...                   │
├─────────────────────────────────────────────┤
│  [🔄] [☁️ رفع] [📥 تصدير] [🗑️ حذف]        │
└─────────────────────────────────────────────┘
```

---

## 🗄️ تخزين السجلات

### محلياً (SQLite + Filesystem)

```sql
-- في الجوال
CREATE TABLE logs (
  id TEXT PRIMARY KEY,
  timestamp INTEGER,
  level TEXT,
  category TEXT,
  message TEXT,
  details JSON,
  session_id TEXT
);
```

**الحدود:**
- 5000 سجل في الذاكرة
- 7 أيام احتفاظ تلقائي
- تقليص تلقائي عند الامتلاء

### على السيرفر (PostgreSQL)

```sql
-- في السيرفر
CREATE TABLE mobile_logs (
  id VARCHAR(128) PRIMARY KEY,
  session_id VARCHAR(128),
  device_id VARCHAR(128),
  user_id VARCHAR(256),
  timestamp TIMESTAMP,
  level VARCHAR(20),
  category VARCHAR(30),
  message TEXT,
  details JSONB,
  stack_trace TEXT,
  device_info JSONB,
  created_at TIMESTAMP DEFAULT NOW()
);
```

---

## 📊 Analytics Dashboard (Server)

### Endpoint: `GET /api/mobile/logs/analytics`

```json
{
  "period_days": 7,
  "total_logs": 15420,
  "error_count": 45,
  "warning_count": 123,
  "fatal_count": 2,
  "top_errors": [
    {
      "message": "OCR timeout",
      "category": "ocr",
      "count": 15,
      "last_occurrence": "2024-05-31T10:30:00Z"
    }
  ],
  "sessions_today": 89,
  "active_devices": 34,
  "crash_rate_percent": 0.13
}
```

---

## 🔍 Troubleshooting with Logs

### المشكلة: التطبيق يتعطل

```bash
# 1. افتح Log Viewer
# 2. فلتر: level = fatal
# 3. شاهد آخر fatal error
# 4. انسخ stack trace
# 5. أرسل للمطور
```

### المشكلة: المزامنة لا تعمل

```bash
# 1. فلتر: category = sync
# 2. فلتر: level = error
# 3. شاهد رسائل الخطأ
# 4. تحقق من: network status, server URL, API key
```

### المشكلة: بطء في OCR

```bash
# 1. فلتر: category = ocr AND category = performance
# 2. شاهد أوقات المعالجة
# 3. قارن: image size vs processing time
# 4. تحقق من: memory usage
```

---

## ⚙️ Configuration

```typescript
// في logger.ts
const LOG_CONFIG = {
  maxLogEntries: 5000,        // الحد الأقصى للسجلات
  maxLogAgeDays: 7,            // مدة الاحتفاظ
  logToConsole: true,          // في وضع التطوير
  logToFile: true,             // حفظ في ملف
  logToServer: false,          // رفع للسيرفر
  minLevelForFile: 'debug',    // أقل مستوى للملف
  minLevelForServer: 'error',  // أقل مستوى للسيرفر
  batchSize: 50,               // حجم الدفعة للرفع
};
```

---

## 🔒 Privacy & Security

- ❌ **لا** نسجل: كلمات المرور، API keys، بيانات المرضى
- ✅ نسجل: أخطاء النظام، أداء العمليات، استخدام الميزات
- 🔐 السجلات مُشفرة عند الرفع (HTTPS)
- 🗑️ السجلات تُحذف تلقائياً بعد 7 أيام

---

## 📞 دعم

- GitHub Issues: https://github.com/DrAbdulmalek/medical-handwriting-ocr/issues
- Email logs: تصدير → إرسال للمطور
