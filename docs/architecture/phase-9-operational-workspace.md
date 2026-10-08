# Fase 9 — Operación retail, confianza y resiliencia

## Qué cambia

Se mantiene FastAPI + React en un solo servicio web. Los nuevos módulos usan
PostgreSQL y el tenant de la sesión; el navegador no recibe credenciales ni
acceso directo a la base. Los jobs existentes siguen separados del servidor web.

| Área | Resultado | Fuente / límite |
| --- | --- | --- |
| Hoy | Cola pequeña de productos, pedidos y reparaciones por revisar | Situación actual; no un listado exhaustivo |
| Productos | Búsqueda paginada y ficha 360°: ventas, compras, stock y disponibilidad | Documentos del rango; stock del último snapshot |
| Reponer | Introducción del producto, disponibilidad observada, tránsito pendiente, plazo físico y exclusiones comerciales | Sin inventar ventas perdidas ni disponibilidad anterior |
| Pedidos | Confirmación, entrega acordada, recepciones parciales e identificación de envíos inciertos | No crea facturas ni ajusta stock en Alegra |
| Caja | Saldo certificado y escenarios de cuatro semanas, por moneda | Cobros esperados no equivalen a dinero disponible |
| Servicio técnico | Equipo/serial, cliente, estados, repuestos, entrega y garantía vinculada | Registro propio; no suma de nuevo ventas facturadas en Alegra |
| Estado | Salud de procesos, checkpoints, eventos fallidos, reintento auditado y respaldo verificado | Una captura de respaldo sola no certifica restauración |
| IA | Herramientas de lectura, paginación, presupuesto/contexto y trazabilidad | Sin SQL libre, compras automáticas ni acceso entre empresas |

## Métricas y filtros

- El tablero inicia en modo **comercial**: `open` y `closed`. El modo
  **auditoría** conserva todos los estados no eliminados. La API conserva su
  valor predeterminado `audit` por compatibilidad; la UI y la IA envían el modo.
- Venta neta = facturas menos notas crédito. Ticket = venta neta / número de
  facturas. Una nota crédito no cuenta como otra factura. Si no hay facturas,
  el ticket es desconocido (`null`), no cero.
- Márgenes solo de líneas con costo asignado; la cobertura por valor usa
  importes absolutos para que notas crédito no distorsionen el denominador.
  No conocer el costo no significa margen cero. Los importes de monedas
  distintas se mantienen separados también en gráficos.
- Las vistas guardadas y la URL conservan filtros, menú, presupuesto y ciclo
  de revisión. No se guardan claves ni contraseñas en el navegador.
- Caja es actual/futura. Hoy, Estado y Pedidos son operativos actuales y no
  usan el período de ventas. Servicio técnico filtra la lista por recepción y
  sus indicadores terminados por entrega; pendientes incluyen toda la cola.
- En Reponer la demanda usa hasta 365 días antes del corte, limitada por la
  primera disponibilidad certificada. Producto/familia/proveedor limitan la
  vista **después** de asignar el presupuesto global. No son presupuestos nuevos.
- Los catálogos se consultan por páginas. Los rankings acotados del dashboard
  no equivalen a todo el catálogo; las herramientas del agente informan esa
  cobertura y ofrecen consulta paginada por producto.

## Reposición y recepción física

1. En **Productos**, marca `active`, `on_request`, `replaced` o `discontinued`.
   Las tres últimas excluyen la compra automática sugerida. Registra una fecha
   de introducción solo si puedes certificarla; no se deduce del backfill.
2. Sigue usando **Reponer → proveedor → productos**. Las nuevas pantallas no
   obligan a registrar reparaciones o caja para revisar esa recomendación.
3. En **Pedidos**, registra confirmación y fecha acordada. Las recepciones son
   idempotentes y no permiten aceptar más unidades que las pendientes.
4. Stock, facturas de proveedor y recepciones no se suman como entradas
   independientes. Para restar del tránsito se toma el mayor entre cantidades
   facturadas y aceptadas físicamente por producto/pedido.
5. El plazo observado se mide solo cuando la recepción está completa. Las
   facturas no prueban puntualidad ni entrega. Un acuerdo explícito de plazo
   tiene prioridad; los promedios observados se redondean hacia arriba y un
   plazo real de cero días no se reemplaza por siete.
