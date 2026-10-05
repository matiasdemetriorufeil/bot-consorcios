# bot-consorcios

Bot de WhatsApp con IA para **Estudio Diego Rufeil**, administración de consorcios de Córdoba
(~800 unidades en 53 consorcios). El bot identifica a los propietarios por teléfono, informa
deuda de expensas y el código de pago de la unidad, responde sobre el reglamento del edificio y deriva a
operadores humanos en Chatwoot. Los datos se leen (solo lectura) del sistema de gestión ConsorPlus.

Stack: Python 3.12 (uv), FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL 16, pytest, ruff, Docker Compose.

## Levantar el entorno

Requisitos: Docker Desktop.

```bash
cp .env.example .env      # completar los valores (nunca subir .env)
docker compose up --build
```

- API: http://localhost:8000 — chequeo: http://localhost:8000/health
- PostgreSQL: `localhost:5432` (los datos persisten en el volumen `pgdata`)

La API se recarga sola al editar archivos en `app/`.

Chatwoot (panel de operadores) está en el mismo compose, en el perfil `chatwoot` (necesita ~4 GB
de RAM): `docker compose --profile chatwoot up -d` → http://localhost:3000. Primer arranque,
admin y API token: [docs/chatwoot.md](docs/chatwoot.md).

## Base de datos y migraciones

El modelo está en `app/db/models.py` (SQLAlchemy 2) y las migraciones en `alembic/versions/`.
Alembic toma la conexión de `DATABASE_URL`.

```bash
docker compose run --rm api alembic upgrade head                      # aplicar migraciones
docker compose run --rm api alembic revision --autogenerate -m "..."  # nueva migración (revisarla a mano)
docker compose run --rm api alembic check                             # modelo y migraciones coinciden
```

Al cambiar dependencias hay que reconstruir la imagen: `docker compose build api`.

## Sincronización con ConsorPlus

El servicio `scheduler` (APScheduler, levantado por `docker compose up`) corre, en hora de Córdoba:

- **03:00 — sincronización nocturna** (`app/sync/nightly.py`): primero el padrón (unidades,
  propietarios, teléfonos, código de pago) y después la deuda de cada unidad activa, que queda en
  `debt_snapshots` con `source="nightly"`. Una unidad que falla no corta el resto. Si ConsorPlus
  está caído (10 fallas seguidas) o rechaza el login, se corta. Si se corre dos veces el mismo
  día, reemplaza el snapshot del día. Aplica la retención: borra los snapshots nightly de más de 60
  días y los live de más de 30, pero siempre deja el último de cada unidad.
- **08:00 — canario** (`app/sync/canary.py`): consulta la unidad `CANARY_BUILDING`/`CANARY_UNIT`
  del `.env` y, si la página de deuda cambió de estructura, deja un error `CANARIO FALLÓ` en el log.

Cada corrida queda en la tabla `sync_runs` (job `roster`, `debt` o `canary`), solo con contadores.

```bash
docker compose run --rm api python -m app.sync.nightly --buildings 1,2   # manual, algunos edificios
docker compose run --rm api python -m app.sync.nightly                   # manual, todos
docker compose run --rm api python -m app.sync.canary                    # canario a mano
docker compose logs -f scheduler
```

Un lock de Postgres impide que se pisen dos sincronizaciones nocturnas (la programada y una manual).

Para el bot, `app.sync.live.refresh_unit(unit_id)` trae en vivo la deuda de una unidad y la guarda
con `source="live"`. Usa una sola sesión de ConsorPlus por proceso (sin login en cada consulta);
espera 6 segundos con la sesión lista o 10 si antes tiene que hacer login (medido: ~1 s y ~4–5,5 s).
Si no llega a tiempo, devuelve la última deuda guardada con `stale=True` y su fecha (la consulta
sigue y se guarda al terminar). La sesión se da por vencida tras `CONSORPLUS_SESSION_IDLE_MINUTES`
(15) sin uso. Cuando escribe un propietario identificado, el bot hace el login por adelantado
(`warm_up`) mientras piensa la respuesta.

## Identidad y verificación (`app/bot/identity.py`)

Reglas determinísticas, sin IA:

- `identify_by_phone`: persona y unidades (con rol) del teléfono. Un teléfono con
  `needs_review` (en conflicto o con característica supuesta) se trata como desconocido.
- `can_view_unit_finance`: **única puerta** para mostrar deuda o código de pago. Solo
  propietarios de la unidad activa.
- Números desconocidos: `start_email_verification` busca la unidad (tolerante a cómo se
  escriba) y manda un código de 6 dígitos distinto a cada email de **propietario** (nunca de
  inquilinos), válido 15 minutos; `confirm_email_code` asocia el teléfono. Límites: 3 inicios
  por teléfono cada 24 h y 5 intentos por verificación. Sin email de propietario:
  `request_operator_verification` deja una solicitud para el panel de operadores.
- `VERIFICATION_EMAIL_EXCLUDE` (emails o dominios separados por coma; por defecto
  `estudiodiegorufeil@gmail.com`): nunca reciben códigos. Una unidad con solo esos emails se
  trata como "sin email" y va a operador.
- Todo queda en `bot_events` (sin códigos ni emails).

