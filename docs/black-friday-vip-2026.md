# Alta VIP Black Friday 2026

El endpoint público `POST /api/vip/signup` recibe `email`, `first_name` opcional y `marketing_consent: true`. Persiste una fila de auditoría con fecha/hora UTC, IP de origen, consentimiento y el resultado de cada operación de Klaviyo. No crea cupones ni usa el flujo de bienvenida general.

La integración busca la lista `Black Friday VIP 2026` en Klaviyo y, si no existe, la crea con alta simple. Después suscribe el perfil a esa lista y envía `Mikels VIP Signup` con la fecha de consentimiento. Si cualquiera de las dos operaciones falla, el endpoint devuelve error y la landing no redirige a la página de gracias; el fallo queda marcado en la fila de auditoría para revisión.

La clave privada existente de Klaviyo debe disponer de `lists:read`, `lists:write`, `profiles:write`, `subscriptions:write` y `events:write`. No se añade ningún secreto nuevo al repositorio ni se muestra en la interfaz.
