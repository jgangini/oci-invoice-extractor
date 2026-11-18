import json
import unittest
from unittest.mock import MagicMock, Mock

from extractor.documents import MAX_PDF
from extractor.preview import preview
from extractor.worker import DocumentWorker
from scripts.sample_zip import sample_pdf


class PreviewTests(unittest.TestCase):
    def inputs(self, data):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        file = Mock()
        file.size.return_value = len(data)
        file.read.return_value = data
        definition = {"fields": [{"key": "number", "type": "string", "labels": ["Factura No."]}]}
        template = Mock()
        template.read.return_value = json.dumps(definition)
        result = Mock()
        cursor.var.side_effect = [Mock(getvalue=lambda: file), Mock(getvalue=lambda: template), Mock(), Mock(), result]
        return connection, cursor, result, file

    def test_single_pdf_uses_real_extractor_and_isolated_preview_calls(self):
        conn, cursor, output, _ = self.inputs(sample_pdf())
        worker = DocumentWorker()
        self.addCleanup(worker.close)
        self.assertEqual(preview(conn, worker, "a"*32), {"status": "processed"})
        calls = cursor.callproc.call_args_list
        self.assertEqual([call.args[0] for call in calls], [
            "inv_data.inv_test_worker.claim", "inv_data.inv_test_worker.stage", "inv_data.inv_test_worker.stage",
            "inv_data.inv_test_worker.save_page", "inv_data.inv_test_worker.finish"
        ])
        self.assertEqual(calls[1].args[1][-1], "EXTRACT_FIELDS")
        arguments = calls[3].args[1]
        self.assertEqual(arguments[6:8], [4, 4])
        self.assertEqual(arguments[-1], "native")
        self.assertEqual(len(json.loads(arguments[8].setvalue.call_args.args[1])), 4)
        fields = json.loads(output.setvalue.call_args.args[1])
        self.assertEqual(fields[0]["method"], "native")
        self.assertEqual(fields[0]["normalized"], "DEMO-001")
        self.assertEqual(len(fields[0]["box"]), 4)

    def test_invalid_pdf_records_only_diagnostic(self):
        conn, cursor, _, _ = self.inputs(b"bad private document")
        worker = DocumentWorker()
        self.addCleanup(worker.close)
        self.assertEqual(preview(conn, worker, "a"*32), {"status": "invalid"})
        self.assertEqual(cursor.callproc.call_args.args[1][-2:], [None, "invalid_pdf"])

    def test_oversize_pdf_never_read(self):
        conn, cursor, _, lob = self.inputs(b"x")
        lob.size.return_value = MAX_PDF + 1
        self.assertEqual(preview(conn, Mock(), "a"*32), {"status": "invalid"})
        lob.read.assert_not_called()
        self.assertEqual(cursor.callproc.call_args.args[1][-1], "pdf_limit")

    def test_invalid_receipt_never_opens_cursor(self):
        conn = Mock()
        self.assertEqual(preview(conn, Mock(), "../test"), {"status": "invalid"})
        conn.cursor.assert_not_called()
