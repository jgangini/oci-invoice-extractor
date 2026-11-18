import json
import unittest
from pathlib import Path

from extractor.documents import page_texts
from extractor.fields import extract
from scripts.sample_zip import sample_pdf


class DemoTest(unittest.TestCase):
    def test_public_sample_without_external_corpus(self):
        template = json.loads((Path(__file__).resolve().parents[1] / "templates/facturas.json").read_text(encoding="utf-8"))
        values = {r["key"]:r.get("normalized") for r in extract(page_texts(sample_pdf()), template)}
        self.assertEqual(values["numero_factura"], "DEMO-001")
        self.assertEqual(values["anio_factura"], "2026")
        self.assertEqual(values["valor_total_factura"], "25000")
        self.assertIsNone(values["cufe"])

