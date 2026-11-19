import io
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageStat
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject

from extractor.geometry import ocr_lines, preview_pages
from extractor.worker import _extract_document
from scripts.sample_zip import sample_pdf


def rotated_pdf():
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(sample_pdf())).pages[0])
    page = writer.add_page(PdfReader(io.BytesIO(sample_pdf())).pages[0])
    content = DecodedStreamObject()
    content.set_data(page.get_contents().get_data().replace(b"Factura No.: DEMO-001", b"Paciente: SAMPLE-B"))
    page[NameObject("/Contents")] = writer._add_object(content)
    page.cropbox = RectangleObject((50, 100, 550, 460))
    page.rotate(90)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class GeometryTests(unittest.TestCase):
    def test_two_page_native_geometry_tracks_visible_crop_and_rotation(self):
        channel = Mock()
        fields, pages = _extract_document(rotated_pdf(), {"fields":[
            {"key":"first","type":"string","labels":["Factura No."]},
            {"key":"second","type":"string","labels":["Paciente"]},
        ]}, channel)
        self.assertNotIn(("stage", "OCR"), [call.args[0] for call in channel.send.call_args_list])
        self.assertEqual([(f["page"], f["normalized"]) for f in fields], [(1, "DEMO-001"), (2, "SAMPLE-B")])
        for field, (number, width, height, jpeg, method, total, fragments) in zip(fields, pages):
            self.assertEqual(number, field["page"])
            self.assertEqual((method, total, len(fragments)), ("native", 4, 4))
            line = next(fragment for fragment in fragments if field["normalized"] in fragment["text"])
            left, top, right, bottom = field["box"]
            self.assertLessEqual(line["box"][0], left)
            self.assertLessEqual(line["box"][1], top)
            self.assertGreaterEqual(line["box"][2], right)
            self.assertGreaterEqual(line["box"][3], bottom)
            with Image.open(io.BytesIO(jpeg)) as image:
                self.assertEqual(image.size, (width, height))
                crop = image.convert("L").crop((int(left*width), int(top*height), int(right*width), int(bottom*height)))
                self.assertLess(ImageStat.Stat(crop).mean[0], 240)
        self.assertLess(fields[0]["box"][0], .3)
        self.assertLess(fields[0]["box"][1], .2)
        self.assertGreater(fields[1]["box"][0], .8)
        self.assertLess(fields[1]["box"][1], .25)
        clipped = next(f for f in pages[1][-1] if "SAMPLE-B" in f["text"])
        self.assertNotIn("Paciente", clipped["text"])

    def test_real_ocr_persists_unmapped_lines_and_matches_image(self):
        channel = Mock()
        fields, pages = _extract_document(sample_pdf(scan=True), {
            "fields":[{"key":"number","type":"string","labels":["Factura No."]}]}, channel)
        number, width, height, _, method, total, fragments = pages[0]
        self.assertEqual((number,method,fields[0]["method"]),(1,"ocr","ocr"))
        self.assertEqual(total, len(fragments))
        self.assertGreater(len(fragments), 1)
        match = next(f for f in fragments if f["text"] == fields[0]["anchor_text"])
        self.assertEqual(match["box"], fields[0]["box"])
        self.assertLess(match["box"][1], .2)
        self.assertGreater(width, height)
        self.assertEqual([x.args[0][1] for x in channel.send.call_args_list], ["OCR","EXTRACT_FIELDS","LOCATE_EVIDENCE"])

    def test_raster_layout_is_shared_by_batch_and_preview(self):
        def recognize(_data, _page, capture):
            cells = [
                ("MED-1EST-R053", 65, 3, 79), ("FACTURA DE SERVICIOS MEDICOS", 7, 4, 55),
                ("DOCUMENTO FICTICIO", 7, 7, 28),
                ("PRESTADOR FICTICIO", 7, 10, 26), ("PAGADOR FICTICIO", 56, 11, 77),
                ("NIT. ficticio 901.999.999-X", 7, 14, 28), ("NIT 888.777.666-5", 56, 14, 76),
                ("NIT 123.456.789-1", 39, 18, 51),
                ("Código", 7, 24, 12), ("Servicio", 16, 24, 24), ("Cant,", 56, 25, 62),
                ("Unitario", 64, 25, 71), ("Total", 80, 25, 86),
                ("090200", 7, 30, 12), ("Glucosa en suero", 16, 30, 32),
                ("1", 56, 31, 57), ("$44.000", 64, 31, 72),
                ("B90201", 7, 35, 12), ("Radiografía de tórax", 16, 35, 36), ("$60.000", 80, 36, 87),
                ("TOTAL$104.000", 70, 41, 87), ("NIT 222.333.444-5", 7, 80, 28),
            ]
            capture([([[x, y], [right, y], [right, y + 3], [x, y + 3]], text, .9)
                     for text, x, y, right in cells], 100, 100)
            return "\n".join(text for text, *_ in cells)

        template = {"fields": [
            {"key": "issuer", "type": "string", "labels": ["NIT del prestador"]},
            {"key": "recipient", "type": "string", "labels": ["NIT del adquirente"]},
            {"key": "services", "type": "string", "labels": ["Descripción de servicios"]},
            {"key": "total", "type": "currency", "labels": ["TOTAL:"]},
            {"key": "number", "type": "string", "labels": ["Factura de servicios medicos"]},
        ]}
        with patch("extractor.worker.recognize", side_effect=recognize):
            batch = _extract_document(sample_pdf(scan=True), template)
            preview, _ = _extract_document(sample_pdf(scan=True), template, Mock())
        expected = ["901.999.999-X", "888.777.666-5", "Glucosa en suero; Radiografía de tórax", "104000",
                    "MED-1EST-R053"]
        self.assertEqual([row["normalized"] for row in batch], expected)
        self.assertEqual([row["normalized"] for row in preview], expected)
        self.assertEqual(preview[2]["box"], [.16, .30, .36, .38])
        self.assertEqual(preview[3]["anchor_text"], "TOTAL$104.000")

    def test_truncated_ocr_never_invents_unique_match(self):
        lines = [([[1,1],[8,1],[8,4],[1,4]],"XYZ",.9)] * 1001
        data = sample_pdf(scan=True)
        fields = [{"key":"id","method":"ocr","page":1,"original":"XYZ"}]
        page, = preview_pages(data, fields, {1:ocr_lines(lines, 10, 10)})
        self.assertNotIn("box", fields[0])
        self.assertEqual((page[4],page[5],len(page[6])), ("ocr",1001,120))

    def test_both_text_sources_share_the_document_and_page_budgets(self):
        ocr = [([[1,1],[8,1],[8,4],[1,4]], "synthetic text", .9)] * 4
        with patch("extractor.geometry.MAX_PAGE_FRAGMENTS", 2), patch("extractor.geometry.MAX_TEST_FRAGMENTS", 3):
            first, second = preview_pages(rotated_pdf(), [], {2: ocr_lines(ocr, 10, 10)})
        self.assertEqual((first[4],first[5],len(first[6])), ("native",4,2))
        self.assertEqual((second[4],second[5],len(second[6])), ("ocr",4,1))
