# WhatsApp (cuando Meta apruebe el negocio)

WhatsApp entra como **otra bandeja de Chatwoot** con el mismo Agent Bot que la bandeja web:
el código del bot no cambia. Lo que cambia es configuración (Meta, Chatwoot y `.env`).

Requisitos: Chatwoot y el bot andando con la bandeja web ([docs/chatwoot.md](chatwoot.md)), el
negocio verificado por Meta y el número de WhatsApp dado de alta en la cuenta de WhatsApp
Business (WABA). Ese número **no puede** estar usándose a la vez en la app de WhatsApp del
celular.

## 1. Datos de Meta

En https://developers.facebook.com → tu app → **WhatsApp → Configuración de la API**
(*API Setup*):

- **Identificador del número de teléfono** (*Phone number ID*) → `WHATSAPP_PHONE_NUMBER_ID`
- **Identificador de la cuenta de WhatsApp Business** (*WhatsApp Business Account ID*) →
  `WHATSAPP_WABA_ID`

Los dos van en `.env` solo como referencia (los usa Chatwoot, no nuestro código). No son
secretos, pero tampoco se suben al repo.

## 2. Token permanente (usuario del sistema)

El token temporal de la página de la API vence en 24 h: hace falta uno **permanente**.

1. https://business.facebook.com → **Configuración del negocio** → *Usuarios → Usuarios del
   sistema* → **Agregar**: nombre `chatwoot`, rol **Administrador**.
2. Con el usuario elegido → **Asignar activos**:
   - *Apps* → tu app → **Control total**.
   - *Cuentas de WhatsApp* → tu WABA → **Control total**.
3. **Generar token** → app: la tuya → vencimiento: **Nunca** → permisos:
   `whatsapp_business_messaging` y `whatsapp_business_management`.
4. Copialo en ese momento (Meta no lo vuelve a mostrar). **No va en `.env` ni en el repo**: se
   pega solo en Chatwoot (paso 4). Si se pierde, se genera otro.

## 3. Túnel con cloudflared (HTTPS público hacia Chatwoot)

Meta solo manda webhooks a una URL **pública con HTTPS**. En desarrollo, Chatwoot está en
`http://localhost:3000`: un túnel de Cloudflare lo publica sin abrir puertos.

Instalar en Windows:

```powershell
winget install --id Cloudflare.cloudflared
```

**Prueba rápida** (URL al azar, cambia cada vez que se reinicia):

```powershell
cloudflared tunnel --url http://localhost:3000
```

Muestra algo como `https://palabras-al-azar.trycloudflare.com`. Sirve para probar, pero cada
reinicio obliga a cambiar la URL en Meta.

**Túnel fijo** (recomendado; requiere un dominio en Cloudflare):

```powershell
cloudflared tunnel login
cloudflared tunnel create chatwoot
cloudflared tunnel route dns chatwoot chatwoot.tu-dominio.com.ar
cloudflared tunnel run --url http://localhost:3000 chatwoot
```

Lo que se publica es **Chatwoot** (puerto 3000), no nuestra api: Meta le habla a Chatwoot y
Chatwoot al bot por el Agent Bot.

Conviene poner `CHATWOOT_FRONTEND_URL=https://chatwoot.tu-dominio.com.ar` en `.env` y reiniciar
Chatwoot (`docker compose --profile chatwoot up -d`): es la URL que Chatwoot usa para armar el
webhook y los links. Ojo: todo lo publicado por el túnel queda accesible desde internet,
incluido el login del panel; usá contraseñas fuertes.

## 4. Bandeja "WhatsApp Cloud" en Chatwoot

1. *Configuración → Bandejas de entrada → Agregar bandeja → WhatsApp*.
2. Proveedor: **WhatsApp Cloud** (configuración manual).
3. Completá:
   - Nombre de la bandeja: `WhatsApp`
   - Número de teléfono: el de la línea, con `+` y código de país (`+549351...`)
   - ID de número de teléfono: `WHATSAPP_PHONE_NUMBER_ID`
   - ID de cuenta de negocio: `WHATSAPP_WABA_ID`
   - Clave de API: el **token permanente** del paso 2
4. Crear → agregá los agentes. Al terminar, en la pestaña **Configuración** de la bandeja
   aparecen la **URL del webhook** (`<CHATWOOT_FRONTEND_URL>/webhooks/whatsapp/+549...`) y el
   **token de verificación del webhook**.
5. Anotá el **ID de la bandeja** (en la URL: `.../settings/inboxes/2` → `2`).

## 5. Webhook de Meta → Chatwoot

En la app de Meta → **WhatsApp → Configuración** (*Configuration*) → **Webhook → Editar**:

- URL de devolución de llamada: `https://chatwoot.tu-dominio.com.ar/webhooks/whatsapp/+549...`
  (la del paso 4, con el dominio del túnel)
- Token de verificación: el del paso 4
- **Verificar y guardar** (Chatwoot tiene que estar levantado y el túnel andando).
- En **Campos del webhook**, suscribite a **`messages`**.

## 6. Mismo Agent Bot

*Configuración → Bandejas de entrada → WhatsApp → pestaña Bot* → elegí el mismo bot
(`Asistente`) → **Actualizar**. No hace falta tocar el bot ni el código.

## 7. `.env`

```ini
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_WABA_ID=...
# Solo la bandeja de WhatsApp: ahí el teléfono lo pone Meta y sí identifica.
CHATWOOT_TRUSTED_PHONE_INBOX_IDS=2
```

Sacá de `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` la bandeja web si la habías agregado para probar.
Reiniciá la api: `docker compose up -d api`.

## 8. Probar

Desde un celular, mandá un WhatsApp al número. Tiene que aparecer en Chatwoot como Pendiente y
el bot contestar. Si el número del celular está cargado en ConsorPlus como propietario de un
edificio piloto, el bot lo identifica sin pedir verificación.

Si no llega nada: `docker compose logs -f chatwoot-rails chatwoot-sidekiq` (¿llega el webhook
de Meta?) y `docker compose logs -f api` (¿llega el del Agent Bot?).

## Pasar a producción

- `APP_ENV=production` (apaga `/dev/chat`).
- **Sin** `SAFE_FETCH_ALLOW_PRIVATE_NETWORK` en Chatwoot: la URL del Agent Bot pasa a ser la
  URL pública con HTTPS de nuestra api (`https://.../webhooks/chatwoot`), no
  `http://host.docker.internal:8000`.
- `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` solo con la bandeja de WhatsApp.
- Ventana de 24 h de WhatsApp: el bot solo responde mensajes entrantes, así que siempre está
  dentro de la ventana. Para escribirle primero a alguien hacen falta plantillas aprobadas
  por Meta (no está implementado).
