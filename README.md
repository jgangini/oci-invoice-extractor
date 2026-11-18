# OCI Invoice Extractor

Procesa facturas PDF en lotes ZIP. La regla de OCI Events entrega las cargas del bucket a OCI Functions. El primer handler registra la versión del objeto y programa el procesamiento asíncrono. La Function lee el texto de cada página, recurre a RapidOCR si es ilegible y guarda los campos en Autonomous Database. Oracle APEX permite administrar las plantillas, configurar rutas y revisar resultados.

## Desplegar en otro tenancy con Deploy Studio

Cada instalación utiliza los recursos, IAM, secretos y datos del tenancy de destino. La fuente de la release es pública. El registro de imágenes y el bucket del despliegue son privados.

1. En la región de destino, preparar un repositorio privado de Container Registry y dos secretos distintos en OCI Vault para las contraseñas de ADMIN de Autonomous y de INV_FN. La selección de compartimento en Studio admite crear uno o elegir uno existente. Si el registro y Vault deben quedar en el mismo compartimento que el stack, crearlo al preparar esos recursos y seleccionarlo después en Studio.
2. Descargar el tag publicado. Construir desde ese checkout con `docker buildx build --platform linux/amd64 --provenance=false --load -t oci-invoice-extractor:0.1.0 .`. Autenticarse en el OCIR propio, etiquetar y subir la imagen. Conservar el OCID de la imagen regional y el digest del manifiesto que entrega el registro. El ID de imagen local que imprime Docker es distinto del digest que solicita Studio.
3. En Deploy Studio elegir **OCI Invoice Extractor**, la release, región, compartimento, imagen, digest y los OCID de secretos. Indicar las IPv4 autorizadas del cliente y del worker, el prefijo de entrada, el operador inicial y su contraseña. El preflight comprueba la imagen y los secretos con el perfil suministrado. El workbook de Functions, si está instalado, automatiza el build, diagnóstico y resolución de los dos campos de imagen mediante `release-inputs`.
4. Revisar el PLAN de Resource Manager y aplicar ese plan. El perfil crea Autonomous OLTP con **4 ECPU y 1 TB**, licencia incluida y autoescalado de CPU desactivado. También aprovisiona subred privada, Service Gateway, Object Storage, Functions, Events y Logging. El hook importa el modelo, crea el workspace **INVOICES**, instala la aplicación 100 bajo **INV_APEX**, verifica el acceso de **INV_FN** y habilita Events. El resultado ofrece la URL HTTPS de APEX, el bucket y el prefijo.
5. Entrar con el operador inicial. Para probar la ruta, generar el ZIP sintético que se describe abajo y subirlo con un nombre nuevo al prefijo indicado. En **Lotes**, abrir el lote, revisar los campos y guardar una corrección. La vista conserva el original y el motivo de la aprobación. Si se reprocesa el lote, una diferencia con una corrección vigente aparece como discrepancia.

