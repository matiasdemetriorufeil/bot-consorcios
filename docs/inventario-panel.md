# Inventario del panel `/admin`

## Actualización 5d.5: ayudas para aprender solas (2026-10-07)

- **Línea de ayuda:** debajo del título de cada pantalla, una línea dice para qué sirve
  (`app/admin/help.py`, `PAGE_HELP`). Un test hace fallar una pantalla nueva que no tenga la suya.
- **Pantallas vacías:** explican qué significa estar vacía y qué hacer (`help.EMPTY`). Abarca
  las pestañas de Conversaciones, Teléfonos, Verificaciones, SUM y las listas de SQLAdmin.
- **Confirmaciones:** antes de Desvincular, Rechazar, Cancelar reserva, Resolver, Eliminar
  turno, Desactivar usuario y Empezar de nuevo aparece un único diálogo
  (`_confirm_dialog.html` + `panel.js`). Dice qué le pasa a la persona del otro lado: en ningún
  caso le llega un aviso.
- **Recorrido de Conversaciones:** 5 pasos en globos. Aparece solo hasta que cada usuaria lo ve
  (`panel_users.tour_seen_at`) y se vuelve a ver con el botón «Ver recorrido» del encabezado
  de Conversaciones (con una conversación abierta arranca ahí mismo; si no, abre primero la
  primera esperando). No está en el menú: parecía otra sección.
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
