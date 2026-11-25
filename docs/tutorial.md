# Revisar una factura en Oracle APEX

Este recorrido usa una factura de ejemplo para comprobar que la aplicación extrae el número y el importe, y para registrar una corrección con su motivo. Necesitas una instancia de OCI Invoice Extractor y una cuenta de operador o revisor. Si acabas de instalarla, abre el enlace HTTPS de **Oracle APEX** en el correo del job. [Guía de despliegue](../README.md#desplegar-en-otro-tenancy-con-deploy-studio)

## Probar una factura individual

1. Desde el checkout, ejecuta `uv run --locked python scripts/sample_zip.py build/demo.zip`. El ZIP contiene `demo.pdf` con datos inventados. Descomprímelo para usar el PDF directamente.
2. En APEX, abre **Process Document**, elige `demo.pdf` y pulsa **Run extraction**. El visor y la lista de resultados aparecen al terminar.
3. Busca **Número de factura** y **Total de la factura**. Con el catálogo inicial deberías obtener `DEMO-001` y un importe de 25.000 COP. Selecciona un campo encontrado para localizar su evidencia en la página. Un campo ausente, como el CUFE en este ejemplo, aparece como **Missing**.

## Procesar el ZIP y guardar una revisión

1. Abre **Upload a ZIP batch**, elige la ruta inicial y sube `demo.zip` con **Upload to bucket**.
2. En **Batch monitor**, anota el número que aparece en **Batch to monitor**. Abre ese lote en **Batches** y selecciona su documento cuando termine. El resultado esperado es un PDF procesado con el mismo número y total de la prueba individual.
3. En **Field review**, selecciona un resultado. Introduce el **Approved value** y el **Reason**, y pulsa **Save correction**. Revisa que se conserve el valor extraído y aparezca la revisión. Si una extracción posterior difiere de una corrección vigente, APEX la muestra como discrepancia.

## Qué ocurre con el archivo

La prueba individual envía el PDF por APEX. En un lote, APEX carga el ZIP al prefijo autorizado del bucket y OCI Events invoca la Function, que registra el objeto y despacha el trabajo asíncrono. Ambos caminos usan el mismo extractor y recurren a OCR cuando la página tiene texto ilegible o escaneado. La ruta del lote y el catálogo vigente determinan qué campos se buscan. `INV_DATA` guarda los resultados y las correcciones del lote. [Arquitectura](arquitectura/README.md) · [Pruebas](testing.md)