El endpoint piloto admite HTTPS desde las redes declaradas en su ACL. La Function llega a los servicios de Oracle por la red privada. Una IP dinámica nueva requiere actualizar la ACL. Para producción con usuarios desde cualquier red, Oracle describe un balanceador HTTPS público con dominio y certificado, Autonomous en endpoint privado y acceso privado del instalador. Ese perfil de red requiere infraestructura adicional. [Arquitectura de producción de Oracle](https://docs.oracle.com/en/solutions/deploy-autonomous-database-and-app/) · [Acceso APEX mediante un dominio propio](https://docs.oracle.com/en/cloud/paas/apex/gsadd/access-apex-using-vanity-url.html)

## Desarrollo y pruebas

Requiere Python 3.11 o 3.12, uv y Docker para comprobar la imagen Linux. Desde el checkout:

```powershell
uv sync --locked
uv run --locked python -m pytest -q
uv run --locked python scripts/sample_zip.py build/demo.zip
uv run --locked python invoke_local.py fixtures/object-create.json --archive build/demo.zip
```

La última orden usa el handler FDK y un almacenamiento local de prueba. Solo imprime conteos. Para probar la ruta OCR, pasar `--scan` al generar un segundo ZIP. Con el corpus privado instalado, `uv run --locked python scripts/check-corpus.py` lee los PDF y compara el número extraído con los CSV después de procesarlos. Los archivos de control nunca forman parte de la entrada al extractor.

El plugin **OCI Functions Workbench** ofrece `bootstrap`, `test`, `invoke`, `build`, `doctor`, `release-inputs` y `logs`. Para `invoke` desde el plugin definir `INVOICE_LOCAL_ARCHIVE` con el ZIP local y pasar el fixture a `--event`. Los mismos comandos de Python y Docker funcionan por sí solos en un checkout público.

## Modelo, plantillas y APEX

El esquema **INV_DATA** posee el modelo y los paquetes. **INV_FN** solo ejecuta la API de ingesta. **INV_APEX** consulta vistas y ejecuta la API de configuración y revisión. Las operaciones comprueban la identidad de la sesión APEX en el servidor. Se usan los roles ADMIN y REVIEW en INV_OPERATOR. Para incorporar revisores, crear sus cuentas de usuario final en el workspace y registrar su nombre en mayúsculas con el rol REVIEW mediante la cuenta de instalación autorizada. [Modelo de datos](docs/modelo/inv-data.html) · [Migraciones Oracle](database/schema.sql)

La plantilla de ejemplo tiene doce campos. Cada campo declara clave, tipo, etiquetas literales y aplicabilidad. Un valor ausente o ambiguo conserva su estado. Los códigos y NIT mantienen ceros iniciales. Los importes conservan evidencia, número y moneda si consta. En APEX, **Plantillas** permite cargar UTF-8, previsualizar y publicar. **Rutas** asocia prefijos sin solapamiento con una versión inmutable y un destino lógico del esquema. El lote conserva la versión seleccionada al ingreso. Los cambios en el JSON no ejecutan SQL. [Plantilla de ejemplo](templates/facturas.json)

La fuente editable está en `apex/app`. La release lleva un export SQL generado con SQLcl contra APEX 26.1. Validar con `apex validate -input apex/app`; importar en un workspace de construcción, exportar la aplicación 100 y probar la reinstalación del export sobre datos existentes. El instalador necesita una versión de APEX compatible con ese export. [Fuentes y licencia de la plantilla APEX](apex/NOTICE.md)

## Operación y diagnóstico

- **No aparece un lote.** Comprobar la regla activa, el evento de creación de objeto, bucket, namespace, extensión y subprefijo. Los subprefijos sin ruta activa se ignoran. Correlacionar el ID de evento, invocación y lote en Logging.
- **Un lote queda pendiente.** Revisar el permiso de autoinvocación y el acceso desde la Function a Vault, Object Storage y Autonomous. Reprocesar desde APEX. Se conserva el avance por PDF. Una ejecución concurrente no duplica la misma generación del documento.
- **Errores de un PDF.** Archivos protegidos, miembros duplicados, nombres inválidos o documentos que exceden el presupuesto producen estados de diagnóstico. El ZIP se procesa en memoria, los nombres de sus miembros no se escriben al sistema de archivos y las cadenas de error de terceros no se publican en los logs.
- **Calidad OCR.** El motor CPU se distribuye dentro de la imagen. El lockfile fija las dependencias y hashes de los paquetes. Se usan los modelos ONNX incluidos en rapidocr-onnxruntime 1.4.4. Un escaneo de prueba permitió leer el identificador y dejó sin resolver una etiqueta de total. La calidad debe medirse por campo con documentos representativos. GenAI permanece desactivada. [RapidOCR](https://github.com/RapidAI/RapidOCR)
- **Actualizar o recuperar.** Conservar la versión de la imagen, el tag y los backups del esquema. Una reinstalación de APEX no elimina los resultados. Revisar el plan de Resource Manager antes de reemplazar almacenamiento o base. Las sentencias DDL de Oracle pueden confirmar cambios parciales; el hook reanuda la instalación mediante objetos y semillas idempotentes.

Los scripts de preflight/postflight controlan las exclusiones, Graphify y el gate Sentrux de cada checkout. Los grafos, baselines, claves, datos de prueba privados y directorios de herramientas se mantienen locales.

