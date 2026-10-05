# WhatsApp: número de prueba de Meta → Chatwoot local → bot

WhatsApp entra como **otra bandeja de Chatwoot** con el mismo Agent Bot que la bandeja web: el
código del bot no cambia. El recorrido de un mensaje:

```
celular → Meta → túnel HTTPS (cloudflared) → Chatwoot → Agent Bot → nuestra api
                                              ↑                          │
celular ← Meta ←──────────────────────────────┴──── respuesta del bot ←──┘
```

Lo que se publica en internet es **Chatwoot** (puerto 3000), nunca nuestra api: Chatwoot le
habla al bot por la red de Docker.

Todos los pasos de esta guía se hacen **a mano**. Los tokens y claves se pegan en Chatwoot o en
`.env` y no se suben al repo.

## Antes de empezar

- Chatwoot y el bot andan con la bandeja web y `/dev/chat` ([docs/chatwoot.md](chatwoot.md)).
- En Meta → tu app → **WhatsApp → Configuración de la API**, el mensaje `hello_world` del número
  de prueba te llega al celular. Eso confirma que tu celular está en la lista de destinatarios
  ("Para") del número de prueba: el número de prueba **solo** puede escribirles a esos números.
- En `.env` ya tenés `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_WABA_ID` (los dos identificadores
  que muestra esa misma página). Chatwoot los pide en el paso 3.

## 1. Túnel HTTPS con cloudflared

Meta solo manda webhooks a una URL pública con HTTPS. El compose trae un servicio `cloudflared`
(perfil `tunnel`, aparte del de Chatwoot) que abre un *quick tunnel* gratis de Cloudflare hacia
`chatwoot-rails`, sin cuenta ni puertos abiertos.

1. Levantalo (Chatwoot tiene que estar arriba; arranca cuando rails está *healthy*):

   ```powershell
   docker compose --profile chatwoot --profile tunnel up -d cloudflared
   docker compose logs cloudflared | Select-String trycloudflare
   ```

   Aparece una URL como `https://palabras-al-azar.trycloudflare.com`.

2. Probala en el navegador: `https://palabras-al-azar.trycloudflare.com/api` tiene que mostrar
   un JSON con la versión de Chatwoot.

3. **`CHATWOOT_FRONTEND_URL`**. En `.env`, poné la URL del túnel (sin `/` al final):

   ```ini
   CHATWOOT_FRONTEND_URL=https://palabras-al-azar.trycloudflare.com
   ```

   y recreá Chatwoot y la api para que la tomen:

   ```powershell
   docker compose --profile chatwoot up -d --force-recreate chatwoot-rails chatwoot-sidekiq api
   docker compose exec chatwoot-rails printenv FRONTEND_URL
   ```

   Por qué: con esa variable Chatwoot arma la URL del webhook
   (`<FRONTEND_URL>/webhooks/whatsapp/<número>`), y **la registra él mismo en Meta al crear la
   bandeja** (paso 3). Si al crearla todavía dice `http://localhost:3000`, Meta la rechaza. Por
   eso este paso va **antes** del 3.

   Efectos secundarios, ninguno grave: el panel se sigue usando en http://localhost:3000;
   `/dev/chat` carga el widget a través del túnel (funciona igual mientras el túnel esté
   arriba); los links y adjuntos de Chatwoot apuntan al túnel.

4. **Si la URL del túnel cambia.** Cambia **cada vez que arranca el contenedor**: al recrearlo,
   al reiniciar Docker Desktop o la PC. Por eso el servicio no tiene política de reinicio: si se
   cae, no vuelve solo con otra URL a escondidas. Cuando cambia, Meta sigue mandando a la vieja y
   no llega nada. Para arreglarlo:

   1. Nueva URL: `docker compose logs cloudflared | Select-String trycloudflare`.
   2. Actualizá `CHATWOOT_FRONTEND_URL` en `.env` y recreá (punto 3).
   3. Que Chatwoot vuelva a registrar el webhook en Meta (reemplazá `2` por el ID de la bandeja de
      WhatsApp, paso 3.6):

      ```powershell
      docker compose exec chatwoot-rails bundle exec rails runner "Whatsapp::WebhookSetupService.new(Inbox.find(2).channel).perform; puts 'webhook registrado'"
      ```

   Cambiar la URL a mano en la página de Meta **no alcanza**: Chatwoot registra la URL a nivel
   del número de teléfono, y esa tiene prioridad sobre la configurada en la app.

   Para no repetir esto en cada reinicio, se puede usar un **túnel fijo** (requiere un dominio
   propio en Cloudflare; corre en Windows, fuera del compose):

   ```powershell
   winget install --id Cloudflare.cloudflared
   cloudflared tunnel login
   cloudflared tunnel create chatwoot
   cloudflared tunnel route dns chatwoot chatwoot.tu-dominio.com.ar
   cloudflared tunnel run --url http://localhost:3000 chatwoot
   ```

⚠️ Mientras el túnel está arriba, **el panel de Chatwoot queda accesible desde internet**
(login, `/super_admin`, API). Contraseñas fuertes y, cuando no estés probando:
`docker compose stop cloudflared`.

