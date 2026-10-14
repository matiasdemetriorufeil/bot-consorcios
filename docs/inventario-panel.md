# Inventario del panel `/admin`

## Actualización 5g: «Pendientes» con el diseño nuevo (2026-10-13)

Solo cambia cómo se ve Pendientes; los números, los permisos y los links son los mismos.

- **Arriba:** el saludo va sobre el título, y el título es más grande. La línea de ayuda ahora
  dice «Todo lo que espera a alguien del estudio. Cada tarjeta te lleva a su lista.». Los
  avisos con sonido van en una tarjeta a la derecha del título («Avisos con sonido apagados» y
  «Activar»), así que en esta pantalla la barra de arriba no se ve en la computadora. En el
  celular sigue la campana.
- **Resumen:** cuántas tareas necesitan atención y cuántas listas están al día. El SUM se
  cuenta siempre como al día.
- **Tarjetas:**
  - van de a tres (de a dos por debajo de 1280 px y de a una en el celular);
  - cada una tiene un ícono, una etiqueta («Por revisar», «Urgente», «Al día» u «Hoy y
    mañana»), el número y el título;
  - la que tiene algo se marca con un borde petróleo, o rojo si es urgente;
  - en las que están en cero, el 0 se ve gris y el botón es blanco.
- **Textos:**
  - «Reclamos que necesitan atención» pasa a «Reclamos con atención»;
  - el SUM vacío dice «No hay turnos reservados para hoy ni para mañana.».
- **Administración:** un título y tres tarjetas chicas. La que tiene un problema se ve en rojo.
- **Capturas:** `docs/panel-capturas/5g/` (fuera de Git).
- **Teléfonos:** «A revisar» va en un solo renglón (a 1024 px su celda tiene menos margen, para
  que la tabla siga entrando).
- **Edificios** (`templates/building_list.html`, solo esa lista; el resto de SQLAdmin sigue
  igual):
  - el buscador tiene la lupa adentro, con «Buscar» en petróleo y «Borrar la búsqueda» como
    link (solo si hay una búsqueda);
  - los filtros Activo / Prueba piloto / Reclamos por el bot son botones Todos / Sí / No en la
    misma tarjeta, sin el panel de costado;
  - el nombre va en negrita y las columnas Sí / No están centradas;
  - «Ver» y «Editar» van uno al lado del otro, bajo «Acciones»;
  - al pie, «N edificios» y «Datos sincronizados desde ConsorPlus», con las páginas en el medio
    si hay más de 25 edificios;
  - ya no está el selector de cuántos edificios mostrar por página.
- **Información de edificios** (`templates/building_info_list.html`, con las mismas clases que
  Edificios):
  - el botón de arriba dice «+ Agregar información de edificio»;
  - el buscador y los filtros Edificio y Categoría van juntos: dos listas desplegables que se
    aplican con «Buscar» o Enter, y se mantienen el orden y la cantidad por página;
  - el edificio va como link en negrita y la categoría como etiqueta gris;
  - cada fila tiene «Ver», «Editar» y un tachito rojo que abre el mismo diálogo de borrar de
    SQLAdmin;
  - al pie, «N registros» y las páginas;
  - ya no se puede borrar varios a la vez (no había casillas en el diseño).

## Actualización 5f.4: ninguna tabla se desplaza dentro de su tarjeta (2026-10-13)

**5f.3, en una línea:** un 403 de permiso (el JSON de `errors.py`) ya no se toma como sesión
vencida: sale el aviso «Esta acción es solo para administradores.» sin recargar ni perder lo
escrito. Además, el motivo de un reclamo («El proveedor no puede atenderlo») va en su propio
renglón, sin barra horizontal.

En 5f.4 no cambia qué hace el panel, solo cómo se acomodan las tablas.

- **Teléfonos y Verificaciones a 1024 px:** los botones de la fila van uno arriba del otro,
  cada uno entero, entre 768 y 1279 px, y las celdas tienen 12 px de margen a los costados. No
  se esconde ninguna columna.
- **Listas de SQLAdmin:**
  - los filtros van al costado solo desde 1280 px. Por debajo quedan plegados arriba y, si se
    abren, siguen abiertos al cambiar de página, ordenar o buscar en la misma lista;
  - Ver y Editar van uno arriba del otro;
  - los encabezados largos se parten en dos renglones;
  - cada lista declara sus columnas secundarias (`column_secondary`), que se esconden por
    debajo de 1280 px y siguen en la ficha:

  | Lista | Se esconde por debajo de 1280 px |
  |---|---|
  | Edificios | Dirección |
  | Información de edificios | Actualizado |
  | Proveedores | Edificios, Problemas |
  | Tipos de problema | Orden, Título en la lista |
  | Sincronizaciones | Tipo, Terminó |
  | Plantillas | Idioma |

  - Proveedores, de 1280 a unos 1550 px y con los filtros al costado, todavía no entra:
    se desplaza dentro de su tarjeta con la primera columna fija y una sombra a la derecha que
    muestra que hay más.
- **Celular:** Usuarios y las dos pantallas de «Reclamos por edificio» pasan a tarjetas de
  fila, como las demás listas. Los encabezados largos de «Proveedores del mes» (Métricas) se
  parten en dos renglones.
- **Medición fija** (`scripts/panel_survey/`, solo desarrollo, con datos inventados):
  - `demo_server.py` levanta el panel sobre `<base>_panel_demo`. Se niega a tocar cualquier
    base que no termine en `_panel_demo` e ignora las credenciales de ConsorPlus; `--drop`
    borra la base de demo;
  - `survey.py <carpeta> [--axe]` saca capturas a 1440, 1024 y 390 px, mide el desplazamiento
    de la página y de las tablas, y **sale con error** si alguna pantalla de la operadora se
    desplaza. axe-core se baja a una carpeta temporal cada vez; no está en el repositorio.
  - El README de la carpeta dice cómo correrlo.

## Actualización 5f.2: ingreso, errores, «Agregar a inicio» y cierre de 5f.1 (2026-10-13)

No cambian los permisos, las URLs de las pantallas ni lo que se manda a nadie.

- **Ingreso** (`templates/sqladmin/login.html`, ahora nuestra):
  - el logo «DR» y «Estudio Diego Rufeil» arriba, la tarjeta centrada;
  - el foco en el usuario al abrir; `autocomplete="username"` y `"current-password"`;
  - Enter manda;
  - el error, una sola vez arriba del botón, sin marcar ningún campo (no dice cuál falló);
  - debajo, «¿Te olvidaste la contraseña? Pedile a un administrador que te la cambie.».
- **Páginas de error** (`app/admin/errors.py`, `templates/sqladmin/error.html`):
  - 404 «No encontramos esa página», 403 (el texto de siempre) y 500 «Algo salió mal…»,
    todas con «Volver a Pendientes»;
  - sin sesión, no muestran el menú;
  - el detalle técnico de un 500 se muestra solo con `APP_ENV=development` exacto. El error
    queda una sola vez en el log, con su traceback;
  - los pedidos de `panel.js` (`X-Panel-Fetch`) reciben JSON con el mismo código de siempre;
  - los 404 de texto plano de las vistas propias («Reclamo inexistente»…) pasaron a esta
    página.
- **Título de la pestaña:** «(N) Pantalla · Panel · Estudio Diego Rufeil» en todas: las
  propias, las de SQLAdmin, las de error y el ingreso. El servidor ya lo dibuja con el número,
  que sale de los contadores del menú, así que no suma consultas.
- **«Agregar a inicio»:**
  - `static/manifest.webmanifest` («Panel · Estudio Diego Rufeil» / «Estudio Rufeil»,
    `start_url` `/admin/`, `standalone`, colores `--c-nav-bg` y `--c-bg`);
  - íconos en `static/icons/`: 32, Apple 180, 192 / 512 y maskable 192 / 512;
  - `theme-color` del color de la barra;
  - sin service worker;
  - en pantalla completa no hay botón Atrás: cada ficha tiene su «← Volver» y en el celular
    está la barra de abajo. La guía avisa que en el iPhone hay que ingresar de nuevo la primera
    vez.
- **Entorno:**
  - fuera de producción, la etiqueta «Desarrollo» al lado del nombre;
  - si falta `APP_ENV`, también aparece, como alarma. **En el servidor hay que poner
    `APP_ENV=production`**;
  - el Chat de prueba sigue solo en desarrollo.
- **Estáticos:**
  - todos desde el servidor, con `?v=` igual al hash de su contenido (calculado al arrancar):
    un cambio llega solo, sin borrar caché;
  - con `?v=`, caché de un año (`immutable`); sin `?v=`, se revalida;
  - gzip solo para CSS, JS, SVG y el manifest: las páginas no se comprimen, por BREACH;
  - ya no se cargan los íconos de Tabler (209 KB), y select2 / flatpickr solo en los
    formularios de SQLAdmin;
  - medido con la caché vacía, Pendientes pasó de unos 17 pedidos y 1,4 MB a 14 pedidos y
    306 KB.
- **Accesibilidad:**
  - `lang="es"`;
  - «Saltar al contenido» con el primer Tab;
  - un solo `<h1>` (el título de la pantalla) y los títulos de tarjeta en `<h2>`;
  - `nav`, `main`, `header` y los `aside` con nombre;
  - los encabezados de columna de botones con texto para lector de pantalla;
  - el chat se puede desplazar con el teclado;
  - axe-core, corrido desde el script de verificación en todas las pantallas a 1440 y 390 px,
    no encuentra problemas.
- **Lo que quedó de 5f.1:**
  - Rechazar y Desvincular con contorno rojo;
  - los estados no se parten en el celular;
  - Métricas sin datos muestra «Sin conversaciones en estos 30 días»;
  - «Desde el…» quedó abajo de «Reclamos del mes»;
  - el menú lateral ya ocupaba todo el alto (lo de 5f.1 era solo la captura de página
    completa).
- **Capturas:** `docs/panel-capturas/5f2/`, fuera de Git.

## Actualización 5f.1: una base visual única para el panel (2026-10-13)

Solo cambia cómo se ve. Quedan igual las URLs, los permisos, los textos de negocio y lo que se
le manda a cualquiera. No se suman librerías ni fuentes: todo anda sin internet.

