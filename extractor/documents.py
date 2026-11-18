"""Read bounded PDF members in memory. Archive paths never become filesystem paths."""
import io
import math
import re
import stat
import zipfile
from pathlib import PurePosixPath

from pypdf import PdfReader

MAX_ZIP = 48 * 1024 * 1024
MAX_PDF = 12 * 1024 * 1024
MAX_PAGES = 64


class InputError(ValueError):
    """A fixed diagnostic code for malformed document input."""


def pdf_members(archive):
    entries = archive.infolist()
    if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 128 * 1024**2:
        raise InputError("archive_limit")
    seen = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                or ":" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16)):
            raise InputError("invalid_archive_path")
        if entry.is_dir() or path.suffix.lower() != ".pdf":
            continue
        if (len(entry.filename.encode("utf-8")) > 1024 or entry.filename in seen
                or entry.flag_bits & 1 or entry.file_size > MAX_PDF):
            raise InputError("invalid_pdf_member")
        seen.add(entry.filename)
        yield entry


def read_archive(data):
    if len(data) > MAX_ZIP:
        raise InputError("archive_limit")
    return zipfile.ZipFile(io.BytesIO(data))


def native_text(page):
    """Keep row/column boundaries on horizontal pages drawn one cell at a time."""
    from collections import defaultdict

    rows = defaultdict(list)
    compatible = True
    size = 0

    def collect(text, cm, tm, font, font_size):
        nonlocal compatible, size
        cell = text.strip()
        if not cell:
            return
        size += len(text)
        if size > 200_000:
            raise InputError("page_text_limit")
        if not compatible:
            return
        if "\n" in cell or "\r" in cell or any(abs(v) > 0.001 for v in (cm[1], cm[2], tm[1], tm[2])):
            compatible = False
            return
        x = tm[4] * cm[0] + cm[4]
        y = tm[5] * cm[3] + cm[5]
        if not (math.isfinite(x) and math.isfinite(y)):
            compatible = False
            return
        rows[round(y, 1)].append((x, cell))

    text = page.extract_text(visitor_text=collect) or ""
    if len(text) > 200_000:
        raise InputError("page_text_limit")
    # Use row ordering only for the recognized invoice table. Free-form text
    # and rotated/multiline layouts retain pypdf's ordinary reading order.
    if compatible and any({"servicio", "unitario", "total"} <= {s.casefold() for _, s in cells}
                          for cells in rows.values()):
        return "\n".join("\t".join(s for _, s in sorted(rows[y])) for y in sorted(rows, reverse=True))
    return text


def readable(text):
    visible = "".join(text.split())
    if len(visible) < 20:
        return False
    damaged = sum(1 for ch in visible if ch == "\ufffd" or ord(ch) < 32)
    damaged += sum(len(match[0]) for match in re.finditer(r"\(cid:\d+\)", visible, re.I))
    return damaged < len(visible) / 4


def page_texts(data, ocr=None):
    if not data.startswith(b"%PDF-"):
        raise InputError("invalid_pdf")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted or not 0 < len(reader.pages) <= MAX_PAGES:
        raise InputError("unsupported_pdf")
    for number, page in enumerate(reader.pages, 1):
        text = native_text(page)
        if readable(text):
            yield number, "native", text
        else:
            yield number, "ocr", (ocr or recognize)(data, number - 1)


def recognize(data, page_index, capture=None):
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR

    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
    with pdfium.PdfDocument(data) as pdf:
        page = pdf[page_index]
        width, height = page.get_size()
        if not (math.isfinite(width * height) and min(width, height) > 0):
            raise InputError("page_geometry")
        scale = min(2.5, 2600 / max(width, height))
        bitmap = page.render(scale=scale)
        try:
            lines, _ = _ocr_engine(bitmap.to_pil().convert("RGB"))
            if capture is not None:
                capture(lines or [], bitmap.width, bitmap.height)
            return "\n".join(item[1] for item in lines or [])
        finally:
            bitmap.close()
            page.close()


_ocr_engine = None
