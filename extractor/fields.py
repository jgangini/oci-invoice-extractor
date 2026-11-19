"""Declarative label matching. Templates supply literals, never executable patterns."""
import datetime
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .ocr_layout import INVOICE_TITLE, layout_candidates

AMOUNT = r"\d+(?:\.\d{3})*(?:,\d{1,2})?"
OCR_TOTAL = re.compile(rf"TOTAL\s*((?:COP|USD|\$)\s*{AMOUNT})", re.I)
OCR_TOTAL_DAMAGED = re.compile(r"TOTA[L1]\s*S\s*(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?)", re.I)


def fold(value):
    return "".join(c for c in unicodedata.normalize("NFD", value.casefold())
                   if not unicodedata.combining(c))


def normalize(kind, value):
    if kind == "string":
        return value
    if kind in ("year", "date"):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                date = datetime.datetime.strptime(value, fmt).date()
                return str(date.year) if kind == "year" else date.isoformat()
            except ValueError:
                continue
        raise ValueError("invalid_date")
    if kind == "currency":
        match = re.fullmatch(rf"\s*(COP|USD|\$)?\s*({AMOUNT})\s*", value, re.I)
        if not match:
            raise ValueError("invalid_amount")
        digits = match[2].split(",")[0].replace(".", "").lstrip("0")
        if len(digits) > 30:
            raise ValueError("invalid_amount")
        amount = Decimal(match[2].replace(".", "").replace(",", "."))
        return format(amount, "f")
    raise ValueError("invalid_type")


def table_candidates(rows):
    """Use column roles only when the header and complete rows agree."""
    for pos, row in enumerate(rows):
        headers = [fold(s) for s in row]
        if len(row) == 2:
            for col, header in enumerate(headers):
                if header.split()[0] not in ("prestador", "emisor"):
                    continue
                if headers[1 - col].split()[0] not in ("pagador", "receptor", "adquirente"):
                    continue
                for cells in rows[pos + 1: pos + 4]:
                    if len(cells) != 2:
                        break
                    nit = re.fullmatch(r"NIT\s*[:#]?\s+([0-9][\d.-]*(?:-[\dA-Za-z])?)", cells[col], re.I)
                    if nit:
                        yield "nit del prestador", nit[1], f"{row[col]} / {cells[col]}"
        if headers == ["codigo", "servicio", "cant.", "unitario", "total"]:
            services = []
            for cells in rows[pos + 1:]:
                if len(cells) != 5 or not cells[0].isdigit() or not cells[2].isdigit():
                    break
                services.append(cells[1])
            if services:
                yield "descripcion de servicios", "; ".join(services), f"{row[1]} / {services[0]}"


def label_key(value, method):
    folded = fold(value)
    return folded.replace(" ", "") if method == "ocr" else folded


def candidates(pages, field):
    labels = {fold(s) for s in field["labels"]}
    for number, method, cells, structured in pages:
        for label, value, evidence, box in structured:
            if label in labels and len(value) <= 2000:
                yield value, number, method, evidence[:250], box
        keys = {label_key(s, method) for s in field["labels"]}
        for idx, (line, key, prefix, inline) in enumerate(cells):
            if method == "ocr" and key == label_key(INVOICE_TITLE, method):
                continue
            if method == "ocr" and field["type"] == "currency" and keys & {"total", "total:"}:
                total = OCR_TOTAL.fullmatch(line) or OCR_TOTAL_DAMAGED.fullmatch(line)
                if total:
                    yield total[1], number, method, line[:250], None
                    continue
                if key == "total":
                    continue
            if key in keys or (key.endswith(":") and key[:-1].strip() in keys):
                if idx + 1 >= len(cells):
                    continue
                value = cells[idx + 1][0]
                if re.match(r"^[^:#=]{1,80}[:#=]", value):
                    continue
            elif prefix in keys or (prefix + ":") in keys and ":" in line:
                value = inline
            else:
                continue
            if value and len(value) <= 2000:
                yield value, number, method, line[:250], None


def page_lines(number, method, text, ocr_page=None):
    rows = [[cell.strip() for cell in row.split("\t") if cell.strip()]
            for row in text.splitlines() if row.strip()]
    cells = []
    for row in rows:
        for cell in row:
            parts = re.split(r"\s*[:#=]\s*", cell, maxsplit=1)
            prefix, value = (label_key(parts[0], method), parts[1]) if len(parts) == 2 else ("", "")
            cells.append((cell, label_key(cell, method), prefix, value))
    structured = [(label, value, evidence, None) for label, value, evidence in table_candidates(rows)]
    if ocr_page is not None:
        structured.extend(layout_candidates(ocr_page, fold))
    return number, method, cells, structured


def extract(pages, template, ocr_pages=None):
    lines = [page_lines(n, method, text, ocr_pages.get(n) if ocr_pages is not None else None)
             for n, method, text in pages]
    results = []
    for field in template["fields"]:
        found = {}
        for value, number, method, evidence, box in candidates(lines, field):
            found.setdefault(value, (number, method, evidence, box))
        result = {"key": field["key"], "status": "absent"}
        if len(found) > 1:
            result["status"] = "ambiguous"
        elif found:
            value, (number, method, evidence, box) = next(iter(found.items()))
            result.update(original=value, page=number, method=method, evidence=evidence)
            if box:
                result.update(anchor_text=evidence, box=box)
            try:
                result["normalized"] = normalize(field.get("format", field["type"]), value)
                result["status"] = "found"
                if field["type"] == "currency":
                    currency = re.match(r"(?i)^(COP|USD)", value)
                    result["currency"] = currency[1].upper() if currency else field.get("currency")
            except (ValueError, InvalidOperation):
                result["status"] = "invalid"
        results.append(result)
    return results
