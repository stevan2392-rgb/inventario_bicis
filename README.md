# Inventario de Bicis

Pequeña aplicación Flask con SQLite para gestionar inventario, compras, facturas y remisiones.

Requisitos

- Python 3.11+ recomendado

Instalación (PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Configura las variables copiando el ejemplo:

```powershell
Copy-Item .env.example .env
# Edita .env con tus credenciales SMTP/Twilio
```

Ejecutar la app (desarrollo)

```powershell
$env:FLASK_APP = 'app.py'
$env:FLASK_ENV = 'development'
python app.py
```

El servidor se ejecutará en http://127.0.0.1:5000 por defecto.

Notas

- La base de datos SQLite se crea automáticamente en el mismo directorio como `inventario.db`.
- Si faltan paquetes, instalarlos con `pip install <package>`.
- Para habilitar el envío automático de facturas y remisiones por correo configura las variables de entorno SMTP:
  - `SMTP_HOST`, `SMTP_PORT` (opcional, 587 por defecto), `SMTP_USERNAME`, `SMTP_PASSWORD`
  - `SMTP_FROM_EMAIL` (si se omite se usa `SMTP_USERNAME`) y `SMTP_FROM_NAME` (opcional)
  - `SMTP_USE_TLS` (`true` por defecto) o establece `false` para usar SSL directo.

- Para habilitar el envío automático por WhatsApp usa Twilio y define:
  - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` (solo el número con prefijo internacional, sin `whatsapp:`)
  - Opcional: `TWILIO_SEND_MEDIA=true` para intentar adjuntar el PDF como media (la URL debe ser pública) y `DEFAULT_COUNTRY_CODE` (por defecto 57).
Despliegue (producción)

1. Copia `.env.example` a `.env` y rellena las variables (usa `FLASK_DEBUG=false` en producción).
2. Instala dependencias: `python -m pip install -r requirements.txt`.
3. Expone la app con un servidor WSGI. En Linux usa `gunicorn app:app --bind 0.0.0.0:${PORT:-8000}`.
4. Configura el proxy/host para servir `static/` y define las variables SMTP/Twilio en el entorno del servidor.
5. Verifica los logs de Twilio/SMTP tras el despliegue para asegurar que los envíos funcionan.
