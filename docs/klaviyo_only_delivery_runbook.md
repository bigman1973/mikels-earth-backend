# Migración de correo a Klaviyo · Runbook de despliegue

**Estado:** código preparado y probado; no desplegar hasta completar la configuración de Flows y la prueba de entrega.

## Alcance

Brevo queda eliminado del backend:

- no se llama a la API de Brevo;
- no se usa `BREVO_API_KEY` ni `BREVO_WEBHOOK_KEY`;
- se retiran sus módulos de envío y contacto;
- se retira `POST /api/blog/webhook/brevo`: ya no se podrán crear ni actualizar posts enviando un email a Brevo;
- se elimina el endpoint de diagnóstico de Brevo, que mostraba una parte de la clave de API.

Las campañas ya existentes en Klaviyo **no se borran ni se modifican**. Los correos automáticos se deben ejecutar mediante **Flows disparados por eventos**, no campañas manuales.

## Registro y reintentos

Cada evento de Klaviyo se inserta primero en `klaviyo_deliveries` y queda con:

- evento, destinatario e identificador idempotente;
- estado: `accepted`, `retrying` o `failed`;
- respuesta HTTP y motivo de fallo;
- número de intentos y siguiente reintento;
- resultado de la alerta para eventos críticos.

Un `202 Accepted` de Klaviyo confirma que Klaviyo recibió el evento; **no prueba la entrega del correo**. La entrega se comprueba en la analítica del Flow.

Los reintentos son a los 5 min, 30 min, 2 h y 8 h; después del quinto intento el registro queda en `failed`, sin ocultarse.

## Servicio de reintentos en Railway

Crear un servicio Cron independiente a partir del mismo repositorio y del mismo commit del backend.

| Ajuste | Valor |
|---|---|
| Comando de inicio | `python -m src.jobs.retry_klaviyo_deliveries` |
| Planificación UTC | `*/5 * * * *` |
| Base de datos | misma `DATABASE_URL` de PostgreSQL de producción |
| Clave de Klaviyo | misma `KLAVIYO_API_KEY` del backend |
| Política de reinicio | ninguna; el proceso debe terminar tras cada ejecución |

El Cron de Railway ejecuta servicios de vida corta y requiere una separación mínima de cinco minutos. Si una ejecución no termina, Railway omite la siguiente: el script libera la sesión de base de datos antes de finalizar.

## Alerta independiente ante fallo crítico

Los eventos críticos son: confirmación de pedido, bienvenida newsletter, confirmación de contacto, confirmación de visita, confirmación de solicitud de producto, disponibilidad de producto, solicitud de reseña y confirmación HORECA.

Configurar antes del despliegue una URL de webhook operativo en `DELIVERY_ALERT_WEBHOOK_URL` (por ejemplo, un canal privado de Teams o Slack). Este canal debe ser independiente de Klaviyo: si Klaviyo falla, Klaviyo no puede ser el mecanismo de alerta.

Si no existe la variable, el fallo sigue persistido y se registra explícitamente como `alert_status=not_configured`; no hay una alerta fingida ni un fallback silencioso.

## Matriz de Flows en Klaviyo

Las plantillas existentes se pueden reutilizar. Renombrar todas las referencias de marca a **Mikel's Fruit**.

| Evento recibido | Flow / finalidad | Clase | Ajuste obligatorio |
|---|---|---|---|
| `Mikels Newsletter Welcome` | Bienvenida Mikel's Fruit | Marketing consentido | Insertar `{{ event.CouponCode }}`; nombre de Flow y email con la marca nueva. |
| `Mikels Placed Order` | Confirmación de pedido | Transaccional | Sin filtro de consentimiento marketing; activar envío. |
| `Mikels Contact Confirmation` | Confirmación de contacto | Transaccional | Sin filtro de consentimiento marketing; activar envío. |
| `Mikels Workshop Visit Confirmation` | Confirmación de visita | Transaccional | Sin filtro de consentimiento marketing; activar envío. |
| `Mikels Product Notification Confirmation` | Confirmación de aviso de producto | Transaccional | Sin filtro de consentimiento marketing; activar envío. |
| `Product Back In Stock` | Producto disponible | Transaccional solicitado | Sin filtro de consentimiento marketing; activar envío. |
| `Mikels HORECA Confirmation` | Confirmación HORECA | Transaccional | Crear Flow y activar envío. |
| `Mikels Review Request` | Solicitud de opinión | Marketing / relación postventa | Añadir espera de **10 días** dentro del Flow; activar envío. |
| `Started Checkout` | Carrito abandonado | Marketing consentido | Mantener un solo Flow y este nombre exacto de evento. |
| `Mikels Post Purchase` | Postcompra | Marketing consentido | Mantener las condiciones comerciales aprobadas. |
| eventos `... Internal` y `Mikels Blog Administration` | Avisos operativos a `info@mikels.es` | Operativo | Crear/validar Flows internos si se desean notificaciones por correo. |

No enviar clientes reales ni los 19 cupones pendientes hasta que la prueba de bienvenida alcance `Delivered` en Klaviyo y muestre el cupón correcto.

## Secuencia de despliegue segura

1. Crear o corregir los Flows anteriores y poner los seis de confirmación en modo transaccional.
2. Configurar el webhook de alerta y el servicio Cron de reintentos.
3. Desplegar el backend.
4. Con una dirección nueva de prueba, generar **un único** evento `Mikels Newsletter Welcome` y comprobar: evento aceptado, ejecución del Flow y correo entregado con el cupón correcto y marca Mikel's Fruit.
5. Probar una confirmación transaccional con una dirección de prueba sin consentimiento de marketing: debe llegar por el Flow correspondiente.
6. Consultar `klaviyo_deliveries`: los eventos deben quedar `accepted` y sin errores.
7. Solo tras esas comprobaciones, seleccionar los 19 registros pendientes reales, reenviar su mismo cupón mediante el evento de bienvenida y verificar la entrega antes de marcar cada uno como resuelto.
8. Eliminar las variables de Brevo de Railway después de confirmar el despliegue en verde. No modificarlas antes: este cambio no las usa, pero su borrado debe quedar como acción de configuración trazable.

## Reversión

Si falla el backend tras el despliegue, revertir el commit de migración y redeplegar. No reintroducir una clave ni un fallback de Brevo para ocultar un fallo: el ledger debe mostrarlo y el Flow de Klaviyo se corrige antes de volver a probar.
