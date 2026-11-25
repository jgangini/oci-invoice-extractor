<p align="center">
  <img src="docs/assets/invoice-scanner.png" alt="Escáner rojo que destaca dos campos de una factura" width="224">
</p>

<h3 align="center">OCI Invoice Extractor</h3>

<p align="center">Extrae campos de facturas PDF y revisa su evidencia y sus correcciones en Oracle APEX.</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/Licencia-MIT-165D63?style=flat-square" alt="Licencia MIT"></a>
  <a href="https://github.com/jgangini/oci-invoice-extractor/releases/tag/v0.3.4"><img src="https://img.shields.io/badge/release-v0.3.4-007EC6?style=flat-square" alt="Release v0.3.4"></a>
</p>

<p align="center"><strong>Tecnologías</strong></p>
<p align="center">
  <a href="terraform/ingestion.tf"><img src="https://img.shields.io/badge/OCI-Functions-C74634?style=flat-square" alt="OCI Functions"></a>
  <a href="apex/app"><img src="https://img.shields.io/badge/Oracle-APEX-C74634?style=flat-square" alt="Oracle APEX"></a>
  <a href="terraform/database.tf"><img src="https://img.shields.io/badge/Oracle-Autonomous%20AI%20Database-C74634?style=flat-square" alt="Autonomous AI Database"></a>
  <a href="terraform"><img src="https://img.shields.io/badge/Infraestructura-Terraform-654FF0?style=flat-square" alt="Terraform"></a>
</p>

<p align="center">
  <a href="#desplegar-en-otro-tenancy-con-deploy-studio">Despliegue</a> ·
  <a href="docs/tutorial.md">Tutorial de uso</a> ·
  <a href="docs/arquitectura/README.md">Arquitectura</a> ·
  <a href="docs/testing.md">Pruebas</a> ·
  <a href="docs/modelo/inv-data.html">Modelo de datos</a>
</p>

OCI Events entrega a OCI Functions los lotes ZIP que llegan a Object Storage. La Function registra el objeto, despacha el procesamiento y usa RapidOCR cuando el PDF tiene texto ilegible o escaneado. Autonomous Database conserva los resultados y Oracle APEX permite revisar facturas, administrar plantillas y configurar rutas.

## Desplegar en otro tenancy con Deploy Studio

