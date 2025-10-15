# Mikel's Earth - Backend API

API backend para el e-commerce de Mikel's Earth, construida con Flask y Stripe para procesamiento de pagos.

## 🌟 Características

- ✅ API RESTful con Flask
- ✅ Integración completa con Stripe
- ✅ Procesamiento de pagos únicos
- ✅ Gestión de suscripciones recurrentes
- ✅ Webhooks de Stripe para eventos
- ✅ Base de datos SQLite (desarrollo) / PostgreSQL (producción)
- ✅ CORS configurado para frontend
- ✅ Modelos de datos para pedidos y suscripciones

## 🛠️ Stack Tecnológico

- **Python** 3.11
- **Flask** 3.1.1
- **SQLAlchemy** - ORM
- **Stripe** 13.0.1 - Procesamiento de pagos
- **Flask-CORS** - Manejo de CORS
- **python-dotenv** - Variables de entorno

## 📋 Requisitos Previos

- Python 3.11 o superior
- pip
- Cuenta de Stripe
- PostgreSQL (para producción)

## 🚀 Instalación

1. Clona el repositorio:
```bash
git clone https://github.com/tu-usuario/mikels-earth-backend.git
cd mikels-earth-backend
```

2. Crea un entorno virtual:
```bash
python3.11 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
```

3. Instala las dependencias:
```bash
pip install -r requirements.txt
```

4. Copia el archivo de variables de entorno:
```bash
cp .env.example .env
```

5. Configura las variables de entorno en `.env`:
```env
STRIPE_SECRET_KEY=sk_test_tu_clave_secreta
STRIPE_WEBHOOK_SECRET=whsec_tu_webhook_secret
DATABASE_URL=sqlite:///src/database/app.db
FRONTEND_URL=http://localhost:5173
```

6. Inicializa la base de datos:
```bash
python3.11 -c "from src.main import db, app; app.app_context().push(); db.create_all()"
```

7. Inicia el servidor:
```bash
python3.11 src/main.py
```

El servidor estará disponible en `http://localhost:5001`

## 📡 Endpoints API

### Health Check
```
GET /health
```
Verifica que el servidor esté funcionando.

### Stripe - Crear Sesión de Checkout (Compra Única)
```
POST /api/stripe/create-checkout-session
Content-Type: application/json

{
  "items": [
    {
      "id": 1,
      "name": "Paraguayo en Almíbar",
      "price": 8.50,
      "quantity": 2
    }
  ],
  "customer_email": "cliente@example.com",
  "customer_name": "Juan Pérez",
  "shipping_address": {
    "address": "Calle Mayor 123",
    "city": "Madrid",
    "postal_code": "28001",
    "country": "España"
  }
}
```

### Stripe - Crear Sesión de Checkout (Suscripción)
```
POST /api/stripe/create-subscription-checkout
Content-Type: application/json

{
  "product": {
    "id": 1,
    "name": "Paraguayo en Almíbar",
    "price": 7.50,
    "frequency": "weekly",
    "quantity": 1
  },
  "customer_email": "cliente@example.com",
  "customer_name": "Juan Pérez",
  "shipping_address": {
    "address": "Calle Mayor 123",
    "city": "Madrid",
    "postal_code": "28001",
    "country": "España"
  }
}
```

### Stripe - Webhook
```
POST /api/stripe/webhook
```
Recibe eventos de Stripe (checkout completado, suscripción creada, etc.)

### Stripe - Estado de Sesión
```
GET /api/stripe/session-status/<session_id>
```
Obtiene el estado de una sesión de checkout.

## 🗄️ Modelos de Base de Datos

### Order (Pedidos)
- `id`: ID único
- `order_number`: Número de pedido
- `customer_email`, `customer_name`, `customer_phone`: Datos del cliente
- `shipping_address`, `shipping_city`, `shipping_postal_code`, `shipping_country`: Dirección
- `items`: JSON con productos
- `subtotal`, `shipping_cost`, `total`: Montos
- `stripe_payment_intent_id`, `stripe_checkout_session_id`: IDs de Stripe
- `payment_status`, `order_status`: Estados
- `created_at`, `updated_at`, `paid_at`: Timestamps

### Subscription (Suscripciones)
- `id`: ID único
- `subscription_number`: Número de suscripción
- `customer_email`, `customer_name`: Datos del cliente
- `product_id`, `product_name`, `product_slug`: Producto
- `quantity`, `unit_price`, `frequency`: Detalles
- `stripe_subscription_id`, `stripe_customer_id`, `stripe_price_id`: IDs de Stripe
- `status`: Estado de la suscripción
- `created_at`, `updated_at`, `next_billing_date`, `cancelled_at`: Timestamps