## 2. Token permanente (usuario del sistema)

El token temporal de la página de la API vence en 24 h; Chatwoot necesita uno **permanente**.

1. https://business.facebook.com → **Configuración del negocio** → *Usuarios → Usuarios del
   sistema* → **Agregar**: nombre `chatwoot`, rol **Administrador**.
2. Con el usuario elegido → **Asignar activos**:
   - *Apps* → tu app → **Control total**.
   - *Cuentas de WhatsApp* → la cuenta cuyo ID es `WHATSAPP_WABA_ID` (la del número de prueba)
     → **Control total**.
3. **Generar token** → app: la tuya → vencimiento: **Nunca** → permisos (exactamente estos dos):
   - `whatsapp_business_messaging` (mandar y recibir mensajes)
   - `whatsapp_business_management` (leer el número y la cuenta, plantillas y webhooks)
4. Copialo en ese momento: Meta no lo vuelve a mostrar (si se pierde, se genera otro). **No va
   en `.env` ni en el repo**: se pega solo en Chatwoot (paso 3).

De paso, anotá la **clave secreta de la app** para el paso 3.7: tu app en
developers.facebook.com → *Configuración de la app → Básica* → **Clave secreta de la app** →
Mostrar.

## 3. Bandeja de WhatsApp en Chatwoot

Chatwoot v4.18 tiene un asistente de configuración manual que valida los datos contra Meta y
configura el webhook solo.

1. *Configuración → Bandejas de entrada → Agregar bandeja → WhatsApp* → **WhatsApp Cloud**
   (configuración manual con credenciales de la API de Cloud).
2. Las primeras pantallas (aplicación, número, token) son instrucciones que ya hiciste:
   **Siguiente**.
3. **Detalles** (los dos ID están en tu `.env`):

   | Campo                                  | Qué va                                  |
   | -------------------------------------- | --------------------------------------- |
   | ID de la cuenta de WhatsApp Business   | el valor de `WHATSAPP_WABA_ID`          |
   | ID de número de teléfono               | el valor de `WHATSAPP_PHONE_NUMBER_ID`  |
   | Token de acceso permanente             | el token del paso 2                     |

   → **Verificar detalles**. Si dice que el token no es válido: revisá que el usuario del
   sistema tenga asignadas la app y esa cuenta de WhatsApp, y los dos permisos.
4. **Revisión**: muestra el nombre del negocio y el número de prueba (`+1 555...`). Nombre de la
   bandeja: `WhatsApp (prueba)` → **Crear bandeja de entrada**.

   En este momento Chatwoot llama a Meta: suscribe la app a la cuenta de WhatsApp (campo
   `messages`) y registra como webhook del número
   `<CHATWOOT_FRONTEND_URL>/webhooks/whatsapp/+1555...`, con un token de verificación que
   genera él. Meta la verifica en el acto: el túnel tiene que estar andando.
5. **Verificá tu conexión**: tienen que quedar en *Verificado* "Acceso al número", "Acceso a
   plantillas", "Llamada de Webhook" y "Suscripción de Webhook", y la "URL de Webhook" tiene que
   empezar con la del túnel. Si algo queda pendiente o con error, revisá el túnel y
   `FRONTEND_URL` y tocá **Reintentar configuración del webhook**. Después: **Continuar para
   añadir agentes** → agregá los operadores → terminá.
6. Anotá el **ID de la bandeja**: está en la URL (`.../settings/inboxes/2` → `2`).
7. **Firma de Meta (obligatorio antes del paso 4).** Con la configuración manual, Chatwoot
   **no** verifica la firma de los webhooks de Meta. Cualquiera que conozca la URL podría
   mandarle a Chatwoot un mensaje falso "desde" el número de un propietario y, como en el paso 4
   esta bandeja pasa a identificar por teléfono, el bot le daría la deuda de esa persona. Con la
   clave secreta de la app guardada en la bandeja, Chatwoot rechaza todo lo que no firmó Meta:

   ```powershell
   docker compose exec chatwoot-rails bundle exec rails c
   ```

   ```ruby
   ch = Inbox.find(2).channel                      # 2 = ID de la bandeja de WhatsApp
   secret = STDIN.noecho(&:gets).strip              # pegá la clave secreta y Enter (no se ve)
   ch.provider_config = ch.provider_config.merge("app_secret" => secret)
   ch.save!(validate: false)
   exit
   ```

   Comprobación (con la URL de tu túnel y el número de la bandeja, `+` escrito como `%2B`):

   ```powershell
   curl.exe -s -o NUL -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "{}" https://palabras-al-azar.trycloudflare.com/webhooks/whatsapp/%2B1555XXXXXXX
   ```

   Tiene que dar `401`: un POST sin la firma de Meta se rechaza. Si más adelante cambiás el
   token desde la configuración de la bandeja, repetí esta comprobación (si la clave se perdió,
   repetí el paso).
