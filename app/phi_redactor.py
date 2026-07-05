"""
PHI Redactor — إخفاء المعلومات الصحية المحمية (Protected Health Information)

يتم استخدامه لمنع عرض بيانات المرضى الحساسة في نتائج OCR.
يدعم: أرقام الهواتف، أرقام الهوية الوطنية، التواريخ، أرقام الملفات الطبية، البريد الإلكتروني.

الاستخدام:
    from app.phi_redactor import phi_redactor
    result = phi_redactor.redact_text("رقم المريض 1234567890 - تاريخ 15/03/2024")
    print(result["redacted_text"])  # "رقم المريض [REDACTED] - تاريخ [REDACTED]"
"""

import re
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


class PHIRedactor:
    """إخفاء المعلومات الصحية المحمية (PHI) من النصوص الطبية العربية والإنجليزية."""

    # أنماط البحث عن المعلومات الحساسة
    _PATTERNS: List[Tuple[str, re.Pattern]] = [
        # أرقام الهواتف (صيغ عربية ودولية)
        ("phone", re.compile(
            r'(?:(?:\+?966|\+?971|\+?965|\+?974|\+?968|\+?20|\+?1)?'
            r'[\s\-]?(?:05|5)?\d[\d\-\(\)\s]{7,14})'
        )),
        # أرقام الهوية الوطنية (10 أرقام متتالية)
        ("national_id", re.compile(r'\b\d{10}\b')),
        # التواريخ (DD/MM/YYYY, DD-MM-YYYY, YYYY/MM/DD)
        ("date", re.compile(
            r'\b(?:\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}[\/\-]\d{1,2}[\/\-]\d{1,2})\b'
        )),
        # أرقام الملفات الطبية (MRN)
        ("patient_id", re.compile(
            r'(?:رقم المريض|رقم الملف|MRN|File\s*[Nn]o|Patient\s*[Ii]D)[:\s\-]*(\d{3,})',
            re.IGNORECASE
        )),
        # البريد الإلكتروني
        ("email", re.compile(r'[\w.\-]+@[\w.\-]+\.\w{2,}')),
        # أرقام العيادات/المرضى القصيرة مع سياق طبي
        ("medical_number", re.compile(
            r'(?:عيادة|مريض|حجز|موعد|رقم|كشف|تذكرة)[\s:]*(\d{3,8})',
            re.IGNORECASE
        )),
    ]

    # كلمات دالة عربية لسياق حساس
    _CONTEXT_KEYWORDS = [
        "اسم المريض", "رقم المريض", "رقم الملف", "الاسم",
        "تاريخ الميلاد", "العمر", "الهاتف", "الجوال",
        "العنوان", "رقم الهوية",
    ]

    def redact_text(self, text: str, redact_context: bool = False) -> Dict[str, Any]:
        """إخفاء المعلومات الحساسة من النص.

        Args:
            text: النص الطبي المراد معالجته.
            redact_context: إذا كان True، يُخفي الأسطر التي تحتوي كلمات دالة
                           حتى لو لم تطابق أنماطاً محددة.

        Returns:
            dict: {
                "redacted_text": النص بعد الإخفاء,
                "redacted_items": قائمة العناصر المخفية مع نوعها وموقعها,
                "items_count": عدد العناصر المخفية,
            }
        """
        if not text:
            return {"redacted_text": text, "redacted_items": [], "items_count": 0}

        redacted_items: List[Dict] = []
        redacted_text = text

        for pattern_name, pattern in self._PATTERNS:
            for match in pattern.finditer(redacted_text):
                original = match.group(0)
                # تجنب إخفاء الأرقام القصيرة جداً (مثل الجرعات)
                if len(original.strip()) <= 3:
                    continue
                redacted_text = redacted_text.replace(original, "[REDACTED]", 1)
                redacted_items.append({
                    "type": pattern_name,
                    "original": original,
                    "position": match.span(),
                })

        # إخفاء سياقي اختياري للأسطر الحساسة
        if redact_context:
            lines = redacted_text.split('\n')
            redacted_lines = []
            for line in lines:
                line_lower = line.lower()
                is_sensitive = any(
                    kw in line_lower for kw in self._CONTEXT_KEYWORDS
                )
                # لا نُخفي إذا كان السطر يحتوي بالفعل على [REDACTED]
                has_redacted = "[REDACTED]" in line
                if is_sensitive and not has_redacted and len(line.strip()) > 5:
                    redacted_lines.append("[REDACTED - سياق حساس]")
                else:
                    redacted_lines.append(line)
            redacted_text = '\n'.join(redacted_lines)

        if redacted_items:
            logger.info(
                "PHI redacted %d items: %s",
                len(redacted_items),
                [f"{i['type']}={i['original'][:20]}" for i in redacted_items[:5]],
            )

        return {
            "redacted_text": redacted_text,
            "redacted_items": redacted_items,
            "items_count": len(redacted_items),
        }

    def extract_metadata(self, text: str) -> Dict[str, Any]:
        """استخراج بيانات تعريفية عن النص دون حجب القيم.

        يُرجع إحصائيات عن أنواع البيانات الموجودة (عدد لا قيمة).
        """
        metadata: Dict[str, Any] = {
            "total_length": len(text),
            "word_count": len(text.split()),
            "line_count": len(text.split('\n')),
        }
        for pattern_name, pattern in self._PATTERNS:
            matches = pattern.findall(text)
            if matches:
                metadata[f"{pattern_name}_found"] = len(matches)

        return metadata


# Singleton
phi_redactor = PHIRedactor()