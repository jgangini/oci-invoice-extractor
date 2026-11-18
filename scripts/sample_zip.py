"""Generate a one-document demonstration ZIP with independently invented values."""
import argparse
import io
from pathlib import Path
import zipfile

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, DecodedStreamObject, NameObject

LINES = ["Factura No.: DEMO-001", "NIT emisor: 000-0", "Fecha de expedicion: 2026-01-15",
         "TOTAL: COP 25.000"]


def sample_pdf(scan=False):
    output = io.BytesIO()
    if scan:
        image = Image.new("RGB", (1200, 800), "white")
        pen = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=38)
        for index, line in enumerate(LINES):
            pen.text((60, 80 + index * 110), line, fill="black", font=font)
        image.save(output, format="PDF", resolution=110)
    else:
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=480)
        font = DictionaryObject({NameObject("/Type"):NameObject("/Font"), NameObject("/Subtype"):NameObject("/Type1"),
                                 NameObject("/BaseFont"):NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 16 Tf 22 TL 30 400 Td " + " T* ".join(f"({line}) Tj" for line in LINES) + " ET").encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(output)
    return output.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(exist_ok=True, parents=True)
    with zipfile.ZipFile(args.output, "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo.pdf", sample_pdf(args.scan))