8. **En Meta (control y respaldo).** Tu app → **WhatsApp → Configuración** → *Webhook*: en
   **Campos del webhook**, `messages` tiene que figurar suscripto (si no, **Suscribirse**). Si
   la URL de devolución de llamada de la app está vacía, podés completarla con la misma URL y el
   **Token de verificación del Webhook** que muestra la pestaña *Configuración* de la bandeja →
   **Verificar y guardar**. No es imprescindible: la URL que registró Chatwoot para el número
   tiene prioridad.

## 4. Agent Bot y teléfono confiable

1. *Configuración → Bandejas de entrada → WhatsApp (prueba) → pestaña Bot* → elegí el mismo bot
   (`Asistente`) → **Actualizar**. Para comprobarlo (con el ID de la bandeja):

   ```powershell
   docker compose exec chatwoot-rails bundle exec rails runner "puts Inbox.find(2).agent_bot_inbox&.status.inspect"
   ```

   → `"active"`.
2. En `.env`, el ID de la bandeja de WhatsApp en la lista de bandejas cuyo teléfono identifica
   (ahí el número lo pone Meta):

   ```ini
   CHATWOOT_TRUSTED_PHONE_INBOX_IDS=2
   ```

   Si habías agregado la bandeja web para simular un propietario, sacala. Recreá la api:
   `docker compose up -d --force-recreate api`.

Chatwoot guarda el teléfono del contacto tal como lo manda Meta (en Argentina, `+549...`, con el
9), que es el formato con el que el bot busca a los propietarios.

## 5. Probar

Desde tu celular, escribile al número de prueba:

| Mensaje                          | Qué tiene que pasar                                                    |
| -------------------------------- | ---------------------------------------------------------------------- |
| `Hola`                           | El bot se presenta como asistente automático.                          |
| `¿Cuánto debo?`                  | Si tu número no es de un propietario en la base: pide edificio y unidad y ofrece el código por email. Si lo es (y el edificio es piloto): la deuda y el código de pago. |
| un audio                         | Pide que lo escribas.                                                  |
| `Quiero hablar con una persona`  | Avisa la derivación; en Chatwoot la conversación pasa a **Abierta**, con nota privada y etiqueta `pide-persona`. |

Después de derivar el bot ya no contesta en esa conversación: para seguir probando, resolvela en
Chatwoot y escribí de nuevo.

**Logs.** En una terminal, `docker compose logs -f api`. Por cada mensaje:

```
Webhook accepted: message 120 of conversation 21
Processing message 120 of conversation 21 (inbox 2, trusted phone: yes, known: no)
Conversation 21: agent turn gemini/..., 1 calls, tokens in ... out ..., cost US$ ...
Conversation 21: reply sent
```

`inbox 2` tiene que ser el de WhatsApp y `trusted phone: yes`; `known: yes` si tu número está en
la base como propietario.

En otra terminal, Chatwoot:

```powershell
docker compose logs -f chatwoot-rails | Select-String "webhooks/whatsapp"
docker compose logs -f chatwoot-sidekiq | Select-String -Pattern "Whatsapp|AgentBot"
```

**Si algo falla:**

| Síntoma                                                     | Causa probable                                                         |
| ----------------------------------------------------------- | ---------------------------------------------------------------------- |
| Ningún `POST /webhooks/whatsapp` en los logs de rails       | Túnel caído o con otra URL (paso 1.4), webhook no verificado (paso 3.5) o `messages` no suscripto (paso 3.8). |
| `POST /webhooks/whatsapp` con `401`                         | La clave secreta del paso 3.7 no es la de la app.                      |
| El mensaje aparece en Chatwoot pero no llega nada a la api  | El bot no está asignado a la bandeja (4.1) o la conversación no está Pendiente. |
| `trusted phone: no` con la bandeja de WhatsApp              | Falta su ID en `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` o no recreaste la api. |
| La respuesta del bot figura con error (ícono rojo) en Chatwoot | Pasá el mouse para ver el error de Meta. `131030`: el destinatario no está en la lista del número de prueba; con números de Argentina el 9 a veces no coincide con el de la lista, probá agregarlo también en el otro formato. `190`: token inválido. |

## Pasar a producción

- Número real: **no** puede estar usándose a la vez en la app de WhatsApp del celular. Si Meta no
  lo tiene ya conectado, al crear la bandeja Chatwoot lo registra en la API con un PIN de
  verificación en dos pasos que genera y guarda él.
- URL fija con HTTPS (túnel fijo o servidor con dominio): sin quick tunnel.
- La **clave secreta de la app** en la bandeja (paso 3.7) es obligatoria.
- `APP_ENV=production` (apaga `/dev/chat`).
- **Sin** `SAFE_FETCH_ALLOW_PRIVATE_NETWORK` en Chatwoot: la URL del Agent Bot pasa a ser la
  URL pública con HTTPS de nuestra api (`https://.../webhooks/chatwoot`), no
  `http://host.docker.internal:8000`.
- `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` solo con la bandeja de WhatsApp.
- Ventana de 24 h de WhatsApp: el bot solo responde mensajes entrantes, así que siempre está
  dentro de la ventana. Para escribirle primero a alguien hacen falta plantillas aprobadas
  por Meta (no está implementado).