## 🔐 Configuración de Stripe

### 1. Obtener Claves API

1. Ve a [Stripe Dashboard](https://dashboard.stripe.com)
2. Desarrolladores → Claves API
3. Copia las claves de prueba o producción

### 2. Configurar Webhooks

1. En Stripe Dashboard: Desarrolladores → Webhooks
2. Añade endpoint: `https://tu-dominio.com/api/stripe/webhook`
3. Selecciona eventos:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
4. Copia el webhook secret

### 3. Probar Webhooks Localmente

Usa Stripe CLI:
```bash
stripe listen --forward-to localhost:5001/api/stripe/webhook
```

## 🌐 Despliegue en Railway

### Opción 1: Desde GitHub

1. Ve a [railway.app](https://railway.app)
2. Crea un nuevo proyecto
3. Conecta tu repositorio de GitHub
4. Railway detectará automáticamente que es una app Flask
5. Añade las variables de entorno en Settings
6. Despliega

### Opción 2: Desde Railway CLI

1. Instala Railway CLI:
```bash
npm install -g @railway/cli
```

2. Inicia sesión:
```bash
railway login
```

3. Inicializa el proyecto:
```bash
railway init
```

4. Despliega:
```bash
railway up
```

### Variables de Entorno en Railway

Configura en el dashboard de Railway:
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `DATABASE_URL` (Railway proporciona PostgreSQL)
- `FRONTEND_URL`
- `FLASK_ENV=production`
- `FLASK_DEBUG=False`

## 🌐 Despliegue en Render

1. Ve a [render.com](https://render.com)
2. Crea un nuevo Web Service
3. Conecta tu repositorio
4. Configura:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn src.main:app`
5. Añade variables de entorno
6. Crea una base de datos PostgreSQL
7. Despliega

### Instalar Gunicorn

Añade a `requirements.txt`:
```
gunicorn==21.2.0
```

## 📁 Estructura del Proyecto

```
mikels_backend/
├── src/
│   ├── models/
│   │   ├── user.py           # Modelo de usuario
│   │   └── order.py          # Modelos de pedidos y suscripciones
│   ├── routes/
│   │   ├── user.py           # Rutas de usuarios
│   │   └── stripe_routes.py  # Rutas de Stripe
│   ├── database/
│   │   └── app.db            # Base de datos SQLite (gitignored)
│   └── main.py               # Aplicación Flask principal
├── venv/                     # Entorno virtual (gitignored)
├── .env                      # Variables de entorno (gitignored)
├── .env.example              # Ejemplo de variables
├── .gitignore
├── requirements.txt
└── README.md
```

## 🧪 Testing

Para ejecutar tests (cuando estén implementados):
```bash
pytest
```

## 📝 Tareas Pendientes

- [ ] Implementar autenticación JWT
- [ ] Añadir tests unitarios y de integración
- [ ] Implementar rate limiting
- [ ] Añadir logging estructurado
- [ ] Migrar a PostgreSQL para producción
- [ ] Implementar caché con Redis
- [ ] Añadir documentación de API con Swagger
- [ ] Implementar sistema de notificaciones por email

## 🐛 Solución de Problemas

### Error: "No module named 'src'"

Asegúrate de estar en el directorio raíz del proyecto y que el entorno virtual esté activado.

### Error: "Database not found"

Inicializa la base de datos:
```bash
python3.11 -c "from src.main import db, app; app.app_context().push(); db.create_all()"
```

### Error: "Stripe API key invalid"

Verifica que las claves en `.env` sean correctas y estén en el formato correcto.

### Webhooks no funcionan

1. Verifica que el webhook secret sea correcto
2. Usa Stripe CLI para probar localmente
3. Comprueba los logs de Stripe Dashboard

## 🤝 Contribuir

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit tus cambios (`git commit -m 'Add some AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abre un Pull Request

## 📄 Licencia

Este proyecto es privado y pertenece a Mikel's Earth.

## 📧 Contacto

**Mikel's Earth**
- Web: https://mikels.es
- Email: info@mikels.es

## 🔗 Enlaces Relacionados

- **Frontend**: [mikels-earth-frontend](https://github.com/tu-usuario/mikels-earth-frontend)
- **Documentación de Stripe**: https://stripe.com/docs
- **Flask Documentation**: https://flask.palletsprojects.com/

---

**Desarrollado con ❤️ para Mikel's Earth**

