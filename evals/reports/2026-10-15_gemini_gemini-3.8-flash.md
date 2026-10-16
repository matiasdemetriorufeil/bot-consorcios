# Evaluación del bot: gemini / gemini-3.8-flash

- Fecha: 2026-10-15
- **Aprobados: 2/2 (100.0%)**
- Costo total: **US$ 0.0729** (US$ 0.0364 por caso)
- Tokens: entrada 94,336 · salida 561 · cache leída 0 · cache escrita 0 · 14 llamadas al modelo
- Duración: 16 s · reintentos por errores transitorios de la API: 0

## Por categoría

| Categoría | Aprobados |
|---|---|
| sin_email | 2/2 |

## Casos

| Caso | Categoría | Resultado | Herramientas | Costo |
|---|---|---|---|---|
| sin_email_operador | sin_email | ✅ | find_unit, offer_choices, find_unit, start_email_verification, find_unit, request_registration | US$ 0.0365 |
| email_excluido_operador | sin_email | ✅ | find_unit, offer_choices, find_unit, start_email_verification, find_unit, request_registration | US$ 0.0364 |

## Fallas

Ninguna.
## Todas las conversaciones

<details>
<summary>✅ sin_email_operador (sin_email)</summary>

Herramientas:

- `find_unit({'unit_text': '2A', 'building_text': 'Los Algarrobos'})` → found
- `offer_choices({'text': 'Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.\n\nPara ver la deuda de la unidad 02-A de Los Algarrobos necesitamos verificar tu identidad. ¿Querés que te mandemos un código de verificación al email registrado del propietario?', 'options': ['Sí, mandalo', 'No']})` → ok
- `find_unit({'unit_text': '2 A', 'building_text': 'Los Algarrobos'})` → found
- `start_email_verification({'unit_id': 10})` → not_sent (no_owner_email)
- `find_unit({'unit_text': '2A', 'building_text': 'Los Algarrobos'})` → found
- `request_registration({'role': 'owner', 'unit_id': 10, 'full_name': 'Elena Sinmail'})` → confirmation_requested

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola, soy la dueña del 2A de Los Algarrobos, quiero saber mi deuda
>
> 🤖 Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.
>
> 🤖 Para ver la deuda de la unidad 02-A de Los Algarrobos necesitamos verificar tu identidad. ¿Querés que te mandemos un código de verificación al email registrado del propietario?
> 🤖 [Opciones: Sí, mandalo / No]

> 👤 sí
>
> 🤖 La unidad LOS ALGARROBOS 02-A no tiene un email de propietario cargado, así que no puedo mandarte el código. Si querés, decime tu nombre y apellido y le pido a una persona del estudio que verifique tu número.

> 👤 Elena Sinmail
>
> 🤖 Te registro como propietario/a de LOS ALGARROBOS 02-A, a nombre de Elena Sinmail.
> 🤖 ¿Está bien?
> 🤖 [Opciones: Sí, enviar / Corregir]

</details>

<details>
<summary>✅ email_excluido_operador (sin_email)</summary>

Herramientas:

- `find_unit({'unit_text': '2B', 'building_text': 'Rodas I'})` → found
- `offer_choices({'text': 'Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.\n\nPara poder ver la deuda de Rodas I 02-B necesitamos verificar tu número. ¿Querés que te mandemos un código de verificación al email registrado del propietario?', 'options': ['Sí, mandalo', 'No']})` → ok
- `find_unit({'unit_text': '2B', 'building_text': 'Rodas I'})` → found
- `start_email_verification({'unit_id': 6})` → not_sent (no_owner_email)
- `find_unit({'unit_text': '2 B', 'building_text': 'Rodas I'})` → found
- `request_registration({'role': 'owner', 'unit_id': 6, 'full_name': 'Gustavo Ficticio'})` → confirmation_requested

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Soy el dueño del 2B del Rodas I, quiero mi deuda
>
> 🤖 Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.
>
> 🤖 Para poder ver la deuda de Rodas I 02-B necesitamos verificar tu número. ¿Querés que te mandemos un código de verificación al email registrado del propietario?
> 🤖 [Opciones: Sí, mandalo / No]

> 👤 sí, mandá el código
>
> 🤖 La unidad RODAS I 02-B no tiene un email de propietario cargado, así que no puedo mandarte el código. Si querés, decime tu nombre y apellido y le pido a una persona del estudio que verifique tu número.

> 👤 Me llamo Gustavo Ficticio
>
> 🤖 Te registro como propietario/a de RODAS I 02-B, a nombre de Gustavo Ficticio.
> 🤖 ¿Está bien?
> 🤖 [Opciones: Sí, enviar / Corregir]

</details>