- **Una base en `panel.css`:** todos los colores, tamaños, espacios, radios, sombras y tiempos
  son variables de `:root`. Fuera de ese bloque no hay hex, `rgb` ni `rgba`, y tampoco
  `font-size`, radios ni sombras escritos a mano. Tabler (el tema de SQLAdmin) toma las mismas
  variables (`--tblr-*` → `--c-*`). Lo controla `tests/admin/test_look.py`.

  | Grupo | Variables |
  |---|---|
  | Principal | `--c-primary #1f5f7a`, `--c-primary-hover #174a5f`, `--c-primary-soft #e7f0f4`, `--c-primary-border`, `--c-on-primary #fff` |
  | Foco | `--c-focus #1f5f7a` (en fondos claros), `--c-focus-on-dark #8fd0ea` (menú oscuro y barra de arriba del celular) |
  | Neutros | `--c-bg #f4f6f8`, `--c-surface #fff`, `--c-surface-2 #f8f9fa`, `--c-surface-3 #eef1f4`, `--c-border #dfe4e8`, `--c-border-strong #c6ced6`, `--c-text #1f2933`, `--c-text-2 #4f5b67`, `--c-placeholder #66717d` |
  | Estados | `--c-ok #1e6b34` / `-bg #e6f4ea` · `--c-progress #1f5f7a` / `-bg #e7f0f4` · `--c-waiting #96410a` / `-bg #fff1e6` · `--c-urgent #fff` / `-bg #b42318` · `--c-info #4b5563` / `-bg #eef0f2` · contador que espera `--c-count-waiting #3d2c00` sobre `#f0b429` |
  | Peligro | `--c-danger #b42318`, `--c-danger-hover #8f1c13`, `--c-danger-soft #fdecec` |
  | Menú | `--c-nav-bg #17323d`, `--c-nav-text`, `--c-nav-text-2`, `--c-nav-active-bg #24495a`, `--c-nav-accent` |
  | Chat y planilla del SUM | `--c-chat-*`, `--c-msg-*`, `--c-phone-*`, `--c-slot-*`, `--c-grid-*` |
  | Letra | `--font-sans` (la del sistema), `--fs-title` 22 px, `--fs-subtitle` 17 px, `--fs-body` 15 px, `--fs-small` 13 px, `--fs-display` (números grandes), `--fs-icon`; `--fw-regular 400`, `--fw-bold 600` |
  | Espacio | `--sp-1` a `--sp-6`: 4 / 8 / 12 / 16 / 24 / 32 px |
  | Forma | `--radius-sm 6px` (botones, campos), `--radius-md 10px` (tarjetas, diálogos); `--shadow-1` (tarjetas), `--shadow-2` (diálogos, avisos, «Más») |
  | Botones | `--btn-h 38px`, `--btn-h-sm 32px`, `--btn-h-touch 44px` (todos en el celular) |
  | Movimiento | `--t-fast 150ms`, `--t-base 200ms`; en 0 con `prefers-reduced-motion` |

  El `html` mide 16 px (medido en el navegador), así que los rem dan 15 / 13 / 17 / 22 px reales.

- **Contraste AA:** un test calcula cada par de texto y fondo (4,5:1) y el anillo de foco contra
  los fondos donde aparece (3:1). Cambiaron los pares que no llegaban:
  - el verde de «no leídos»;
  - «Al día»;
  - «Disponible» en la planilla del SUM;
  - el gris del chat;
  - el placeholder;
  - el naranja de «esperando».
- **Foco con teclado:** un anillo de 3 px separado 2 px del elemento, en links, botones,
  `summary`, campos, interruptores y casillas.
  - Es petróleo en lo claro y claro en el menú oscuro y la barra de arriba del celular.
  - La barra de abajo y «Más» son blancas, así que llevan el anillo petróleo, metido hacia
    adentro en los ítems de ancho completo.
- **Botones:** principal, secundario, peligro y link, en 38 o 32 px (44 en el celular), con un
  solo radio y el mismo espacio entre ícono y texto. «Guardar y seguir editando» y los demás
  contornos pasaron a secundario. Los «Eliminar» repetidos (turnos del SUM, filas de SQLAdmin)
  ahora son links rojos: el diálogo de confirmación no cambia.
- **Encabezado común:** el botón principal de cada pantalla (Nuevo reclamo, «+ Agregar» de
  SQLAdmin) va al lado del título; en el celular, abajo y a lo ancho.
- **Tablas:**
  - fila de 48 px, encabezado chico gris y hover suave;
  - números a la derecha con cifras parejas;
  - en escritorio, el texto largo se corta con «…» (el texto completo queda en `title`); en el
    celular va completo.
- **Tarjetas:** las mismas en todo el panel. Ya no se estiran al alto de la de al lado
  (Usuarios, Configuración del SUM, ficha del reclamo).
- **Estado vacío:** un solo bloque (macro `empty_state`: ícono, la frase de `help.py` y, si
  hace falta, un botón), en Conversaciones, Reclamos, Teléfonos, Verificaciones, SUM, Reclamos
  por edificio y las listas de SQLAdmin.
- **SQLAdmin:**
  - formularios con la etiqueta arriba, sin caja gris y con los interruptores alineados;
  - errores en rojo debajo del campo, con `aria-describedby` y `aria-invalid`;
  - una sola marca de obligatorio (« *» rojo);
  - listas sin el título repetido y con el buscador, la paginación y los filtros al estilo del
    panel;
  - fichas en dos columnas (`dl`), sin la doble caja.
- **Menú:**
  - el ítem activo con fondo y una barra clara a la izquierda;
  - aire entre los bloques;
  - al pie, quién entró y «Salir»: el admin de rescate aparece como «Administrador»;
  - en el celular, el nombre va dentro de «Más», arriba de Salir.
- **Métricas:**
  - los números del mes en tarjetas como las de Pendientes;
  - las conversaciones por día en un gráfico compacto de 30 columnas, con un `aria-label`
    (total, el día con más y el día con menos);
  - debajo, «Ver por día» plegable con la tabla.
- **Ingreso:** el logo arriba del título.
- **Capturas:**
  - antes: `docs/panel-capturas/5f1-antes/`;
  - después: `docs/panel-capturas/5f1/`, con los mismos nombres más las de 1024 px;
  - el recorrido con teclado: `teclado-*.png`;
  - las imágenes de la guía se rehicieron con la base de demo.

## Actualización 5e.4: el menú del admin ordenado y la guía fácil de consultar (2026-10-12)

Sin cambios de URLs, permisos ni reglas.

- **«Administración» en 4 bloques plegables**, en este orden:

  | Bloque | Pantallas |
  |---|---|
  | Edificios | Edificios, Información de edificios, Reclamos por edificio |
  | Reclamos | Proveedores, Tipos de problema, Configuración de reclamos |
  | Bot | Configuración del bot, Respuestas rápidas, Plantillas de WhatsApp |
  | Sistema | Usuarios, Métricas, Sincronizaciones, Chat de prueba (solo en desarrollo) |

  - Cada vista de admin dice su bloque (`menu_group`). El orden está en
    `app.admin.MENU_GROUPS` y `VIEWS`.
  - El bloque de la pantalla abierta llega abierto desde el servidor.
  - Los demás quedan como la usuaria los dejó: `localStorage["panel-menu-groups"]`, con
    try/catch, compartido por el menú y «Más».
  - Los mismos bloques aparecen en «Más» del celular.
  - La operadora no ve ningún cambio: no tiene pantallas de admin, así que no se dibujan ni el
    título ni los bloques.
- **Guía** (`docs/guia-empleadas.md`) **en dos partes, con índice arriba:**
  - **Todos los días:** Empezá el día en Pendientes; Cómo es un día (nuevo: al llegar, durante
    el día, antes de irte, reglas); Conversaciones; Responder cuando pasó más de un día;
    Reclamos (corta: qué ves, el botón principal, cargar uno, cerrarlo); Teléfonos;
    Verificaciones; Reservas del SUM.
  - **Si pasa algo raro:** Cuando algo no anda; Reclamos: el proveedor y los avisos (el horario,
    el recordatorio, los avisos al estudio, si no contesta o no puede, los urgentes), movido
    desde Reclamos.
- **Conversor** (`app/admin/guide.py`):
  - **Anclas:** cada parte y cada sección lleva un `id` hecho con su título (`slug`: sin
    acentos, en minúsculas, con guiones, y `-2` si se repite). El índice (`guide_contents`) usa
    las mismas.
  - **Links internos:** permite `[texto](#ancla)`; lo demás sigue escapado.
  - **Imágenes:** llevan `width` y `height` (leídos del PNG), así un link a una sección cae en
    ella aunque las imágenes de arriba carguen después.
  - **Arreglos de paso:**
    - un ítem de lista que sigue en el renglón de abajo con 2 espacios se partía en un
      párrafo aparte;
    - la negrita que cruzaba el salto de renglón se rompía.
- **«Ver en la guía» →** al final de la línea de ayuda de cada pantalla de la operadora
  (Pendientes, Conversaciones, Reclamos, Teléfonos, Verificaciones, SUM): abre la guía en su
  sección (`help.PAGE_GUIDE`). Las pantallas de admin no lo llevan.
- **Celular:** la guía va en una columna, con las imágenes al ancho de la pantalla y el índice
  arriba.
- **Imágenes de la guía:** se rehicieron las que muestran la línea de ayuda (01–07 y 10–12).
  La 08 y la 09 no cambian.
- **Capturas:** `docs/panel-capturas/5e4/`, fuera de Git. Base de demo aparte, borrada al
  terminar.

## Actualización 5e.3: panel ágil, cómodo en el celular, y detalles visuales (2026-10-12)

No cambia ninguna regla de negocio ni lo que se le manda a nadie, salvo el plural de «hace N»
y «hasta N días antes».

- **Acciones sin recargar** (`app/admin/fetch.py`):
  - **Dónde:** Teléfonos (Aprobar, Aprobar seleccionados, Desvincular), Verificaciones
    (Rechazar) y la ficha del reclamo (todas sus acciones y la nota interna).
  - **Cómo:** el formulario lleva `data-fetch` y `panel.js` lo manda con la cabecera
    `X-Panel-Fetch: 1`. El mismo endpoint, con las mismas validaciones y la misma auditoría,
    responde JSON con:
    - el texto del aviso;
    - las filas que se van (con una transición corta);
    - los bloques redibujados por el servidor (`_phone_row.html`; `_claim_detail.html`, que
      es `#claim-page`);
    - los contadores.
  - Enseguida se piden los números de 5e.1, y los contadores del menú cambian sin esperar.
  - **Sin la cabecera** (sin JavaScript), todo sigue igual: `Flash` y redirección.
  - **Si no se sabe cómo terminó** (red cortada, error del servidor): nunca se reenvía.
    Queda el aviso «No se pudo confirmar si se guardó. Recargá la página para ver cómo
    quedó.» con «Recargar», y los botones vuelven a estar habilitados. La sesión vencida sigue
    el camino de 5e.1.
