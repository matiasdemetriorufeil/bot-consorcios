# CLAUDE.md — Reglas permanentes para Claude Code

## Contexto del proyecto

Vamos a construir un bot de WhatsApp con IA para una administración de consorcios de Córdoba
(Argentina), "Estudio Diego Rufeil": unas 800 unidades en 53 consorcios. El bot atiende a
propietarios por WhatsApp: los identifica por teléfono, informa deuda de expensas y cupones de
pago, responde sobre el reglamento del edificio y deriva a humanos. Los operadores usan Chatwoot.
Los datos salen del sistema de gestión "ConsorPlus" (una web ASP.NET WebForms sin API) mediante
scraping de SOLO LECTURA.

Stack: Python 3.12 (uv), FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL 16, pytest, ruff.
Todo corre con Docker Compose. Desarrollo en Windows con Docker Desktop.

## ⚠️ REGLA CRÍTICA: ConsorPlus es SOLO LECTURA

- ConsorPlus es un sistema **en producción con datos reales**. El código **SOLO puede leer**.
- **Nunca** enviar postbacks ni clicks de botones que guarden, generen, impriman, envíen,
  anulen o eliminen.
- Toda interacción con ConsorPlus pasa **exclusivamente** por el módulo `app/consorplus/`, que
  tiene una lista cerrada (allowlist) de acciones permitidas.
- **Nunca agregar acciones a esa allowlist sin que el usuario lo pida explícitamente.**
- Ante la mínima duda sobre si una acción modifica algo en ConsorPlus: no hacerla y preguntar.

## Datos sensibles

- Nunca escribir claves ni datos reales de propietarios (nombres, teléfonos, unidades, deudas,
  emails, etc.) en el código, los tests ni los commits.
- Los datos reales solo pueden estar en `private/` (ignorado por git).
- Tests y fixtures usan siempre datos inventados.

## Convenciones

- Código y nombres (variables, funciones, clases, tablas, archivos) en **inglés**.
- Textos que ve el usuario final en **español rioplatense (voseo)**: "tenés", "podés", "querés".

## Antes de terminar cada tarea

- Correr `ruff check .`, `ruff format --check .` y `pytest`, y que pasen.
- Si hay una duda de diseño, **preguntar antes de decidir**.

## Formato de respuesta al terminar cada tarea

Responder SIEMPRE con este formato:

```
RESUMEN
- Objetivo del paso:
- Archivos creados/modificados:
- Decisiones tomadas (y por qué):
- Comandos para probar:
- Resultado de tests y lint (pegar el final de la salida):
- Pendientes, dudas o riesgos:
```
