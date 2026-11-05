# Formularios seguros: candidata 9.1 del 5 de noviembre de 2026

La candidata contiene únicamente honeypot, teléfono opcional y la infraestructura mínima para que un descarte no inicialice tablas/semillas. Se mantiene intacto el servicio Klaviyo del hotfix `6fe7fa553a4fe634521cca044a77e9da7dabe89f`: ningún PATCH ni Subscribe Profiles para perfiles VIP existentes, ningún restablecimiento de consentimientos o supresiones. No activa BF26, cupones ni cambia catálogo.

## Límites activos y desactivados

El JSON de VIP/contacto está limitado a 16.384 bytes y debe ser un objeto. Si `website` o `_hp` contienen algo distinto de `null`/cadena vacía, se responde HTTP 200 con `{"success":true,"message":"Solicitud recibida correctamente."}`. No hay fila de consentimiento, perfil, evento, correo, IP observada, log ni intento persistido. Los campos `false`, cero, objetos y arrays se consideran llenos. Se mantiene la heurística de tiempo y mensajes del contacto para solicitudes no atrapadas.

El teléfono sigue siendo opcional. `null`, vacío y nueve dígitos españoles pasan. Exactamente diez dígitos sin prefijo `+` o `00`, incluso con espacios/paréntesis/puntos/guiones, se rechazan HTTP 400 `invalid_phone` antes de consentimiento/proveedores. Esta regla acotada no promete validar todos los números internacionales ni comprobar su existencia. No se añade teléfono visible al VIP.

**Todos los límites IP de la candidata están deshabilitados.** Se retiraron las reglas en memoria de VIP, contacto, newsletter, HORECA, avísame y reseñas. No hay fallback compartido por `unknown`, cabecera ausente o IP de proxy; ni XFF/socket ni configuración de entorno pueden activar limitación IP. `src/services/form_security.py` expone `IP_RATE_LIMITS_ENABLED = False`.

Railway documenta `X-Real-IP` como IP remota y su soporte afirma sobrescritura en el edge público. No garantiza rango estable del peer ni un número fijo de saltos XFF; su soporte distingue acceso privado/TCP fuera del edge. No hay certificación técnica de que todas las vías al listener de este deployment atraviesen el edge. La existencia del servicio/dominios es insuficiente para esa certificación. Por autorización del usuario se elige desactivación, no limitar juntos a los clientes ni atribuir al proxy la identidad del cliente.[1] [2] [3]

## Evidencia de consentimiento y esquema

Las fechas UTC se conservan. Una `X-Real-IP` sintácticamente válida se retiene exclusivamente en la nueva fila `vip_signups.source_ip` como **observación no verificada**, con `source='vip_black_friday_2026:ip_unverified:x_real_ip'`. Una cabecera ausente, múltiple o inválida deja `source_ip='unverified'` y `source='vip_black_friday_2026:ip_unverified:unavailable'`. Nunca se usa XFF ni `remote_addr` como alternativa. Al servicio Klaviyo se pasa el marcador `unverified`, no una observación incierta como afirmación de IP real. No se modifican filas históricas.

No hay nuevas columnas ni migraciones requeridas en esta candidata. Se utilizan las columnas existentes `vip_signups.source_ip VARCHAR(45) NOT NULL`, `source VARCHAR(100) NOT NULL` y `consented_at TIMESTAMP`. La tabla VIP debe existir antes de atender formularios; éstos ya no disparan el bootstrap heredado que también toca catálogo. El padre coordina cualquier preparación de esquema si faltara.

## Módulo preparado PostgreSQL: probado, NO conectado

Sólo se revisó en lectura la implementación preparada de `c5802a9`/`4cf5350`, sin fusionar sus cambios BF ni servicios Klaviyo. La migración `migrations/add_form_attempts.sql` y el módulo `claim_attempt` de esa rama no se incorporan ni se ejecutan en la candidata. Si otro integrador los lleva, necesitan aprobación separada y el contador IP debe seguir sin uso mientras no se certifique ingress.

Ese esquema preparado necesita `form_attempts`: `id SERIAL`, `endpoint VARCHAR(10)` con CHECK VIP/contacto, `ip_hash VARCHAR(64) NOT NULL`, `attempted_at TIMESTAMP NOT NULL`, `rate_consumed BOOLEAN NOT NULL`, `email VARCHAR(255)`, `canonical_email VARCHAR(255)`, `duplicate BOOLEAN`, `vip_claim BOOLEAN`, `outcome VARCHAR(30)`. Incluye índices `(ip_hash, attempted_at)`, `(endpoint, canonical_email)` y UNIQUE parcial de `canonical_email WHERE vip_claim = TRUE`, más CHECK de consistencia de reserva VIP. No está registrado en `db.metadata` y no debe aparecer por `db.create_all()`.

En ese preparado —no en esta candidata— el límite es tres intentos consumidos por IP en ventana móvil de 3.600 segundos, compartida entre VIP/contacto; los inválidos consumen y honeypots no. Advisory lock transaccional `2026110403`, READ COMMITTED explícito y reloj DB tras adquirir el lock. `lock_timeout=1500ms`, `statement_timeout=3000ms`. No hay purga automática ni política de retención nueva. La deduplicación usa alias Gmail exclusivamente; conserva buzón de entrega y lee en sólo lectura consentimientos anteriores. No se presentan esos límites como activos.

Se probó ese módulo con PostgreSQL 16.15 **local desechable**, socket UNIX y listener TCP desactivado, con 12 procesos `spawn`/barriers. Tres rondas produjeron 3 reservas y 9 rechazos cada una. Doce identidades alias desde IPs sintéticas distintas produjeron una reserva y once duplicados. Se probó actualización final repetida, replay de reserva fallida, migración tres veces y preservación exacta de fila histórica. Son pruebas locales, no producción.

## Integración del embudo y frontend

El hook puro es `honeypot_filled(data)` y el resultado fijo `HONEYPOT_OUTCOME='honeypot_discarded'` en `src/services/form_security.py`. Cada ruta tiene una rama temprana antes de IP/teléfono/DB/proveedores. Otro agente implementa los contadores; cualquier integración debe conservar esa frontera y no guardar payload ni crear consentimiento. Un 200 sin `signup_id` no es conversión. Frontend añade `website` invisible en VIP y reutiliza `_hp`/company en contacto; el éxito neutro va a `/vip/gracias` sin marcador de signup. Diseño, casilla, privacidad, noindex, reloj y textos aprobados no cambian.

## Verificación local

Backend: 240 tests completos y 64 focalizados con red bloqueada, Klaviyo/Stripe mockeados. Frontend: 143 tests completos, 8 focalizados y build offline con red física bloqueada. La generación SEO normal intentó llamar a la API y fue bloqueada; el build válido posterior usa explícitamente el catálogo frontend versionado y un artículo sintético como mock, no datos certificados de producción. No hubo push, despliegue, diagnóstico remoto ni migración de producción.

## References

[1]: https://docs.railway.com/networking/public-networking/specs-and-limits "Railway Public Networking Specs and Limits"
[2]: https://station.railway.com/questions/security-clarification-x-forwarded-for-fb0bba28 "Railway clarification of public edge overwrite and alternate network paths"
[3]: https://station.railway.com/questions/security-critical-questions-on-edge-prox-8fddd775 "Railway employee response on overwritten headers and variable hops"
