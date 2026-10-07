# WhatsApp: Meta → nuestra api → bot

El bot habla **directo** con la WhatsApp Cloud API: Meta le manda los webhooks a nuestra api
(`/webhooks/whatsapp`) y el bot responde por la Graph API. Las conversaciones y los mensajes
quedan en nuestra base (`wa_contacts`, `wa_conversations`, `wa_messages`) y las operadoras las
atienden en el panel (**/admin → Conversaciones**).

```
celular → Meta → túnel (cloudflared-wa) → webhook-proxy (solo /webhooks/whatsapp) → api
celular ← Meta ←────────────── Graph API (texto, botones, listas, plantillas) ←──── api
```

En internet se publica **solo** `/webhooks/whatsapp`. El panel nunca queda público.

Todos los pasos en Meta se hacen **a mano**. Los tokens y claves van en `.env` y nunca se suben
al repo.

## 0. Sin celular: el chat de prueba

Para probar el bot y la bandeja no hace falta nada de lo que sigue. Con `APP_ENV=development`,
el panel tiene **/admin → Chat de prueba**:

1. Elegí un teléfono: uno inventado (por defecto `+5493515550000`, alguien que el bot no
   conoce) o el de una persona de la base (buscala por nombre, unidad o edificio): el bot la
   reconoce como si le escribiera desde su celular, con su deuda y sus reservas.
2. Escribí como esa persona. El mensaje entra como si lo hubiera mandado Meta y lo procesa el
   mismo bot que atiende WhatsApp: los botones y las listas se pueden tocar.
3. La conversación aparece en **Conversaciones** como una más. Con dos usuarias (dos
   navegadores, o una ventana privada) se prueba todo: una escribe en el chat de prueba y la
   otra **toma control**, responde, la **devuelve al bot** o la resuelve. Lo que responde la
   operadora aparece en el chat de prueba.
4. **Empezar de nuevo** borra los mensajes de esa conversación de prueba.

Lo que se le manda a un teléfono del chat de prueba (del bot o de una operadora) se guarda
igual que siempre pero **nunca sale a WhatsApp**: el contacto queda marcado como simulado
(`wa_contacts.simulated`). Eso vale aunque el teléfono sea el de un propietario real. Sin
`WHATSAPP_ACCESS_TOKEN` funciona igual; lo que no se puede es responderle a un contacto real.

En producción (`APP_ENV=production`) el chat de prueba no existe (404) y nada se simula.

## 1. La app de Meta y el número de prueba

1. https://developers.facebook.com → **Mis apps** → crear una app de tipo **Negocios** y
   agregarle el producto **WhatsApp**. Meta crea una cuenta de WhatsApp Business de prueba con
   un **número de prueba** (`+1 555...`).
2. Tu app → **WhatsApp → Configuración de la API**:
   - Anotá en `.env` el **Identificador del número de teléfono** (`WHATSAPP_PHONE_NUMBER_ID`) y
     el **Identificador de la cuenta de WhatsApp Business** (`WHATSAPP_WABA_ID`).
   - En **Para**, agregá tu celular a la lista de destinatarios (Meta manda un código por
     WhatsApp para confirmarlo). El número de prueba **solo** puede escribirles a los números
     de esa lista (hasta 5).
   - Mandá el mensaje `hello_world`: si te llega, el número de prueba y tu celular andan.

