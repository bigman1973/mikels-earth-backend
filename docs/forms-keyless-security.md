# VIP/contacto · protección local sin claves externas

## Alcance y contrato

Solo `POST /api/vip/signup` y `POST /api/contact/send-message`. `website` y `_hp` son aliases de honeypot en ambos endpoints. Frontend utiliza `website` en VIP y conserva `_hp`/`_ts` en contacto; cualquier alias relleno responde 200 con `{ "success": true, "message": "Solicitud recibida correctamente." }`, antes de validar IP/DB, sin alta, correo, evento Klaviyo, consentimiento, log del contenido ni fila de auditoría. Los envíos VIP nuevos conservan 201 y contacto legítimo 200.

Las peticiones a estos endpoints no ejecutan el hook heredado de `create_all`/seeds/migraciones. La tabla nueva tiene metadata independiente y requiere migración SQL explícita: no la crea el arranque ni `db.create_all` de Flask.

## Límite real compartido

Máximo **3 peticiones por IP en cualquier ventana móvil de 3.600 segundos**, no ventanas de reloj. Hay bucket común `vip+contact` por IP y controles por IP+endpoint (`vip=3`, `contact=3`); el común evita multiplicar el límite alternando formularios. Peticiones con datos inválidos también consumen cuota; honeypots no consumen ni producen fila. La cuarta responde 429 y `Retry-After`; se admite el siguiente intento cuando el más antiguo cumple una hora exacta. Intentos denegados no generan filas que un atacante pueda multiplicar.

Los eventos contados viven en `form_attempts` dentro del **PostgreSQL existente**. El claim adquiere `pg_advisory_xact_lock`, lee la hora de PostgreSQL después del lock, cuenta ambos buckets y registra el intento/identidad en una transacción corta. La serialización es global entre workers; se libera antes de cualquier red/consentimiento. El índice parcial único de identidad VIP añade una segunda barrera contra doble flujo. Timeouts 1.500 ms para lock y 3.000 ms para sentencia. El lock global prima corrección y sencillez sobre throughput; probar su capacidad antes de producción.

**Sin fallback a memoria ni SQLite en producción.** DB caída, tabla ausente, dialecto no PG, secreto ausente o configuración IP inválida producen 503, no alta ni entrega. SQLite solo se autoriza con `TESTING=True`, URI en memoria y `FORM_SECURITY_SQLITE_FIXTURE=True`; no demuestra capacidad ni concurrencia PG real.

## Identidad y auditoría

La dirección para guardar/enviar se limita a trim y lowercase del original; espacios internos son inválidos. La identidad canónica se guarda únicamente en la tabla nueva. Solo `gmail.com` y `googlemail.com`: quitar puntos del localpart, unificar a `gmail.com`, quitar `+etiqueta` (alias del mismo buzón; coincide con el helper newsletter ya existente). No generalizar a Workspace personalizado u otros dominios. Un correo con puntos no es una prueba de bot.

VIP: una identidad nueva reserva `vip_claim`; repeticiones son 200 genérico y `duplicate=True`, sin otra alta/consentimiento/flujo/cupón. Se consulta `vip_signups` previo en **solo lectura**, incluida identidad Gmail canónica, sin backfill ni modificar emails, fechas, IPs, estados o consentimiento históricos. Contacto: la duplicación es solo marca audit; mensajes distintos legítimos se envían mientras el límite IP los admita. El contador de duplicados se obtiene por `GROUP BY endpoint, duplicate, outcome` separado de las altas únicas; no existe endpoint público de recuento ni modificación de perfiles.

El claim VIP se conserva también si falla entrega o el proceso se interrumpe: evita reenvíos y dobles flujos. **No hay reintento automático después de fallos/pending**: reconciliación local/manual revisada pendiente, sin borrar claims ni tocar consentimiento previo. Una respuesta genérica de duplicado no promete entrega efectiva.

El helper Klaviyo VIP anterior hacía PATCH de fecha/IP/fuente de consentimiento en perfiles existentes. Se elimina ese PATCH: un perfil existente bloquea la integración VIP (audit local `vip_failed`, respuesta 502), sin suscribir, suprimir, dar de baja ni emitir el evento. Se exige lookup 200 con lista vacía antes de crear un perfil nuevo. **Pendiente de decisión explícita**: cómo añadir un perfil existente a VIP respetando baja/supresión/consentimiento, sin alterar evidencia previa. No se inventa una política. Los helpers newsletter/transaccionales de otros endpoints no se modifican.