Cada instalación utiliza los recursos, IAM, secretos y datos de la tenencia de destino. La configuración de OCIR está orientada al realm comercial de OCI. La fuente de la release es pública y el registro de imágenes y el bucket del despliegue son privados. El perfil que despliega debe poder invocar la Function en el compartimento elegido. `manage functions-family` ya incluye ese permiso. Para un perfil con permisos granulares se requiere `use fn-invocation`. [Permisos de OCI Functions](https://docs.oracle.com/en-us/iaas/Content/Functions/Tasks/functionsrestrictinguseraccess.htm)

1. En Deploy Studio elegir **OCI Invoice Extractor**, la release y la región. Cargar el perfil OCI y la clave mediante los controles del instalador. Seleccionar o crear el compartimento.
2. En **Object Storage**, elegir **Create new bucket** e indicar un nombre disponible, o **Use existing bucket** y elegir uno por nombre. Deploy Studio propone `oci-invoice-extractor` para un bucket nuevo y crea la carpeta `source/`. Un bucket reutilizado debe ser privado, de almacenamiento Standard, admitir escritura y tener habilitados los eventos de objetos. Al reutilizar un bucket, **Input folder prefix** indica el prefijo de entrada.
3. En **OCI Vault**, **Use existing vault** permite seleccionar por nombre un vault y su clave AES habilitada. **Create new vault** crea un vault y una clave dedicados a la instalación. Cada instalación tiene sus propios secretos.
4. En **Oracle AI Database**, elegir **Create new database** y el perfil **Oracle AI Database 26ai · Transaction Processing (OLTP)**. La base se crea con 4 ECPU y 1 TB con licencia incluida y sin autoescalado de CPU. Introducir o generar la contraseña en **Database admin password**. En **Network access → Allowed IP ranges**, indicar las IPv4 públicas del worker y de los usuarios APEX, separadas por comas (redes /24 a /32). Para una sola IP, añadir /32. **Use existing database** permite elegir por nombre una base OLTP 26ai en estado AVAILABLE e introducir la contraseña ADMIN para la instalación. El instalador comprueba acceso TLS desde el worker, compatibilidad APEX y disponibilidad de los nombres reservados. La Function usa una VCN nueva. En este perfil de reutilización se admite un endpoint público que ya permite esas conexiones. La base existente debe tener habilitado el principal de recurso de ADMIN. El instalador requiere permisos para crear y retirar el perfil de Oracle que identifica sus esquemas. Si los esquemas INV_DATA, INV_FN o INV_APEX, el workspace INVOICES o la aplicación 100 pertenecen a otra instalación, el preflight detiene el despliegue antes de modificar la base. La capacidad, ACL, credenciales ADMIN y ciclo de vida de la base existente permanecen bajo el control de su propietario.
5. Definir el operador APEX y generar o introducir su contraseña. Pulsar **Validate** y luego **Deploy**. El worker prepara la imagen versionada de la release en el registro privado de destino. Resource Manager muestra el plan y el estado de sus recursos. El instalador crea el workspace **INVOICES**, instala la aplicación 100 y verifica que la Function lea su secreto de Vault y ejecute una consulta de solo lectura como `INV_FN`. OCI Events se habilita después de esa comprobación. Este flujo requiere una versión de Deploy Studio compatible con la preparación de imágenes y los hooks del manifiesto.
6. En el correo del job terminado, abrir **Oracle APEX → Access URL** e iniciar sesión con el usuario y la contraseña iniciales. **Object Storage** indica el bucket y el prefijo para los ZIP. Usa el [tutorial de APEX](docs/tutorial.md) para comprobar la carga y la revisión con una factura sintética.

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

La instalación carga la plantilla **Invoice Catalog**, con doce campos, y la asocia a la ruta inicial. Si Studio crea el bucket, también deja una copia en `source/invoice-catalog.json`. Cada campo declara su clave, tipo, etiquetas y aplicabilidad. Los nombres visibles de los campos, las etiquetas de búsqueda y la documentación del catálogo están en español. Los códigos y NIT mantienen sus ceros iniciales. Un campo ausente o ambiguo conserva su estado. [Catálogo inicial](source/invoice-catalog.json)

En **Templates**, el administrador abre y edita el catálogo vigente. Puede importar un JSON UTF-8 para sustituirlo, guardar los cambios y descargar la definición. Se aceptan archivos antiguos con la propiedad `version`, que se omite al guardar y exportar. **Routes** asocia cada prefijo a un destino lógico. Los lotes nuevos y las pruebas de PDF toman el catálogo vigente al ingresar. Cada extracción conserva su definición para mantener los resultados y las reanudaciones coherentes con los campos originales.

En **Process Document**, el revisor selecciona un PDF de hasta 12 MiB con **Upload File** y ejecuta la extracción con el catálogo vigente. **Run extraction** se habilita al seleccionar un PDF válido. Al pulsar el botón, los campos de carga se ocultan y aparece un indicador junto a **Run extraction** hasta que el documento termine. APEX recibe el PDF por partes verificadas y devuelve el identificador de la prueba cuando recibe el archivo completo. La misma pestaña conserva el PDF y consulta cada segundo el estado de esa prueba. El nombre de la plantilla utilizada se muestra encima de las etapas. Si falla el envío, vuelve a mostrar el formulario con un mensaje. Durante el envío y la extracción, el visor y el panel de resultados muestran sus esqueletos. Cuando la extracción termina con éxito, el visor revela las páginas de evidencia o el PDF original en cuanto se cargan. Al terminar, el formulario sigue oculto. **New execution** abre el formulario y oculta la revisión anterior. La vista previa y las páginas de evidencia usan direcciones propias de cada extracción, validadas en la sesión de APEX. Si falla una consulta, la reintenta. La Function aplica el mismo extractor que procesa los ZIP. Los selectores **Page** y **Overlay** están junto al visor. Cuando los paneles aparecen lado a lado, la lista de campos se desplaza de forma independiente dentro de la altura del documento. **Template fields** muestra **Found** en verde junto a los campos encontrados y **Missing** en gris sobre una fila de fondo gris suave. Combina **Field** y **Field status** para filtrar la lista y los recuadros del visor por nombre y estado. Cada valor localizado enlaza con su recuadro de color. **Extracted fields** muestra el total y los campos encontrados. **Recognized text** muestra los fragmentos de texto nativo o de RapidOCR, incluso los que quedan fuera del catálogo, y su cabecera indica cuántos hay en la página. **Text region** permite aislar uno de esos fragmentos y su recuadro. Al pasar el puntero sobre una fila con evidencia, el visor oscurece gradualmente el resto de la página y destaca el área correspondiente. Pulsa la fila o su marca para fijar el resaltado. Haz clic fuera de la hoja, pulsa una zona sin marcar o usa Escape para quitarlo. La etiqueta aparece separada del valor impreso. Cuando la posición es ambigua, el visor indica que falta evidencia localizada.

El visor renderiza hasta 16 páginas por prueba, priorizando las que contienen campos. La capa de texto admite hasta 120 líneas por página y 800 en total e indica cuántas presenta. Las miniaturas y los resultados se consultan durante 24 horas desde la cuenta que inició la prueba. La siguiente prueba elimina los registros vencidos. El PDF de trabajo de la Function se libera cuando finaliza o falla la extracción.

En **Upload a ZIP batch**, el revisor selecciona una ruta y envía un archivo de hasta 48 MiB al bucket. La base firma la carga con su identidad OCI y solo puede crear objetos dentro del prefijo autorizado. El monitor permite seguir también un lote que llegó directamente a Object Storage. Muestra el estado de cada PDF y estima el tiempo restante con el trabajo ya terminado.

La fuente editable está en `apex/app`. La release lleva un export SQL generado con SQLcl contra APEX 26.1. Validar con `apex validate -input apex/app`; importar en un workspace de construcción, exportar la aplicación 100 y probar la reinstalación del export sobre datos existentes. El instalador necesita una versión de APEX compatible con ese export. [Fuentes y licencia de la plantilla APEX](apex/NOTICE.md)

## Operación y diagnóstico

**Destroy** elimina los recursos creados para esa instalación. Al confirmarlo se eliminan también los ZIP, las versiones históricas y las cargas incompletas de un bucket creado por el despliegue. Un bucket reutilizado conserva su contenido. En una base reutilizada, Studio solicita la contraseña ADMIN para retirar la aplicación 100, el workspace INVOICES y los esquemas propios INV_DATA, INV_APEX e INV_FN, incluidos los resultados. El instalador comprueba la identidad de cada esquema y del workspace antes de eliminarlos. Si se reutilizaron el vault y su clave, solo se programa la eliminación de los secretos de esa instalación. Los servicios de OCI sujetos a un periodo obligatorio de retención terminan de eliminarse en la fecha asignada por Oracle.

- **No aparece un lote.** Comprobar la regla activa, el evento de creación de objeto, bucket, namespace, extensión y subprefijo. Los subprefijos sin ruta activa se ignoran. Correlacionar el ID de evento, invocación y lote en Logging.
- **Un lote queda pendiente.** Revisar el permiso de autoinvocación y el acceso desde la Function a Vault, Object Storage y Autonomous. Reprocesar desde APEX. Se conserva el avance por PDF. Una ejecución concurrente no duplica la misma generación del documento.
- **Errores de un PDF.** Archivos protegidos, miembros duplicados, nombres inválidos o documentos que exceden el presupuesto producen estados de diagnóstico. El ZIP se procesa en memoria, los nombres de sus miembros no se escriben al sistema de archivos y las cadenas de error de terceros no se publican en los logs.
- **Calidad OCR.** El motor CPU se distribuye dentro de la imagen. El lockfile fija las dependencias y hashes de los paquetes. Se usan los modelos ONNX incluidos en rapidocr-onnxruntime 1.4.4. En páginas rasterizadas, el extractor distingue `TOTAL$644.000` del encabezado de columna `Total`. También admite `TOTALS` y `TOTA1S` cuando el importe completo tiene separadores de miles. Las coordenadas permiten leer la columna de servicios bajo los cinco encabezados esperados y asignar NIT a bloques alineados de prestador y pagador. Los valores conservan la lectura del OCR y pueden contener errores de reconocimiento. En cuatro escaneos sintéticos, volver a leer recortes no recuperó dígitos que el OCR inicial hubiera perdido. GenAI permanece desactivada. [RapidOCR](https://github.com/RapidAI/RapidOCR)
- **Actualizar o recuperar.** Conservar la versión de la imagen, el tag y los backups del esquema. Una reinstalación de APEX no elimina los resultados. Revisar el plan de Resource Manager antes de reemplazar almacenamiento o base. Las sentencias DDL de Oracle pueden confirmar cambios parciales; el hook reanuda la instalación mediante objetos y semillas idempotentes.

Los scripts de preflight/postflight controlan las exclusiones, Graphify y el gate Sentrux de cada checkout. Los grafos, baselines, claves, datos de prueba privados y directorios de herramientas se mantienen locales.

## Licencia

El código y la documentación propios de OCI Invoice Extractor se distribuyen bajo [MIT](LICENSE). Los componentes de Oracle y sus iconos se rigen por sus respectivos avisos. [Oracle APEX y UPL](apex/NOTICE.md) · [Iconos de OCI](docs/arquitectura/assets/licenses/oci-NOTICE.txt)

## Documentación

- [Tutorial de Oracle APEX](docs/tutorial.md)
- [Arquitectura y modelo de datos](docs/arquitectura/README.md)
- [Pruebas locales y aceptación en Deploy Studio](docs/testing.md)