- **Avisos chicos abajo** (`#panel-toasts`, `role="status"`): los `Flash` de todas las páginas
  (también SQLAdmin) y los de las acciones sin recargar.
  - Los de éxito se van a los 4 s.
  - Los de error quedan hasta tocar «×» (`role="alert"`).
- **«Guardando…»:** todo formulario POST (también Conversaciones, SUM y SQLAdmin) deshabilita
  sus botones mientras se manda; el redactor dice «Enviando…». La confirmación ahora reenvía
  el botón que se tocó (su `name`/`value`).
- **Celular (< 992 px):**
  - **Barra de arriba de 56 px:** logo, «Estudio Rufeil», la campana de avisos (tachada si
    están apagados: tocarla los activa) y el menú. «Salir» pasó a «Más». La franja de avisos
    apagados no se ve en el celular; la de sesión vencida, sí.
  - **Barra fija abajo,** para los dos roles: Pendientes, Conversaciones, Reclamos y «Más»,
    con los contadores de 5e.1.
  - **«Más»** sube desde abajo, con Teléfonos y Verificaciones (con contadores), Reservas de
    SUM, Guía, «Administración» (solo el admin) y Salir.
  - **Que no tape nada:** la página deja el lugar de la barra. En una conversación abierta, el
    título se oculta y el chat ocupa la pantalla entre las dos barras, con el redactor arriba
    de la de abajo.
  - **Para tocar:** botones, campos, pestañas y el menú de al menos 44 px.
  - **Títulos** más chicos.
  - **Revisé las 15 pantallas** de la operadora a 390 px: nada se sale del ancho y no queda
    nada de menos de 44 px.
  - A 1024 px se ve como en escritorio. La tabla de Reclamos va en un renglón por reclamo
    recién desde 1280 px; antes se cortaba a 1024.
- **Un solo componente de estado** (`panel.css` `.state` + `labels.STATE` + el filtro Jinja
  `state`):
  - verde, resuelto o al día;
  - azul, en curso;
  - naranja, espera a alguien del estudio;
  - rojo, urgente;
  - gris, cerrado o informativo.

  Se usa en Conversaciones, Reclamos, Teléfonos, Verificaciones, SUM, Usuarios y Reclamos por
  edificio. Los contadores son `.count` (`count-waiting`, `count-urgent`). Se borraron
  `claim-open/progress/closed`, `chat-status-*`, `phone-status-*`, `menu-badge-*` y los
  `bg-*-lt` de las etiquetas.
- **Pendientes:**
  - el saludo va arriba de la línea de ayuda;
  - con «Todo al día», las tarjetas en cero son una línea cada una (ícono, nombre, «Al día»,
    «Ver»).
- **Plural:**
  - `formatting.ago`: «hace 1 hora», «hace 3 horas», «hace 1 día»;
  - los avisos de reclamos: «Sin solucionar hace 1 día», en `app/claims/texts.py`;
  - el SUM: «hasta 1 día antes», en `app/amenities/booking.py`.
- **Guía:** las 12 imágenes rehechas (la de Conversaciones ya muestra «Cargar reclamo»), y un
  párrafo sobre el celular y las acciones sin recargar.
- **Capturas:** `docs/panel-capturas/5e3/` (fuera de Git), a 1440, 1024 y 390 px. Base de demo
  aparte, borrada al terminar.

## Actualización 5e.2: Reclamos más fáciles de seguir y de cargar (2026-10-12)

No cambian las reglas de `app/claims/service.py` ni lo que se les manda a proveedores o
vecinos. Lo único nuevo que se escribe es la nota interna en la conversación desde la que se
carga un reclamo.

- **Lista de Reclamos:**
  - **Se actualiza sola.** El pedido único de 5e.1 trae `claims_stamp` (última actualización y
    último evento de los reclamos, sin datos de nadie). Si cambia, `panel.js` vuelve a pedir la
    misma URL con `&partial=1` y reemplaza las pestañas, la tabla y el pie (se mantienen la
    pestaña, los filtros, la búsqueda y la página). No reemplaza mientras se escribe en el
    buscador, si hay algo cambiado sin aplicar o con los filtros abiertos: lo hace después.
  - **Los filtros** quedan siempre plegados; el resumen dice cuáles están aplicados («Filtros:
    Edificio, Estado»).
  - **Columnas:** N.º (+ Urgente), Fecha, **Edificio y unidad** («TORRE EJEMPLO · 02-B» o «… ·
    todo el edificio»), Problema, Quién reclamó (+N), Lo atiende, Estado. Un renglón por
    reclamo en escritorio.
  - **Estado:** etiqueta corta («Falta avisar», «Avisado», «Confirmado», «Lo tiene el
    estudio», «Solucionado», «Cancelado»), con el texto largo al pasar el mouse. Debajo, hace
    cuánto y el motivo de atención. Los dos textos salen de un solo diccionario,
    `labels.CLAIM_STATUS_TEXT`.
- **Ficha del reclamo, «Qué hacer»:** un botón principal según el estado y lo demás en
  «Otras acciones» (`claims.claim_actions`).

  | Estado | Botón principal | Otras acciones |
  |---|---|---|
  | Falta avisar (con WhatsApp) | Reenviar al proveedor · fuera de horario «Mandar al proveedor» (ahora o en el próximo horario) · programado «Mandar ahora» | Marcar como avisado, Cambiar quién lo atiende, Cerrar, Cancelar |
  | Falta avisar (sin WhatsApp) | Marcar como avisado | Cambiar quién lo atiende, Cerrar, Cancelar |
  | Avisado | — («Esperando que X confirme · avisado hace …» y el recordatorio) | Cerrar, Cambiar quién lo atiende, Cancelar |
  | Confirmado | Cerrar como solucionado | Cambiar quién lo atiende, Cancelar |
  | Lo tiene el estudio, tuvo proveedor (no puede, se lo sacaron, el problema tiene proveedor en el edificio) | Elegir proveedor | Cerrar, Cancelar |
  | Lo tiene el estudio por diseño | Cerrar como solucionado | Elegir proveedor, Cancelar |

  - Las cajas (el motivo para cerrar o cancelar, el proveedor, cuándo mandar) aparecen dentro
    del diálogo de confirmación. «Confirmar» no avanza con el motivo vacío y el servidor lo
    sigue validando («Contá el motivo.»).
  - Confirmaciones nuevas: `close_claim` (les avisa por WhatsApp a quien reclamó y a los que se
    sumaron), `mark_sent`, `send_claim_now`, `send_claim_choose`,
    `change_claim_provider_hours`.
- **«Cargar reclamo» desde Conversaciones** (ficha del contacto, también la plegable del
  celular; nunca para un proveedor): abre «Nuevo reclamo» con `?conversation=<id>`.
  - **Una unidad:** la persona ya está elegida.
  - **Varias:** ella elige la unidad.
  - **Número de nadie:** «No está en el padrón», con el teléfono y el nombre de WhatsApp.
  
  El reclamo queda con `wa_conversation_id` y en la conversación se anota «Se cargó el
  reclamo #N.» (o «Se sumó al reclamo #N.»), con link (`/admin/claims/number/N`).
- **Al vecino** no le llega nada al cargarlo desde el panel. Después, como con los del bot, le
  llega «la empresa confirmó» y «está solucionado». No hay mensajes nuevos.
- **«Nuevo reclamo» empieza por la persona:**
  - **«¿Quién reclama?»** busca en el padrón por nombre, unidad o teléfono, con los formatos de
    Teléfonos (`GET /admin/claims/people`). Desde 2 letras y 350 ms después de la última tecla.
  - **Al elegir**, el resto del formulario llega sin recargar (`GET /admin/claims/new/form`).
  - **«No está en el padrón»:** edificio, unidad opcional, nombre y teléfono.
  - Se sacó el desplegable largo de personas.
  - Sin JavaScript funciona igual, recargando.
- **Guía:** sección 6 actualizada, con tres imágenes nuevas (`10-reclamos-lista.png`,
  `11-reclamo-que-hacer.png`, `12-nuevo-reclamo.png`).
- Capturas: `docs/panel-capturas/5e2/`, fuera de Git. Base de demo aparte, borrada al terminar.

## Actualización 5e.1: «Pendientes», contadores y avisos en todo el panel (2026-10-12)

**Menú (los dos roles):** Pendientes, Conversaciones, Reclamos, Teléfonos, Verificaciones,
Reservas de SUM, Guía (y, para el admin, «Administración» abajo). `/admin/` y el ingreso abren
**Pendientes** (antes, Conversaciones).

| Pantalla | URL | Tipo | Quién | Qué tiene |
|---|---|---|---|---|
| Pendientes | `/admin/pending` | Propia | admin y operadora | Saludo con el nombre y la fecha («Buen día, Marta · lunes 12/10»). Una tarjeta por cosa que espera, con su número, una frase y un botón a la lista ya filtrada: **Esperando persona** (cuántas urgentes y hace cuánto espera la más vieja → `?tab=waiting`), **Mías sin responder** (tomadas por la usuaria con el último mensaje del contacto, sin contar las notas internas → `?tab=mine`), **Reclamos que necesitan atención** (con motivo de aviso o urgentes abiertos → `?tab=open&attention=yes`), **Teléfonos a revisar**, **Verificaciones pendientes** (fecha de la más vieja) y **Reservas del SUM** de hoy y mañana (informativa, link a la planilla de cada edificio en ese día). En cero, la tarjeta se ve gris y dice «Al día»; todo en cero (sin contar el SUM): «Todo al día» grande. Para el admin, una fila chica: última sincronización nocturna (lo peor de las últimas 36 h, en rojo si falló, tuvo errores o no hubo), proveedores activos sin WhatsApp con algún edificio y preguntas que el bot no supo responder esta semana (derivaciones «Sin respuesta» desde el lunes). Las tarjetas se redibujan solas. En el celular, una columna. |

