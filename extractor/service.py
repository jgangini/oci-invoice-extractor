import json
import logging
import os
import time
import zipfile

import oci
import oracledb
from pypdf.errors import PyPdfError

from .documents import InputError, MAX_ZIP, pdf_members, read_archive
from .preview import preview
from .worker import DocumentError, DocumentWorker

LOG = logging.getLogger(__name__)
ENGINE = "0.2.0"


def source_event(event, config):
    if event.get("eventType") != "com.oraclecloud.objectstorage.createobject":
        return None
    data = event.get("data") or {}
    details = data.get("additionalDetails") or {}
    key = data.get("resourceName", "")
    if (details.get("bucketName") != config["BUCKET_NAME"]
            or details.get("namespace") != config["OBJECT_NAMESPACE"]
            or not key.startswith(config["SOURCE_PREFIX"]) or not key.lower().endswith(".zip")):
        return None
    return key, details.get("eTag") or details.get("etag"), details.get("versionId")


class Store:
    def __init__(self, connection):
        self.connection = connection
        self.cursor = connection.cursor()
        self.worker = DocumentWorker()

    def call(self, name, *args):
        self.cursor.callproc("inv_data.inv_ingest." + name, list(args))

    def resume(self, batch_id, generation):
        args = [self.cursor.var(str, size=2048) for _ in range(3)]
        self.call("resume_batch", batch_id, generation, *args)
        return tuple(v.getvalue() for v in args)

    def open(self, key, etag, version):
        batch, gen = (self.cursor.var(int) for _ in range(2))
        payload = self.cursor.var(oracledb.DB_TYPE_CLOB)
        self.call("open_batch", key, etag, version, batch, gen, payload)
        self.connection.commit()
        if not batch.getvalue():
            return None
        return batch.getvalue(), gen.getvalue(), json.loads(payload.getvalue().read())

    def member(self, batch, gen, archive, entry, template):
        run = self.cursor.var(int)
        self.call("start_member", batch, gen, entry.filename, ENGINE, run)
        if not run.getvalue():
            self.connection.rollback()
            return
        try:
            fields = self.worker.extract(archive.read(entry), template)
        except DocumentError as exc:
            self.call("fail_member", run.getvalue(), str(exc)[:80])
        except (ValueError, PyPdfError, RuntimeError, zipfile.BadZipFile) as exc:
            self.call("fail_member", run.getvalue(), type(exc).__name__)
        else:
            payload = self.cursor.var(oracledb.DB_TYPE_CLOB)
            payload.setvalue(0, json.dumps(fields, ensure_ascii=False))
            self.call("save_fields", run.getvalue(), payload)
        self.connection.commit()

    def index(self, batch, gen, members):
        names = self.cursor.var(oracledb.DB_TYPE_CLOB)
        names.setvalue(0, json.dumps([entry.filename for entry in members], ensure_ascii=False))
        completed = self.cursor.var(oracledb.DB_TYPE_CLOB)
        self.call("index_members", batch, gen, names, completed)
        self.connection.commit()
        result = completed.getvalue()
        return set(json.loads(result.read())) if result else set()

    def finish(self, batch, gen, count, code=None):
        self.call("finish_batch", batch, gen, count, code)
        self.connection.commit()


def process(event, config, store, storage, enqueue, budget=200, dispatch=False):
    continuation = "batch_id" in event and "generation" in event
    if continuation:
        try:
            key, etag, version = store.resume(int(event["batch_id"]), int(event["generation"]))
        except oracledb.DatabaseError as exc:
            if exc.args[0].code == 1403:
                return {"status": "superseded"}
            raise
    else:
        candidate = source_event(event, config)
        if candidate is None:
            return {"status": "ignored"}
        key, etag, version = candidate
    ns, bucket = config["OBJECT_NAMESPACE"], config["BUCKET_NAME"]
    params = {"version_id": version} if version else {}
    meta = storage.head_object(ns, bucket, key, **params).headers
    current = meta["etag"].strip('"')
    if etag and etag.strip('"') != current:
        return {"status": "superseded"}
    version = version or meta.get("version-id")
    if version:
        params["version_id"] = version
    opened = store.open(key, current, version)
    if opened is None:
        return {"status": "ignored"}
    batch, gen, template = opened
    if continuation and gen != event["generation"]:
        return {"status": "superseded"}
    if int(meta["content-length"]) > MAX_ZIP:
        store.finish(batch, gen, 0, "archive_limit")
        return {"status": "invalid", "batch_id": batch}
    if dispatch and not continuation:
        enqueue(batch, gen)
        return {"status": "queued", "batch_id": batch}
    response = storage.get_object(ns, bucket, key, if_match=meta["etag"], **params)
    try:
        data = response.data.raw.read(MAX_ZIP + 1)
    finally:
        response.data.close()
    deadline = time.monotonic() + budget
    try:
        with read_archive(data) as archive:
            members = list(pdf_members(archive))
            if not members:
                raise InputError("no_pdf_members")
            completed = store.index(batch, gen, members)
            remaining = [entry for entry in members if entry.filename not in completed]
            for index, entry in enumerate(remaining, 1):
                store.member(batch, gen, archive, entry, template)
                if index < len(remaining) and time.monotonic() >= deadline:
                    enqueue(batch, gen)
                    return {"status": "continued", "batch_id": batch}
    except (InputError, zipfile.BadZipFile) as exc:
        store.finish(batch, gen, 0, str(exc) if isinstance(exc, InputError) else type(exc).__name__)
        return {"status": "invalid", "batch_id": batch}
    store.finish(batch, gen, len(members))
    return {"status": "processed", "batch_id": batch, "documents": len(members)}


def invoke(event, function_id):
    import base64
    config = os.environ
    if "batch_id" not in event and "preview_id" not in event and source_event(event, config) is None:
        return {"status": "ignored"}
    signer = oci.auth.signers.get_resource_principals_signer()
    storage = oci.object_storage.ObjectStorageClient({}, signer=signer)
    secrets = oci.secrets.SecretsClient({}, signer=signer)
    secret = secrets.get_secret_bundle(config["DB_SECRET_OCID"]).data.secret_bundle_content.content

    def enqueue(batch, gen):
        management = oci.functions.FunctionsManagementClient({}, signer=signer)
        endpoint = management.get_function(function_id).data.invoke_endpoint
        client = oci.functions.FunctionsInvokeClient({}, signer=signer, service_endpoint=endpoint)
        client.invoke_function(function_id, json.dumps({"batch_id": batch, "generation": gen}), fn_invoke_type="detached")

    with oracledb.connect(user=config["DB_USER"], password=base64.b64decode(secret).decode(),
                          dsn=config["DB_DSN"]) as conn:
        store = Store(conn)
        try:
            if set(event) == {"preview_id"}:
                return preview(conn, store.worker, event["preview_id"])
            return process(event, config, store, storage, enqueue, dispatch=True)
        finally:
            store.worker.close()

