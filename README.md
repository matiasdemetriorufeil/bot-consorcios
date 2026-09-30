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
con `source="live"`. Si ConsorPlus no responde en 6 segundos, devuelve la última deuda guardada con
`stale=True` y su fecha.

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
