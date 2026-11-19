"""Locate preview evidence on the same visible PDFium canvas used for the JPEG."""
import io
import math
import re
from contextlib import closing

import pypdfium2 as pdfium

MAX_IMAGES = 16
MAX_IMAGE_BYTES = 256 * 1024
MAX_PAGE_FRAGMENTS = 120
MAX_TEST_FRAGMENTS = 800


def _bounds(points, width, height):
    if len(points) < 2 or width <= 0 or height <= 0:
        return None
    x, y = zip(*points)
    edges = (min(x) / width, min(y) / height, max(x) / width, max(y) / height)
    if not all(math.isfinite(v) for v in edges):
        return None
    box = [round(max(0, min(1, v)), 6) for v in edges]
    return box if box[0] < box[2] and box[1] < box[3] else None


def ocr_lines(lines, width, height):
    bounded = [(s[:2001], _bounds(points, width, height)) for points, s, _ in lines[:1000]]
    return len(lines), bounded


def _ocr_match(lines, field):
    value = field["original"]
    evidence = field.get("evidence", "")
    pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
    matches = [(s, box) for s, box in lines if box and s == evidence and value in s]
    if not matches:
        matches = [(s, box) for s, box in lines if box and pattern.search(s)]
    return (matches[0][0][:250], matches[0][1]) if len(matches) == 1 else None


def _unique_hit(text, value):
    with closing(text.search(value, match_case=True, match_whole_word=True)) as search:
        hit = search.get_next()
        return hit if hit and search.get_next() is None else None


def _native_match(text, bitmap, page, field):
    value = field["original"]
    hit = _unique_hit(text, value)
    if not hit:
        evidence = field.get("evidence", "")
        if value not in evidence or len(evidence) <= len(value):
            return None
        hit = _unique_hit(text, evidence)
        value = evidence
    if not hit or text.count_rects(*hit) != 1:
        return None
    box = _bitmap_box(text.get_rect(0), bitmap, bitmap.get_posconv(page))
    return (value[:250], box) if box else None


def _bitmap_box(rect, bitmap, position):
    left, bottom, right, top = rect
    corners = [position.to_bitmap(x, y) for x in (left, right) for y in (bottom, top)]
    return _bounds(corners, bitmap.width, bitmap.height)


def _native_lines(text, page, bitmap, limit):
    left, bottom, right, top = page.get_bbox()
    position = bitmap.get_posconv(page)
    lines = []
    total = 0
    for index in range(text.count_rects()):
        x0, y0, x1, y1 = text.get_rect(index)
        visible = max(x0, left), max(y0, bottom), min(x1, right), min(y1, top)
        if visible[0] >= visible[2] or visible[1] >= visible[3]:
            continue
        total += 1
        if len(lines) < limit:
            label = text.get_text_bounded(*visible).strip()
            box = _bitmap_box(visible, bitmap, position)
            if label and box:
                lines.append((label, box))
            else:
                total -= 1
    return total, lines


def _jpeg(bitmap):
    with bitmap.to_pil().convert("RGB") as image:
        for quality in (70, 35):
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality)
            if output.tell() <= MAX_IMAGE_BYTES:
                return output.getvalue()
    return None


def _render(page, number, fields, ocr_page, limit):
    width, height = page.get_size()
    if min(width, height) <= 0 or not math.isfinite(width * height):
        return None
    with closing(page.render(scale=min(2, 1280 / max(width, height)))) as bitmap:
        picture = _jpeg(bitmap)
        if not picture:
            return None
        with closing(page.get_textpage()) as text:
            method = "native" if ocr_page is None else "ocr"
            total, lines = _native_lines(text, page, bitmap, limit) if ocr_page is None else ocr_page
            # Truncated OCR candidates cannot prove a unique field match.
            candidates = lines if total == len(lines) else []
            for field in fields:
                if field.get("page") != number or not field.get("original") or field.get("box"):
                    continue
                try:
                    match = _ocr_match(candidates, field) if field["method"] == "ocr" else _native_match(text, bitmap, page, field)
                except (pdfium.PdfiumError, ValueError):
                    continue
                if match:
                    field["anchor_text"], field["box"] = match
        fragments = [{"text": label[:500], "box": box, "cut": int(len(label) > 500)}
                     for label, box in lines[:limit]]
        return number, bitmap.width, bitmap.height, picture, method, total, fragments


def preview_pages(data, fields, ocr_pages):
    images = []
    remaining_fragments = MAX_TEST_FRAGMENTS
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        return images
    with doc:
        matched = [f["page"] for f in fields if f.get("page")]
        numbers = list(dict.fromkeys([*matched, *range(1, len(doc) + 1)]))[:MAX_IMAGES]
        for number in sorted(numbers):
            try:
                with closing(doc[number - 1]) as page:
                    image = _render(page, number, fields, ocr_pages.get(number),
                                    min(MAX_PAGE_FRAGMENTS, remaining_fragments))
            except (pdfium.PdfiumError, OverflowError, ValueError):
                continue
            if image:
                remaining_fragments -= len(image[-1])
                images.append(image)
    return images
