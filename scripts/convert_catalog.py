"""Synchronize catalog structure with the CSV while retaining the public English descriptions."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "source/invoice-catalog.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    by_key = {field["key"]: field for field in template["fields"]}
    fields = []
    seen = set()
    with (ROOT / ".source/catalog.csv").open(encoding="cp1252", newline="") as source:
        rows = csv.reader(source)
        next(rows)
        for ident, group, _, key, dtype, _, _, _, applicable, _ in rows:
            if key in seen or key not in by_key:
                raise ValueError(f"Add a distinct English field definition for {key} before synchronizing.")
            field = by_key[key]
            field.update(id=ident, group={"Base": "Core", "Candidato": "Additional"}[group], type=dtype,
                         applicability=("required" if "requerido" in applicable else
                                        "conditional" if "condicional" in applicable else "optional"))
            fields.append(field)
            seen.add(key)
    if seen != by_key.keys():
        raise ValueError("The CSV and public template contain different fields.")
    template["fields"] = fields
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
