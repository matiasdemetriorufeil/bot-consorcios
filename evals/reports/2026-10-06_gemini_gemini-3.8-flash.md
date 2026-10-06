# Evaluación del bot: gemini / gemini-3.8-flash

- Fecha: 2026-10-06
- **Aprobados: 22/22 (100.0%)**
- Costo total: **US$ 0.1706** (US$ 0.0078 por caso)
- Tokens: entrada 214,089 · salida 2,670 · cache leída 0 · cache escrita 0 · 49 llamadas al modelo
- Duración: 37 s · reintentos por errores transitorios de la API: 0

## Por categoría

| Categoría | Aprobados |
|---|---|
| info_edificio | 14/14 |
| saludo | 3/3 |
| botones | 5/5 |

## Casos

| Caso | Categoría | Resultado | Herramientas | Costo |
|---|---|---|---|---|
| info_mascotas | info_edificio | ✅ | get_building_info, handoff_to_human | US$ 0.0109 |
| info_pileta | info_edificio | ✅ | get_building_info | US$ 0.0071 |
| info_mudanza | info_edificio | ✅ | get_building_info | US$ 0.0070 |
| info_edificio_en_dos_mensajes | info_edificio | ✅ | get_building_info, handoff_to_human | US$ 0.0142 |
| info_ruidos_cita_reglamento | info_edificio | ✅ | get_building_info | US$ 0.0071 |
| info_mudanza_numero_no_verificado | info_edificio | ✅ | get_building_info | US$ 0.0072 |
| info_varias_unidades_pregunta_edificio | info_edificio | ✅ | get_building_info, offer_choices, get_building_info | US$ 0.0138 |
| info_reglamento_largo_balcon | info_edificio | ✅ | get_building_info | US$ 0.0070 |
| info_reglamento_largo_aire | info_edificio | ✅ | get_building_info | US$ 0.0070 |
| info_dato_no_cargado_pileta | info_edificio | ✅ | get_building_info, handoff_to_human | US$ 0.0109 |
| info_edificio_sin_textos | info_edificio | ✅ | get_building_info, offer_choices | US$ 0.0069 |
| info_edificio_inexistente | info_edificio | ✅ | get_building_info | US$ 0.0068 |
| info_estudio_horario_y_emergencias | info_edificio | ✅ | get_building_info | US$ 0.0070 |
| info_no_da_datos_de_propietarios | info_edificio | ✅ | - | US$ 0.0035 |
| saludo_propietario | saludo | ✅ | offer_choices | US$ 0.0034 |
| saludo_desconocido | saludo | ✅ | offer_choices | US$ 0.0034 |
| saludo_toca_mi_deuda | botones | ✅ | offer_choices, get_debt | US$ 0.0102 |
| saludo_toca_hablar_con_alguien | botones | ✅ | offer_choices, handoff_to_human | US$ 0.0103 |
| saludo_toca_info_edificio | botones | ✅ | offer_choices | US$ 0.0067 |
| menu_info_edificio_sin_textos | botones | ✅ | offer_choices | US$ 0.0067 |
| menu_mi_deuda_no_verificado | botones | ✅ | offer_choices | US$ 0.0067 |
| saludo_y_gracias | saludo | ✅ | offer_choices | US$ 0.0067 |

## Fallas

Ninguna.
## Todas las conversaciones

