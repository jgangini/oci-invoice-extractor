"""Invoke the FDK using a recorded event and an explicitly local ZIP, without OCI."""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from fdk import fixtures
import func
from extractor.documents import page_texts, pdf_members, read_archive
from extractor.fields import extract
from extractor.service import source_event

ROOT = Path(__file__).resolve().parent


async def main(args):
    template = json.loads((ROOT / "source/invoice-catalog.json").read_text(encoding="utf-8"))

    def offline(event, _fn_id):
        cfg = {"BUCKET_NAME": args.bucket, "OBJECT_NAMESPACE": args.namespace, "SOURCE_PREFIX": "entrada/"}
        if not source_event(event, cfg):
            return {"status": "ignored"}
        archive_path = args.archive or os.environ.get("INVOICE_LOCAL_ARCHIVE")
        if not archive_path:
            raise ValueError("Indique --archive o INVOICE_LOCAL_ARCHIVE.")
        counts = {"documents": 0, "found": 0, "absent": 0, "ambiguous": 0, "invalid": 0}
        with read_archive(Path(archive_path).read_bytes()) as archive:
            for member in pdf_members(archive):
                for field in extract(page_texts(archive.read(member)), template):
                    counts[field["status"]] += 1
                counts["documents"] += 1
        return {"status": "local", **counts}

    with patch.object(func, "invoke", offline):
        call = await fixtures.setup_fn_call(func.handler, content=io.BytesIO(args.event.read_bytes()))
        content, status, _ = await call
    print(content.decode() if isinstance(content, bytes) else content)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--bucket", default="invoices")
    parser.add_argument("--namespace", default="demo")
    raise SystemExit(asyncio.run(main(parser.parse_args())))

