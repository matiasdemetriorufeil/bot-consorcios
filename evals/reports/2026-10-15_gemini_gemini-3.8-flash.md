# Evaluación del bot: gemini / gemini-3.8-flash

- Fecha: 2026-10-15
- **Aprobados: 4/4 (100.0%)**
- Costo total: **US$ 0.0288** (US$ 0.0072 por caso)
- Tokens: entrada 37,081 · salida 266 · cache leída 0 · cache escrita 0 · 6 llamadas al modelo
- Duración: 25 s · reintentos por errores transitorios de la API: 0

## Por categoría

| Categoría | Aprobados |
|---|---|
| inquilino | 1/1 |
| reclamos | 1/1 |
| deuda | 1/1 |
| verificacion | 1/1 |

## Casos

| Caso | Categoría | Resultado | Herramientas | Costo |
|---|---|---|---|---|
| familiar_pide_deuda | inquilino | ✅ | - | US$ 0.0050 |
| familiar_reclamo | reclamos | ✅ | start_claim | US$ 0.0047 |
| manual_propietario_deuda | deuda | ✅ | get_debt | US$ 0.0095 |
| numero_quitado | verificacion | ✅ | find_unit, offer_choices | US$ 0.0097 |

## Fallas

Ninguna.
## Todas las conversaciones

<details>
<summary>✅ familiar_pide_deuda (inquilino)</summary>

Herramientas:

- ninguna

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola, ¿cuánto debemos de expensas en el depto?
>
> 🤖 Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.
>
> 🤖 La información de la deuda y expensas solo está disponible para los propietarios verificados de la unidad. Como figurás registrada como familiar u otro, no puedo mostrarte ese saldo. 
>
> 🤖 Podés pedirle al propietario de la unidad que consulte desde su número o verifique sus datos con nosotros. ¿Te ayudo con alguna otra consulta sobre el edificio o algún reclamo?

</details>

<details>
<summary>✅ familiar_reclamo (reclamos)</summary>

Herramientas:

- `start_claim({'category_hint': 'El portón de la cochera no abre desde ayer'})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 El portón de la cochera no abre desde ayer
>
> 🧾 Hola, soy el asistente automático del Estudio Diego Rufeil. Si en algún momento querés hablar con una persona, decímelo.
>
> 🤖 ¿Es por *Portón de cochera*?
> 🤖 [Opciones: Sí / Elegir otro / Cancelar]

</details>

<details>
<summary>✅ manual_propietario_deuda (deuda)</summary>

Herramientas:

- `get_debt({'unit_id': 5})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 ¿Cuánto debo de expensas?
>
> 🧾 Hola, soy el asistente automático del Estudio Diego Rufeil. Si en algún momento querés hablar con una persona, decímelo.
>
> 🧾 *RODAS I 04-C*
> 🧾 Saldo total: *$70.400,00*
> 🧾 • 09/2026 EXPENSAS ORDINARIAS: $70.400,00
> 🧾 Dato al 30/09/2026 a las 14:05.
>
> 🧾 Código de pago Siro: *1111222233334444007*
> 🧾 Con este código podés pagar por Pago Mis Cuentas o Red Link (home banking o cajero).
>
> 🤖 ¿Te ayudo con alguna otra consulta?

</details>

<details>
<summary>✅ numero_quitado (verificacion)</summary>

Herramientas:

- `find_unit({'unit_text': '04-B', 'building_text': 'Rodas II'})` → found
- `offer_choices({'text': 'Hola. Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.\n\nPara consultar la deuda de RODAS II 04-B necesitamos verificar tu número. ¿Querés que te enviemos un código de verificación al email registrado del propietario?', 'options': ['Sí, mandalo', 'No']})` → ok

Conversación (🧾 = mensaje de deuda armado por el código):

> 👤 Hola, ¿cuánto se debe de expensas en el Rodas II 04-B?
>
> 🤖 Hola. Soy el asistente automático del estudio; si querés hablar con una persona, decímelo.
>
> 🤖 Para consultar la deuda de RODAS II 04-B necesitamos verificar tu número. ¿Querés que te enviemos un código de verificación al email registrado del propietario?
> 🤖 [Opciones: Sí, mandalo / No]

</details>