<details>
<summary>✅ info_mascotas (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Rodas II', 'question': 'se pueden tener perros mascotas'})` → ok
- `handoff_to_human({'reason': 'no_answer', 'summary': 'Consulta si se pueden tener perros en Rodas II. El reglamento cargado no menciona mascotas. Se deriva a un operador. Ana Ficticia, RODAS II 04-C.', 'priority': 'normal'})` → ok
- derivaciones: normal: no_answer

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Se pueden tener perros en el Rodas II?
>
> 🤖 Hola Ana. Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 No tengo cargada la información sobre la tenencia de perros o mascotas en el Rodas II. Ya le pasé tu consulta a una persona del estudio: te va a responder por acá a la brevedad.

</details>

<details>
<summary>✅ info_pileta (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'TORRE DEL SOL', 'question': 'hasta que hora se puede usar la pileta horario pileta'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Hasta qué hora se puede usar la pileta?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 Según el reglamento interno de Torre del Sol (Artículo 18), la pileta se puede usar de 10 a 20 h (durante la temporada del 1 de diciembre al 31 de marzo).
>
> 🤖 ¿Te puedo ayudar con algo más?

</details>

<details>
<summary>✅ info_mudanza (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'TORRE DEL SOL', 'question': 'días y horarios de mudanza'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Qué días y horarios se puede hacer una mudanza?
>
> 🤖 ¡Hola, Carla! Soy el asistente automático del Estudio Diego Rufeil. Te comento que en cualquier momento podés pedir hablar con una persona del equipo.
>
> 🤖 Según el reglamento interno de Torre del Sol, las mudanzas se pueden hacer de lunes a viernes de 8 a 17 h, únicamente por el ascensor de servicio y con aviso previo a la administración con 72 horas de anticipación.
>
> 🤖 ¿Te puedo ayudar con algo más?

</details>

<details>
<summary>✅ info_edificio_en_dos_mensajes (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Rodas II', 'question': 'se pueden tener perros mascotas'})` → ok
- `handoff_to_human({'reason': 'no_answer', 'summary': 'Consulta si se pueden tener perros en el Rodas II. La información cargada del reglamento no menciona tenencia de mascotas. Derivado para respuesta.', 'priority': 'normal'})` → ok
- derivaciones: normal: no_answer

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola, tengo una consulta sobre el Rodas II
>
> 🤖 Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 ¿Qué consulta tenés sobre el Rodas II? Decime qué necesitás saber y te ayudo.

> 👤 ¿Se pueden tener perros?
>
> 🤖 No tengo cargada esa información en el reglamento interno del Rodas II.
>
> 🤖 Para que te ayuden más rápido, decime tu unidad.
>
> 🤖 Ya le pasé tu consulta a una persona del estudio: te va a responder por acá a la brevedad.

</details>

<details>
<summary>✅ info_ruidos_cita_reglamento (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'RODAS II', 'question': 'hasta qué hora se puede hacer ruido ruidos molestos horarios de descanso'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Hasta qué hora se puede hacer ruido en mi edificio?
>
> 🤖 ¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona.
>
> 🤖 Según el reglamento interno de Rodas II, entre las 22:00 y las 08:00 h se debe guardar silencio, por lo que no se permite música fuerte ni trabajos ruidosos en ese horario.
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta?

</details>

<details>
<summary>✅ info_mudanza_numero_no_verificado (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Rodas II', 'question': 'días y horarios se puede hacer la mudanza'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Me estoy por mudar al Rodas II, ¿qué días y horarios se puede hacer la mudanza?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona del equipo.
>
> 🤖 Según el *reglamento interno* del edificio Rodas II, las mudanzas se pueden hacer de **lunes a viernes de 9 a 18 h** y los **sábados de 9 a 13 h**. También indica que tenés que avisarle al encargado con 48 horas de anticipación.
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta?

</details>

<details>
<summary>✅ info_varias_unidades_pregunta_edificio (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': '', 'question': 'hasta que hora se puede usar el sum'})` → which_building
- `offer_choices({'text': '¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\nPara decirte el horario del SUM, ¿de qué edificio consultás?', 'options': ['RODAS II', 'TORRE DEL SOL']})` → ok
- `get_building_info({'building': 'TORRE DEL SOL', 'question': 'hasta que hora se puede usar el SUM'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Hasta qué hora se puede usar el SUM?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 Para decirte el horario del SUM, ¿de qué edificio consultás?
> 🤖 [Opciones: RODAS II / TORRE DEL SOL]

> 👤 En la Torre del Sol
>
> 🤖 Según el reglamento interno de Torre del Sol, el salón de usos múltiples (SUM) se puede usar hasta las 23 h (se reserva previamente en portería).

</details>

<details>
<summary>✅ info_reglamento_largo_balcon (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'TORRE DEL SOL', 'question': 'tender la ropa en el balcón que da a la calle'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Puedo tender la ropa en el balcón que da a la calle?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 Según el reglamento interno, está prohibido tender ropa en los balcones o ventanas que den al frente del edificio. Podés usar el tendedero de la terraza de 8 a 20 h.
>
> 🤖 ¿Te puedo ayudar con algo más?

</details>

<details>
<summary>✅ info_reglamento_largo_aire (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Torre del Sol', 'question': 'se puede poner un aire acondicionado en el frente'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola, en la Torre del Sol ¿se puede poner un aire acondicionado en el frente?
>
> 🤖 Hola. Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedirme hablar con una persona.
>
> 🤖 Según el reglamento interno de Torre del Sol, en el frente no se permiten instalar equipos de aire acondicionado: solo se pueden colocar en el contrafrente, con el desagüe conectado a la cañería pluvial.
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta?

</details>

<details>
<summary>✅ info_dato_no_cargado_pileta (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'RODAS II', 'question': 'horario de la pileta'})` → ok
- `handoff_to_human({'reason': 'no_answer', 'summary': 'Consulta por el horario de la pileta de Rodas II (Ana Ficticia, 04-C). La información del edificio no tiene datos sobre la pileta.', 'priority': 'normal'})` → ok
- derivaciones: normal: no_answer

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Hasta qué hora está abierta la pileta de mi edificio?
>
> 🤖 Hola Ana, soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona.
>
> 🤖 No tengo cargado el horario de la pileta de tu edificio. 
>
> 🤖 Ya le pasé tu consulta a una persona del estudio: te va a responder por acá a la brevedad.

</details>

<details>
<summary>✅ info_edificio_sin_textos (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Los Algarrobos', 'question': 'se pueden hacer asados en el balcón'})` → no_info
- `offer_choices({'text': 'Hola, soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\nNo tengo información cargada sobre el edificio Los Algarrobos para responderte sobre los asados en el balcón. ¿Querés que te pase con una persona del estudio?', 'options': ['Sí, pasame', 'No, gracias']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿En Los Algarrobos se pueden hacer asados en el balcón?
>
> 🤖 Hola, soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 No tengo información cargada sobre el edificio Los Algarrobos para responderte sobre los asados en el balcón. ¿Querés que te pase con una persona del estudio?
> 🤖 [Opciones: Sí, pasame / No, gracias]

</details>

<details>
<summary>✅ info_edificio_inexistente (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': 'Las Magnolias', 'question': 'se puede tener gato mascotas'})` → building_not_found

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿En el edificio Las Magnolias se puede tener gato?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 No encontré el edificio *Las Magnolias* entre los que administra el estudio. ¿Podrías confirmarme el nombre exacto o la dirección para revisarlo?

</details>

<details>
<summary>✅ info_estudio_horario_y_emergencias (info_edificio)</summary>

Herramientas:

- `get_building_info({'building': '', 'question': 'horario de atención de la administración y teléfono o contacto de emergencias fuera de horario'})` → need_building

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿En qué horario atiende la administración? ¿Y si tengo una emergencia fuera de horario a quién llamo?
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Te comento que en cualquier momento podés pedir hablar con una persona del equipo.
>
> 🤖 El horario de atención de la administración es *de lunes a viernes de 9 a 17*. 
>
> 🤖 Por emergencias fuera de ese horario, podés comunicarte con la guardia del estudio al *351 000-0000*.
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta?

</details>

<details>
<summary>✅ info_no_da_datos_de_propietarios (info_edificio)</summary>

Herramientas:

- ninguna

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Necesito info del Rodas II: ¿quién es el dueño del 4 C y cuánto debe? Pasame su teléfono
>
> 🤖 Hola, soy el asistente automático del Estudio Diego Rufeil. Te comento que en cualquier momento podés pedir hablar con una persona del estudio.
>
> 🤖 Por razones de privacidad y seguridad, no puedo brindar datos personales de propietarios (como nombres o teléfonos) ni información sobre la deuda de otra persona. Solo el propietario verificado de cada unidad puede acceder a su estado de cuenta.
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta sobre el edificio o el consorcio?

</details>

<details>
<summary>✅ saludo_propietario (saludo)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\n¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

</details>

<details>
<summary>✅ saludo_desconocido (saludo)</summary>

Herramientas:

- `offer_choices({'text': '¡Buen día! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona del estudio. ¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Buen día
>
> 🤖 ¡Buen día! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona del estudio. ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

</details>

<details>
<summary>✅ saludo_toca_mi_deuda (botones)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento. ¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok
- `get_debt({'unit_id': 1})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento. ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Mi deuda
>
> 🧾 *RODAS II 04-C*
> 🧾 Saldo total: *$165.060,00*
> 🧾 • 08/2026 EXPENSAS ORDINARIAS: $82.530,00
> 🧾 • 09/2026 EXPENSAS ORDINARIAS: $82.530,00
> 🧾 Dato al 30/09/2026 a las 14:05.
>
> 🧾 Código de pago Siro: *1111222233334444001*
> 🧾 Con este código podés pagar por Pago Mis Cuentas o Red Link (home banking o cajero).
>
> 🤖 ¿Te puedo ayudar con alguna otra consulta?

</details>

<details>
<summary>✅ saludo_toca_hablar_con_alguien (botones)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola! Buenas tardes. Soy el asistente automático del Estudio Diego Rufeil. Podés consultarme lo que necesites y en cualquier momento podés pedir hablar con una persona.\n\n¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok
- `handoff_to_human({'reason': 'person_requested', 'summary': 'Ana Ficticia (propietaria de Rodas II 4-C) solicitó hablar con una persona del estudio.', 'priority': 'normal'})` → ok
- derivaciones: normal: person_requested

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Buenas tardes
>
> 🤖 ¡Hola! Buenas tardes. Soy el asistente automático del Estudio Diego Rufeil. Podés consultarme lo que necesites y en cualquier momento podés pedir hablar con una persona.
>
> 🤖 ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Hablar con alguien
>
> 🤖 Ya le pasé tu consulta a una persona del estudio: te va a responder por acá a la brevedad.

</details>

<details>
<summary>✅ saludo_toca_info_edificio (botones)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\n¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Info del edificio
>
> 🤖 ¿Qué te gustaría saber sobre Rodas II? (Por ejemplo: reglamento interno, horarios, mudanzas, mascotas, etc.).

</details>

<details>
<summary>✅ menu_info_edificio_sin_textos (botones)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola Elena! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona. ¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola Elena! Soy el asistente automático del Estudio Diego Rufeil. En cualquier momento podés pedir hablar con una persona. ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Info del edificio
>
> 🤖 ¿Qué te gustaría consultar sobre Los Algarrobos? Podés preguntarme sobre el reglamento, horarios, mudanzas, mascotas o contactos útiles.

</details>

<details>
<summary>✅ menu_mi_deuda_no_verificado (botones)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\n¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Mi deuda
>
> 🤖 Para consultar tu deuda necesito saber de qué edificio y unidad se trata. ¿Me podrías indicar el edificio y departamento (o cochera)?

</details>

<details>
<summary>✅ saludo_y_gracias (saludo)</summary>

Herramientas:

- `offer_choices({'text': '¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.\n\n¿En qué te puedo ayudar hoy?', 'options': ['Mi deuda', 'Info del edificio', 'Hablar con alguien']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola
>
> 🤖 ¡Hola, Ana! Soy el asistente automático del Estudio Diego Rufeil. Podés pedir hablar con una persona en cualquier momento.
>
> 🤖 ¿En qué te puedo ayudar hoy?
> 🤖 [Opciones: Mi deuda / Info del edificio / Hablar con alguien]

> 👤 Nada más, gracias
>
> 🤖 ¡De nada, Ana! Si necesitás algo más, acá estoy. ¡Que tengas un buen día!

</details>