Con números argentinos hay un detalle con la lista de destinatarios: ver
[Números argentinos con el número de prueba](#números-argentinos-con-el-número-de-prueba).

## 2. Token de acceso (`WHATSAPP_ACCESS_TOKEN`)

La página **Configuración de la API** muestra un token **temporal** que vence en 24 h: sirve
para una primera prueba. Para seguir, uno **permanente** de un usuario del sistema:

1. https://business.facebook.com → **Configuración del negocio** → *Usuarios → Usuarios del
   sistema* → **Agregar**: nombre `bot-consorcios`, rol **Administrador**.
2. Con el usuario elegido → **Asignar activos**:
   - *Apps* → tu app → **Control total**.
   - *Cuentas de WhatsApp* → la de `WHATSAPP_WABA_ID` → **Control total**.
3. **Generar token** → app: la tuya → vencimiento: **Nunca** → permisos (exactamente estos dos):
   - `whatsapp_business_messaging` (mandar y recibir mensajes)
   - `whatsapp_business_management` (leer el número y la cuenta, plantillas y webhooks)
4. ⚠️ **Meta muestra el token una sola vez.** Pegalo directo en `.env` como
   `WHATSAPP_ACCESS_TOKEN` (sin comillas ni espacios). Si se pierde, se genera otro.

## 3. Clave secreta de la app (`WHATSAPP_APP_SECRET`)

Meta firma **cada** webhook con la clave secreta de la app (encabezado `X-Hub-Signature-256`,
HMAC-SHA256 del cuerpo). La api rechaza con 401 todo lo que no tenga una firma válida, y sin la
clave configurada rechaza todo con 503. Es lo que impide que alguien mande un mensaje falso
"desde" el número de un propietario para ver su deuda.

Tu app en developers.facebook.com → *Configuración de la app → Básica* → **Clave secreta de la
app** → Mostrar → pegala en `.env` como `WHATSAPP_APP_SECRET`.

## 4. Token de verificación (`WHATSAPP_VERIFY_TOKEN`)

Es una clave que **inventás vos**: Meta la manda una vez, al configurar el webhook (paso 7), y
la api la compara antes de aceptar. Generala con:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

y pegala en `.env` como `WHATSAPP_VERIFY_TOKEN` (la misma va en Meta en el paso 7).

Para ver qué variables tenés cargadas sin mostrar los valores:

```powershell
Select-String -Path .env -Pattern '^WHATSAPP_[A-Z_]+=' | ForEach-Object { ($_.Line -split '=')[0] }
```

Después: `docker compose up -d api` (recrea el contenedor con el `.env` nuevo).

## 5. Suscribir la app a la cuenta de WhatsApp

Sin esta suscripción Meta no le entrega los mensajes a la app. Se hace **una vez** por cuenta de
WhatsApp (WABA), con el token del paso 2, desde PowerShell. El token se pide con `Read-Host`,
así no queda en la pantalla ni en el historial:

```powershell
$waba  = Read-Host "ID de la cuenta de WhatsApp Business (WHATSAPP_WABA_ID)"
$token = [System.Net.NetworkCredential]::new("", (Read-Host "Token" -AsSecureString)).Password
$auth  = @{ Authorization = "Bearer $token" }

Invoke-RestMethod -Method Post -Uri "https://graph.facebook.com/v26.0/$waba/subscribed_apps" -Headers $auth
Invoke-RestMethod -Method Get  -Uri "https://graph.facebook.com/v26.0/$waba/subscribed_apps" -Headers $auth | ConvertTo-Json -Depth 5

Remove-Variable token, auth
```

- El `POST` tiene que responder `success : True`.
- El `GET` tiene que listar tu app (su nombre y su ID) en `data`.

Si responde un error de permisos, revisá que el usuario del sistema tenga asignadas la app y la
cuenta con **Control total** y que el token tenga `whatsapp_business_management`.

**Alternativa: Graph API Explorer** (https://developers.facebook.com/tools/explorer): *Meta App*
→ tu app, un token con los dos permisos, método **POST**, ruta `{WABA_ID}/subscribed_apps` →
**Enviar** (`"success": true`); con **GET** y la misma ruta, tu app aparece en `data`.

## 6. Túnel HTTPS (solo el webhook)

Meta solo manda webhooks a una URL pública con HTTPS. El perfil `tunnel-wa` del compose levanta
un proxy (Caddy, [deploy/webhook-proxy/Caddyfile](../deploy/webhook-proxy/Caddyfile)) que deja
pasar **solo** `/webhooks/whatsapp` hacia la api (todo lo demás: 404) y un cloudflared que
publica ese proxy con un *quick tunnel* gratis de Cloudflare, sin cuenta ni puertos abiertos:

```powershell
docker compose --profile tunnel-wa up -d cloudflared-wa
docker compose --profile tunnel-wa logs cloudflared-wa | Select-String trycloudflare
```

Aparece una URL como `https://palabras-al-azar.trycloudflare.com`. Probala:

- `https://<url>/admin` → **404** (el panel no se publica).
- `https://<url>/webhooks/whatsapp` (GET sin parámetros) → **403**.

**La URL cambia cada vez que arranca el contenedor** (al recrearlo, al reiniciar Docker Desktop
o la PC). Por eso no tiene política de reinicio: si se cae, no vuelve solo con otra URL a
escondidas. Cuando cambia, Meta sigue mandando a la vieja y no llega nada: buscá la nueva URL
(comando de arriba) y actualizala en Meta (paso 7, **Editar**). Para no repetirlo, un túnel fijo
con dominio propio en Cloudflare (`cloudflared tunnel create ...`) apuntado al proxy.

Para apagarlo: `docker compose --profile tunnel-wa stop cloudflared-wa webhook-proxy`.

## 7. Webhook de Meta → nuestra api

1. Tu app → **WhatsApp → Configuración** (en algunas versiones del panel: *Casos de uso →
   WhatsApp → Personalizar → Configuración básica*) → sección *Webhook* → **Editar**:
   - **URL de devolución de llamada**: `https://<url>/webhooks/whatsapp`
   - **Token de verificación**: el valor de `WHATSAPP_VERIFY_TOKEN`.
2. **Verificar y guardar**. Meta hace el GET de verificación en el acto: en
   `docker compose logs api` aparece `WhatsApp webhook verified by Meta`. Si falla: el túnel no
   está andando, la URL no es la actual o el token no coincide (en el log:
   `WhatsApp webhook verification rejected`).
3. En **Campos del webhook**, **Suscribirse** a `messages` (mensajes y estados de entrega) y, si
   el estudio también usa la app de WhatsApp Business en el celular (coexistencia),
   `smb_message_echoes`.

## 8. Probar

Desde tu celular (en la lista de destinatarios), escribile al número de prueba:

| Mensaje                          | Qué tiene que pasar                                                    |
| -------------------------------- | ---------------------------------------------------------------------- |
| `Hola`                           | El bot se presenta como asistente automático.                          |
| `¿Cuánto debo?`                  | Si tu número no es de un propietario en la base: pide edificio y unidad y ofrece el código por email. Si lo es (y el edificio es piloto): la deuda y el código de pago. |
| un audio                         | Pide que lo escribas.                                                  |
| `Quiero hablar con una persona`  | Avisa la derivación; en **Conversaciones** aparece en «Esperando persona», con la nota de derivación y la etiqueta `pide-persona`. |

Después de derivar, el bot ya no contesta en esa conversación hasta que una persona la devuelva
al bot o la resuelva.

**Logs.** `docker compose logs -f api`. Por cada mensaje:

```
WhatsApp webhook accepted: 1 new message(s)
Processing whatsapp message 120 of conversation 21 (trusted phone: yes, known: no)
Conversation 21: agent turn gemini/..., 1 calls, tokens in ... out ..., cost US$ ...
Conversation 21: reply sent in 1 message(s) (0 debt block(s), 0 option(s))
```

`known: yes` si tu número está en la base como propietario.

**Si algo falla:**

| Síntoma                                                     | Causa probable                                                         |
| ----------------------------------------------------------- | ---------------------------------------------------------------------- |
| No aparece ningún `WhatsApp webhook accepted`               | Túnel caído o con otra URL (paso 6), webhook no verificado o `messages` sin suscribir (paso 7), app sin suscribir a la cuenta (paso 5). |
| `WhatsApp webhook rejected: invalid or missing signature`   | `WHATSAPP_APP_SECRET` no es la clave secreta de **esta** app.          |
| El webhook responde 503                                     | Falta `WHATSAPP_APP_SECRET` (POST) o `WHATSAPP_VERIFY_TOKEN` (GET), o falta el token o el número para responder. |
| El mensaje del bot figura «No se entregó» en Conversaciones | El error de Meta está en el mensaje. `131030`: el destinatario no está en la lista del número de prueba (con números de Argentina, ver abajo). `190`: token vencido o inválido. `131047`: pasaron más de 24 h desde el último mensaje del contacto. |

## Números argentinos con el número de prueba

**Solo afecta al número de prueba de Meta**: el número real no tiene lista de destinatarios.

Cuando un celular argentino escribe, WhatsApp identifica al remitente (`wa_id`) **con el 9**:
`549` + área + número. Pero la lista de destinatarios del número de prueba solo acepta el
formato **con 15**: `54` + área + `15` + número. El bot responde al `wa_id` con 9, que no
figura en la lista, y Meta lo rechaza con `131030`.

La solución, **solo para desarrollo**: `WHATSAPP_DEV_RECIPIENT_REWRITE` en `.env`, un par
`origen:destino` por cada celular de la lista, separados por coma, solo dígitos (sin `+`). Por
ejemplo, para el número inventado 351 555-0000 de Córdoba:

```ini
WHATSAPP_DEV_RECIPIENT_REWRITE=5493515550000:543511555550000
```

Solo cambia a quién se manda: la conversación y el contacto siguen con el número con 9, que es
con el que el bot identifica al propietario. Funciona solo con `APP_ENV=development` (en
producción se ignora y lo avisa en el log). En `docker compose logs api` aparece
`[DEV_RECIPIENT_REWRITE] recipient 549... -> 54...15...` por cada envío reescrito.

## Cómo se comporta

- El bot solo contesta si la conversación está **con el bot**. Al derivar pasa a **esperando
  persona** (motivo, prioridad, resumen y etiquetas, más una nota interna) y deja de contestar
  hasta que una persona la devuelva o la resuelva. Resuelta, vuelve al bot con el próximo
  mensaje del contacto, y el bot arranca de cero (saluda).
- Un mensaje mandado desde la app de WhatsApp Business del celular (`smb_message_echoes`) se
  guarda como mensaje del estudio y la conversación pasa a **con una persona**.
- Ventana de 24 h: texto libre y botones solo dentro de las 24 h del último mensaje del
  contacto; fuera, solo plantillas aprobadas. Si el bot no puede contestar porque la ventana se
  cerró, deriva con el motivo `window_closed` (etiqueta `ventana-cerrada`).
- Si una persona del panel toma la conversación mientras el bot arma la respuesta, no sale nada
  más del bot: cada envío bloquea la fila de la conversación y vuelve a mirar el estado, y los
  botones del panel bloquean la misma fila.
- Opciones: hasta 3 van como **botones**; más, como **lista** (botón «Ver opciones»). Al
  tocar, llega el título de la opción y el bot lo toma como texto. Si Meta las rechazara, van
  numeradas en el texto.
- Estados (enviado, entregado, leído; "reproducido" cuenta como leído; fallido con el código de
  Meta) llegan por el webhook y actualizan el mensaje.
- Adjuntos entrantes: se bajan enseguida (los links de Meta vencen en minutos) al volumen
  `wa_media`, hasta `WHATSAPP_MEDIA_MAX_BYTES`, solo imágenes, PDF, Word, Excel, texto y
  audios. Se ven en el panel, solo con sesión.
- Cada mensaje de WhatsApp se guarda una vez (Meta reintenta hasta recibir un 200; el id del
  mensaje es único) y se responde en segundo plano.
- Si la api se reinicia con mensajes sin responder, al arrancar responde los de los últimos
  `WHATSAPP_RECOVERY_MINUTES` y deja los más viejos marcados sin responder (warning en el log
  y evento `unanswered_after_restart`).

## Atender desde el panel

Las conversaciones se atienden en **/admin → Conversaciones** (ver el README). Antes:

1. Crear un usuario por empleada en **Usuarios** (rol Operadora o Admin).
2. Cargar en **Plantillas de WhatsApp** las plantillas aprobadas en Meta (WhatsApp Manager →
   Plantillas), sin variables: nombre exacto, idioma (`es_AR`, `es`...) y el texto.
3. Opcional: **Respuestas rápidas**.
4. En cada navegador, tocar **🔔 Avisos** una vez (el navegador pide permiso para las
   notificaciones; funcionan con https o en localhost).

Todo lo que se hace ahí (tomar, devolver, resolver, responder, plantilla, nota) queda en
`bot_events` como `admin_action` con el usuario, sin el texto de los mensajes.

## Pasar a producción

- Número real: **no** puede estar usándose a la vez en la app común de WhatsApp. Se registra en
  la Cloud API desde WhatsApp Manager (o con coexistencia, si el estudio sigue usando la app de
  WhatsApp Business en el celular: entonces suscribir también `smb_message_echoes`).
- Token permanente (paso 2), `WHATSAPP_APP_SECRET` y `WHATSAPP_VERIFY_TOKEN` en el `.env` del
  servidor; `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_WABA_ID` del número real.
- URL fija con HTTPS (túnel fijo o servidor con dominio), que publique **solo**
  `/webhooks/whatsapp` (como el proxy del paso 6): el panel nunca en internet. Actualizarla en
  Meta (paso 7).
- `APP_ENV=production`: apaga el chat de prueba y `WHATSAPP_DEV_RECIPIENT_REWRITE`.
- Para escribirle primero a alguien (fuera de la ventana de 24 h) hacen falta plantillas
  aprobadas por Meta, cargadas en el panel.
