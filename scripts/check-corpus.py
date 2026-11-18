"""Evaluate the published template against each PDF and its separate index oracle."""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from extractor.documents import page_texts, pdf_members, read_archive
from extractor.fields import extract


def check(directory, template):
    started = monotonic()
    actual = Counter()
    methods = Counter()
    layouts = Counter()
    matches = documents = 0
    packages = sorted(directory.glob("*.zip"))
    for package in packages:
        with read_archive(package.read_bytes()) as archive:
            results = {}
            for entry in pdf_members(archive):
                pages = list(page_texts(archive.read(entry)))
                methods.update(method for _, method, _ in pages)
                fields = extract(pages, template)
                actual.update((field["key"], field["status"]) for field in fields)
                number = next(field for field in fields if field["key"] == "numero_factura")
                results[entry.filename] = number.get("normalized")
            # Only the verifier reads the metadata. The production functions above
            # receive raw PDF bytes and the APEX-compatible template.
            indexes = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(indexes) != 1:
                raise ValueError(f"{package.name}: expected one index")
            with archive.open(indexes[0]) as stream, io.TextIOWrapper(stream, encoding="utf-8-sig") as text:
                for row in csv.DictReader(text, delimiter=";"):
                    documents += 1
                    layouts[row["diseno"]] += 1
                    matches += results.pop(row["archivo"]) == row["numero_factura"]
            if results:
                raise ValueError(f"{package.name}: unindexed PDF")
    return {"archives": len(packages), "documents": documents, "invoice_number_matches": matches,
            "page_methods": dict(methods), "layouts": dict(layouts),
            "field_statuses": {f["key"]: {s: actual[f["key"], s] for s in
                              ("found", "absent", "ambiguous", "invalid")} for f in template["fields"]},
            "seconds": round(monotonic() - started, 2)}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--directory", type=Path, default=ROOT / ".source/test/4600")
    args = cli.parse_args()
    template = json.loads((ROOT / "source/invoice-catalog.json").read_text(encoding="utf-8"))
    summary = check(args.directory, template)
    print(json.dumps(summary, indent=2))
    if (summary["archives"], summary["documents"], summary["invoice_number_matches"]) != (46, 4600, 4600):
        raise SystemExit(1)
