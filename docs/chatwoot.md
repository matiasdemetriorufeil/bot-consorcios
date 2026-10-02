# Chatwoot (desarrollo local)

Chatwoot es el panel donde los operadores atienden las conversaciones que el bot deriva. En
desarrollo corre dentro del mismo `docker-compose.yml`, armado a partir del
`docker-compose.production.yaml` oficial de Chatwoot y de su guía de Docker
(https://developers.chatwoot.com/self-hosted/deployment/docker).

| Servicio            | Imagen                          | Para qué                                    |
| ------------------- | ------------------------------- | ------------------------------------------- |
| `chatwoot-rails`    | `chatwoot/chatwoot:v4.18.0`     | Web y API, en http://localhost:3000         |
| `chatwoot-sidekiq`  | `chatwoot/chatwoot:v4.18.0`     | Tareas en segundo plano (envíos, webhooks)  |
| `chatwoot-db`       | `pgvector/pgvector:pg16`        | Postgres **propio** de Chatwoot             |
| `chatwoot-redis`    | `redis:7-alpine`                | Colas y cache, con contraseña               |

Su Postgres está separado del nuestro (`db`): otro contenedor, otro volumen
(`chatwoot_pgdata`) y sin puerto publicado en Windows. Chatwoot necesita la extensión `vector`,
por eso usa la imagen de pgvector, igual que el compose oficial.

Los cuatro servicios están en el **perfil `chatwoot`**: `docker compose up` a secas **no** los
levanta. Para que siempre arranquen, agregá `COMPOSE_PROFILES=chatwoot` al `.env`.

## Requisitos (Windows + Docker Desktop)

- **~4 GB de RAM libres para Docker.** Recién levantado, rails y sidekiq usan ~400 MB cada uno,
  pero suben con uso, al preparar la base y al actualizar. Docker Desktop corre sobre WSL 2 y por
  defecto le da a la VM la mitad de la RAM de la PC. Para ver cuánta tiene:

  ```powershell
  docker info --format "{{.MemTotal}}"   # en bytes; 8 GB ≈ 8000000000
  ```

  Si hace falta más, creá o editá `%UserProfile%\.wslconfig`:

  ```ini
  [wsl2]
  memory=6GB
  ```

  y reiniciá WSL con `wsl --shutdown` (Docker Desktop se vuelve a levantar solo o desde el ícono).
- **~3,5 GB de disco** para las imágenes (la de Chatwoot pesa ~2,7 GB).

## Variables (`.env`)

Todas las de Chatwoot llevan el prefijo `CHATWOOT_`. Los contenedores de Chatwoot **no** leen el
`.env` completo, porque tenemos variables con el mismo nombre que Chatwoot también usa
(`POSTGRES_PASSWORD`, `SMTP_*`); el compose les pasa solo lo que necesitan.

| Variable                      | Uso                                                             |
| ----------------------------- | --------------------------------------------------------------- |
| `CHATWOOT_VERSION`            | Tag de la imagen (`v4.18.0`). Fijo para que no cambie solo.     |
| `CHATWOOT_FRONTEND_URL`       | URL con la que se entra (`http://localhost:3000`).              |
| `CHATWOOT_SECRET_KEY_BASE`    | Clave de Rails. Solo letras y números, larga.                   |
| `CHATWOOT_POSTGRES_PASSWORD`  | Contraseña del Postgres de Chatwoot.                            |
| `CHATWOOT_REDIS_PASSWORD`     | Contraseña del Redis de Chatwoot.                               |
| `CHATWOOT_BASE_URL`           | Para nuestra app: dónde está la API de Chatwoot (ver abajo).    |
| `CHATWOOT_ACCOUNT_ID`         | Para nuestra app: ID de la cuenta (ver abajo).                  |
| `CHATWOOT_BOT_TOKEN`          | Para nuestra app: token del Agent Bot (ver "Conectar el bot").  |
| `CHATWOOT_API_TOKEN`          | Para nuestra app: token de un usuario admin (ver abajo).        |
| `CHATWOOT_WEBHOOK_SECRET`     | Para nuestra app: "Webhook Secret" del Agent Bot.               |
| `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` | Bandejas cuyo teléfono identifica (solo WhatsApp).         |
| `CHATWOOT_WEBSITE_TOKEN`      | Token de la bandeja Website, para `/dev/chat`.                  |

Para generar las tres claves:

```powershell
python -c "import secrets; print(secrets.token_hex(64))"   # CHATWOOT_SECRET_KEY_BASE
python -c "import secrets; print(secrets.token_hex(16))"   # cada contraseña
```

> Cambiar `CHATWOOT_POSTGRES_PASSWORD` después del primer arranque no cambia la contraseña de la
> base ya creada (Postgres la toma solo al inicializar el volumen).

`CHATWOOT_BASE_URL` depende de dónde corra nuestro código:

- desde el contenedor `api` / `scheduler`: `http://chatwoot-rails:3000`
- desde Windows (scripts, pruebas a mano): `http://localhost:3000`

## Primer arranque

```powershell
docker compose --profile chatwoot pull
docker compose --profile chatwoot run --rm chatwoot-rails bundle exec rails db:chatwoot_prepare
docker compose --profile chatwoot up -d
```

- `db:chatwoot_prepare` crea la base y corre las migraciones. Es normal ver en el log
  `ERROR -- : Failed to configure AI Agents SDK: ... relation "installation_configs" does not exist`
  la primera vez: la base todavía está vacía cuando arranca. Tiene que terminar con
  `Loading Installation config`.
- Si el pull se corta (`short read ... unexpected EOF`), repetilo: retoma lo que ya bajó.
- Rails tarda 30–60 s en arrancar. Chequeo: `curl -I http://localhost:3000/api` → `200`, o
  `docker compose ps` → `chatwoot-rails ... (healthy)`.

## Crear el primer administrador y la cuenta

Con la base recién creada, Chatwoot redirige cualquier página a
**http://localhost:3000/installation/onboarding**. Ese formulario aparece **una sola vez**:

1. Entrá a http://localhost:3000.
2. Completá nombre, **nombre de la empresa** (ese es el nombre de la cuenta, p. ej.
   `Estudio Diego Rufeil`), email y contraseña.
3. Enviá el formulario. Se crean a la vez:
   - el usuario, como **administrador** de la cuenta y además **super admin** de la instalación;
   - la **cuenta** (en Chatwoot, "cuenta" = la organización: agentes, inboxes y conversaciones
     viven dentro de una cuenta).
4. Quedás logueado en el panel. El idioma se cambia en *Configuración del perfil*.

El registro público está apagado (`ENABLE_ACCOUNT_SIGNUP=false`): nadie más puede crear cuentas
desde la pantalla de login.

**Agentes (operadores).** Se agregan desde *Configuración → Agentes*. Como en desarrollo no hay
SMTP configurado, las invitaciones por mail **no se envían**: para darles contraseña, entrá al
panel de super admin (http://localhost:3000/super_admin, con el usuario del onboarding) →
*Users* → editar el usuario.

**Más cuentas.** Si alguna vez hace falta otra, se crea desde
http://localhost:3000/super_admin → *Accounts*.

### Si el onboarding ya no aparece

Si la instalación ya tiene usuarios (por ejemplo, alguien completó el formulario y nadie sabe la
contraseña), se puede crear un admin desde la consola de Rails:

```powershell
docker compose --profile chatwoot exec chatwoot-rails bundle exec rails c
```

```ruby
account = Account.find_or_create_by!(name: "Estudio Diego Rufeil")
user = User.new(name: "Admin", email: "admin@example.com", password: "CambiarEsto-123!",
                password_confirmation: "CambiarEsto-123!")
user.skip_confirmation!
user.type = "SuperAdmin"
user.save!
AccountUser.create!(account: account, user: user, role: :administrator)
```

(Email y contraseña de ejemplo: usá los tuyos y no los guardes en el repo.)

## ID de la cuenta → `CHATWOOT_ACCOUNT_ID`

Está en la URL del panel: `http://localhost:3000/app/accounts/1/dashboard` → el ID es `1`.

## API token → `CHATWOOT_API_TOKEN`

1. En el panel, clic en tu avatar (abajo a la izquierda) → **Configuración del perfil**
   (*Profile settings*).
2. Al final de la página, sección **Token de acceso** (*Access Token*): copiá el valor.
3. Pegalo en `.env` como `CHATWOOT_API_TOKEN=...` y reiniciá la api: `docker compose up -d api`.

El token actúa **como ese usuario** (con sus permisos de administrador). Se manda en el header
`api_access_token`. Para probarlo:

```powershell
curl.exe -H "api_access_token: TU_TOKEN" http://localhost:3000/api/v1/profile
curl.exe -H "api_access_token: TU_TOKEN" http://localhost:3000/api/v1/accounts/1/conversations
```

El token se puede regenerar desde la misma pantalla; el anterior deja de funcionar.

El bot usa este token **solo** para leer el historial de la conversación y actualizar los
atributos del contacto (el token del Agent Bot no tiene permiso para eso). Todo lo que escribe
en la conversación lo hace con el token del Agent Bot, así figura como enviado por el bot.
Conviene un usuario administrador propio para la integración (p. ej. "Integración bot") en vez
del token personal de alguien.

## Conectar el bot (Agent Bot)

Mientras Meta verifica el negocio, probamos con una bandeja de **chat web** (Website).
WhatsApp se agrega después como otra bandeja con el mismo Agent Bot, sin tocar código
([docs/whatsapp.md](whatsapp.md)).

Cómo funciona:

1. La persona escribe → Chatwoot crea la conversación en estado **Pendiente** (la bandeja tiene
   un bot) y le manda el evento `message_created` a `POST /webhooks/chatwoot`, firmado.
2. Nuestra api verifica la firma, guarda el ID del mensaje (si llega dos veces, se contesta una
   sola) y responde 200 enseguida. La respuesta se genera en segundo plano.
3. El bot lee los últimos 20 mensajes de la conversación, contesta y, si deriva: deja una
   **nota privada** con el resumen, agrega **etiquetas** (motivo, edificio y `urgente` si
   corresponde) y pasa la conversación a **Abierta**.
4. El bot solo atiende conversaciones **Pendientes**. Una vez abierta (derivada o tomada por un
   operador) no responde ni vuelve a derivar. Si se resuelve y la persona escribe de nuevo,
   Chatwoot la vuelve a poner en Pendiente y la atiende el bot otra vez.

   ⚠️ Para que un operador tome una conversación que todavía tiene el bot, tiene que
   **asignársela o marcarla como Abierta**: responder en una conversación Pendiente no la
   abre, y el bot seguiría contestando.

Otras reglas:

- **Consorcios piloto:** si quien escribe está identificado y ninguna de sus unidades es de un
  edificio con `pilot = true`, va directo a una persona (etiqueta `fuera-de-piloto`). Los
  números desconocidos se atienden igual. Para marcar edificios:
  `UPDATE buildings SET pilot = true WHERE consorplus_code IN (31, 40);`
- **Horario de atención** (`OFFICE_HOURS_START`, `OFFICE_HOURS_END`, `OFFICE_WEEKDAYS` en
  `.env`, por defecto lunes a viernes de 9 a 17): fuera de horario, el aviso de derivación dice
  cuándo le responden ("el lunes a partir de las 9"). No contempla feriados.
- **Audios, stickers, ubicaciones y videos:** le pide que lo escriba. **Imágenes y archivos**
  sin texto: agradece y avisa que el estudio los ve si se deriva.
- **Atributos del contacto:** cuando identifica a la persona, carga en el contacto `unit`,
  `building` y `verified`.

### 1. Bandeja Website

1. *Configuración → Bandejas de entrada → Agregar bandeja → Sitio web* (*Settings → Inboxes →
   Add Inbox → Website*).
2. Nombre: `Chat web (pruebas)`. Dominio: `localhost:8000`. Lo demás, por defecto.
3. Agregá los agentes (operadores) que van a atender y terminá.
4. En la pestaña **Configuración** de la bandeja está el script del widget: copiá el valor de
   `websiteToken` → `CHATWOOT_WEBSITE_TOKEN` en `.env`.
5. El **ID de la bandeja** está en la URL: `.../settings/inboxes/1` → `1`.

### 2. Agent Bot

1. *Configuración → Bots → Agregar bot* (*Settings → Bots → Add Bot*).
2. Nombre: `Asistente` (es el nombre que ve la gente en el chat). URL del webhook:

   ```
   http://api:8000/webhooks/chatwoot
   ```

   (`api` es el nombre del servicio en el compose: Chatwoot y la api están en la misma red de
   Docker.)
3. Al crearlo, Chatwoot muestra el **Webhook Secret** y el **token de acceso** del bot (después
   también se ven editando el bot):
   - Webhook Secret → `CHATWOOT_WEBHOOK_SECRET`
   - Token de acceso → `CHATWOOT_BOT_TOKEN`
4. Asignalo a la bandeja: *Configuración → Bandejas de entrada → (la bandeja) → pestaña
   Bot* (*Bot Configuration*) → elegí el bot → **Actualizar**.

**La firma.** Chatwoot v4.18 firma cada webhook del Agent Bot con su Webhook Secret: manda
`X-Chatwoot-Timestamp` (segundos) y `X-Chatwoot-Signature` =
`sha256=` + HMAC-SHA256(secreto, `"{timestamp}.{cuerpo}"`) (está en
`lib/webhooks/trigger.rb` de Chatwoot). La api rechaza con 401 lo que no venga firmado o tenga
más de 5 minutos, y con 503 todo si falta `CHATWOOT_WEBHOOK_SECRET`. Si se regenera el
secreto en Chatwoot, hay que actualizar el `.env`.

> Si nuestra api responde con error o no contesta en 5 segundos, Chatwoot pasa la conversación
> a Abierta sola (para que no quede sin atender). Por eso la api responde rápido y procesa
> después.

### 3. Etiquetas y atributos (recomendado)

Las etiquetas funcionan aunque no existan, pero creándolas en *Configuración → Etiquetas* se
ven con color y se pueden filtrar: `pide-persona`, `molesto`, `reclamo-deuda`,
`pago-no-acreditado`, `plan-de-pago`, `emergencia`, `sin-respuesta`, `otro-motivo`,
`error-tecnico`, `fuera-de-piloto`, `urgente`. Las de edificio son `edificio-<nombre>` (p. ej.
`edificio-rodas-ii`).

Para que los atributos del contacto se vean con nombre: *Configuración → Atributos
personalizados → Agregar*, aplicado a **Contacto**: `Unidad` (clave `unit`, texto), `Edificio`
(clave `building`, texto) y `Verificado` (clave `verified`, casilla).

### 4. `.env`

```ini
APP_ENV=development
CHATWOOT_BASE_URL=http://chatwoot-rails:3000
CHATWOOT_ACCOUNT_ID=1
CHATWOOT_BOT_TOKEN=...          # token del Agent Bot
CHATWOOT_API_TOKEN=...          # token de un usuario administrador
CHATWOOT_WEBHOOK_SECRET=...     # Webhook Secret del Agent Bot
CHATWOOT_TRUSTED_PHONE_INBOX_IDS=
CHATWOOT_WEBSITE_TOKEN=...      # websiteToken de la bandeja Website
CHATWOOT_FRONTEND_URL=http://localhost:3000
```

Después: `docker compose run --rm api alembic upgrade head` (tabla de idempotencia) y
`docker compose up -d api chatwoot-rails chatwoot-sidekiq` (para que tomen las variables).

### 5. Probar desde `/dev/chat`

Abrí http://localhost:8000/dev/chat (solo existe con `APP_ENV=development`): carga el widget
de la bandeja Website. Escribí; la respuesta del bot aparece en el widget y la conversación en
el panel de Chatwoot (filtro **Pendientes** mientras la tiene el bot, **Abiertas** cuando
deriva). Logs: `docker compose logs -f api chatwoot-sidekiq`.

Para empezar una conversación nueva, borrá las cookies de `localhost:8000` o abrí una ventana
de incógnito (el widget recuerda al contacto).

### Teléfono confiable

El teléfono del contacto **solo identifica** si la conversación viene de una bandeja listada en
`CHATWOOT_TRUSTED_PHONE_INBOX_IDS`. En producción va **solo la de WhatsApp**, donde el número lo
pone Meta. En cualquier otra (como el chat web, donde cualquiera puede escribir cualquier
número) la persona es **desconocida** y, para ver deuda, pasa por la verificación por email.

En esas bandejas la verificación se asocia a un número interno por contacto de Chatwoot
(`+54 9 11 09xxxxxx`, que no es una línea real), no al teléfono que haya escrito la persona.

### Simular un propietario conocido (solo desarrollo)

1. En `.env`, agregá el ID de la bandeja web: `CHATWOOT_TRUSTED_PHONE_INBOX_IDS=1` y reiniciá la
   api (`docker compose up -d api`). **Nunca en producción.**
2. Abrí `/dev/chat` y mandá un primer mensaje (crea el contacto).
3. En Chatwoot abrí la conversación → contacto (panel derecho) → **Editar** → cargá en
   *Teléfono* el número de un propietario que esté en la base, en formato `+549351...`.
4. Asegurate de que su edificio sea piloto (`pilot = true`); si no, va directo a una persona.
5. Escribí de nuevo: ahora el bot lo identifica (cada mensaje lleva el teléfono que tiene el
   contacto en ese momento).

Al terminar, sacá el ID de `CHATWOOT_TRUSTED_PHONE_INBOX_IDS` y el teléfono del contacto.

## Comandos útiles

```powershell
docker compose --profile chatwoot up -d                        # levantar
docker compose --profile chatwoot stop                         # parar (libera la RAM)
docker compose --profile chatwoot logs -f chatwoot-rails chatwoot-sidekiq
docker compose --profile chatwoot exec chatwoot-rails bundle exec rails c   # consola
```

**Actualizar de versión:** cambiar `CHATWOOT_VERSION` en `.env` (sin saltear versiones mayores:
Chatwoot recomienda pasar por las intermedias) y después:

```powershell
docker compose --profile chatwoot pull
docker compose --profile chatwoot run --rm chatwoot-rails bundle exec rails db:chatwoot_prepare
docker compose --profile chatwoot up -d
```

**Empezar de cero** (borra usuarios, conversaciones y adjuntos de Chatwoot; no toca nuestra base):

```powershell
docker compose --profile chatwoot rm -s -f chatwoot-rails chatwoot-sidekiq chatwoot-db chatwoot-redis
docker volume rm bot-consorcios_chatwoot_pgdata bot-consorcios_chatwoot_redis bot-consorcios_chatwoot_storage
```

Después, borrá en nuestra base los números internos de contactos web verificados (ver
"Teléfono confiable"): los IDs de contacto vuelven a empezar y un contacto nuevo heredaría la
verificación de uno viejo.

```powershell
docker compose exec db psql -U postgres -d bot_consorcios -c "DELETE FROM phones WHERE e164 LIKE '+5491109%'"
```

⚠️ **No usar `docker compose down -v`**: borra también el volumen `pgdata` de nuestra base.

## Limitaciones conocidas en desarrollo

- **Webhooks hacia nuestra api.** Chatwoot bloquea por defecto los webhooks a direcciones de red
  privada (protección SSRF), y `http://api:8000` dentro de Docker lo es. En el compose de
  desarrollo los servicios de Chatwoot tienen `SAFE_FETCH_ALLOW_PRIVATE_NETWORK=true`.
  **En producción no va**: ahí el webhook apunta a una URL pública con HTTPS.
- **Sin email:** no hay SMTP configurado, así que Chatwoot no manda invitaciones, recuperación de
  contraseña ni notificaciones por mail.
- **Solo local:** todo se publica en `127.0.0.1`; desde otra PC de la red no se accede.