- **Un solo pedido de números** (`GET /admin/pending/numbers`, JSON, `no-store`): lo hace
  `panel.js` cada 5 s en **todas** las pantallas del panel. Trae los números, textos cortos ya
  formateados, ids y las alertas de reclamos que ya mostraban los avisos (título, problema y
  edificio). No trae nombres, teléfonos ni unidades. La fila del admin solo se calcula y se manda
  para el admin. Con `?cards=1` (solo Pendientes) suma las tarjetas ya armadas.
  - **Una sola pestaña** hace el pedido (candado `panel-poll` del navegador) y les pasa los
    números a las demás por `BroadcastChannel`. Si se cierra, otra pestaña lo toma. Una pestaña
    visible que no recibe números en 12 s los pide ella.
  - **Sesión vencida:** solo un 401, un 403 o terminar en `/admin/login` cuentan como sesión
    vencida. Un 500 o un corte de red se reintentan sin recargar. Si hay texto sin enviar en un
    formulario, no recarga: muestra la franja «Se cerró tu sesión: copiá tu mensaje y volvé a
    entrar» con «Entrar» (se abre en otra pestaña). Vale también para el poll de la bandeja.
- **Se borró** `GET /admin/claims/poll`. El poll de Conversaciones trae solo la lista y la
  conversación abierta (los contadores de sus pestañas siguen en la lista).
- **Contadores en el menú**, al lado del nombre y actualizados solos: Conversaciones
  (esperando), Reclamos (necesitan atención), Teléfonos (a revisar) y Verificaciones
  (pendientes). Rojo si hay urgentes, amarillo si no.
- **Título de la pestaña:** «(N) Panel · Estudio Diego Rufeil», con N = esperando + reclamos
  urgentes.
- **Avisos con sonido desde cualquier pantalla:**
  - una conversación nueva en «Esperando persona» (vuelve a avisar si se deriva de nuevo);
  - un reclamo urgente nuevo;
  - un reclamo que pasa a necesitar atención;
  - un mensaje nuevo en una conversación de la usuaria, salvo que la esté mirando.
  
  Lo ya avisado se guarda en el navegador (`localStorage`, compartido por las pestañas y bajo
  un candado), así un aviso suena una sola vez, aunque se cambie de pantalla o haya dos
  pestañas.
- **Barra arriba de todas las pantallas:**
  - el botón «🔔 Activar avisos» / «🔔 Avisos activos» (salió de Conversaciones y de Reclamos);
  - si el navegador no dio permiso, la franja «Los avisos con sonido están apagados. Activar»;
  - si lo bloqueó, cómo desbloquearlo.
  
  El sonido se habilita solo con el primer clic o tecla en cada página.
- **Reclamos:** el filtro «Atención» dice «Necesitan atención (incluye urgentes)» y lista
  también los urgentes abiertos, así la tarjeta, el menú y la lista dan el mismo número.
- **Conversaciones:** `?reason=no_answer` (desde la fila del admin) lista las derivadas por
  «Sin respuesta» desde el lunes, en cualquier estado, con «Quitar filtro». El recorrido no
  cambia: la primera vez sigue llevando al ejemplo al abrir Conversaciones.
- **Guía:** sección «Empezá el día en Pendientes» al principio.
- Capturas: `docs/panel-capturas/5e1/`, fuera de Git. Base de demo aparte con datos inventados,
  borrada al terminar.

## Actualización 8.6: horario de proveedores, recordatorios y avisos (2026-10-08)

- **Configuración de reclamos** (Administración, solo admin, `/admin/claim-settings/list` abre
  el formulario de la única fila): días con casillas Lunes…Domingo, horario desde/hasta,
  feriados (una fecha por renglón, ej. 24/12/2026), «Urgentes a cualquier hora», recordatorio
  (horas de horario / minutos si es urgente), aviso al estudio (ídem) y días sin solucionar.
  Valida en el servidor (HH:MM, desde < hasta, al menos un día, fechas, números desde 1, aviso
  después del recordatorio). Se usa sin reiniciar (caché de 60 s, en el mismo proceso al
  instante). Auditado (`claim_settings_changed`, solo nombres de campos).
- **Ficha del reclamo:** programado → «Se le manda a X mañana a las 8:00» + «Mandar ahora»;
  fuera de horario «Reenviar» y «Cambiar quién lo atiende» ofrecen «ahora» o «en el próximo
  horario (cuándo)»; «Recordatorio enviado …»; el motivo del aviso («El proveedor no confirmó»,
  «Sin solucionar hace N días», «Lo tiene el estudio hace N días», «Todavía no se le avisó al
  proveedor»).
- **Lista de reclamos:** filtro «Atención → Necesitan atención» y el motivo bajo el estado.
- **Avisos del panel:** los nuevos motivos suenan y cuentan como los urgentes.
- **Métricas:** «Reclamos del mes» por estado, edificio y problema; por proveedor: enviados,
  horas promedio hasta «Recibido» y hasta solucionado, «no pudo atenderlo» y con recordatorio.
- **Chat de prueba:** «Tareas de reclamos» con «Como si fueran las» (fecha y hora) y «Correr
  tareas de reclamos ahora».
- **Servidor:** tarea cada 5 minutos (`app/claims/jobs.py`): programados, recordatorio
  (`reclamo_recordatorio_proveedor`) y avisos; lock de Postgres y `FOR UPDATE SKIP LOCKED`.

## Actualización 8.5: el proveedor por WhatsApp (2026-10-08)

- **Ficha del reclamo:** «Reenviar al proveedor» (falta avisar + proveedor con WhatsApp, con
  confirmación), «Marcar como avisado» (avisado por teléfono), aviso «Este proveedor no tiene
  WhatsApp cargado…», alerta si no se le pudo avisar o dijo que no puede. «Cambiar quién lo
  atiende» le manda el reclamo al nuevo (la confirmación lo dice); «Cerrar como solucionado»
  les avisa a los vecinos; «Cancelar» no. La historia muestra cada aviso con su entrega
  (enviado, entregado, leído, no se pudo entregar).
- **Avisos del panel:** además de los urgentes, suenan y cuentan en el menú los reclamos que el
  proveedor no puede atender y los que no se le pudieron mandar.
- **Conversaciones:** pestaña «Proveedores» (estado nuevo «Proveedor»: el bot nunca contesta),
  etiqueta «Proveedor · empresa» y, en la ficha del contacto, sus reclamos abiertos.
- **Chat de prueba:** «Hablar como proveedor» (proveedores activos con WhatsApp): sus mensajes
  quedan simulados y se ven las plantillas con sus botones, que se pueden tocar.

## Actualización 8.3: reclamos por WhatsApp (2026-10-08)

- **Reclamos urgentes:** Conversaciones y Reclamos (lista y ficha) avisan con sonido y
  notificación del navegador cuando entra un reclamo urgente abierto, y el menú muestra al lado
  de «Reclamos» un contador rojo de urgentes abiertos (en todas las pantallas). El botón
  «🔔 Avisos» está en Conversaciones y en Reclamos. La conversación del vecino no se deriva.
- **Bot:** el vecino registra el reclamo por WhatsApp con botones y listas (el modelo solo lo
  arranca). El menú del saludo es una lista «Ver opciones»: Mi deuda, Registrar reclamo, Mis
  reclamos, Info del edificio, Reservar el SUM, Hablar con alguien.

## Actualización 8.2: reclamos (2026-10-08)

**Menú de la operadora:** Conversaciones, Reclamos, Teléfonos, Verificaciones, Reservas de SUM,
Guía. El admin ve lo mismo y, abajo, «Administración». La página «Próximamente» de Reclamos
(5b.4) se borró. Todavía el bot no crea reclamos ni se le manda nada a nadie.

