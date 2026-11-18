import json
import re
import uuid

import oracledb

from .documents import MAX_PDF
from .worker import DocumentError


def preview(connection, worker, test_id):
    if not isinstance(test_id, str) or not re.fullmatch(r"[0-9a-f]{32}", test_id):
        return {"status": "invalid"}
    claim_id = uuid.uuid4().hex
    with connection.cursor() as cursor:
        source = cursor.var(oracledb.DB_TYPE_BLOB)
        template = cursor.var(oracledb.DB_TYPE_CLOB)

        def call(name, *args):
            cursor.callproc("inv_data.inv_test_worker." + name, [test_id, claim_id, *args])
            connection.commit()

        call("claim", source, template)
        file = source.getvalue()
        if file is None:
            return {"status": "ignored"}
        try:
            if not 0 < file.size() <= MAX_PDF:
                raise DocumentError("pdf_limit")
            fields, pages = worker.extract(file.read(), json.loads(template.getvalue().read()),
                                          on_stage=lambda stage: call("stage", stage))
        except DocumentError as exc:
            call("finish", None, str(exc)[:80])
            return {"status": "invalid"}
        for number, width, height, image, method, total, fragments in pages:
            picture = cursor.var(oracledb.DB_TYPE_BLOB)
            picture.setvalue(0, image)
            lines = cursor.var(oracledb.DB_TYPE_CLOB)
            lines.setvalue(0, json.dumps(fragments, ensure_ascii=False))
            cursor.callproc("inv_data.inv_test_worker.save_page",
                            [test_id, claim_id, number, width, height, picture, total, len(fragments), lines, method])
        result = cursor.var(oracledb.DB_TYPE_CLOB)
        result.setvalue(0, json.dumps(fields, ensure_ascii=False))
        call("finish", result, None)
    return {"status": "processed"}
