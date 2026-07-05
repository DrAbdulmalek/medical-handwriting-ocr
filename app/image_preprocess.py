"""
Image Preprocessing — تحسين الصور قبل OCR للخط العربي اليدوي.

يوفر معالجة مسبقة للصور الطبية باستخدام:
- CLAHE (تحسين التباين التكيفي) — يحسن التباين محلياً
- Denoising — إزالة التشويش
- Binarization — تحويل إلى أبيض وأسود بحد Otsu
- Page Bounds Detection — كشف حدود الصفحة البيضاء
- Auto Deskew — تصحيح الميل تلقائياً
- Smart Auto Crop — قص تلقائي ذكي

الاستخدام:
    from app.image_preprocess import preprocess_for_ocr
    enhanced = preprocess_for_ocr(image_bgr)

    from app.image_preprocess import deskew_and_crop
    processed = deskew_and_crop(image_bgr)
"""

import cv2
import numpy as np
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def preprocess_for_ocr(
    image_bgr: np.ndarray,
    clahe_clip: float = 2.0,
    clahe_grid: int = 8,
    denoise_h: int = 10,
    binarize: bool = True,
) -> np.ndarray:
    """تحسين صورة طبية يدوية عربية قبل إرسالها لمحركات OCR.

    خطوات المعالجة:
    1. تحويل إلى رمادي
    2. CLAHE — تحسين التباين التكيفي المحلي
    3. إزالة التشويش (Non-local Means Denoising)
    4. ثنائية الألوان (Otsu Binarization) — اختياري

    Args:
        image_bgr: صورة BGR من OpenCV.
        clahe_clip: حد القص لـ CLAHE (افتراضي 2.0).
        clahe_grid: حجم شبكة CLAHE (افتراضي 8).
        denoise_h: قوة إزالة التشويش (افتراضي 10).
        binarize: هل يتم تطبيق الثنائية؟ (افتراضي True).

    Returns:
        الصورة المحسنة (BGR إذا binarize=False، رمادي إذا binarize=True).
    """
    if image_bgr is None:
        logger.warning("preprocess_for_ocr: received None image")
        return image_bgr

    # Step 1: تحويل إلى رمادي
    if len(image_bgr.shape) == 3:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = image_bgr

    # Step 2: CLAHE — تحسين التباين التكيفي
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(clahe_grid, clahe_grid))
    enhanced = clahe.apply(gray)

    # Step 3: إزالة التشويش
    denoised = cv2.fastNlMeansDenoising(enhanced, h=denoise_h)

    if not binarize:
        # إعادة تحويل إلى BGR
        return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)

    # Step 4: ثنائية الألوان (Otsu)
    _, binary = cv2.threshold(
        denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    return binary


def preprocess_crop_for_ocr(crop_bgr: np.ndarray) -> np.ndarray:
    """تحسين قطعة صورة صغيرة (crop) لمحرك OCR.

    إعدادات محسّنة للقطع الصغيرة من الوصفات الطبية:
    - CLAHE أخف (clip=1.5) لأن القطع صغيرة غالباً
    - إزالة تشويش أخف (h=7)
    - بدون ثنائية (لأن بعض المحركات تفضل الصور الرمادية)
    """
    return preprocess_for_ocr(
        crop_bgr,
        clahe_clip=1.5,
        clahe_grid=4,
        denoise_h=7,
        binarize=False,
    )


# ===========================================================================
# Page Bounds, Deskew & Smart Crop  (from v13.1 integration)
# ===========================================================================

def find_page_bounds(
    image: np.ndarray,
    min_page_area_ratio: float = 0.15,
) -> Optional[Tuple[int, int, int, int]]:
    """Detect the white page region within a gray-background scan.

    Uses contour-based detection (cv2.findContours) which is more reliable
    than np.where for images with gray borders around a white page.

    Returns (x, y, w, h) or None if no clear page region is found.
    """
    if image is None or image.size == 0:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    h, w = gray.shape
    min_page_area = int(h * w * min_page_area_ratio)

    # Primary: threshold at 200 to find bright page region
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_rect = None
    best_area = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_page_area:
            continue
        x, y, cw, ch = cv2.boundingRect(cnt)
        roi = gray[y:y + ch, x:x + cw]
        if roi.size == 0 or roi.mean() < 180:
            continue
        if area > best_area:
            best_area = area
            best_rect = (x, y, cw, ch)

    # Fallback: Canny edge detection for documents without clear threshold
    if best_rect is None:
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) == 4:
                area = cv2.contourArea(cnt)
                if area > min_page_area and area > best_area:
                    x, y, cw, ch = cv2.boundingRect(cnt)
                    best_area = area
                    best_rect = (x, y, cw, ch)

    # Last resort: brightness-based row/col analysis
    if best_rect is None:
        row_means = gray.mean(axis=1)
        col_means = gray.mean(axis=0)
        light_rows = np.where(row_means > row_means.mean() + 20)[0]
        light_cols = np.where(col_means > col_means.mean() + 20)[0]
        if len(light_rows) > 0 and len(light_cols) > 0:
            best_rect = (
                int(light_cols[0]), int(light_rows[0]),
                int(light_cols[-1] - light_cols[0] + 1),
                int(light_rows[-1] - light_rows[0] + 1),
            )

    return best_rect