6. Si editas/reordenas las líneas del pedido después de recibirlo, revisa la
   alerta de recepciones sin referencia coincidente. No se reasignan a otro
   producto ni se borran evidencias para ocultar la inconsistencia.
7. Días documentados sin stock solo se excluyen del entrenamiento cuando hay
   al menos 14 días observados y cobertura de la mitad del período. Días no
   observados no prueban disponibilidad. Poco historial reduce la confianza.
8. Los componentes sin evidencia física conservan incertidumbre en el score
   de proveedor. El proxy de margen usado solo para ordenar oportunidades
   queda identificado; no es un beneficio calculado ni un KPI financiero.

La sugerencia, el resumen y la aprobación incluyen el flete por proveedor.
Umbral ausente significa que no se ha acordado envío gratis, no flete cero;
un umbral explícito de cero sí permite envío gratis desde cualquier importe.
Los costos de envío son estimaciones operativas, no cargos creados en Alegra.

Un POST a Alegra de resultado incierto no se reenvía automáticamente. En
**Pedidos → Envíos con resultado incierto**, busca primero la orden en Alegra,
indica su ID y verifica proveedor, referencia del plan y cantidades. La app
solo hace un GET y vincula una coincidencia comprobada. Crear/enviar órdenes
sigue requiriendo guardar, aprobar y confirmar explícitamente.

## Caja: arranque sin falsa precisión

1. Certifica el saldo combinado real de caja/bancos al cierre de una fecha,
   en su moneda. La app no lo deduce de ventas ni intenta reconstruir 2024.
2. Registra movimientos manuales solo si **no** están registrados en Alegra.
   De lo contrario los contarías dos veces. Planeados y realizados son distintos.
3. El saldo reconstruido añade pagos de Alegra y movimientos manuales realizados
   posteriores al cierre certificado. Pagos de dirección desconocida se excluyen
   con advertencia. La ausencia de sincronización de pagos hoy también se avisa.
4. Las cuatro semanas muestran escenario conservador sin cobros pendientes y
   esperado con cobros estimados. Restan saldos de proveedor, compromisos no
   facturados de pedidos y egresos manuales previstos. Vencimientos ausentes
   se colocan prudentemente en la primera semana, con aviso.

No usar un escenario como saldo bancario. Si faltan pagos, saldos o vencimientos,
revisa la advertencia antes de tomar decisiones de compra.

## Servicio técnico

Recibe equipo y serial, registra diagnóstico/aprobación, reparación, listo y
entrega. Solo se permiten transiciones definidas; no pasar de recibido a
entregado. El trabajo puede vincularse a una factura existente del mismo tenant.
Las piezas registradas no descuentan inventario; esa salida debe registrarse en
Alegra por el procedimiento operativo de la empresa.

Para margen operativo se requieren importe cobrado, costo de mano de obra y
la certificación **costos completos**. Agregar otra pieza revoca esa
certificación. Un importe desconocido sigue como desconocido, aunque no se hayan
registrado piezas. Garantías deben referirse al mismo serial de un trabajo
entregado y estar dentro del plazo registrado. Esto es control operativo, no
una definición de obligaciones legales de garantía.

## Resiliencia y seguridad

- Eventos reclamados por un worker muerto se recuperan después de 15 minutos,
  con token de propietario. El proceso antiguo no puede confirmar resultados
  sobre el propietario nuevo. Los reintentos manuales solo admiten fallidos y
  dejan auditoría por tenant.
- Reconciliación: ventana y último día completo en `sync_runs`; una interrupción
  reejecuta el día incompleto idempotentemente. Evita ejecuciones solapadas por
  recurso. La propiedad expira tras una hora sin heartbeat y se renueva/fencea
  antes de confirmar cada lote/día. Una ejecución exitosa sí permite volver a
  conciliar la ventana para recoger cambios posteriores.
- Cookie privada, comprobación de origen para escrituras, bloqueo persistente
  de 15 minutos tras diez intentos fallidos y revocación de todas las sesiones.
  Cambiar la contraseña invalida sesiones existentes.
- Logs de duración por ruta e identificador de petición; consultas lentas sin
  SQL ni parámetros. El access log de Uvicorn elimina el query string para no
  publicar tokens del webhook. Los errores SQL ocultan parámetros.
- `/healthz` = proceso vivo. `/readyz` = base accesible y migración esperada.
  Ninguno devuelve credenciales. Los endpoints de Estado sí requieren sesión.
