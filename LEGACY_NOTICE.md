# ⚠️ LEGACY REPOSITORY - ARCHIVED

## This repository is archived and no longer maintained as a standalone project.

**🎯 Please use [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite) instead.**

## Why?

- All features have been **integrated** into omni-medical-suite
- **Better performance** and maintenance
- **Unified API** and documentation
- **Active development** and support

## Migration Status: ✅ Complete

| Feature | Status | Notes |
|---------|--------|-------|
| Core OCR | Migrated | Now part of omni-medical-suite OCR Fusion V2 |
| Correction | Migrated | Integrated with MedicalContextProtector |
| Training | Migrated | Part of continuous learning loop |
| Benchmarks | Migrated | Available in medical-ocr-benchmarks |

## What to Do?

1. **Stop using this repository**
2. **Migrate to omni-medical-suite**
3. **Check the migration guide** in the main repository: [MIGRATION.md](https://github.com/DrAbdulmalek/omni-medical-suite/blob/main/MIGRATION.md)

## Quick Migration Steps

### For medical-handwriting-ocr users:
```bash
# Install omni-medical-suite
pip install git+https://github.com/DrAbdulmalek/omni-medical-suite.git

# Use the handwriting OCR module
from omni_medical_suite import HandwritingOCR
ocr = HandwritingOCR()
result = ocr.process("handwritten_document.png")
```

### For medical-ocr-postprocessor users:
```bash
# Install omni-medical-suite
pip install git+https://github.com/DrAbdulmalek/omni-medical-suite.git

# Use the postprocessor module
from omni_medical_suite import MedicalPostprocessor
postprocessor = MedicalPostprocessor()
result = postprocessor.process("ocr_output.txt")
```

## Contact

For questions about the migration, please:
- Open an issue in [omni-medical-suite](https://github.com/DrAbdulmalek/omni-medical-suite/issues)
- Join our [Discussions](https://github.com/DrAbdulmalek/omni-medical-suite/discussions)
- Contact: [DrAbdulmalek](https://github.com/DrAbdulmalek)

---

**Note:** This repository will remain available for reference but will not receive updates or bug fixes.

Last updated: 2026-06-28
