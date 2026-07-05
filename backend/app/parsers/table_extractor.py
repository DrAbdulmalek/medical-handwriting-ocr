"""
Table Extractor Module for Medical Handwriting OCR.

Provides advanced table detection and extraction from images and PDF
documents using Surya OCR and Camelot, with fallback mechanisms when
external libraries are unavailable.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models
# =============================================================================


class TableData(BaseModel):
    """Structured representation of an extracted table."""

    table_id: str = Field("", description="Unique identifier for this table extraction")
    headers: List[str] = Field(default_factory=list, description="Column header texts")
    rows: List[List[str]] = Field(
        default_factory=list,
        description="Table body rows (list of cell-string lists)",
    )
    confidence: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Overall extraction confidence (0–1)",
    )
    bbox: Optional[Dict[str, int]] = Field(
        None,
        description="Bounding box {x1, y1, x2, y2} in image coordinates",
    )
    row_count: int = Field(0, description="Number of data rows")
    col_count: int = Field(0, description="Number of columns")
    page_number: Optional[int] = Field(None, description="Page number (for PDF sources)")
    source: str = Field(
        "",
        description="Extraction method used (e.g. 'surya', 'camelot', 'pdfplumber')",
    )
    raw_cells: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Raw cell data before cleaning (for debugging)",
    )

    def to_markdown(self) -> str:
        """Render the table as a Markdown table string."""
        if not self.headers and not self.rows:
            return ""
        col_count = self.col_count or max(len(self.headers), max((len(r) for r in self.rows), default=0))
        headers = self.headers or [f"Col{i}" for i in range(col_count)]

        def _pad(row: List[str], width: int) -> List[str]:
            padded = list(row)
            while len(padded) < width:
                padded.append("")
            return padded[:width]

        lines: List[str] = []
        header_line = "| " + " | ".join(_pad(headers, col_count)) + " |"
        sep_line = "| " + " | ".join(["---"] * col_count) + " |"
        lines.append(header_line)
        lines.append(sep_line)
        for row in self.rows:
            lines.append("| " + " | ".join(_pad(row, col_count)) + " |")
        return "\n".join(lines)

    def to_dict_list(self) -> List[Dict[str, str]]:
        """Convert rows to a list of dicts mapping headers to cell values."""
        col_count = self.col_count or max(len(self.headers), max((len(r) for r in self.rows), default=0))
        headers = self.headers or [f"col_{i}" for i in range(col_count)]
        result: List[Dict[str, str]] = []
        for row in self.rows:
            record: Dict[str, str] = {}
            for i, h in enumerate(headers):
                record[h] = row[i] if i < len(row) else ""
            result.append(record)
        return result


# =============================================================================
# TableExtractor
# =============================================================================


class TableExtractor:
    """
    Advanced table extraction engine for medical documents.

    Primary engines:
    * **Surya OCR** – deep-learning based table recognition from images
    * **Camelot** – PDF-focused table extraction with line-based detection

    Fallback engines:
    * **pdfplumber** – simple PDF table extraction
    * **OpenCV** – basic contour-based table detection from images

    All extracted data passes through a cleaning pipeline that normalises
    whitespace, merges split cells, and validates structure.
    """

    def __init__(self) -> None:
        self._surya_available: Optional[bool] = None
        self._camelot_available: Optional[bool] = None
        self._pdfplumber_available: Optional[bool] = None
        self._cv2_available: Optional[bool] = None
        self._pil_available: Optional[bool] = None
        self._min_table_confidence: float = 0.3
        logger.info("TableExtractor initialized")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_tables_from_image(self, image_path: str) -> List[TableData]:
        """
        Detect and extract tables from an image file.

        Parameters
        ----------
        image_path : str
            Path to the image (PNG, JPEG, TIFF, etc.).

        Returns
        -------
        list[TableData]
            List of extracted tables sorted by confidence (highest first).
        """
        logger.info("Extracting tables from image: %s", image_path)

        tables: List[TableData] = []

        # Try Surya first
        if self._check_surya():
            tables = self._extract_surya_image(image_path)

        # Fallback: OpenCV contour-based detection
        if not tables:
            logger.info("Surya returned no tables; falling back to OpenCV")
            tables = self._extract_cv2_contours(image_path)

        # Clean all tables
        cleaned = [self.clean_table_data(t) for t in tables]
        cleaned.sort(key=lambda t: t.confidence, reverse=True)

        logger.info("Extracted %d tables from image", len(cleaned))
        return cleaned

    def extract_tables_from_pdf(self, file_path: str) -> List[TableData]:
        """
        Detect and extract tables from a PDF document.

        Parameters
        ----------
        file_path : str
            Path to the PDF file.

        Returns
        -------
        list[TableData]
            List of extracted tables across all pages.
        """
        logger.info("Extracting tables from PDF: %s", file_path)

        tables: List[TableData] = []

        # Try Camelot
        if self._check_camelot():
            tables = self._extract_camelot(file_path)

        # Fallback: pdfplumber
        if not tables:
            logger.info("Camelot returned no tables; falling back to pdfplumber")
            tables = self._extract_pdfplumber(file_path)

        # Clean all tables
        cleaned = [self.clean_table_data(t) for t in tables]
        cleaned.sort(key=lambda t: t.confidence, reverse=True)

        logger.info("Extracted %d tables from PDF", len(cleaned))
        return cleaned

    def clean_table_data(self, raw_table: TableData) -> TableData:
        """
        Clean and normalise raw table data.

        Performs:
        * Whitespace normalisation in cells
        * Empty row / column removal
        * Header validation
        * Confidence adjustment
        * Column count alignment

        Parameters
        ----------
        raw_table : TableData
            The raw table data to clean.

        Returns
        -------
        TableData
            Cleaned table with consistent structure.
        """
        cleaned = raw_table.model_copy(deep=True)

        # Normalise cell whitespace
        cleaned.headers = [self._normalise_cell(h) for h in cleaned.headers]
        cleaned.rows = [
            [self._normalise_cell(c) for c in row]
            for row in cleaned.rows
        ]

        # Remove completely empty rows
        cleaned.rows = [row for row in cleaned.rows if any(cell.strip() for cell in row)]

        # Remove completely empty columns
        if cleaned.rows:
            col_count = max(len(r) for r in cleaned.rows)
            non_empty_cols: List[int] = []
            for col_idx in range(col_count):
                has_content = any(
                    col_idx < len(row) and row[col_idx].strip()
                    for row in cleaned.rows
                )
                if has_content:
                    non_empty_cols.append(col_idx)

            cleaned.headers = [cleaned.headers[i] for i in non_empty_cols if i < len(cleaned.headers)]
            cleaned.rows = [
                [row[i] for i in non_empty_cols if i < len(row)]
                for row in cleaned.rows
            ]

        # Align column count
        col_count = max(len(cleaned.headers), max((len(r) for r in cleaned.rows), default=0))
        cleaned.col_count = col_count
        cleaned.row_count = len(cleaned.rows)

        # Pad headers
        while len(cleaned.headers) < col_count:
            cleaned.headers.append(f"Col{len(cleaned.headers)}")

        # Pad rows
        for row in cleaned.rows:
            while len(row) < col_count:
                row.append("")

        # Adjust confidence based on data quality
        if cleaned.rows:
            empty_cell_ratio = sum(
                1 for row in cleaned.rows for cell in row if not cell.strip()
            ) / (col_count * len(cleaned.rows))
            confidence_penalty = empty_cell_ratio * 0.3
            cleaned.confidence = max(0.0, cleaned.confidence - confidence_penalty)

        if not cleaned.rows and not cleaned.headers:
            cleaned.confidence = 0.0

        return cleaned

    # ------------------------------------------------------------------
    # Availability checks
    # ------------------------------------------------------------------

    def _check_surya(self) -> bool:
        """Check if surya-ocr is available."""
        if self._surya_available is None:
            try:
                from surya.ocr import run_ocr  # noqa: F401
                self._surya_available = True
                logger.info("surya-ocr is available")
            except ImportError:
                self._surya_available = False
                logger.warning("surya-ocr is not installed")
        return self._surya_available

    def _check_camelot(self) -> bool:
        """Check if camelot-py is available."""
        if self._camelot_available is None:
            try:
                import camelot  # noqa: F401
                self._camelot_available = True
                logger.info("camelot-py is available")
            except ImportError:
                self._camelot_available = False
                logger.warning("camelot-py is not installed")
        return self._camelot_available

    def _check_pdfplumber(self) -> bool:
        """Check if pdfplumber is available."""
        if self._pdfplumber_available is None:
            try:
                import pdfplumber  # noqa: F401
                self._pdfplumber_available = True
                logger.info("pdfplumber is available")
            except ImportError:
                self._pdfplumber_available = False
                logger.warning("pdfplumber is not installed")
        return self._pdfplumber_available

    def _check_cv2(self) -> bool:
        """Check if OpenCV is available."""
        if self._cv2_available is None:
            try:
                import cv2  # noqa: F401
                self._cv2_available = True
                logger.info("OpenCV is available")
            except ImportError:
                self._cv2_available = False
        return self._cv2_available

    def _check_pil(self) -> bool:
        """Check if Pillow is available."""
        if self._pil_available is None:
            try:
                from PIL import Image  # noqa: F401
                self._pil_available = True
                logger.info("Pillow is available")
            except ImportError:
                self._pil_available = False
        return self._pil_available

    # ------------------------------------------------------------------
    # Surya extraction
    # ------------------------------------------------------------------

    def _extract_surya_image(self, image_path: str) -> List[TableData]:
        """Extract tables from an image using Surya OCR."""
        tables: List[TableData] = []
        try:
            import uuid
            from PIL import Image as PILImage
            from surya.table_recognition import TableRecognitionPredictor
            from surya.ocr import run_ocr

            predictor = TableRecognitionPredictor()
            img = PILImage.open(image_path)

            # Detect table regions
            table_regions = predictor.detect(img)

            for idx, region in enumerate(table_regions):
                try:
                    bbox = region.bbox if hasattr(region, "bbox") else None
                    if bbox:
                        x1, y1, x2, y2 = [int(v) for v in bbox]
                    else:
                        continue

                    # Crop table region
                    table_crop = img.crop((x1, y1, x2, y2))

                    # Run OCR on the cropped table region
                    ocr_results = run_ocr([table_crop], [""])

                    headers: List[str] = []
                    rows: List[List[str]] = []
                    confidence = 0.8

                    if ocr_results and ocr_results[0]:
                        text_lines = ocr_results[0].text_lines if hasattr(ocr_results[0], "text_lines") else []
                        for line_idx, line in enumerate(text_lines):
                            cells = self._split_table_line(line.text if hasattr(line, "text") else str(line))
                            if line_idx == 0 and self._is_likely_header(cells):
                                headers = cells
                            else:
                                rows.append(cells)

                    if not headers and rows:
                        headers = rows.pop(0)

                    tables.append(
                        TableData(
                            table_id=str(uuid.uuid4()),
                            headers=headers,
                            rows=rows,
                            row_count=len(rows),
                            col_count=max(len(headers), max((len(r) for r in rows), default=0)),
                            confidence=confidence,
                            bbox={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                            source="surya",
                        )
                    )
                except Exception as exc:
                    logger.debug("Failed to process Surya table region %d: %s", idx, exc)

        except Exception as exc:
            logger.warning("Surya table extraction failed: %s", exc)

        return tables

    def _split_table_line(self, line: str) -> List[str]:
        """Split a table line into cells using common delimiters."""
        # Try common delimiters
        for delimiter in [" | ", "\t", "  "]:
            if delimiter in line:
                cells = line.split(delimiter)
                if len(cells) >= 2:
                    return [c.strip() for c in cells]
        return [line.strip()]

    def _is_likely_header(self, cells: List[str]) -> bool:
        """Heuristic: determine if a row of cells looks like a header."""
        if not cells:
            return False
        # Headers often have shorter cells and are more uniform
        avg_len = sum(len(c) for c in cells) / len(cells)
        return avg_len < 30

    # ------------------------------------------------------------------
    # Camelot extraction
    # ------------------------------------------------------------------

    def _extract_camelot(self, file_path: str) -> List[TableData]:
        """Extract tables from a PDF using Camelot."""
        tables: List[TableData] = []
        try:
            import uuid
            import camelot

            # Try both stream and lattice modes
            for mode in ["stream", "lattice"]:
                try:
                    extracted = camelot.read_pdf(file_path, flavor=mode, pages="all")
                    for idx in range(len(extracted)):
                        df = extracted[idx].df
                        confidence = extracted[idx].accuracy / 100.0

                        if confidence < self._min_table_confidence:
                            continue

                        headers: List[str] = []
                        rows: List[List[str]] = []

                        if len(df) > 0:
                            # First row as header
                            headers = [str(v) for v in df.iloc[0].tolist()]
                            for row_idx in range(1, len(df)):
                                rows.append([str(v) for v in df.iloc[row_idx].tolist()])

                        # Get bounding box if available
                        bbox = None
                        try:
                            bbox_coords = extracted[idx]._bbox
                            if bbox_coords and len(bbox_coords) == 4:
                                bbox = {
                                    "x1": int(bbox_coords[0]),
                                    "y1": int(bbox_coords[1]),
                                    "x2": int(bbox_coords[2]),
                                    "y2": int(bbox_coords[3]),
                                }
                        except Exception:
                            pass

                        tables.append(
                            TableData(
                                table_id=str(uuid.uuid4()),
                                headers=headers,
                                rows=rows,
                                row_count=len(rows),
                                col_count=max(len(headers), max((len(r) for r in rows), default=0)),
                                confidence=confidence,
                                bbox=bbox,
                                page_number=extracted[idx].page,
                                source=f"camelot-{mode}",
                            )
                        )
                except Exception as mode_exc:
                    logger.debug("Camelot %s mode failed for %s: %s", mode, file_path, mode_exc)

        except Exception as exc:
            logger.warning("Camelot extraction failed: %s", exc)

        return tables

    # ------------------------------------------------------------------
    # pdfplumber extraction
    # ------------------------------------------------------------------

    def _extract_pdfplumber(self, file_path: str) -> List[TableData]:
        """Extract tables from a PDF using pdfplumber as fallback."""
        tables: List[TableData] = []
        try:
            import uuid
            import pdfplumber

            with pdfplumber.open(file_path) as pdf:
                for page_idx, page in enumerate(pdf.pages):
                    extracted_tables = page.extract_tables()
                    for tbl_idx, raw_table in enumerate(extracted_tables):
                        if not raw_table or len(raw_table) < 2:
                            continue

                        headers = [str(c or "") for c in raw_table[0]]
                        rows = [[str(c or "") for c in row] for row in raw_table[1:]]

                        tables.append(
                            TableData(
                                table_id=str(uuid.uuid4()),
                                headers=headers,
                                rows=rows,
                                row_count=len(rows),
                                col_count=max(len(headers), max((len(r) for r in rows), default=0)),
                                confidence=0.7,
                                page_number=page_idx + 1,
                                source="pdfplumber",
                            )
                        )

        except Exception as exc:
            logger.warning("pdfplumber extraction failed: %s", exc)

        return tables

    # ------------------------------------------------------------------
    # OpenCV contour-based fallback
    # ------------------------------------------------------------------

    def _extract_cv2_contours(self, image_path: str) -> List[TableData]:
        """
        Basic table detection using OpenCV contour analysis.

        This is a last-resort fallback that looks for rectangular regions
        with grid-like internal line structures.
        """
        tables: List[TableData] = []

        if not self._check_cv2() or not self._check_pil():
            return tables

        try:
            import uuid
            import cv2
            import numpy as np
            from PIL import Image as PILImage

            img = cv2.imread(image_path)
            if img is None:
                logger.error("Failed to read image: %s", image_path)
                return tables

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)

            # Detect horizontal and vertical lines
            horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
            vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))

            horizontal_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
            vertical_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=2)

            # Combine line masks
            table_mask = cv2.add(horizontal_lines, vertical_lines)

            # Find contours
            contours, _ = cv2.findContours(table_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = w * h
                img_area = img.shape[0] * img.shape[1]

                # Filter: table must be reasonable size and not too small
                if area < img_area * 0.01 or w < 100 or h < 50:
                    continue

                # Crop and run basic text extraction
                try:
                    from PIL import Image as PILImage
                    crop_img = img[y : y + h, x : x + w]
                    gray_crop = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
                    _, binary_crop = cv2.threshold(gray_crop, 150, 255, cv2.THRESH_BINARY)

                    # Detect horizontal lines in crop to determine rows
                    h_lines_crop = cv2.morphologyEx(
                        cv2.bitwise_not(binary_crop), cv2.MORPH_OPEN, horizontal_kernel, iterations=2
                    )
                    row_contours, _ = cv2.findContours(h_lines_crop, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    if len(row_contours) < 2:
                        continue

                    tables.append(
                        TableData(
                            table_id=str(uuid.uuid4()),
                            headers=[],
                            rows=[],
                            row_count=0,
                            col_count=0,
                            confidence=0.4,
                            bbox={"x1": x, "y1": y, "x2": x + w, "y2": y + h},
                            source="opencv-contours",
                        )
                    )
                except Exception as crop_exc:
                    logger.debug("Failed to process OpenCV contour crop: %s", crop_exc)

        except Exception as exc:
            logger.warning("OpenCV contour extraction failed: %s", exc)

        return tables

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_cell(cell: str) -> str:
        """
        Normalise a table cell string.

        * Collapse internal whitespace
        * Strip leading / trailing whitespace
        * Replace common non-breaking space characters
        """
        if not cell:
            return ""
        cell = cell.replace("\xa0", " ").replace("\u200b", "")
        cell = re.sub(r"\s+", " ", cell)
        return cell.strip()


# =============================================================================
# Singleton instance
# =============================================================================

table_extractor = TableExtractor()