def auto_detect_skew(image: np.ndarray) -> float:
    """Detect page skew angle using minAreaRect with projection validation.

    Uses the largest text contour's minimum-area bounding rectangle to
    estimate skew, then validates by checking horizontal projection peaks.
    Returns angle in degrees (0.0 if image is straight or detection fails).
    """
    if image is None or image.size == 0:
        return 0.0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image.copy()
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0

    min_area = gray.shape[0] * gray.shape[1] * 0.001
    valid_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > min_area]
    if not valid_contours:
        return 0.0

    # Use largest contour for angle estimation
    largest = max(valid_contours, key=cv2.contourArea)
    rect = cv2.minAreaRect(largest)
    angle = rect[2]

    # Normalize angle to [-45, +45]
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle = angle - 90

    # Small angles → no correction needed
    if abs(angle) < 1.0:
        return 0.0

    # For large angles, validate with horizontal projection peak count
    if abs(angle) > 5.0:
        center = (gray.shape[1] // 2, gray.shape[0] // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]),
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        h_proj = np.sum(rotated < 128, axis=1)
        peaks = [i for i in range(1, len(h_proj) - 1)
                 if h_proj[i] > h_proj[i - 1] and h_proj[i] > h_proj[i + 1] and h_proj[i] > 50]

        if len(peaks) < 5:
            # Try opposite angle
            test_angle = -angle
            M = cv2.getRotationMatrix2D(center, test_angle, 1.0)
            rotated = cv2.warpAffine(gray, M, (gray.shape[1], gray.shape[0]),
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=255)
            h_proj = np.sum(rotated < 128, axis=1)
            peaks2 = [i for i in range(1, len(h_proj) - 1)
                      if h_proj[i] > h_proj[i - 1] and h_proj[i] > h_proj[i + 1] and h_proj[i] > 50]
            if len(peaks2) > len(peaks):
                angle = test_angle

    return max(-45.0, min(45.0, round(angle * 2) / 2))


def _deskew_image(image: np.ndarray, angle: float) -> np.ndarray:
    """Rotate image to correct skew with white border fill."""
    if abs(angle) < 0.5 or image is None:
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)

    cos_val = abs(np.cos(np.radians(angle)))
    sin_val = abs(np.sin(np.radians(angle)))
    new_w = int(h * sin_val + w * cos_val)
    new_h = int(h * cos_val + w * sin_val)
    M[0, 2] += (new_w - w) / 2
    M[1, 2] += (new_h - h) / 2

    border_val = (255, 255, 255) if len(image.shape) == 3 else 255
    return cv2.warpAffine(image, M, (new_w, new_h),
                         borderMode=cv2.BORDER_CONSTANT, borderValue=border_val)


def smart_auto_crop(image: np.ndarray, padding: int = 10) -> np.ndarray:
    """Crop image to detected page content with padding.

    Uses find_page_bounds() for contour-based page detection.
    Returns original image if no bounds detected.
    """
    if image is None or image.size == 0:
        return image

    bounds = find_page_bounds(image)
    if bounds is None:
        return image

    x, y, w, h = bounds
    img_h, img_w = image.shape[:2]
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(img_w, x + w + padding)
    y2 = min(img_h, y + h + padding)

    return image[y1:y2, x1:x2]


def deskew_and_crop(image: np.ndarray, padding: int = 5) -> np.ndarray:
    """Full preprocessing pipeline: deskew → crop.

    Convenience function that combines auto_detect_skew + smart_auto_crop.
    This is the recommended entry point for scanned document images.

    Args:
        image: BGR or grayscale numpy array.
        padding: Pixels of padding around detected page bounds.

    Returns:
        Preprocessed image (deskewed and cropped).
    """
    if image is None or image.size == 0:
        return image

    # Step 1: Detect and correct skew
    angle = auto_detect_skew(image)
    if abs(angle) > 0.5:
        logger.info("Auto-deskew: correcting %.1f°", angle)
        image = _deskew_image(image, angle)

    # Step 2: Detect page bounds and crop
    cropped = smart_auto_crop(image, padding=padding)
    if cropped.shape != image.shape:
        ch, cw = cropped.shape[:2]
        logger.info("Auto-crop: %dx%d → %dx%d", image.shape[1], image.shape[0], cw, ch)

    return cropped