| Pantalla | URL | Tipo | Quién | Qué tiene |
|---|---|---|---|---|
| Reclamos | `/admin/claims` | Propia | admin y operadora | Pestañas Abiertos (por defecto), Cerrados y Todos, con la cantidad. Búsqueda por número («1003» o «#1003»), unidad, nombre o parte del teléfono (también de los vecinos que se sumaron). Filtros plegables: edificio, problema, quién lo atiende (proveedor o «El estudio»), estado, urgente. Columnas: N.º (+ «Urgente»), Fecha, Edificio, Unidad o «Todo el edificio», Problema, Quién reclamó (+N), Lo atiende, Estado (naranja: abierto; azul: en curso; gris: cerrado) y hace cuánto está así. Urgentes abiertos primero, después los más nuevos. En el celular, tarjetas. Botón «Nuevo reclamo». |
| Reclamo | `/admin/claims/{id}` | Propia | admin y operadora | Datos, descripción y respuesta a la pregunta extra, quién reclamó y los vecinos sumados (con su unidad y «Ver conversación» si escribieron por WhatsApp), fotos, reclamo anterior y la historia. Si está abierto: «Cambiar quién lo atiende», «Cerrar como solucionado» (con motivo), «Cancelar reclamo» (con motivo y confirmación). Siempre: «Nota interna». |
| Nuevo reclamo | `/admin/claims/new` | Propia | admin y operadora | Primero el edificio; después el problema (solo los habilitados ahí), la unidad (obligatoria si el problema es de una unidad), qué pasa y quién reclamó (del padrón, filtrado por la unidad, u otra persona con nombre y teléfono opcional). Si ya había uno abierto del mismo problema en el edificio, la persona se suma a ese y se abre ese. |

- **Conversaciones:** la ficha del contacto tiene un bloque «Reclamos» con sus últimos 5
  (número, problema, estado y link).
- **Reglas:** todas en `app/claims/service.py`; cada cambio queda en la historia del reclamo y
  en el registro de acciones del panel.
- Capturas: `docs/panel-capturas/8-2/`, fuera de Git.

## Actualización 8.1: configuración de reclamos (2026-10-07)

Tres pantallas nuevas en «Administración», solo admin (la operadora no las ve en el menú y, si
escribe la URL, recibe la página de 403 del panel; los POST también lo chequean en el
servidor). Todavía no hay reclamos ni el bot los toma: es solo la configuración.

| Nombre visible | URL | Tipo | Notas |
|---|---|---|---|
| Proveedores | `/admin/provider/list` | SQLAdmin | Empresa, contacto, WhatsApp (formato «351 555-0101»), edificios y problemas que atiende, activo. Búsqueda por empresa, contacto, email o parte del número. Sin borrar: se desactiva. El WhatsApp se carga en cualquier formato (se guarda normalizado) y no puede repetirse entre proveedores activos. Si el número es de un propietario o inquilino, o de un contacto de Conversaciones, avisa sin bloquear. Si se desactiva uno asignado, avisa en cuántos edificios estaba. |
| Tipos de problema | `/admin/claim-category/list` | SQLAdmin | Orden, problema, título en la lista, alcance, urgente, activo. Contador de caracteres en el título (24) y la descripción (72) de la lista de WhatsApp; el límite también se valida en el servidor y en la base. La ayuda de «Alcance» explica qué cambia. Sin borrar: se desactiva. |
| Reclamos por edificio | `/admin/building-claims` y `/admin/building-claims/{id}` | Propia | Lista de edificios activos con habilitados y sin proveedor; en cada uno, la tabla «Problemas y proveedores» (habilitado, «¿Quién lo atiende?» con los proveedores activos + «Lo atiende el estudio», orden), que se guarda entera. «Copiar de otro edificio» con confirmación. Avisos: problemas que atiende el estudio, proveedores sin WhatsApp o desactivados. Con más de 10 habilitados marca desde cuál van a una segunda lista («Más opciones»). |

- **Edificios:** nuevo interruptor «Reclamos por el bot» (columna, filtro y formulario).
  Todavía no hace nada; lo usa el paso 8.3.
- **Por qué una entrada propia y no dentro de Edificios:** el formulario de Edificios es el de
  SQLAdmin y una tabla editable con copiar y avisos no entra bien ahí.
- **Datos iniciales:** `scripts/seed_claim_categories.py` (12 tipos de problema; con
  `--building <código>` los habilita en ese edificio, sin proveedor).

## Actualización 5d.5: ayudas para aprender solas (2026-10-07)

- **Línea de ayuda:** debajo del título de cada pantalla, una línea dice para qué sirve
  (`app/admin/help.py`, `PAGE_HELP`). Un test hace fallar una pantalla nueva que no tenga la suya.
- **Pantallas vacías:** explican qué significa estar vacía y qué hacer (`help.EMPTY`). Abarca
  las pestañas de Conversaciones, Teléfonos, Verificaciones, SUM y las listas de SQLAdmin.
- **Confirmaciones:** antes de Desvincular, Rechazar, Cancelar reserva, Resolver, Eliminar
  turno, Desactivar usuario y Empezar de nuevo aparece un único diálogo
  (`_confirm_dialog.html` + `panel.js`). Dice qué le pasa a la persona del otro lado: en ningún
  caso le llega un aviso.
- **Recorrido de Conversaciones:** 5 pasos en globos sobre una **conversación de ejemplo**
  (`/admin/conversations/example`, `app/admin/example.py`): datos fijos armados en el servidor,
  con la misma plantilla que la bandeja real. No se guarda en la base y nunca manda nada: sus
  botones solo dicen qué harían. Se abre con el botón «Cómo usar» del encabezado de
  Conversaciones (no está en el menú). La primera vez que una usuaria del panel entra a
  Conversaciones la lleva sola al ejemplo (`panel_users.tour_seen_at`); al admin de rescate del
  `.env` nunca. Al terminar, «Volver a mis conversaciones» vuelve a la pestaña donde estaba.
- **Guía:** «Guía» en el menú abre `docs/guia-empleadas.md` dentro del panel. Sus imágenes, con
  datos inventados, están en `docs/guia-empleadas/` y van a Git.
- **Textos de Conversaciones para la operadora:** donde decía «plantilla» ahora dice «mensajes ya
  preparados».
- **Menú:** «Guía» aparece para los dos roles, después de Reservas de SUM.

## Actualización 5d.4: castellano y un solo estilo (2026-10-07)

Lo que sigue reemplaza lo que el relevamiento original decía de textos en inglés, valores
técnicos y estilo.

- **SQLAdmin en castellano.** La versión queda fija en `sqladmin==0.32.0`.
  - Sus textos se traducen con un diccionario (`app/admin/i18n.py`), sin copiar plantillas.
  - Solo hay copias de `list.html` y `details.html`.
  - `edit`, `create`, `base` y `layout` sobrescriben bloques.
  - En `_macros.html` están el menú y los campos de formulario.
  - Los mensajes de validación salen del catálogo en castellano de WTForms.
  - El login dice «Ingresar al panel del Estudio Diego Rufeil».
  - `tests/admin/test_sqladmin_pin.py` avisa si una actualización de SQLAdmin cambia algo de
    lo que adaptamos.
- **Nada técnico a la vista:**
  - Fechas: «hoy 11:32», «ayer 18:05», «07/10 11:32» o «07/10/2026».
  - Teléfonos: «351 555-0101».
  - Edificios sin código, salvo en su propia columna de la pantalla Edificios.
  - Valores traducidos desde un solo diccionario (`app/admin/labels.py`).
  - Sincronizaciones con errores y estadísticas legibles.
  - Métricas con herramientas en castellano y «US$ 0,20».
  - Motivos de derivación: etiqueta corta («Reclamo de deuda») y texto largo en el resumen.
- **Un solo estilo** (`app/admin/static/panel.css`):
  - colores propios sobre Tabler;
  - letra de 15 px;
  - logo «DR» y favicon.
- **Botones por lo que hacen:**
  - azul: la acción esperada;
  - rojo: lo que no se deshace;
  - gris: volver o cancelar.
  - Siempre con texto.
- **Celular:**
  - «Salir» queda arriba;
  - las listas son tarjetas;
  - los filtros aparecen plegados.

Capturas: `docs/panel-capturas/5d4/`, fuera de Git.

## Actualización: simplificación por rol (2026-10-07)

Lo que sigue en esta sección reemplaza al inventario original donde lo contradiga. Lo demás,
textos en inglés y aspecto de SQLAdmin, sigue como se relevó.

**Menú de la operadora:** Conversaciones, Teléfonos, Verificaciones, Reservas de SUM.

**Menú del admin:**
- Lo mismo que la operadora.
- Debajo del título «Administración»: Edificios, Información de edificios, Configuración del bot,
  Plantillas de WhatsApp, Respuestas rápidas, Usuarios, Métricas, Sincronizaciones y Chat de
  prueba (este último solo en desarrollo).

**Cambios por pantalla:**
- **Permisos:** se controlan en el servidor.
  - Las vistas `AdminOnly` y las páginas de configuración del SUM (`require_admin`) dan 403 a la
    operadora.
  - El 403 se muestra dentro del panel, con el menú: «Esta sección es solo para
    administradores.» (`templates/sqladmin/error.html`).
- **Reclamos:** fuera del menú. La ruta `/admin/claims` sigue.
- **Teléfonos** (`/admin/phones`, vista propia): reemplaza «Teléfonos» y «Teléfonos a revisar».
  - Pestañas «A revisar (N)», la de entrada si hay alguno, y «Todos».
  - Columnas Teléfono, Persona, Unidades, Origen, Estado (Verificado / A revisar / En conflicto /
    Sin verificar) y Fecha.
  - «Aprobar» y «Desvincular» por fila; «Aprobar seleccionados» en lote.
  - Filtros Estado y Origen, plegados.
  - En el celular, cada fila es una tarjeta.
- **Verificaciones** (`/admin/verifications`, vista propia):
  - «Aprobar» y «Rechazar» por fila, sin columna id y con el estado en castellano.
  - La página de aprobar trae «Ver conversación» y el texto «Se va a vincular este teléfono con el
    propietario elegido».
- **Reservas de SUM:** la operadora ve la lista y la planilla, reserva y cancela. La configuración
  y «Agregar el SUM a un edificio» son solo de admin.
- **Edificios:** el Nombre es de solo lectura («Viene de ConsorPlus…») y el servidor ignora un
  nombre que llegue igual.
- **Conversaciones:**
  - En una resuelta no aparecen «Tomar control» ni «Devolver al bot», y el servidor los rechaza.
    Responderla la vuelve a abrir en «Mías».
  - La etiqueta del motivo está en castellano («Reclamo por la deuda»).
- **Configuración del bot:**
  - El menú abre directo el formulario.
  - Las ayudas dicen «Si lo dejás vacío, el bot usa el texto (o valor) por defecto».
- **Chat de prueba:** solo admin, buscador de personas incluido.

Capturas de esta actualización: `docs/panel-capturas/paso2/`, fuera de Git.

---

Relevamiento del panel tal como estaba el 2026-10-07 (commit `6361e16`), sin cambios de código.
Fuentes: `app/admin/` (vistas y plantillas), SQLAdmin 0.32.0 (`.venv/.../sqladmin/templates`) y
capturas de un panel de demo con datos inventados (ver «Capturas» al final).

Convenciones de este documento:

- **SQLAdmin**: pantalla generada por SQLAdmin (`ModelView`) con sus plantillas por defecto
  (lista, detalle, edición y creación).
- **Propia**: `BaseView` con plantilla propia en `app/admin/templates/`, que igual extiende
  `sqladmin/layout.html` (mismo menú y mismo tema Tabler).
- 🇬🇧 = texto que se ve en inglés · ⚙️ = nombre o valor técnico visible · 🔒 = campo que se ve y
  nadie debería tocar.

---

## 1. Menú

El menú es la barra lateral oscura de SQLAdmin (en el celular, una barra arriba con botón
hamburguesa). Arriba dice **«Estudio Diego Rufeil»** en texto (no hay logo ni favicon). Abajo hay
un botón gris **«Logout»** 🇬🇧. `/admin/` redirige a Conversaciones.

| # | Nombre visible | URL | Tipo | Rol que la ve | Notas |
|---|---|---|---|---|---|
| 1 | Conversaciones | `/admin/conversations` | Propia | admin, operadora | Bandeja de WhatsApp. Es la home. |
| 2 | Chat de prueba | `/admin/dev-chat` | Propia | admin, operadora | **Solo desarrollo** (`APP_ENV=development`; en otro entorno no está en el menú y da 404). No es solo para admin. |
| 3 | Edificios | `/admin/building/list` | SQLAdmin | admin | |
| 4 | Información de edificios | `/admin/building-info/list` | SQLAdmin | admin | |
| 5 | Teléfonos | `/admin/phones/list` | SQLAdmin | admin, operadora | |
| 6 | Teléfonos a revisar | `/admin/phone/list` | SQLAdmin | admin, operadora | URL `phone` contra `phones` de la anterior. |
| 7 | Verificaciones pendientes | `/admin/verification-request/list` | SQLAdmin + página propia para aprobar | admin, operadora | |
| 8 | Sincronizaciones | `/admin/sync-run/list` | SQLAdmin (solo lectura) | admin | |
| 9 | Configuración general | `/admin/bot-settings/list` | SQLAdmin | admin | |
| 10 | Métricas | `/admin/metrics` | Propia | admin | |
| 11 | Reservas de SUM | `/admin/amenities` | Propia | admin, operadora | Incluye configuración del SUM y turnos. |
| 12 | Reclamos | `/admin/claims` | Propia | admin, operadora | Página vacía («Próximamente»). |
| 13 | Plantillas de WhatsApp | `/admin/wa-template/list` | SQLAdmin | admin | |
| 14 | Respuestas rápidas | `/admin/quick-reply/list` | SQLAdmin | admin | |
| 15 | Usuarios | `/admin/users` | Propia | admin | |

Fuera del menú:

- **Login** `/admin/login` y **Logout** `/admin/logout`: plantilla de SQLAdmin.
- **Adjuntos de WhatsApp** `/admin/wa/media/{id}`: no está en el menú (`is_visible=False`). La
  usa la bandeja para mostrar fotos y bajar archivos. Cualquier usuario logueado.

**Menú de la operadora** (verificado con un usuario `operator`): Conversaciones, Chat de prueba
(solo en desarrollo), Teléfonos, Teléfonos a revisar, Verificaciones pendientes, Reservas de SUM,
Reclamos. Si escribe la URL de una vista de admin recibe una página en blanco con
**«403 Forbidden»** 🇬🇧, sin menú ni botón para volver.

El orden no sigue el uso: «Chat de prueba» queda segundo, y entre Verificaciones y Reservas hay
cuatro pantallas de admin (Sincronizaciones, Configuración, Métricas) y otras dos de admin quedan
al final (Plantillas, Respuestas rápidas), separadas de Usuarios solo por el orden.

---

## 2. Pantallas

### Elementos que SQLAdmin agrega solo (todas las pantallas SQLAdmin)

Para no repetirlo en cada pantalla:

- **Lista**: encabezado de tarjeta con el nombre en plural; botón **«+ New &lt;nombre&gt;»** 🇬🇧
  si se puede crear; menú **«Actions»** 🇬🇧 con las acciones en lote (si no hay ninguna, el botón
  aparece gris y deshabilitado); **«Delete selected items»** 🇬🇧 en ese menú si se puede borrar;
  casilla por fila y «Select all»; por fila, íconos de **ojo (View)**, **lápiz (Edit)** y
  **tacho (Delete)** 🇬🇧 (tooltips) según los permisos; buscador **«Search: …»** + botón
  **«Search»** 🇬🇧 + «✕»; panel lateral **«Filters»** 🇬🇧 con opciones **«All / Yes / No»** 🇬🇧;
  pie **«Showing 1 to N of N items»**, **«prev» / «next»**, **«Show 25 / Page»** 🇬🇧.
- **Detalle**: título de página con el nombre en singular, tarjeta **«Id: N»**, tabla de dos
  columnas **«Column» / «Value»** 🇬🇧, botones **«Go Back»**, **«Edit»**, **«Delete»** 🇬🇧 según
  permisos, y además las acciones en lote de la vista como botones.
- **Edición / creación**: título **«Edit &lt;nombre&gt;»** / **«New &lt;nombre&gt;»** 🇬🇧,
  contador **«Number of characters: N»** 🇬🇧 bajo cada área de texto, botones **«Cancel»**,
  **«Save»**, **«Save and continue editing»** y (al crear) **«Save and add another»** 🇬🇧.
- **Borrar**: modal **«Please confirm»** con **«Cancel» / «Delete»** 🇬🇧. Las acciones propias con
  confirmación usan un modal con **«Cancel» / «Yes»** 🇬🇧 y el mensaje en español.
- Las listas de SQLAdmin **no muestran título de página** (el encabezado gris queda vacío); las
  de detalle y las propias sí.
- Fechas: en las listas y detalles de SQLAdmin salen crudas, con zona y microsegundos
  (`2026-10-07 11:32:45.893754-03:00`) ⚙️, salvo en Teléfonos, que las formatea
  (`07/10/2026 11:32`).
- Booleanos: ✓ verde / ✗ roja.
- Exportar está apagado en todas (`can_export = False`); importar también (por defecto).
- SQLAdmin 0.32 no trae traducción al español (tiene de, ja, ru, tr, az, en): todo lo anterior
  sale en inglés.

### 2.1 Login (`/admin/login`) — SQLAdmin

- Para qué: entrar al panel.
- Textos: **«Login to Estudio Diego Rufeil»**, **«Username»**, placeholder **«Enter username»**,
  **«Password»**, botón **«Login»** 🇬🇧. Usuario o clave mal: **«Invalid credentials.»** 🇬🇧. El
  bloqueo por intentos sí está en español («Demasiados intentos fallidos…»).
- Tarjeta blanca centrada, sin logo.

### 2.2 Conversaciones (`/admin/conversations`, `/admin/conversations/{id}`) — Propia

- Para qué: atender las conversaciones de WhatsApp (tomar, responder, devolver al bot, resolver,
  notas).
- Escritorio: tres columnas (lista 340 px · chat · ficha 300 px) que ocupan el alto de la
  pantalla.
- **Lista**:
  - Buscador «Nombre, teléfono o unidad» (busca en todas las pestañas y muestra «Resultados para
    «…» en todas las pestañas») y botón **«🔔 Activar avisos» / «🔔 Avisos activos»** (sonido y
    notificación del navegador).
  - Pestañas con contador: **Esperando persona** (por defecto, contador rojo), **Mías**, **Con el
    bot**, **Resueltas**.
  - Cada fila: nombre (o perfil de WhatsApp), «hace N min», teléfono ⚙️ en formato E.164
    (`+5493515550101`) · edificio · unidad (hasta 2, luego «+N»), último mensaje, contador verde
    de no leídos, etiquetas: **URGENTE** (borde rojo), motivo de derivación como **slug** ⚙️
    (`emergencia`, `reclamo-deuda`, `pide-persona`, `pago-no-acreditado`, `sin-respuesta`,
    `error-tecnico`, `fuera-de-piloto`, `ventana-cerrada`, `otro-motivo`…; el texto largo queda en
    el tooltip), estado (solo al buscar), «La tiene &lt;nombre&gt;» o «Desde el celular».
  - Se actualiza cada 4 s; el título de la pestaña muestra «(N)» esperando.
- **Chat**:
  - Encabezado: nombre + teléfono E.164 ⚙️, estado (Esperando persona / Con una persona / Con el
    bot / Resuelta), motivo (slug ⚙️), URGENTE, «La tiene &lt;nombre&gt;».
  - Botones: **Tomar control** (si no la tiene una misma), **Devolver al bot** (si no está con el
    bot), **Resolver** (si no está resuelta), **Nota interna**. En una conversación resuelta
    siguen apareciendo «Tomar control» y «Devolver al bot».
  - Resumen de la derivación en fondo naranja («Urgencia en el edificio · …»).
  - Hilo estilo WhatsApp: separador de día, burbujas del contacto, del bot («🤖 Bot»), de la
    operadora (con su nombre), notas internas amarillas («📝 Nota interna · nombre») y la nota de
    derivación del bot («🤖 Derivación del bot (nota interna)»), tipo de mensaje («FOTO»,
    «DOCUMENTO»…), fotos en línea, adjuntos para bajar, tildes ✓/✓✓/⚠, «No se entregó: …».
  - Redactor con dos pestañas, **Responder** y **Nota interna**. Responder: aviso («Al responder
    tomás la conversación…» / «La tiene X: si respondés, sigue asignada…»), área de texto,
    desplegable **Respuestas rápidas**, **Enviar**. Con la ventana de 24 h cerrada: aviso,
    desplegable de plantillas activas y **Enviar plantilla** (o «No hay plantillas cargadas: un
    admin las carga en «Plantillas de WhatsApp»»). Sin WhatsApp configurado: aviso amarillo.
    Nota interna: área + **Guardar nota**.
- **Ficha del contacto**: nombre, teléfono, «en WhatsApp: &lt;perfil&gt;», «Teléfono verificado» /
  «Teléfono sin verificar (cargado en ConsorPlus)», **Unidades** (edificio · unidad, rol, última
  deuda con monto «$45.230,50», «al día» / «con deuda», «consultada el …»), **Reservas de SUM**
  próximas, o «Número no identificado…».
- Celular (390 px): se ve la lista **o** el chat. El chat trae «← Volver», la ficha plegada en
  «▸ Ficha del contacto» y los botones en dos renglones. Funciona bien; arriba se pierden unos
  200 px entre la barra del menú y el título «Conversaciones».
- Mensajes flash en español.

### 2.3 Chat de prueba (`/admin/dev-chat`) — Propia, **solo desarrollo**

- Para qué: escribirle al bot como si fuera un teléfono, sin pasar por Meta.
- Aviso amarillo «Solo en desarrollo…». En escritorio el `<strong>` del aviso rompe el renglón
  (la frase «nunca se mandan a WhatsApp» queda en una columna aparte).
- Izquierda: un «celular» (verde WhatsApp) con el hilo, botones de opciones tocables y caja
  «Mensaje» + enviar.
- Derecha: **Teléfono de prueba** (campo con `+5493515550000` ⚙️ por defecto, «Usar este
  teléfono»), **Buscar una persona de la base** («Nombre, unidad o edificio», «Buscar»; lista
  nombre · teléfono · unidades: **muestra datos reales de propietarios si la base es la real**),
  estado y botones **Ver en Conversaciones** y **Empezar de nuevo** (borra los mensajes).
- La ve también una operadora (no es `AdminOnly`).

### 2.4 Edificios (`/admin/building/list`) — SQLAdmin, admin

- Para qué: ver los edificios que trae ConsorPlus y marcar activo / prueba piloto.
- Columnas: **Código ConsorPlus** ⚙️, **Nombre** (con el código adelante: «901 TORRE EJEMPLO» ⚙️),
  **Dirección**, **Activo**, **Prueba piloto**.
- Búsqueda: nombre, dirección. Orden: código, nombre, activo (por defecto nombre).
- Filtros: Activo, Prueba piloto (All/Yes/No 🇬🇧).
- Botones: ver, editar. No se crea ni se borra. «Actions» vacío y deshabilitado.
- Edición: **Nombre*** 🔒, **Dirección**, **Activo**, **Prueba piloto** (interruptores).
  - 🔒 **Nombre**: la sincronización nocturna de ConsorPlus lo pisa
    (`app/sync/roster.py:_upsert_building`), así que editarlo acá no dura. Lo mismo vale para la
    fuente de verdad en general: los edificios vienen de ConsorPlus.
- Detalle: lo anterior + Creado, Actualizado (fecha cruda ⚙️).

### 2.5 Información de edificios (`/admin/building-info/list`) — SQLAdmin, admin

- Para qué: cargar el reglamento, horarios, contactos y emergencias que el bot usa para responder.
- Columnas: **id** ⚙️, **Edificio** (link al detalle del edificio, con código), **Categoría**
  (valor crudo: `reglamento`, `horarios`, `contactos`, `emergencias`, `otros` ⚙️), **Título**,
  **Actualizado** (fecha cruda ⚙️).
- Búsqueda: título, nombre del edificio. Orden: categoría, título, actualizado.
- Filtros: Edificio (todos, también los inactivos, con código), Categoría (valores crudos ⚙️).
- Botones: **«+ New Información de edificio»** 🇬🇧, ver, editar, borrar (también en lote).
- Formulario: **Edificio** (desplegable con código; no marcado obligatorio), **Categoría***
  (desplegable con valores crudos ⚙️), **Título***, **Contenido*** (15 renglones).
- Detalle: + Contenido, Creado.

### 2.6 Teléfonos (`/admin/phones/list`) — SQLAdmin, admin y operadora

- Para qué: buscar de quién es un número y desvincularlo si cambió de dueño o está mal asociado.
- Columnas: **Número** (E.164 ⚙️), **Persona**, **Unidades de la persona** (edificio · unidad
  (rol)), **Fuente** (ConsorPlus / Bot (código por email) / Operador), **Verificado**, **Fecha**.
  En 1440 px las tres últimas quedan **fuera de la pantalla** (scroll horizontal dentro de la
  tabla, con el panel de filtros al lado).
- Búsqueda (placeholder «Search: número o nombre»): dígitos del número (acepta «351 555-0301»,
  «0351…», «+54 9…»), texto como figura en ConsorPlus o nombre.
- Orden: número, fuente, verificado, fecha (por defecto fecha, más nuevo primero). 50 por página.
- Filtros: Verificado, A revisar (característica supuesta), En conflicto (figura para otra
  persona) (cortado), Fuente.
- Botones: ver. Acción en lote (y botón en el detalle) **Desvincular**, con confirmación larga
  (avisa que la sincronización lo vuelve a crear si sigue en ConsorPlus). No se crea, edita ni
  borra por el camino de SQLAdmin.
- Detalle: **ID** ⚙️, Número, Como figura en ConsorPlus, Persona, Unidades, Fuente, Verificado,
  A revisar, En conflicto, Fecha (dd/mm/aaaa hh:mm).
- Celular: **roto**. El panel «Filters» se encima a la lista: la tabla queda en una columna de
  ~100 px de la que solo se ven las casillas, y el buscador y la paginación se superponen con los
  filtros (captura `06-telefonos-celular.png`).

### 2.7 Teléfonos a revisar (`/admin/phone/list`) — SQLAdmin, admin y operadora

- Para qué: aprobar los teléfonos de ConsorPlus a los que se les supuso la característica
  (`needs_review` ⚙️) o borrarlos.
- Columnas: **id** ⚙️, **Teléfono (normalizado)** ⚙️, **Como figura en ConsorPlus**, **Persona**,
  **Origen** (valor crudo `consorplus` ⚙️), **Verificado** (siempre ✗ acá: no aporta),
  **Cargado** (fecha cruda ⚙️).
- Búsqueda: teléfono, texto original, persona (placeholder con los nombres de las columnas, cortado).
  Sin filtros ni orden.
- Botones: ver, **borrar** (tacho por fila, «Delete selected items» 🇬🇧 en lote, «Delete» en el
  detalle; solo deja borrar si sigue a revisar). Acción en lote (y botón en el detalle)
  **Aprobar** con confirmación («Quedan verificados»).
- Detalle: las mismas columnas.
- Inconsistencias con «Teléfonos»: «Origen» vs «Fuente», `consorplus` vs «ConsorPlus», «Cargado»
  vs «Fecha», fecha cruda vs formateada, borrar vs desvincular.
- Celular: la tabla se corta a la derecha (scroll horizontal); se ven id y teléfono.

### 2.8 Verificaciones pendientes (`/admin/verification-request/list`) — SQLAdmin + propia, admin y operadora

- Para qué: aprobar o rechazar a quien pidió que una persona confirme su identidad (unidades sin
  email del propietario).
- Columnas: **id** ⚙️, **Edificio** (sin código), **Unidad**, **Nombre declarado**, **Teléfono**
  (E.164 ⚙️), **Fecha** (cruda ⚙️). Solo las pendientes, más viejas primero.
- Sin búsqueda, filtros ni orden.
- Acciones en lote (y botones en el detalle): **Aprobar (elegir propietario)** (de a una; lleva a
  la página de aprobar) y **Rechazar** (confirmación). No se crea, edita ni borra.
- Detalle: + **Estado** con el valor crudo `pending` ⚙️; botones «Go Back» 🇬🇧, «Rechazar»,
  «Aprobar (elegir propietario)».
- **Aprobar verificación** (`/admin/verification-request/approve/{id}`, plantilla propia):
  «Verificación #N» ⚙️, Edificio, Unidad, Nombre declarado, Teléfono, Fecha; radios con los
  propietarios de la unidad; texto «queda verificado, origen "manual"» ⚙️; **Aprobar** /
  **Cancelar**. Si ya estaba resuelta: «Esta solicitud ya está resuelta (`approved`/`rejected`)» ⚙️.
  Sin propietarios: aviso para rechazar desde el listado.
- No hay link a la conversación de esa persona.

### 2.9 Sincronizaciones (`/admin/sync-run/list`) — SQLAdmin, admin (solo lectura)

- Para qué: ver cómo anduvieron las corridas contra ConsorPlus.
- Columnas: **id** ⚙️, **Tipo** (`nightly`/`live` ⚙️), **Tarea** (`debt`/`roster`/`canary` ⚙️),
  **Inicio**, **Fin** (crudas ⚙️), **Estado** (`ok`/`partial`/`failed` ⚙️), **OK**, **Con error**
  (fuera de la pantalla en 1440 px).
- Orden: inicio (por defecto, más nueva primero), tarea, estado. Filtro: Tarea (valores crudos ⚙️).
- Solo ver. «Actions» vacío.
- Detalle: + **Errores** (texto), **Estadísticas** (JSON crudo ⚙️).

### 2.10 Configuración general (`/admin/bot-settings/list`) — SQLAdmin, admin

- Para qué: textos y horarios del bot.
- La lista tiene una sola fila, «Configuración del bot» + Actualizado (fecha cruda ⚙️); hay que
  entrar con el lápiz. No se crea ni se borra. «Actions» vacío.
- Formulario («Edit Configuración general» 🇬🇧): **Mensaje de bienvenida**, **Horario de atención:
  desde**, **hasta** (HH:MM), **Días de atención** (`0,1,2,3,4` ⚙️, 0 = lunes), **Texto fuera de
  horario**, **Contacto para urgencias fuera de horario**, **URL de autogestión**, **Cómo pagar
  con el código**. Cada uno con ayuda en español que menciona **«.env»** ⚙️ («Vacío: se usa el
  valor de .env (o el predeterminado)»), sin mostrar cuál es ese valor.
- Detalle: los mismos campos.

### 2.11 Métricas (`/admin/metrics`) — Propia, admin

- Para qué: uso del bot del mes.
- Tarjetas: **Conversaciones del mes** (desde el 1°), **Derivadas a una persona** (%, «N de M»),
  **Costo de IA del mes (estimado)** en «US$ 0.20» (punto decimal, no coma) con «según
  LLM_PRICE_*» ⚙️ o «N llamadas sin precio cargado (LLM_PRICE_*)» ⚙️.
- **Conversaciones por día (últimos 30 días)**: 30 renglones con barra (larga; en celular, 30
  renglones a lo alto).
- **Herramientas más usadas (mes)**: nombres internos de las herramientas del bot (`get_debt`,
  `search_unit`, `get_building_info`…) ⚙️.
- Sin filtros ni acciones.

### 2.12 Reservas de SUM (`/admin/amenities` y subpáginas) — Propia, admin y operadora

- **Edificios con SUM** (`/admin/amenities`): por edificio, nombre del SUM, «Deshabilitado»,
  «N turno(s) por semana · N reserva(s) en los próximos 7 días · el bot puede reservar / el bot no
  reserva»; botones **Planilla** y **Configuración**. Abajo, **Agregar el SUM a un edificio**
  (desplegable de edificios activos sin SUM + «Agregar SUM»).
- **Planilla semanal** (`/admin/amenities/{id}/week`): título «SUM · TORRE EJEMPLO»; botones
  «← Edificios», «Configuración», semana anterior / **Hoy** / siguiente; «Semana del … al …»;
  leyenda (Disponible, Reservado, No reservable ahora, Sin turno); grilla de 7 días × horas
  (08 a 24 por defecto, se amplía con turnos nocturnos). Bloque libre → reservar; reservado →
  ficha de la reserva; no reservable → muestra el motivo al tocarlo. Avisos si el SUM está
  deshabilitado o sin turnos. En el celular muestra **un día** con flechas ‹ › para cambiar de
  día; se ve bien.
- **Reservar** (`/admin/amenities/{id}/book?slot=…&date=…`): «Miércoles 07/10/2026 · 20:00 a
  02:00…»; **Unidad** (desplegable «1A · Propietario…»), **Notas (opcional)**; «Volver» /
  **Reservar**. Si rompe una regla (anticipación, límite por mes, deuda): aviso con casilla
  **«Reservar igual (queda registrado)»**.
- **Reserva** (`/admin/amenities/{id}/reservations/{rid}`): «Reserva #N» ⚙️, Confirmada /
  Cancelada, Día, Turno, Unidad, **Origen** (WhatsApp (bot) / Panel + teléfono E.164 ⚙️),
  Creada, Cancelada, Notas; «Volver a la planilla», **Cancelar reserva** (confirmación del
  navegador).
- **Configuración del SUM** (`/admin/amenities/{id}/config`):
  - Reglas y límites: **Nombre**, **Reglas para quien reserva**, **Anticipación mínima (horas)**,
    **Anticipación máxima (días)**, **Reservas por unidad por mes** (vacío = sin límite),
    **Cancelación hasta (horas antes)**; interruptores **Habilitado para reservas**, **El bot
    puede reservar**, **No reservan las unidades con deuda…**; **Guardar**.
  - Turnos por día (lunes a domingo) con tacho para borrar (confirmación del navegador).
  - **Agregar un turno**: días (Lu…Do, atajos Todos / Lunes a viernes / Fin de semana / Ninguno),
    Desde, Hasta, Nombre (opcional).
  - **Copiar los turnos de un día** a otros días.
- Todo esto (incluida la configuración y agregar SUM) lo ve y lo puede cambiar una operadora.

### 2.13 Reclamos (`/admin/claims`) — Propia, admin y operadora

- Página vacía: ícono, «Próximamente» y «Próximamente: acá vas a ver y seguir los reclamos de los
  propietarios.» (la palabra repetida).

### 2.14 Plantillas de WhatsApp (`/admin/wa-template/list`) — SQLAdmin, admin

- Para qué: las plantillas aprobadas en Meta que la bandeja ofrece con la ventana de 24 h cerrada.
- Columnas: **Nombre para las operadoras**, **Nombre en Meta** (`retomar_conversacion` ⚙️),
  **Idioma** (`es_AR` ⚙️), **Activa**. Orden: nombre, activa. Sin búsqueda ni filtros.
- Botones: **«+ New Plantilla de WhatsApp»** 🇬🇧, ver, editar. No se borra (se desactiva).
  «Actions» vacío.
- Formulario: Nombre para las operadoras, **Nombre en Meta** («Exactamente como figura aprobada
  en Meta… Solo plantillas sin variables»), **Idioma** («es_AR o es»), **Texto** (5 renglones),
  **Activa**.
- Detalle: + Texto, Actualizada.

### 2.15 Respuestas rápidas (`/admin/quick-reply/list`) — SQLAdmin, admin

- Para qué: textos guardados que la operadora inserta con un clic.
- Columnas: **Título**, **Orden**, **Activa**. Orden: título, orden (por defecto orden y título).
- Botones: **«+ New Respuesta rápida»** 🇬🇧, ver, editar, borrar (también en lote).
- Formulario: **Título**, **Texto** («Se inserta tal cual…»), **Orden** («Las de número más
  chico aparecen primero»), **Activa**.

### 2.16 Usuarios (`/admin/users`, `/admin/users/{id}`) — Propia, admin

- Lista: **Nombre**, **Usuario**, **Rol** (Admin / Operadora), **Estado** (Activo / Desactivado),
  **Último ingreso**, botón **Editar**. Pie: «Además está el admin de rescate &lt;usuario&gt;
  (definido en .env)…» ⚙️.
- **Nuevo usuario**: Nombre (se ve en las conversaciones), Usuario para entrar, Rol (con la
  explicación de qué ve cada uno), Contraseña (mín. 12), Repetila; **Crear usuario**.
- **Editar usuario**: «Nombre · usuario», Activo/Desactivado, «Rol: … · Último ingreso: …»;
  formularios separados: **Guardar nombre**, **Cambiar rol**, **Cambiar contraseña** (dos
  campos), **Desactivar** / **Reactivar**. No deja cambiarse el rol ni desactivarse a una misma.
  «Volver a usuarios».

---

## 3. Estilo

- **Una sola base visual**: todas las pantallas, propias y de SQLAdmin, usan el layout de SQLAdmin
  (Tabler + Bootstrap 5 + Font Awesome): barra lateral oscura, fondo gris claro, tarjetas blancas,
  botones azules. No hay CSS global propio ni override de `layout.html`; las vistas propias
  agregan `<style>` dentro de su plantilla (Conversaciones, Chat de prueba, Planilla del SUM) o
  usan solo clases de Tabler (el resto).
- **Título y logo**: `title="Estudio Diego Rufeil"` como texto en la barra y en la pestaña del
  navegador. Sin logo, sin favicon, sin colores de marca.
- **Diferencias entre pantallas**:
  - Las listas de SQLAdmin son tablas densas con casillas, íconos sin texto, «Actions», «Filters»
    y paginación en inglés, sin título de página, con ids y valores crudos. Se nota que son
    «la base de datos».
  - Las propias (Conversaciones, SUM, Usuarios, Métricas, Verificación) tienen título, textos en
    español, botones con palabras y formato de fechas local. Conversaciones y el Chat de prueba
    tienen look de WhatsApp (fondo beige, burbujas).
  - Dentro de las propias el estilo es parejo; el salto fuerte es entre propias y SQLAdmin, y
    entre las dos pantallas de teléfonos.
  - Los botones de acciones propias en los detalles de SQLAdmin (Desvincular, Aprobar, Rechazar)
    salen grises, todos iguales, sin distinguir la acción peligrosa.
- **Celular (390 px)**:
  - El menú pasa a una barra arriba con «Estudio Diego Rufeil», hamburguesa y «Logout». Al abrirlo,
    la lista de 15 entradas ocupa toda la pantalla y el botón «Logout» queda flotando a la derecha,
    a media altura.
  - **Conversaciones**: bien resuelta (lista o chat, «← Volver», ficha plegable, botones grandes).
  - **Planilla del SUM**: bien (un día por vez).
  - **Reservar / Reserva / Configuración del SUM / Usuarios / Verificación**: formularios en una
    columna, usables.
  - **Listas de SQLAdmin sin filtros** (Teléfonos a revisar, Verificaciones, Plantillas…):
    usables con scroll horizontal; se ven 2 o 3 columnas por vez.
  - **Listas de SQLAdmin con filtros** (Teléfonos, Edificios, Información de edificios,
    Sincronizaciones): **rotas**, el panel de filtros tapa la tabla.
  - Métricas: 30 renglones de barras, largo pero legible.

---

## 4. Opinión (separada del inventario)

Para dos empleadas que atienden conversaciones, aprueban teléfonos y verificaciones y cargan
reservas.

### Qué le sobra

- **Chat de prueba** en el menú de la operadora (aunque sea solo en desarrollo): es una
  herramienta de quien desarrolla, y su buscador lista personas reales de la base.
- **Reclamos**: una entrada de menú que no hace nada; mejor que aparezca cuando exista.
- En **Teléfonos a revisar**: las columnas id, Teléfono (normalizado) en E.164 y Verificado
  (siempre ✗), y el botón **borrar** (el tacho por fila al lado del ojo es fácil de tocar sin
  querer; desvincular ya existe en Teléfonos).
- En **Verificaciones**: la columna id y la página de detalle intermedia (el «Column / Value» no
  agrega nada a lo que ya está en la lista y en la página de aprobar).
- En **Teléfonos**: los filtros «A revisar» y «En conflicto» son para quien entiende el modelo de
  datos; para ellas alcanza con buscar.
- Las casillas y el menú «Actions» para aprobar de a uno: obligan a tildar y abrir un desplegable
  en vez de tener un botón «Aprobar» en la fila.
- En **Reservas de SUM**, la configuración del SUM (reglas, límites, turnos, «el bot puede
  reservar», «agregar SUM a un edificio»): no es tarea de todos los días y cambia lo que el bot
  hace con todos los propietarios.

### Qué confunde

- **Dos pantallas de teléfonos** con nombres, columnas y acciones distintas para la misma tabla
  («Origen» vs «Fuente», «Cargado» vs «Fecha», `consorplus` vs «ConsorPlus», borrar vs
  desvincular).
- La **mezcla de idiomas**: «Actions», «Search», «Filters», «All/Yes/No», «Showing 1 to 2 of 2
  items», «Go Back», «Save and continue editing», «Logout», el login entero y «403 Forbidden».
- **Valores crudos**: fechas con microsegundos y zona, `pending`, `consorplus`, `nightly`,
  `partial`, `reglamento`, `es_AR`, `get_debt`, «origen "manual"», «.env», «LLM_PRICE_*».
- **Motivos de derivación como slugs** con guiones (`reclamo-deuda`, `pide-persona`) en la lista
  y el chat, cuando ya existe el texto en castellano (va en el tooltip, que en el celular no se ve).
- **Teléfonos en E.164** (`+5493515550101`) en la bandeja, la ficha y las listas, en vez de
  «351 555-0101» como lo dicen las personas.
- **Nombres de edificio con el código** («901 TORRE EJEMPLO») en Edificios e Información de
  edificios, y sin código en el resto.
- En una conversación **resuelta** siguen «Tomar control» y «Devolver al bot».
- En Verificaciones, **aprobar es de a una pero se elige desde un menú de acciones en lote**: si
  tildan dos, sale un aviso.
- El **«403 Forbidden»** sin menú si una operadora abre una URL de admin (por ejemplo, un link
  que le pasó la admin).
- En el celular, las listas con filtros (Teléfonos, la que más van a usar después de la bandeja)
  no se pueden usar.

### Qué solo debería ver un admin

Hoy ya son solo de admin: Edificios, Información de edificios, Sincronizaciones, Configuración
general, Métricas, Plantillas de WhatsApp, Respuestas rápidas y Usuarios. Me parece bien y
agregaría:

- **Chat de prueba** (en desarrollo): solo admin.
- **Configuración del SUM** y **Agregar el SUM a un edificio**: solo admin. La operadora necesita
  la lista, la planilla, reservar y cancelar.
- **Desvincular** en Teléfonos y **borrar** en Teléfonos a revisar: son irreversibles y la
  sincronización puede volver a crear el teléfono; se podría dejar en admin o, si se queda en la
  operadora, con un texto claro. Aprobar, en cambio, es tarea de ellas.
- **Edificios**: además de ser solo de admin, el campo **Nombre** no debería poder editarse (lo
  pisa la sincronización con ConsorPlus).

Con eso, el menú de la operadora quedaría en: Conversaciones, Teléfonos (con «a revisar» adentro),
Verificaciones y Reservas de SUM.

---

## Capturas

En `docs/panel-capturas/` (fuera de Git, en `.gitignore`), una versión de escritorio (1440 px) y
una de celular (390 px) por pantalla, más el menú del celular abierto y tres capturas como
operadora (`20-…`, `21-…`).

Cómo se hicieron: Playwright no está instalado, así que se usó Chrome sin cabeza manejado por
DevTools (script fuera del repo). El panel corrió en un proceso aparte (`APP_ENV=development`, un
cliente de WhatsApp falso, el bot sin responder) sobre una base de Postgres **separada**
(`<base>_panel_demo`) con datos inventados (edificios «901 TORRE EJEMPLO», teléfonos 555, nombres
«Ana Ejemplo»…). La base se borró al terminar. Nunca se usaron datos reales.

En las capturas de página entera la barra lateral termina a los 900 px: es un efecto de la
captura (la barra mide lo que la ventana), no del panel.