La confirmación transaccional de contacto mantiene el nombre en las propiedades del evento, pero ya no envía `first_name` para sobrescribir un perfil existente. No se añade suscripción comercial por escribir un mensaje.

## Teléfono

No se crea campo obligatorio VIP. Ausente, null o vacío es válido. Se rechaza exactamente diez dígitos sin prefijo explícito `+`/`00`, ignorando espacios, guiones, puntos y paréntesis; 400 `invalid_phone`. Nueve dígitos españoles siguen admitidos; no se exige internacionalizar todos los teléfonos ni se altera el teléfono histórico.

## Hash/IP y gate antes de desplegar

HMAC-SHA256 deriva la clave de la `SECRET_KEY` de Flask ya configurada con prefijo estable `mikels:vip-contact:ip:v1:`. No hay clave externa nueva ni lectura/impresión de secretos en esta tarea. Misma `SECRET_KEY` en todos los workers, estable durante la ventana: su rotación cambia buckets y puede reiniciar la cuota, por lo que debe coordinarse. Tabla audit almacena hash IP; una alta VIP nueva almacena fecha UTC del claim e IP verificada actual por la evidencia existente.

Configuración normal: `FORM_CLIENT_IP_MODE=direct` usa solo `REMOTE_ADDR` y **ignora X-Forwarded-For**. Esto es correcto solo con acceso directo o un peer que sea de verdad el visitante. En Railway podría ser el proxy y compartiría quota entre visitantes: **no desplegar hasta verificarlo**.

Modo posterior aprobado: `FORM_CLIENT_IP_MODE=verified_header`, `FORM_TRUSTED_PROXY_CIDRS=<CIDR reales comprobados>`, `FORM_VERIFIED_IP_HEADER=<cabecera única comprobada>`. No se asume nombre de cabecera ni CIDR Railway; se rechazan redes universales, XFF/Forwarded y cadenas IP. Requiere comprobar peer real, que ingress sobrescribe la cabecera del usuario y que no existe ruta directa que la eluda. Peer no confiable/header ausente/valor múltiple/configuración incompleta: 503. Los rangos `192.0.2.0/24` de tests son documentación, **no configuración Railway**.

## Migración, aprobación y prueba

`migrations/add_form_attempts.sql` es aditiva, una tabla con índices, **no ejecutada**. Tras aprobación, aplicar en staging aislado con PG real; confirmar esquema, rollback, relojes, timeouts, aislamiento READ COMMITTED, índice único y compartir SECRET_KEY. Lanzar cuatro o más peticiones simultáneas de mismo IP entre al menos dos workers y endpoints alternos: exactamente tres claims; repetir misma identidad desde IP distintos: como máximo un VIP. Comprobar caída DB/tabla faltante 503, frontera de una hora, perfil existente sin mutations y mensajes contacto distintos. Verificar IP/proxy antes de aprobar despliegue. No conectar a PG real ni producción dentro de esta implementación local.

Rollback: revertir código tras aprobación, conservar tabla aditiva y claims como evidencia. Nunca borrar tabla/claims para desbloquear identidades; retención/purga y acceso a emails audit requieren decisión expresa. No se introduce tarea automática, Redis, servicio nuevo, campaña ni petición real de prueba.

## Turnstile futuro, no activo

No hay validación ni llamada Turnstile en este cambio. Para ese día: widget **Cloudflare Turnstile** en la cuenta Cloudflare del negocio, dominios frontend aprobados, site key pública en variables del frontend y secret key privada solo en variables protegidas Railway del backend; definir/verificar hostname y action por endpoint. El proxy sigue requiriendo la validación anterior. No basta pegar claves: implementación/revisión/activación posterior autorizadas.

**Almacenamiento técnico compartido**, en una frase: la misma base de datos que ya usa la tienda guarda un pequeño registro de intentos para que todos los servidores cuenten juntos los envíos y no dejen pasar el cuarto.