Emails: `EMAIL_BACKEND=console` (desarrollo: no manda nada, loguea el email y el código) o
`smtp` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`).

## Agente conversacional (`app/llm/`, `app/bot/agent.py`)

Funciona con Gemini o Claude según `LLM_PROVIDER` / `LLM_MODEL` (vacío = default por
proveedor). Herramientas en `app/bot/tools.py`; tokens y costo estimado (`LLM_PRICE_*`) en
`bot_events`.

Probarlo en la terminal (solo desarrollo, requiere `EMAIL_BACKEND=console`):

```bash
uv run python scripts/chat_cli.py --phone +5493515550977       # número que escribe
uv run python scripts/chat_cli.py --as-owner-of 1 1025         # propietario simulado
```

### Evaluación automática (`evals/`)

`evals/cases.yaml` tiene casos con datos inventados (`evals/seed.py`); `evals/run.py` los corre
contra el proveedor real sobre la base `<POSTGRES_DB>_eval` (se reconstruye en cada corrida, no
toca ConsorPlus ni manda emails) y deja el informe en `evals/reports/`. Cuesta dinero: no corre
en `pytest` ni en CI.

```bash
uv run python -m evals.run                                        # proveedor de .env
uv run python -m evals.run --provider anthropic --model claude-haiku-4-5
uv run python -m evals.run --only deuda_simple urgencia_gas
```

## Chatwoot (`app/chatwoot/`)

El bot atiende como **Agent Bot** de Chatwoot: `POST /webhooks/chatwoot` recibe los mensajes
(firma HMAC verificada, idempotente, respuesta en segundo plano), el historial (últimos 20
mensajes) se lee de Chatwoot y, al derivar, deja una nota privada, etiquetas y pasa la
conversación a Abierta. Solo atiende conversaciones **Pendientes**: una vez derivada o tomada
por un operador, no contesta ni vuelve a derivar.

El teléfono del contacto solo identifica en las bandejas de `CHATWOOT_TRUSTED_PHONE_INBOX_IDS`
(en producción, solo WhatsApp). Los propietarios de edificios fuera del piloto
(`buildings.pilot`) van directo a una persona.

- Configuración, Agent Bot y prueba con el chat web (`/dev/chat`): [docs/chatwoot.md](docs/chatwoot.md)
- Conectar WhatsApp cuando Meta apruebe: [docs/whatsapp.md](docs/whatsapp.md)

## Panel de administración (`app/admin/`, `/admin`)

Panel SQLAdmin para los operadores en `http://localhost:8000/admin`:

- **Edificios**: nombre, dirección, activo y piloto (los edificios los crea la sincronización).
- **Información de edificios**: reglamento, horarios, contactos, emergencias y otros, por edificio.
- **Teléfonos**: todos los teléfonos, con búsqueda por número (se comparan los dígitos, así que
  sirve `0351 555-0000`) o por nombre, filtros por verificado, a revisar, en conflicto y fuente,
  y las unidades de cada persona. No se crean ni editan; **Desvincular** borra el teléfono
  (número que cambió de dueño o mal asociado). Si el número sigue en ConsorPlus, la
  sincronización nocturna lo vuelve a crear: hay que corregirlo también allá.
- **Teléfonos a revisar** (característica supuesta al importar): aprobar o eliminar.
- **Verificaciones pendientes** (unidades sin email de propietario): aprobar eligiendo el
  propietario (el teléfono queda asociado con origen `manual`) o rechazar.
- **Sincronizaciones**: historial de `sync_runs`, solo lectura.
- **Configuración general**: bienvenida, horario, texto fuera de horario, contacto de
  urgencias, URL de autogestión y cómo pagar. El valor del panel tiene prioridad sobre `.env`
  (vacío = se usa el de `.env`) y el bot lo toma en menos de un minuto, sin reiniciar.
- **Métricas**: conversaciones por día, porcentaje derivado, herramientas más usadas y costo
  de IA del mes (de `bot_events`).

Ingreso con un único usuario de `.env`:

```powershell
uv run python scripts/hash_admin_password.py   # pide la contraseña e imprime el hash
```

Completar en `.env` `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` (el hash, nunca la contraseña) y
`ADMIN_SECRET_KEY`. Tras 5 intentos fallidos el ingreso se bloquea 15 minutos. Todas las
acciones del panel quedan en `bot_events` (`event_type = 'admin_action'`, con el usuario y los
campos cambiados, sin sus valores). El panel nunca muestra códigos de verificación ni valores
de `.env`.

## Tests y lint

Los tests de base de datos usan un Postgres real: la base `<POSTGRES_DB>_test` en el mismo
servidor (o la de `TEST_DATABASE_URL`). Se crea sola y se reconstruye con las migraciones en cada
corrida; cada test corre en una transacción que se descarta. Si Postgres no está levantado, esos
tests se saltean con un aviso, salvo que esté definida la variable `CI`: ahí fallan. En GitHub
Actions (`.github/workflows/ci.yml`) corren contra un servicio `postgres:16`.

Dentro del contenedor:

```bash
docker compose exec api pytest
docker compose exec api ruff check .
docker compose exec api ruff format --check .
```

O localmente con [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

## Datos reales

Los archivos con datos reales (propietarios, exports, capturas) van **solo** en `private/`,
que está ignorada por git.
