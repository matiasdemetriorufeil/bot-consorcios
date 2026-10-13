# Relevamiento del panel (solo desarrollo)

Capturas y mediciones de todas las pantallas del panel a 1440, 1024 y 390 px, sobre una base de
demo con datos inventados. Necesita Chrome (la ruta sale de la variable `CHROME` o de la de
Windows); no corre en pytest ni en el contenedor.

1. En una terminal: `uv run python scripts/panel_survey/demo_server.py` (crea `<base>_panel_demo`).
2. En otra: `uv run python scripts/panel_survey/survey.py 5f4 [--axe] [--no-shots]`. Deja las
   capturas en `docs/panel-capturas/5f4/` (fuera de Git) y **sale con error** si alguna pantalla
   de la operadora se desplaza de costado (la página o una tabla) en algún ancho.
3. Al terminar: cortar el servidor y `uv run python scripts/panel_survey/demo_server.py --drop`.

Seguridad: solo toca una base cuyo nombre termina en `_panel_demo`, nunca usa las credenciales
de ConsorPlus (las ignora aunque estén en el entorno) y el WhatsApp es falso. axe-core se baja en
una carpeta temporal cada vez (`--axe`): no está en el repositorio.
