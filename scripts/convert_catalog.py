"""Rebuild the published pilot template from the local business catalog."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "nit_emisor": ["NIT emisor", "NIT del emisor", "NIT del prestador"],
    "anio_factura": ["Fecha de expedición", "Fecha de emisión"],
    "numero_factura": ["Número de factura", "Factura No.", "Factura de servicios medicos"],
    "cufe": ["CUFE"], "fecha_expedicion": ["Fecha de expedición", "Fecha de emisión"],
    "alfa_factura": ["Alfa factura"], "nit_receptor": ["NIT receptor", "NIT del adquirente"],
    "nombre_receptor": ["Nombre del receptor", "Razón social del adquirente"],
    "nombre_paciente": ["Paciente", "Nombre del paciente"],
    "documento_paciente": ["Documento del paciente", "Identificación paciente"],
    "descripcion_servicios": ["Descripción de servicios"], "valor_total_factura": ["TOTAL:", "Valor total factura"],
}


def main():
    fields = []
    with (ROOT / ".source/catalog.csv").open(encoding="cp1252", newline="") as source:
        rows = csv.reader(source)
        next(rows)
        for ident, group, label, key, dtype, meaning, exclusions, normalization, applicable, cross in rows:
            # The public template retains the definition, without private attribution in parentheses.
            if key == "anio_factura":
                meaning = meaning.split(" (", 1)[0].rstrip(".") + "."
            fields.append(dict(id=ident, group=group, key=key, label=label, type=dtype, definition=meaning,
                               exclusions=exclusions, normalization=normalization, source=cross,
                               applicability=("required" if "requerido" in applicable else
                                              "conditional" if "condicional" in applicable else "optional"),
                               labels=LABELS[key]))
            if key == "anio_factura":
                fields[-1]["format"] = "year"
    template = json.dumps(dict(name="Facturas", version=1, fields=fields), ensure_ascii=False, indent=2)
    (ROOT / "templates").mkdir(exist_ok=True)
    (ROOT / "templates/facturas.json").write_text(template + "\n", encoding="utf-8")
    if "~'" in template:
        raise ValueError("SQL quote delimiter in template")
    sql = """declare
 v_id number; v_prefix varchar2(512); v_count number;
begin
 begin
  select version_id into v_id from inv_template where name='Facturas' and version_no=1;
 exception when no_data_found then
  inv_catalog.install_version(q'~%s~',v_id);
 end;
 merge into inv_target t using(select 'FACTURAS' k from dual) s on(t.target_key=s.k)
  when not matched then insert values(s.k,'Facturas');
 select source_prefix into v_prefix from inv_settings where id=1;
 select count(*) into v_count from inv_route;
 if v_count=0 then insert into inv_route values(v_prefix,v_id,'FACTURAS'); end if;
end;
/
"""
    (ROOT / "database/seed.sql").write_text(sql % template, encoding="utf-8")


if __name__ == "__main__":
    main()

