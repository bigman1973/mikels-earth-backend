# Bloque 9.3: corte de compras BF26

Preparación exclusivamente LOCAL para revisión/integración con el despliegue de catálogo del día 14, nunca antes ni por separado. Decisión del bloque 12 aprobada por Jordi el 2026-11-05: asumir la limitación de Stripe y quitar el margen de 31 minutos, sin más construcción para eliminararla. No autoriza desplegar, activar productos, ejecutar expiraciones reales ni programar automatización. La retirada de catálogo sigue siendo manual; este mecanismo sólo limita Checkout, no cambia `active`, visibilidad, noindex, precios, IVA, envío, consentimientos ni textos VIP.

El último segundo válido es **2026-11-30 23:59:59 Europe/Madrid = 22:59:59Z**. El corte exclusivo es **2026-12-01 00:00 Europe/Madrid = 2026-11-30T23:00:00Z**. Se comprueba `now >= corte` con fecha consciente de zona y reloj de servidor; no se acepta un reloj o indicador de campaña del navegador.

## Creación y sesiones abiertas

La identidad BF procede de los seis SKU aprobados resueltos en base de datos, incluidos carritos mixtos y referencias por ID/slug/SKU. Tras el corte se devuelve `409 BF26_CAMPAIGN_CLOSED` antes de ERP, stock o Stripe. Se repite la comprobación después de operaciones lentas y justo antes de abrir Checkout. No se habilita BF por fecha: las barreras de disponibilidad/inactividad existentes siguen vigentes. BF no puede abrir una sesión de suscripción.

Stripe requiere `expires_at` entre 30 minutos y 24 horas desde creación.[1] Para todas las sesiones BF, incluidas las del último tramo del día 30 y sus últimos segundos, se solicita exactamente `expires_at = int(now.timestamp()) + 1800`, sin minuto de margen. Stripe recibe un timestamp Unix entero: con un reloj de segundo completo equivale a `now.timestamp() + 1800`; los microsegundos se descartan, no se redondea hacia arriba ni se añade margen. La reserva y el snapshot conservan la misma expiración. No se cambia el comportamiento de sesiones ajenas a BF.

En la frontera de 30 minutos exactos la expiración coincide con el corte y `purchase_campaign_requires_sweep=false`; si queda menos tiempo, la expiración solicitada rebasa medianoche y se conserva `purchase_campaign_requires_sweep=true`, junto con `purchase_campaign=BF26` y `purchase_campaign_closes_at`. No se adelanta el cierre a las 23:30 Madrid. La latencia de creación, el truncado a segundos o el desfase de reloj pueden hacer que Stripe rechace ese mínimo. Se acepta esta limitación: la defensa existente devuelve `503 BF26_CHECKOUT_UNAVAILABLE`, sin URL ni reintento con 31 minutos, 24 horas u omisión de `expires_at`. No se afirma que toda petición anterior al corte obtenga sesión. Cualquier validación real que exija una duración distinta debe notificarse, no ocultarse mediante un fallback.

Se guarda el snapshot fiscal/de precios antes de exponer una sesión BF pagable. Los errores ambiguos de creación/vinculación no lo borran. La clave idempotente de creación es técnica y sin PII. Si la llamada cruza el corte, el servidor intenta expirar la sesión y no devuelve una URL abierta; si falla la expiración devuelve `503 BF26_CLOSURE_PENDING`, deja la evidencia para catchup y conserva cualquier pago que ya haya ganado la carrera.

## Job application-native, pendiente de orquestación

La función `src.jobs.bf26_expiration.run_once` no hace nada antes del corte. Desde el corte expira sesiones BF identificadas por la vinculación local de reservas a productos BF y también recorre paginado las sesiones Stripe abiertas, filtrando `mode=payment` y los metadatos BF. El segundo recorrido cubre sesiones huérfanas de una creación cuya vinculación local falló y las creadas por llamadas en vuelo después de medianoche. No depende de que las reservas locales sigan activas. No excluye fechas de creación posteriores al corte. Limita el escaneo a la ventana de 24 horas máxima de Checkout.[2]

El endpoint `expire` sólo acepta sesiones abiertas. Una sesión ya expirada o completa produce error; se recupera el estado con `retrieve` para clasificar `expired`, `complete` o `retry`.[3] La idempotencia del job es por estado y por deduplicación en cada pasada. Repetir una expiración no crea un pago ni devuelve dinero. No se reutiliza una clave permanente para `expire`, porque Stripe API v1 cachea incluso respuestas 500 de una clave y podría bloquear el catchup durante al menos 24 horas.[4]

El punto de entrada explícito es:

```bash
python -m src.jobs.bf26_expiration --execute --max-pages 100
```

