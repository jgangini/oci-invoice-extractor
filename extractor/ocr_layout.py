"""Recover field candidates from the positions of complete OCR fragments."""
import re


SERVICE_HEADERS = ["codigo", "servicio", "cant", "unitario", "total"]
SERVICE_CODE = re.compile(r"[A-Z0-9]\d{5}", re.I)
MONEY_CELL = re.compile(r"(?:\$\s*)?\d+(?:\.\d{3})*(?:,\d{1,2})?")
NIT = re.compile(r"nit[\s.:#]*(?:ficticio\s*)?((?:\d{1,3}(?:\.\d{3}){2}|\d{9})-[\dA-Z])", re.I)
INVOICE_TITLE = "factura de servicios medicos"
INVOICE_NUMBER = re.compile(r"(?=.{1,64}$)(?=.*\d)[A-Z0-9]+(?:[- /][A-Z0-9]+)*", re.I)
ROLES = {
    "prestador": "nit del prestador",
    "emisor": "nit del prestador",
    "pagador": "nit del adquirente",
    "receptor": "nit del adquirente",
    "adquirente": "nit del adquirente",
}


def _center(box):
    return (box[1] + box[3]) / 2


def _rows(fragments, slope=0):
    def baseline(box):
        return _center(box) - slope * box[0]

    row = []
    fragments = sorted(fragments, key=lambda item: baseline(item[1]))
    for text, box in fragments:
        if row:
            first = row[0][1]
            tolerance = min(first[3] - first[1], box[3] - box[1]) * 0.75
            if baseline(box) - baseline(first) > tolerance:
                yield sorted(row, key=lambda item: item[1][0])
                row = []
        row.append((text, box))
    if row:
        yield sorted(row, key=lambda item: item[1][0])


def _service_tables(fragments, fold):
    headers = [(fold(text).rstrip(".,:"), text, box) for text, box in fragments]
    for label, _, code in headers:
        if label != "codigo":
            continue
        nearby = sorted(((key, value, box) for key, value, box in headers
                         if box[0] >= code[0] and key in SERVICE_HEADERS
                         and abs(_center(box) - _center(code)) <= 1.6 * (code[3] - code[1])),
                        key=lambda item: item[2][0])
        if [key for key, _, _ in nearby] != SERVICE_HEADERS or len({box[0] for _, _, box in nearby}) != 5:
            continue
        total = nearby[-1][2]
        slope = (_center(total) - _center(code)) / (total[0] - code[0])
        headers_row = [(value, box) for _, value, box in nearby]
        rows = _rows(fragments, slope)
        for row in rows:
            if row == headers_row:
                services = _service_rows(rows, row)
                if services:
                    yield services
                break


def _service_rows(rows, headers):
    code_x, service_x, quantity_x, price_x, _ = (box[0] for _, box in headers)
    description_x = (code_x + service_x) / 2
    amount_x = (quantity_x + price_x) / 2
    descriptions = []
    boxes = []
    for row in rows:
        if not any(box[0] < description_x and SERVICE_CODE.fullmatch(text) for text, box in row):
            break
        cells = [(text, box) for text, box in row if description_x <= box[0] < box[2] <= quantity_x]
        description = " ".join(text for text, _ in cells)
        if not any(char.isalpha() for char in description):
            return None
        if not any(box[0] >= amount_x and MONEY_CELL.fullmatch(text) for text, box in row):
            return None
        descriptions.append(description)
        boxes.extend(box for _, box in cells)
    if descriptions:
        box = [min(b[0] for b in boxes), min(b[1] for b in boxes),
               max(b[2] for b in boxes), max(b[3] for b in boxes)]
        return "descripcion de servicios", "; ".join(descriptions), descriptions[0], box
    return None


def _role_nits(rows, headers):
    left, right = (box for _, box in headers)
    split = (left[2] + right[0]) / 2
    bottom = max(left[3], right[3]) + 4 * max(left[3] - left[1], right[3] - right[1])
    for row in rows:
        if min(box[1] for _, box in row) > bottom:
            break
        for text, box in row:
            match = NIT.fullmatch(text)
            if match and not box[0] < split < box[2]:
                role = headers[int(box[0] >= split)][0]
                yield role, match[1], text, box


def layout_candidates(ocr_page, fold):
    total, fragments = ocr_page
    if total != len(fragments):
        return
    fragments = [(text, box) for text, box in fragments if box]
    yield from _service_tables(fragments, fold)
    title = INVOICE_TITLE.replace(" ", "")
    titles = [box for text, box in fragments if fold(text).replace(" ", "") == title]
    for _, top, right, bottom in titles:
        for value, box in fragments:
            if box[0] >= right and top - (bottom - top) <= _center(box) <= bottom and INVOICE_NUMBER.fullmatch(value):
                yield INVOICE_TITLE, value, value, box
    rows = list(_rows(fragments))
    for index, row in enumerate(rows):
        if len(row) != 2:
            continue
        labels = [fold(text).rstrip(".,:") for text, _ in row]
        roles = [ROLES.get(label.split()[0]) if label else None for label in labels]
        if (any(bottom < row[0][1][1] for _, _, _, bottom in titles)
                and re.match(r"ips(?:\s|modelo)", labels[0]) and re.match(r"eps(?:\s|modelo)", labels[1])):
            roles = [ROLES["prestador"], ROLES["pagador"]]
        if all(roles) and roles[0] != roles[1]:
            yield from _role_nits(rows[index + 1:], list(zip(roles, (box for _, box in row))))
