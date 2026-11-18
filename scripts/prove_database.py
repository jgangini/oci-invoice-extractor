"""Exercise real Oracle identities in a disposable local PDB with the sample corpus."""
import io
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import oracledb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extractor.service import process, Store


class SampleStorage:
    def __init__(self, archive):
        self.archive = archive

    def head_object(self, *_args, **_kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(headers={"etag": "sample", "content-length": str(self.archive.stat().st_size)})

    def get_object(self, *_args, **_kwargs):
        from types import SimpleNamespace
        buf = io.BytesIO(self.archive.read_bytes())
        return SimpleNamespace(data=SimpleNamespace(raw=buf, close=buf.close))


def denied(cursor, statement, codes):
    try:
        cursor.execute(statement)
    except oracledb.DatabaseError as exc:
        if exc.args[0].code not in codes:
            raise
    else:
        raise AssertionError("Privilege boundary failed")


def main():
    dsn = os.environ["INV_TEST_DSN"]
    if not dsn.startswith("127.0.0.1:") or not dsn.endswith("/FREEPDB1"):
        raise ValueError("Requires disposable local FREEPDB1")
    event = json.loads((ROOT / "fixtures/object-create.json").read_text())
    event["data"]["resourceName"] = "entrada/proof-" + uuid4().hex + ".zip"
    event["data"]["additionalDetails"]["eTag"] = "sample"
    cfg = dict(SOURCE_PREFIX="entrada/", BUCKET_NAME="invoices", OBJECT_NAMESPACE="demo")
    storage = SampleStorage(next((ROOT / ".source/test/4600").rglob("*.zip")))
    with oracledb.connect(user="INV_FN", password=os.environ["INV_TEST_FN_PASSWORD"], dsn=dsn) as fn:
        store = Store(fn)
        one = process(event, cfg, store, storage, None, budget=300)
        if one["documents"] != 100:
            raise AssertionError(one)
        assert process(event, cfg, store, storage, None)["status"] == "ignored"
        store.worker.close()
        denied(fn.cursor(), "select * from inv_data.inv_result", {942,1031})
        denied(fn.cursor(), "begin inv_data.inv_ui.register_target('TEST','X'); end;", {6550,1031})
    with oracledb.connect(user="INV_APEX", password=os.environ["INV_TEST_APEX_PASSWORD"], dsn=dsn) as db:
        c = db.cursor()
        denied(c, "update inv_data.inv_result set status='absent' where 1=0", {942,1031})
        denied(c, "begin inv_data.inv_ui.register_target('TEST','X'); end;", {20003})
        c.callproc("apex_session.create_session", [100, 1, "PROOF_REVIEWER"])
        denied(c, "begin inv_data.inv_ui.register_target('TEST','X'); end;", {20003})
        c.execute("select count(*),count(distinct document_id) from inv_data.inv_v_results where batch_id=:b", b=one["batch_id"])
        assert c.fetchone() == (1200,100)
        c.execute("select result_id,original_text from inv_data.inv_v_results where batch_id=:b and field_key='numero_factura' fetch first 1 row only", b=one["batch_id"])
        result, original = c.fetchone()
        c.callproc("inv_data.inv_ui.approve", [result,0,"PRUEBA-001","Validación controlada"])
        db.commit()
        c.execute("select original_text, effective_value, actor from inv_data.inv_v_results where result_id=:r",r=result)
        assert c.fetchone() == (original, "PRUEBA-001", "PROOF_REVIEWER")
        try:
            c.callproc("inv_data.inv_ui.approve", [result,0,"STALE","Simular edición concurrente"])
        except oracledb.DatabaseError as exc:
            assert exc.args[0].code == 20007
        else:
            raise AssertionError("Se aceptó la revisión obsoleta")
    print("100 documentos, 1200 resultados, evento duplicado, tres permisos denegados y revisión concurrente verificados.")


if __name__ == "__main__":
    main()

