declare
 v_id number; v_prefix varchar2(512); v_count number;
begin
 begin
  select version_id into v_id from inv_template where name='Facturas' and version_no=1;
 exception when no_data_found then
  inv_catalog.install_version(q'~{
  "name": "Facturas",
  "version": 1,
  "fields": [
    {
      "id": "F-01",
      "group": "Base",
      "key": "nit_emisor",
      "label": "NIT del emisor",
      "type": "string",
      "definition": "Número tributario del emisor que expide la factura.",
      "exclusions": "Ubicar el NIT asociado al emisor. Excluir el del receptor, pagador, paciente y terceros. No escoger entre candidatos ambiguos.",
      "normalization": "Conservar el texto original, guion, dígito de verificación y ceros. Crear una forma de comparación aparte.",
      "source": "PDF o imagen; comparar con NIT del proveedor en P8.",
      "applicability": "required",
      "labels": [
        "NIT emisor",
        "NIT del emisor",
        "NIT del prestador"
      ]
    },
    {
      "id": "F-02",
      "group": "Base",
      "key": "anio_factura",
      "label": "Año de expedición",
      "type": "string",
      "definition": "Año de la fecha de expedición de la factura.",
      "exclusions": "Extraer los cuatro dígitos de la fecha de expedición visible. Excluir período de atención, radicación, vencimiento e impresión.",
      "normalization": "AAAA; no derivar de fecha de creación de P8 sin aprobación expresa.",
      "source": "PDF o imagen; fecha de creación en P8 es un dato distinto.",
      "applicability": "required",
      "labels": [
        "Fecha de expedición",
        "Fecha de emisión"
      ],
      "format": "year"
    },
    {
      "id": "F-03",
      "group": "Base",
      "key": "numero_factura",
      "label": "Número de factura",
      "type": "string",
      "definition": "Identificador alfanumérico asignado por el emisor, con prefijo si corresponde.",
      "exclusions": "Buscar el número principal de factura. Excluir CUFE, ID P8, número de soporte, autorización y radicado. Si hay varias facturas, no escoger sin regla.",
      "normalization": "Conservar prefijos, guiones y ceros iniciales; acordar normalización adicional solo para comparar.",
      "source": "PDF o imagen; comparar con número de factura informado por P8.",
      "applicability": "required",
      "labels": [
        "Número de factura",
        "Factura No.",
        "Factura de servicios medicos"
      ]
    },
    {
      "id": "F-04",
      "group": "Base",
      "key": "cufe",
      "label": "CUFE",
      "type": "string",
      "definition": "Identificador adicional de factura electrónica, distinto del número de factura.",
      "exclusions": "Extraer solo si está visible como texto legible en la representación gráfica. No sustituir por número de factura, CUDE o dato de P8.",
      "normalization": "Preservar el código completo como texto. Si no aparece, registrar ausente en el documento.",
      "source": "P8 puede traer CUFE para factura electrónica; conservar ese valor separado del hallado en la imagen. XML excluido de la PoC.",
      "applicability": "conditional",
      "labels": [
        "CUFE"
      ]
    },
    {
      "id": "F-05",
      "group": "Candidato",
      "key": "fecha_expedicion",
      "label": "Fecha de expedición",
      "type": "date",
      "definition": "Fecha en que el emisor expidió la factura; sustenta F-02.",
      "exclusions": "Buscar fecha explícita de expedición; excluir fecha de ingreso a P8, servicio, radicación y vencimiento.",
      "normalization": "Conservar texto y fecha normalizada separada; evitar ambigüedad día/mes.",
      "source": "Imagen; P8 fecha de creación no equivale automáticamente a expedición.",
      "applicability": "optional",
      "labels": [
        "Fecha de expedición",
        "Fecha de emisión"
      ]
    },
    {
      "id": "F-06",
      "group": "Candidato",
      "key": "alfa_factura",
      "label": "Alfa factura",
      "type": "string",
      "definition": "Atributo adicional usado por ciertos proveedores para distinguir facturas cuyo número se repite.",
      "exclusions": "Extraer solo si aparece explícito y separado del número de factura; no inferir del prefijo.",
      "normalization": "Conservar valor literal, incluido formato y ceros.",
      "source": "Jhon señala que algunos proveedores lo incluyen en P8 y en la factura.",
      "applicability": "optional",
      "labels": [
        "Alfa factura"
      ]
    },
    {
      "id": "F-07",
      "group": "Candidato",
      "key": "nit_receptor",
      "label": "NIT del receptor",
      "type": "string",
      "definition": "Identificador tributario de la entidad que recibe la factura.",
      "exclusions": "Buscar el NIT del receptor o adquirente; no confundir con NIT del emisor o de terceros.",
      "normalization": "Conservar texto original; normalización para búsquedas por definir.",
      "source": "Imagen; los datos del receptor no necesariamente están en P8.",
      "applicability": "optional",
      "labels": [
        "NIT receptor",
        "NIT del adquirente"
      ]
    },
    {
      "id": "F-08",
      "group": "Candidato",
      "key": "nombre_receptor",
      "label": "Nombre del receptor",
      "type": "string",
      "definition": "Nombre de la entidad a la que se factura.",
      "exclusions": "Identificar entidad facturada, no proveedor ni paciente.",
      "normalization": "Conservar literal; homologación de nombres por definir.",
      "source": "Imagen; útil para preguntas por entidad facturada.",
      "applicability": "optional",
      "labels": [
        "Nombre del receptor",
        "Razón social del adquirente"
      ]
    },
    {
      "id": "F-09",
      "group": "Candidato",
      "key": "nombre_paciente",
      "label": "Nombre del paciente",
      "type": "string",
      "definition": "Paciente asociado a servicios facturados, cuando esté identificado.",
      "exclusions": "No asumir que toda factura incluye paciente; excluir nombres del emisor, receptor y personal asistencial.",
      "normalization": "Conservar literal; no atribuir paciente si la factura incluye varios sin asignación clara.",
      "source": "Solo imagen; Jhon pide consultas por paciente, pero el campo no está en todos los documentos.",
      "applicability": "optional",
      "labels": [
        "Paciente",
        "Nombre del paciente"
      ]
    },
    {
      "id": "F-10",
      "group": "Candidato",
      "key": "documento_paciente",
      "label": "Documento del paciente",
      "type": "string",
      "definition": "Identificación del paciente cuando aparezca en la factura.",
      "exclusions": "Asociar explícitamente al paciente, no al emisor o receptor.",
      "normalization": "Preservar tipo si consta, número y ceros iniciales.",
      "source": "Solo imagen; aplicar acceso acorde al dato personal.",
      "applicability": "optional",
      "labels": [
        "Documento del paciente",
        "Identificación paciente"
      ]
    },
    {
      "id": "F-11",
      "group": "Candidato",
      "key": "descripcion_servicios",
      "label": "Descripción de servicios",
      "type": "string",
      "definition": "Texto que describe los servicios o conceptos facturados.",
      "exclusions": "Extraer la descripción visible de servicios; no confundir con título del archivo o descripción genérica en P8.",
      "normalization": "Conservar texto. La extracción de ítems individuales requiere diseño y prueba aparte.",
      "source": "Imagen; sirve para búsqueda, pero agregaciones por servicio precisan ítems estructurados.",
      "applicability": "optional",
      "labels": [
        "Descripción de servicios"
      ]
    },
    {
      "id": "F-12",
      "group": "Candidato",
      "key": "valor_total_factura",
      "label": "Valor total de factura",
      "type": "currency",
      "definition": "Gran total exigible de la factura, si está presente.",
      "exclusions": "Identificar total final; excluir subtotal, impuestos individuales, precio unitario y saldo anterior.",
      "normalization": "Conservar moneda, texto y valor numérico normalizado por separado.",
      "source": "Imagen; P8 también informa valor total, útil para contraste.",
      "applicability": "optional",
      "labels": [
        "TOTAL:",
        "Valor total factura"
      ]
    }
  ]
}~',v_id);
 end;
 merge into inv_target t using(select 'FACTURAS' k from dual) s on(t.target_key=s.k)
  when not matched then insert values(s.k,'Facturas');
 select source_prefix into v_prefix from inv_settings where id=1;
 select count(*) into v_count from inv_route;
 if v_count=0 then insert into inv_route values(v_prefix,v_id,'FACTURAS'); end if;
end;
/
