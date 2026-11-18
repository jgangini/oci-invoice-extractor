import io
import json
import unittest
from unittest.mock import Mock
import zipfile

from extractor.service import process, source_event

CONFIG = dict(BUCKET_NAME="invoices", SOURCE_PREFIX="entrada/", OBJECT_NAMESPACE="demo")
EVENT = {"eventType": "com.oraclecloud.objectstorage.createobject",
         "data": {"resourceName": "entrada/lote.zip",
                  "additionalDetails": {"bucketName": "invoices", "namespace": "demo", "eTag": "v1"}}}


class RoutingTests(unittest.TestCase):
    def test_unrelated_objects_do_not_use_storage(self):
        storage = Mock()
        for key in ("otra/lote.zip", "entrada/lote.csv", "entrada/"):
            event = {**EVENT, "data": {**EVENT["data"], "resourceName": key}}
            self.assertEqual(process(event, CONFIG, Mock(), storage, Mock())["status"], "ignored")
        storage.head_object.assert_not_called()

    def test_changed_object_never_read_as_the_old_version(self):
        storage = Mock()
        storage.head_object.return_value.headers = {"etag": "v2"}
        self.assertEqual(process(EVENT, CONFIG, Mock(), storage, Mock())["status"], "superseded")
        storage.get_object.assert_not_called()

    def test_bounded_work_commits_member_before_continuation(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("a.pdf", b"%PDF-")
            archive.writestr("b.pdf", b"%PDF-")
        store, storage, enqueue = Mock(), Mock(), Mock()
        store.open.return_value = (1, 2, {"fields": []})
        store.index.return_value = set()
        storage.head_object.return_value.headers = {"etag": '"v1"', "content-length": str(len(data.getvalue()))}
        storage.get_object.return_value.data.raw = io.BytesIO(data.getvalue())
        result = process(EVENT, CONFIG, store, storage, enqueue, budget=0)
        self.assertEqual(result["status"], "continued")
        self.assertEqual(store.member.call_count, 1)
        names = [entry.filename for entry in store.index.call_args.args[2]]
        self.assertEqual(names, ["a.pdf", "b.pdf"])
        self.assertLess([c[0] for c in store.mock_calls].index("index"), [c[0] for c in store.mock_calls].index("member"))
        enqueue.assert_called_once_with(1, 2)
        store.finish.assert_not_called()

    def test_continuation_skips_terminal_documents_including_errors(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name in ("complete.pdf", "error.pdf", "pending.pdf"):
                archive.writestr(name, b"%PDF-")
        store, storage, enqueue = Mock(), Mock(), Mock()
        store.resume.return_value = ("entrada/lote.zip", "v1", None)
        store.open.return_value = (5, 2, {"fields": []})
        store.index.return_value = {"complete.pdf", "error.pdf"}
        storage.head_object.return_value.headers = {"etag": "v1", "content-length": str(len(data.getvalue()))}
        storage.get_object.return_value.data.raw = io.BytesIO(data.getvalue())
        result = process({"batch_id": 5, "generation": 2}, CONFIG, store, storage, enqueue, budget=0)
        self.assertEqual(result["documents"], 3)
        self.assertEqual(store.member.call_args.args[3].filename, "pending.pdf")
        enqueue.assert_not_called()
        store.finish.assert_called_once_with(5, 2, 3)

    def test_initial_delivery_enqueues_before_reading_a_pdf(self):
        store, storage, enqueue = Mock(), Mock(), Mock()
        store.open.return_value = (3, 1, {})
        storage.head_object.return_value.headers = {"etag": "v1", "content-length": "100"}
        result = process(EVENT, CONFIG, store, storage, enqueue, dispatch=True)
        self.assertEqual(result["status"], "queued")
        enqueue.assert_called_once_with(3, 1)
        storage.get_object.assert_not_called()

    def test_bad_zip_persists_diagnostic(self):
        store, storage = Mock(), Mock()
        store.open.return_value = (1, 2, {})
        storage.head_object.return_value.headers = {"etag": "v1", "content-length": "1"}
        storage.get_object.return_value.data.raw = io.BytesIO(b"x")
        result = process(EVENT, CONFIG, store, storage, Mock())
        self.assertEqual(result["status"], "invalid")
        store.finish.assert_called_once_with(1, 2, 0, "BadZipFile")

