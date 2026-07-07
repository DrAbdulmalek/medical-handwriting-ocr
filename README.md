> ## Repository Archived

This repository has been **archived**. Its code and functionality have been consolidated into the
[omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) monorepo.

| Detail | Value |
|--------|-------|
| **Suite location** | `apps/handwriting-demo/` |
| **Original purpose** | Flagship Gradio demo for Arabic medical handwriting OCR |
| **Archived on** | 2026-07-07 |
| **Status** | Read-only - no further updates will be made here |

Please use [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) for all development.

---

> ⚠️ **قيد الدمج ضمن omni-medical-suite (لم يكتمل بعد)**
>
> هذا الـSpace كان نشرة تجريبية منفصلة (Streamlit). القرار الحالي هو دمجه ضمن
> [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite)،
> لكن **الدمج الفعلي للكود لم يتم بعد** — هذا الملف يوثّق القرار لا الإنجاز.
>
> **الرابط الحي الموصى به حالياً**: [medical-ocr-demo](https://huggingface.co/spaces/DrAbdulmalek/medical-ocr-demo)
> (مبني على `omni-medical-suite/app/gradio_full_hitl.py`، وهو التطبيق المُعتمَد الوحيد).
>
> البنية التحتية الافتراضية المحذوفة من omni-medical-suite (K8s/Helm/واجهة ويب) محفوظة
> في [future-dev-ideas](https://github.com/DrAbdulmalek/future-dev-ideas) لو احتجتها لاحقاً.

---
title: Medical Handwriting OCR
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# 🏥 Medical Handwriting OCR (نشرة تجريبية سابقة)

تطبيق Streamlit بسيط للتعرف الضوئي على الوصفات الطبية العربية/الإنجليزية.

**ملاحظة صريحة:** هذا الملف لا يمثّل النشر الإنتاجي الحالي. للاستخدام الفعلي استخدم
[medical-ocr-demo](https://huggingface.co/spaces/DrAbdulmalek/medical-ocr-demo).

## المصدر
- الكود: [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite)
- المعالجة المسبقة: [scanner-fixer](https://github.com/DrAbdulmalek/scanner-fixer)
