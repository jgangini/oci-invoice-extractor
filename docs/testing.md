# Pruebas de OCI Invoice Extractor

Ejecuta las comprobaciones desde la raíz con Python 3.11 o 3.12, uv y Terraform. Para los dobles de proveedor de `terraform test` se requiere Terraform 1.7 o posterior. Las pruebas de controles APEX usan Chromium cuando está disponible en el entorno.

```powershell
uv sync --locked
uv run --locked python -m pytest -q
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform validate
terraform -chdir=terraform test
```

Python cubre la extracción, la previsualización, los controles de APEX y los hooks de instalación. Los planes de Terraform usan un proveedor simulado. Comprueban los modos de creación y reutilización de bucket, base y Vault sin aprovisionar recursos OCI.

Al crear una base, Terraform solicita APEX y ORDS habilitados. OCI devuelve después su lista completa de herramientas. Un plan posterior debe conservar esa lista y evitar un `UpdateAutonomousDatabase` que solo vuelva a enviar APEX y ORDS.

`uv run --locked python scripts/sample_zip.py build/demo.zip` crea una factura reproducible. `uv run --locked python invoke_local.py fixtures/object-create.json --archive build/demo.zip` ejecuta el handler FDK con el catálogo del proyecto y muestra solo conteos. Usa otro nombre de ZIP y `--scan` para probar una entrada rasterizada.

## Aceptación en Deploy Studio

1. Comprueba el manifiesto de la revisión con `deploy_studio.py check <proyecto> --studio <checkout-cloudtechnext>` del plugin OCI Deploy Studio. Fija el tag y el SHA de esa misma revisión para la prueba en vivo.
2. Revisa los recursos de PLAN y APPLY en Jobs. Después de Apply, el hook instala APEX e invoca la Function antes de habilitar Events. Espera una respuesta de solo lectura para un lote inexistente. Eso comprueba la red de la Function, su permiso de Vault, el inicio de sesión en Autonomous Database y el permiso del paquete de ingesta. El evento esperado es **APEX installed, Function database access verified and Events rule enabled.**
3. Comprueba el enlace HTTPS y el inicio de sesión con el operador inicial desde una red autorizada. Sigue el [tutorial de APEX](tutorial.md) para verificar el PDF directo, el ZIP, los valores de muestra y una corrección. Este paso comprueba el acceso a ORDS y el recorrido completo del archivo.
4. Verifica en el correo **Oracle APEX**, **Object Storage** y el adjunto `deployment.log`. El correo incluye solo las credenciales iniciales de APEX. Registra los resultados de aceptación con el ID del job y conserva los datos de acceso dentro del entorno de prueba.

La [imagen de la ficha](assets/oci-invoice-extractor-card.png) corresponde al archivo público `/assets/oci-invoice-extractor-card.png` del portal. Antes de verificar una instalación del portal, comprueba que esa ruta devuelva la imagen con `Content-Type: image/png`.