**No se ha ejecutado este comando contra ningún proveedor.** Requiere `DATABASE_URL` y `STRIPE_SECRET_KEY` en el entorno aprobado. No importa `src.main`, no ejecuta migraciones, `create_all`, seeds ni handlers masivos al importar. No dispone de endpoint público. Sin `--execute` no actúa. Antes del corte devuelve un resultado agregado sin leer claves ni llamar a Stripe. Después devuelve únicamente contadores, sin IDs, PII ni errores crudos; código de salida 2 si hay fallo o requiere retry.

El padre debe decidir/provisionar la orquestación nativa en el entorno de la aplicación, para disparar la primera pasada en el corte y repetir tras fallos/reinicios y mientras puedan existir creaciones en vuelo. No sirve una única pasada sin catchup. `scan_failed`, `retry` o `page_limit_reached` requieren una nueva invocación; el límite de páginas debe dimensionarse para la carga real y aumentarse si impide completar el recorrido. Los timeouts y el watchdog del proceso son responsabilidad del runner aprobado. Un reinicio vuelve a descubrir las sesiones aún abiertas sin checkpoint. No se han creado cron, tareas Manus, trigger, workers persistentes ni cambios de infraestructura.

La orquestación sigue pendiente y el job de corte **no está activado**. Su latencia no bloquea la decisión aprobada: al activarse en el entorno autorizado expirará las sesiones abiertas que Stripe permita, sin prometer atomicidad ni cancelar un pago que gane la carrera. No se amplía la arquitectura para compensarlo.

## Webhooks y métodos diferidos

La hora de recepción no decide si se conserva un pedido pagado. No se rechaza un webhook cobrado por haber llegado tras el corte. Stripe puede reintentar durante tres días en live mode y no garantiza orden.[5] Se mantiene el snapshot aunque haya vencido su fecha y se deduplica por sesión/PaymentIntent junto con las protecciones de persistencia existentes. La fecha `event.created` no es una prueba suficiente de liquidación ni se usa para negar fulfillment.

Checkout mantiene exclusivamente `payment_method_types=['card']`, captura actual y demás parámetros comerciales. No se habilitan métodos async, captura manual ni refunds. Para sesiones históricas que pudieran usar métodos diferidos, `completed` con `payment_status=unpaid` no se considera cobro: se espera `async_payment_succeeded`. El éxito posterior sigue registrándose una sola vez, incluso después del corte, en lugar de perder un pago ya realizado.[6] Expirar sesiones abiertas no revoca pagos iniciados en una sesión ya completa/en procesamiento.

## Limitación asumida y ejecución pendiente

**No es garantizable un corte instantáneo absoluto a través de Checkout alojado + job HTTP manteniendo abierto todo el lunes.** La duración mínima de Stripe impide crear una nueva sesión de los últimos 29:59 con `expires_at` en medianoche. El job necesariamente opera con latencia y Stripe sólo puede expirar mientras el estado siga abierto. Un pago en carrera puede completarse antes de que Stripe procese expire; una sesión completa con método diferido puede liquidarse después. Jordi ha asumido esta brecha el 2026-11-05 y ha descartado más construcción para eliminarla: no es un bloqueo de esta decisión ni se presenta como garantía absoluta. No se propone captura manual, cambio de métodos, cobro seguido de refund automático ni fiscalidad alternativa para prometer lo imposible. Los pagos ganadores se sirven y sus webhooks cobrados no se rechazan por el corte.

Quedan pendientes de ejecución del padre la integración con los cambios de otras ramas, el despliegue autorizado junto al catálogo del día 14 y la orquestación del corte. Los tests de corte usan SQLite con proveedores mockeados; el runner integrado añade PostgreSQL 16 efímero por socket Unix para los tests que lo requieren y bloquea sockets IP mediante seccomp. No se usan secretos ni se validan cuentas, API version/configuración live, aceptación real del mínimo por Stripe, entrega webhook real, latencias ni infraestructura en producción. La conservación de pedidos tardíos no cambia la política heredada de reservas consumidas sólo en estado activo; conciliación operativa de stock para reservas ya expiradas/liberadas por procesos existentes sigue fuera de este bloque, sin ajustes automáticos físicos ni ERP.

## References

[1]: https://docs.stripe.com/api/checkout/sessions/create "Stripe Create a Checkout Session — expires_at"
[2]: https://docs.stripe.com/api/checkout/sessions/list "Stripe List all Checkout Sessions"
[3]: https://docs.stripe.com/api/checkout/sessions/expire "Stripe Expire a Checkout Session"
[4]: https://docs.stripe.com/api/idempotent_requests "Stripe Idempotent requests — cached v1 errors"
[5]: https://docs.stripe.com/webhooks "Stripe Webhooks — delivery, retries and ordering"
[6]: https://docs.stripe.com/checkout/fulfillment.md?ui=full-page "Stripe Fulfill orders — delayed methods and idempotency"
