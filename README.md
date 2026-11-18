# OCI Invoice Extractor

Procesa facturas PDF en lotes ZIP. La regla de OCI Events entrega las cargas del bucket a OCI Functions. El primer handler registra la versión del objeto y programa el procesamiento asíncrono. La Function lee el texto de cada página, recurre a RapidOCR si es ilegible y guarda los campos en Autonomous Database. Oracle APEX permite administrar las plantillas, configurar rutas y revisar resultados.

## Desplegar en otro tenancy con Deploy Studio

Cada instalación utiliza los recursos, IAM, secretos y datos del tenancy de destino. La fuente de la release es pública. El registro de imágenes y el bucket del despliegue son privados.

1. En Deploy Studio elegir **OCI Invoice Extractor**, la release y la región. Cargar el perfil OCI y la clave mediante los controles del instalador. Seleccionar o crear el compartimento.
2. En **Object Storage**, elegir **Create new bucket** e indicar un nombre disponible, o **Use existing bucket** y elegir uno por nombre. Un bucket reutilizado debe ser privado, de almacenamiento Standard, admitir escritura y tener habilitados los eventos de objetos. Un bucket nuevo incluye la carpeta `source/` para subir los ZIP. Al reutilizar un bucket, **Input folder prefix** permite elegir una carpeta existente.
3. En **Oracle AI Database**, elegir **Create new database** y el perfil **Oracle AI Database 26ai · Transaction Processing (OLTP)**. La base se crea con 4 ECPU y 1 TB con licencia incluida y sin autoescalado de CPU. Introducir o generar la contraseña en **Database admin password**. En **Network access → Allowed IP ranges**, indicar las IPv4 públicas del worker y de los usuarios APEX, separadas por comas (redes /24 a /32). Para una sola IP, añadir /32. **Use existing database** permite elegir por nombre una base OLTP 26ai en estado AVAILABLE e introducir la contraseña ADMIN para la instalación. El instalador comprueba acceso TLS desde el worker, compatibilidad APEX y disponibilidad de los nombres reservados. La Function usa una VCN nueva. En este perfil de reutilización se admite un endpoint público que ya permite esas conexiones. La base existente debe tener habilitado el principal de recurso de ADMIN. Si los esquemas INV_DATA, INV_FN o INV_APEX, el workspace INVOICES o la aplicación 100 pertenecen a otra instalación, el preflight detiene el despliegue antes de modificar la base. La capacidad, ACL, credenciales ADMIN y ciclo de vida de la base existente permanecen bajo el control de su propietario.
4. Definir el operador APEX y generar o introducir su contraseña. Pulsar **Validate** y luego **Deploy**. El worker prepara la imagen versionada de la release en el registro privado de destino. Vault gestiona la credencial de la Function. El plan de Resource Manager aprovisiona los recursos. El instalador crea el workspace **INVOICES**, instala la aplicación 100 y habilita Events tras verificar la conexión de la Function. El resultado incluye la URL HTTPS de APEX, el bucket y el prefijo. Este flujo requiere una versión de Deploy Studio compatible con el paso de preparación de imágenes.
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

