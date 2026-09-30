# bot-consorcios

Bot de WhatsApp con IA para **Estudio Diego Rufeil**, administración de consorcios de Córdoba
(~800 unidades en 53 consorcios). El bot identifica a los propietarios por teléfono, informa
deuda de expensas y cupones de pago, responde sobre el reglamento del edificio y deriva a
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
