import io
import unittest
from unittest.mock import patch
import zipfile

from extractor import documents
from extractor.fields import extract


def field(key, kind, label):
    return {"key": key, "type": kind, "labels": [label]}


class FieldTests(unittest.TestCase):
    def test_conflicting_identifiers_are_not_committed(self):
        pages = [(1, "native", "Factura No.: A-01"), (2, "native", "Factura No.: B-02")]
        result, = extract(pages, {"fields": [field("number", "string", "Factura No.")]})
        self.assertEqual(result["status"], "ambiguous")
        self.assertNotIn("normalized", result)

    def test_context_dates_currency_and_explicit_nulls(self):
        template = {"fields": [field("issued", "date", "Fecha de emisión"),
                               field("total", "currency", "TOTAL:"),
                               field("patient", "string", "Paciente")]}
        results = {v["key"]: v for v in extract([(1, "native",
            "Fecha de emisión: 31/02/2025\nSubtotal: 7.000\nTOTAL:\nCOP 8.000,50")], template)}
        self.assertEqual(results["issued"]["status"], "invalid")
        self.assertEqual(results["total"]["normalized"], "8000.50")
        self.assertEqual(results["total"]["currency"], "COP")
        self.assertEqual(results["patient"]["status"], "absent")

    def test_only_aligned_issuer_column_can_supply_nit(self):
        template = {"fields": [field("nit", "string", "NIT del prestador")]}
        page = "PRESTADOR\tPAGADOR\nSin identificación\tNIT 987-6"
        result, = extract([(1, "native", page)], template)
        self.assertEqual(result["status"], "absent")

    def test_labels_are_literal_and_next_field_is_not_a_value(self):
        template = {"fields": [field("pattern", "string", "Paciente.*"), field("p", "string", "Paciente")]}
        results = extract([(1, "native", "Paciente:\nFecha de emisión: 29/08/2024")], template)
        self.assertEqual([row["status"] for row in results], ["absent", "absent"])


class InputLimits(unittest.TestCase):
    def test_oversize_archive_and_member(self):
        with patch.object(documents, "MAX_ZIP", 2), self.assertRaisesRegex(ValueError, "archive_limit"):
            documents.read_archive(b"123")
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("invoice.pdf", b"%PDF-larger")
        with documents.read_archive(blob.getvalue()) as archive, patch.object(documents, "MAX_PDF", 5):
            with self.assertRaisesRegex(ValueError, "invalid_pdf_member"):
                list(documents.pdf_members(archive))

    def test_archive_metadata_and_non_pdf_are_not_input(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "w") as archive:
            archive.writestr("index.csv", "not an invoice")
            archive.writestr("invoices/test.PDF", b"not a PDF")
        with documents.read_archive(blob.getvalue()) as archive:
            item, = documents.pdf_members(archive)
            with self.assertRaisesRegex(ValueError, "invalid_pdf"):
                list(documents.page_texts(archive.read(item)))

    def test_illegible_layer_is_not_counted_as_usable_text(self):
        self.assertFalse(documents.readable("(cid:45)" * 12))
        self.assertFalse(documents.readable("\ufffd" * 100))
        self.assertTrue(documents.readable("This native paragraph needs no OCR for absent fields."))