- No se introduce un mart incremental sin medirlo: los costos FIFO y documentos
  editados pueden afectar resultados posteriores. Se conserva el refresco
  transaccional existente; primero medir duración, filas y consultas lentas.

## Despliegue en Railway

1. Publicar esta versión y conservar las variables actuales. No hay nuevas
   claves obligatorias ni un segundo servicio frontend. Mantener `DATABASE_URL`,
   `APP_ENV=production`, `APP_SECRET_KEY`, `DASHBOARD_PASSWORD` y
   `DASHBOARD_TENANT_ID`. Las claves de IA permanecen solo en el servicio API.
2. Pre-deploy del API: `python -m app.cli migrate`. Solo un responsable aplica
   migraciones; los workers/crons no deben competir por ejecutarlas.
3. Start command del API: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
   Usar el Dockerfile multi-stage del repositorio, no un build Python sin React.
4. Cambiar el healthcheck a `/readyz` después de configurar el pre-deploy.
5. Ejecutar una vez `python -m app.cli refresh-mart <tenant-uuid>` y comprobar
   login, Productos, Reponer, Pedidos y Estado. Las tablas propias nuevas empiezan
   vacías: se llenan al registrar datos, no con valores fabricados.
6. Conservar worker, reconciliación, snapshot y refresco del mart existentes.
   Los módulos nuevos no requieren otro servicio permanente.
7. Si pagos/notas crédito no tienen un ETL completo recurrente, configurar una
   ejecución diaria del extractor existente antes del mart:

   ```text
   python -m app.cli backfill-all <tenant-uuid> --resources contact,item,warehouse,seller,payment,credit_note --requests-per-minute 100
   ```

   Es una reconciliación completa idempotente, **no incremental**: medir duración
   y espaciarla para no solaparse con otros consumidores del mismo token.
   No inventar filtros de actualización que Alegra no garantice. El Cron actual
   de facturas/compras no reemplaza este trabajo para pagos históricos editados.

La verificación local no certifica el despliegue de producción. Publicar código
no prueba un despliegue saludable: comprobar la migración efectiva, `/readyz`,
login, respuestas autenticadas y la interfaz con los datos reales.

## Respaldo con restauración comprobable

Requiere `pg_dump`, `pg_restore` y `psql` de versión compatible con el servidor,
disponibles en PATH. Ejecutar desde un equipo/runner seguro con `DATABASE_URL`
del origen. El snapshot exportado y el dump comparten una misma vista consistente.

```text
python -m app.backups capture <tenant-uuid> --output data/retail-YYYYMMDD.dump
```

Conservar `.dump` y `.json` cifrados fuera del repo, con política de retención
propia. La herramienta no configura almacenamiento externo ni el plan de
backups de Railway y no declara éxito de restauración por existir un archivo.

Restaurar manualmente en una base **nueva y aislada** mediante `pg_restore`.
No restaurar sobre producción ni usar `--clean` sin un procedimiento aprobado.
Las credenciales se pasan por variables `PGHOST`, `PGPORT`, `PGUSER`,
`PGDATABASE`, `PGPASSWORD`/`PGSSLMODE`, no por argumentos visibles.

Configurar `RESTORED_DATABASE_URL` y comprobar:

```text
python -m app.backups verify <tenant-uuid> --manifest data/retail-YYYYMMDD.json
```

La verificación rechaza origen=destino y compara versión, filas y huellas de
tablas del tenant, más tablas compartidas/no particionadas por tenant. Solo
entonces registra `backup_verifications` en el origen. Las huellas detectan
cambios accidentales; no son una firma contra manipulación maliciosa del manifiesto.

## Verificación y límites

CI incorpora PostgreSQL 16, migraciones desde cero, pruebas de consultas,
autenticación, tenant, notas crédito, recepción, caja, garantía, lease, IA offline,
build React y Playwright en escritorio/móvil. Las pruebas PostgreSQL exigen
`TEST_DATABASE_URL` de una base local/CI cuyo nombre termine en `_test`.

Localmente se verificaron todas las migraciones desde cero y una captura/
restauración real aislada. Esto **no** certifica backups de producción ni
respuestas de un modelo real: la evaluación de IA usa un cliente simulado y
compara datos/contexto contra SQL, sin consumir claves ni presupuesto del usuario.

La app mantiene un único usuario. No se añaden roles SaaS, permisos por empleado,
ventas perdidas estimadas, costos históricos inexistentes ni órdenes automáticas.
