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
| `CHATWOOT_API_TOKEN`          | Para nuestra app: token de acceso (ver abajo).                  |
| `CHATWOOT_ACCOUNT_ID`         | Para nuestra app: ID de la cuenta (ver abajo).                  |
| `CHATWOOT_WEBHOOK_SECRET`     | Para nuestra app: se usa en la Etapa 4.                         |

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

> En la Etapa 4 conviene evaluar usar un **Agent Bot** (super admin → *Agent Bots*, tiene su
> propio token) en vez del token de un usuario humano, para que los mensajes del bot no figuren
> como enviados por el admin.

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

⚠️ **No usar `docker compose down -v`**: borra también el volumen `pgdata` de nuestra base.

## Limitaciones conocidas en desarrollo

- **Webhooks hacia nuestra api.** Chatwoot bloquea por defecto los webhooks a direcciones de red
  privada (protección SSRF), y `http://api:8000` dentro de Docker lo es. Hay que resolverlo antes
  de la Etapa 4 (decisión pendiente).
- **Sin email:** no hay SMTP configurado, así que Chatwoot no manda invitaciones, recuperación de
  contraseña ni notificaciones por mail.
- **Solo local:** todo se publica en `127.0.0.1`; desde otra PC de la red no se accede.
