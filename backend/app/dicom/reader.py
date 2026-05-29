"""
DICOM file reader for medical imaging integration.
Supports extracting embedded text overlays and metadata from DICOM files.
"""

import io
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

try:
    import pydicom
    from pydicom.dataset import Dataset
    from PIL import Image as PILImage
    HAS_PYDICOM = True
except ImportError:
    HAS_PYDICOM = False
    logger.warning("pydicom not installed. DICOM support disabled.")


@dataclass
class DICOMExtractedText:
    """Represents text extracted from a DICOM file."""
    text: str
    source: str  # 'overlay', 'metadata', 'annotation', 'pixel_data'
    confidence: float = 1.0
    bounding_box: Optional[Dict] = None
    metadata: Optional[Dict] = None


class DICOMReader:
    """
    Reads DICOM files and extracts text content from multiple sources:
    - Text overlays (60xx,xxxx) tags
    - DICOM metadata fields (PatientName, StudyDescription, etc.)
    - Structured annotations (SQ sequences)
    - Pixel data converted to images for OCR processing
    """

    # DICOM tags commonly containing text
    TEXT_TAGS = {
        (0x0008, 0x0080): "InstitutionName",
        (0x0008, 0x0081): "InstitutionAddress",
        (0x0008, 0x1030): "StudyDescription",
        (0x0008, 0x103E): "SeriesDescription",
        (0x0010, 0x0010): "PatientName",
        (0x0010, 0x0020): "PatientID",
        (0x0010, 0x0030): "PatientBirthDate",
        (0x0010, 0x1010): "PatientAddress",
        (0x0010, 0x4000): "PatientComments",
        (0x0008, 0x1090): "ManufacturerModelName",
        (0x0008, 0x1020): "StudyDate",
        (0x0008, 0x1032): "ProcedureCodeSequence",
        (0x4008, 0x0100): "ReportText",
        (0x4008, 0x0102): "ReportStatus",
        (0x4008, 0x0119): "VerifyingObserverName",
        (0x0070, 0x0001): "GraphicAnnotationSequence",
    }

    def __init__(self):
        if not HAS_PYDICOM:
            raise RuntimeError("pydicom is required for DICOM support. Install with: pip install pydicom")

    def read_file(self, file_path: str) -> Optional[Dataset]:
        """
        Read a DICOM file.
        
        Args:
            file_path: Path to the .dcm file or zip containing DICOM.
        
        Returns:
            pydicom Dataset or None if reading fails.
        """
        try:
            ds = pydicom.dcmread(file_path, force=True)
            return ds
        except Exception as e:
            logger.error(f"Failed to read DICOM file {file_path}: {e}")
            return None

    def read_from_bytes(self, data: bytes) -> Optional[Dataset]:
        """Read DICOM from bytes."""
        try:
            ds = pydicom.dcmread(io.BytesIO(data), force=True)
            return ds
        except Exception as e:
            logger.error(f"Failed to read DICOM from bytes: {e}")
            return None

    def extract_all_text(self, file_path: str) -> List[DICOMExtractedText]:
        """
        Extract all text content from a DICOM file.
        
        Args:
            file_path: Path to the DICOM file.
        
        Returns:
            List of extracted text entries.
        """
        ds = self.read_file(file_path)
        if ds is None:
            return []

        texts = []

        # 1. Extract from metadata fields
        texts.extend(self._extract_metadata_text(ds))

        # 2. Extract from text overlays
        texts.extend(self._extract_overlay_text(ds))

        # 3. Extract from graphic annotations
        texts.extend(self._extract_annotation_text(ds))

        # 4. Extract from report text
        texts.extend(self._extract_report_text(ds))

        # 5. Generate image for OCR processing
        image = self._get_pixel_image(ds)
        if image is not None:
            texts.append(DICOMExtractedText(
                text="[PIXEL_DATA_IMAGE]",
                source="pixel_data",
                confidence=0.0,
                metadata={"width": image.width, "height": image.height}
            ))

        logger.info(f"Extracted {len(texts)} text entries from DICOM file")
        return texts

    def _extract_metadata_text(self, ds: Dataset) -> List[DICOMExtractedText]:
        """Extract text from DICOM metadata tags."""
        texts = []
        for tag, tag_name in self.TEXT_TAGS.items():
            try:
                if tag in ds:
                    value = ds[tag].value
                    if value and str(value).strip():
                        text_str = str(value).strip()
                        texts.append(DICOMExtractedText(
                            text=text_str,
                            source="metadata",
                            confidence=1.0,
                            metadata={"tag": tag_name, "tag_hex": f"({tag[0]:04X},{tag[1]:04X})"}
                        ))
            except Exception:
                continue
        return texts

    def _extract_overlay_text(self, ds: Dataset) -> List[DICOMExtractedText]:
        """Extract text from DICOM overlay data (60xx tags)."""
        texts = []
        overlay_group = 0x6000
        
        while overlay_group <= 0x601F:
            try:
                overlay_data_tag = (overlay_group, 0x3000)
                if overlay_data_tag in ds:
                    overlay_rows = ds.get((overlay_group, 0x0010), 0)
                    overlay_cols = ds.get((overlay_group, 0x0011), 0)
                    overlay_text = ds.get((overlay_group, 0x0013), "")
                    
                    if overlay_text:
                        texts.append(DICOMExtractedText(
                            text=str(overlay_text),
                            source="overlay",
                            confidence=0.9,
                            metadata={
                                "rows": overlay_rows,
                                "cols": overlay_cols,
                                "overlay_group": f"0x{overlay_group:04X}"
                            }
                        ))
            except Exception:
                pass
            
            overlay_group += 2
        
        return texts

    def _extract_annotation_text(self, ds: Dataset) -> List[DICOMExtractedText]:
        """Extract text from graphic annotations."""
        texts = []
        try:
            if (0x0070, 0x0001) in ds:
                annotations = ds[0x0070, 0x0001].value
                if annotations:
                    for annotation in annotations:
                        text_item = getattr(annotation, "TextObjectSequence", None)
                        if text_item:
                            for item in text_item.value:
                                text_value = getattr(item, "UnformattedTextValue", "")
                                if text_value:
                                    texts.append(DICOMExtractedText(
                                        text=str(text_value),
                                        source="annotation",
                                        confidence=0.85,
                                        metadata={"type": "text_object"}
                                    ))
        except Exception as e:
            logger.debug(f"Annotation extraction failed: {e}")
        return texts

    def _extract_report_text(self, ds: Dataset) -> List[DICOMExtractedText]:
        """Extract text from structured report fields."""
        texts = []
        try:
            report_text = ds.get((0x4008, 0x0100), "")
            if report_text and str(report_text).strip():
                texts.append(DICOMExtractedText(
                    text=str(report_text).strip(),
                    source="report",
                    confidence=1.0,
                    metadata={"field": "ReportText"}
                ))

            # Additional report fields
            interpretation = ds.get((0x4008, 0x0115), "")
            if interpretation and str(interpretation).strip():
                texts.append(DICOMExtractedText(
                    text=str(interpretation).strip(),
                    source="report",
                    confidence=0.9,
                    metadata={"field": "InterpretationText"}
                ))
        except Exception:
            pass
        return texts

    def _get_pixel_image(self, ds: Dataset) -> Optional[PILImage.Image]:
        """
        Convert DICOM pixel data to PIL Image for OCR processing.
        Handles common transfer syntaxes and applies windowing.
        """
        try:
            if not hasattr(ds, 'PixelData'):
                return None

            pixel_array = ds.pixel_array
            
            # Normalize to 0-255
            if pixel_array.dtype.kind == 'f':
                pixel_array = pixel_array.astype(np.float32)
                pixel_array = ((pixel_array - pixel_array.min()) / 
                              (pixel_array.max() - pixel_array.min() + 1e-10) * 255)
                pixel_array = pixel_array.astype(np.uint8)
            elif pixel_array.dtype.kind in ('i', 'u'):
                pixel_array = pixel_array.astype(np.float32)
                pixel_array = ((pixel_array - pixel_array.min()) / 
                              (pixel_array.max() - pixel_array.min() + 1e-10) * 255)
                pixel_array = pixel_array.astype(np.uint8)

            # Convert to PIL Image
            if len(pixel_array.shape) == 2:
                image = PILImage.fromarray(pixel_array, mode='L')
            elif len(pixel_array.shape) == 3:
                image = PILImage.fromarray(pixel_array[:, :, :3], mode='RGB')
            else:
                return None

            # Resize if very large (for OCR efficiency)
            max_dim = 2000
            if max(image.size) > max_dim:
                ratio = max_dim / max(image.size)
                new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
                image = image.resize(new_size, PILImage.LANCZOS)

            return image
        except Exception as e:
            logger.error(f"Failed to convert pixel data: {e}")
            return None

    def get_metadata_summary(self, file_path: str) -> Dict:
        """
        Get a summary of DICOM metadata.
        """
        ds = self.read_file(file_path)
        if ds is None:
            return {}

        summary = {}
        for tag, tag_name in self.TEXT_TAGS.items():
            try:
                if tag in ds:
                    summary[tag_name] = str(ds[tag].value)
            except Exception:
                continue

        summary["rows"] = getattr(ds, "Rows", None)
        summary["columns"] = getattr(ds, "Columns", None)
        summary["bits_allocated"] = getattr(ds, "BitsAllocated", None)
        summary["modality"] = getattr(ds, "Modality", None)
        summary["sop_class_uid"] = str(getattr(ds, "SOPClassUID", ""))

        return summary

    def get_image_for_ocr(self, file_path: str) -> Optional[bytes]:
        """
        Get DICOM pixel data as PNG bytes for OCR processing.
        """
        ds = self.read_file(file_path)
        if ds is None:
            return None

        image = self._get_pixel_image(ds)
        if image is None:
            return None

        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        return buffer.getvalue()
