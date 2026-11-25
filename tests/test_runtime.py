import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import oci
import oracledb

from extractor import service
from extractor.service import process, source_event

CONFIG = dict(BUCKET_NAME="invoices", SOURCE_PREFIX="entrada/", OBJECT_NAMESPACE="demo")
EVENT = {"eventType": "com.oraclecloud.objectstorage.createobject",
         "data": {"resourceName": "entrada/lote.zip",
                  "additionalDetails": {"bucketName": "invoices", "namespace": "demo", "eTag": "v1"}}}


class RoutingTests(unittest.TestCase):
    def test_missing_probe_batch_leaves_database_and_storage_unchanged(self):
        store, storage, enqueue = Mock(), Mock(), Mock()
        store.resume.side_effect = oracledb.DatabaseError(SimpleNamespace(code=1403))
        result = process({"batch_id": -1, "generation": -1}, CONFIG, store, storage, enqueue)
        self.assertEqual(result, {"status": "superseded"})
        self.assertEqual([entry[0] for entry in store.mock_calls], ["resume"])
        self.assertEqual(storage.mock_calls, [])
        enqueue.assert_not_called()

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

    def test_registered_batch_redelivery_uses_real_sdk_detached_post_and_requires_202(self):
        function_id = "ocid1.fnfunc.oc1.ord.fixture"
        endpoint = "https://fixture.functions.us-chicago-1.oci.oraclecloud.com"
        signer = Mock(spec=oci.auth.signers.SecurityTokenSigner)
        signer.region = "us-chicago-1"
        stream = Mock()
        statuses = iter((200, 202))
        invoked = []

        def transport(client, request, *_options):
            if client.service == "secrets":
                bundle = SimpleNamespace(secret_bundle_content=SimpleNamespace(content="dGVzdA=="))
                return oci.response.Response(200, {}, bundle, request)
            if client.service == "object_storage":
                self.assertEqual(request.method, "HEAD")
                return oci.response.Response(200, {"etag":"v1","content-length":"100"}, None, request)
            if client.service == "functions_management":
                return oci.response.Response(200, {}, SimpleNamespace(invoke_endpoint=endpoint), request)
            self.assertEqual(client.service, "functions_invoke")
            self.assertEqual(request.method, "POST")
            self.assertTrue(request.url.startswith(endpoint + "/"))
            self.assertTrue(request.url.endswith(f"/functions/{function_id}/actions/invoke"))
            self.assertEqual(request.header_params["fn-invoke-type"], "detached")
            self.assertIsInstance(request.body, bytes)
            invoked.append(json.loads(request.body))
            return oci.response.Response(next(statuses), {}, stream, request)

        store = Mock()
        store.open.return_value = (37, 2, {})
        config = {**CONFIG, "DB_SECRET_OCID":"fixture", "DB_USER":"test", "DB_DSN":"fixture"}
        with (patch.dict(service.os.environ, config),
              patch.object(oci.auth.signers, "get_resource_principals_signer", return_value=signer),
              patch.object(oci.base_client.BaseClient, "request", autospec=True, side_effect=transport),
              patch.object(service.oracledb, "connect"), patch.object(service, "Store", return_value=store)):
            with self.assertRaisesRegex(RuntimeError, "not accepted"):
                service.invoke(EVENT, function_id)
            self.assertEqual(service.invoke(EVENT, function_id), {"status":"queued","batch_id":37})
        self.assertEqual(invoked, [{"batch_id":37,"generation":2}]*2)
        self.assertEqual(stream.close.call_count, 2)
        self.assertEqual(store.open.call_count, 2)
        store.finish.assert_not_called()

    def test_bad_zip_persists_diagnostic(self):
        store, storage = Mock(), Mock()
        store.open.return_value = (1, 2, {})
        storage.head_object.return_value.headers = {"etag": "v1", "content-length": "1"}
        storage.get_object.return_value.data.raw = io.BytesIO(b"x")
        result = process(EVENT, CONFIG, store, storage, Mock())
        self.assertEqual(result["status"], "invalid")
        store.finish.assert_called_once_with(1, 2, 0, "BadZipFile")

    def test_versioned_continuation_uses_sdk_conditional_get(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("synthetic.pdf", b"%PDF-")
        data.seek(0)
        signer = Mock(spec=oci.auth.signers.SecurityTokenSigner)
        signer.region = "us-chicago-1"
        store = Mock()
        store.resume.return_value = ("entrada/lote.zip", "v1", "object-version")
        store.open.return_value = (37, 2, {})
        store.index.return_value = set()
        download = Mock(raw=data)

        def transport(client, request, *_options):
            self.assertEqual(client.service, "object_storage")
            self.assertEqual(dict(request.query_params)["versionId"], "object-version")
            if request.method == "HEAD":
                return oci.response.Response(200, {"etag":'"v1"', "content-length":str(len(data.getvalue()))}, None, request)
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.header_params["if-match"], '"v1"')
            return oci.response.Response(200, {}, download, request)

        with patch.object(oci.base_client.BaseClient, "request", autospec=True, side_effect=transport):
            storage = oci.object_storage.ObjectStorageClient({}, signer=signer)
            result = process({"batch_id":37, "generation":2}, CONFIG, store, storage, Mock(), dispatch=True)
        self.assertEqual(result, {"status":"processed", "batch_id":37, "documents":1})
        download.close.assert_called_once()
        store.finish.assert_called_once_with(37, 2, 1)

