# Funcionalidad del Agente de Agenda — Casos de Uso

Documento centrado en **qué hace el agente y cómo se comporta**, desde el punto de vista del usuario. Cada caso incluye: cuándo aplica, qué hace el usuario, qué hace el agente y un ejemplo real.

---

## Caso 1 — Añadir un evento por mensaje de voz

**Cuándo aplica**: el usuario tiene algo que agendar y prefiere dictarlo (típicamente en movilidad, conduciendo, entre reuniones).

**Qué hace el usuario**: manda un audio al bot de Telegram desde móvil o portátil.

**Qué hace el agente**:
1. Transcribe el audio.
2. Extrae la información clave: qué, cuándo, con quién, cuánto dura, si es personal o de empresa.
3. Si falta información crítica (fecha, por ejemplo), pregunta.
4. Aplica las reglas de confirmación (Caso 10) y agenda o propone.

**Ejemplo**:
> **Usuario (audio)**: *"Ponme una llamada con el proveedor Juan mañana antes de comer."*
>
> **Agente**: *"Vale. Llamada con Juan (proveedor), 10 min, mañana. Te propongo las 11:00 o las 12:30. ¿Cuál prefieres?"*
>
> **Usuario**: *[toca botón "11:00"]*
>
> **Agente**: *"Hecho. Añadido a tu calendario mañana a las 11:00."*

---

## Caso 2 — Añadir un evento por mensaje de texto

**Cuándo aplica**: el usuario está delante del teclado o prefiere escribir.

**Qué hace el usuario**: escribe el mensaje al bot.

**Qué hace el agente**: mismo comportamiento que el Caso 1, saltándose la transcripción.

**Ejemplo**:
> **Usuario**: *"Reunión con el equipo de marketing el jueves a las 10."*
>
> **Agente**: *"Reunión interna con marketing, 30 min por defecto. ¿Confirmas duración o la cambias?"*
>
> **Usuario**: *"Que dure 1h."*
>
> **Agente**: *"Perfecto. Jueves 10:00–11:00, reunión con marketing. Agendado."*

---

## Caso 3 — Consultar la agenda por chat

**Cuándo aplica**: pregunta rápida sin necesidad de abrir el visualizador.

**Qué hace el usuario**: pregunta en lenguaje natural.

**Qué hace el agente**: consulta el calendario (Bitrix, que ya agrega Google + Office + eventos manuales), filtra por el rango pedido y responde en formato lista breve.

**Ejemplos**:
> **Usuario**: *"¿Qué tengo mañana?"*
>
> **Agente**:
> *"Mañana tienes:*
> *• 9:30 — Reunión equipo (1h)*
> *• 11:00 — Llamada Juan proveedor (10 min)*
> *• 14:00 — Comida (bloqueado)*
> *• 17:00 — Dentista"*

> **Usuario**: *"¿Estoy libre el viernes por la tarde?"*
>
> **Agente**: *"Sí, tienes libre de 15:00 a 19:00 el viernes."*

---

## Caso 4 — Consultar la agenda en el visualizador

**Cuándo aplica**: el usuario quiere una vista completa, semanal, con contexto visual.

**Qué hace el usuario**: pulsa el botón fijo del bot **"📅 Ver mi agenda"** desde móvil o portátil.

**Qué hace el agente**: abre la Mini App dentro de Telegram. Muestra:
- Vista semanal (o diaria).
- Eventos coloreados por categoría (personal / empresa) y por prioridad (alta / media / baja).
- Bloques "no negociables" (comidas, familia, horas libres) claramente marcados.
- Al tocar un evento, se ve el detalle: duración, personas involucradas, tipo de actividad.

**Notas**:
- En v1 el visualizador es **solo lectura**: para modificar cualquier cosa se vuelve al chat.
- Se ve igual en móvil (Telegram app) y en portátil (Telegram Desktop) — un solo desarrollo, dos dispositivos.

---

## Caso 5 — Mover o reprogramar un evento

**Cuándo aplica**: cambio de planes, conflicto, o cliente/proveedor pide otra hora.

**Qué hace el usuario**: pide el cambio por texto o voz.

**Qué hace el agente**: identifica el evento, verifica que el nuevo hueco esté libre, y confirma. Si hay conflicto, avisa.

**Ejemplos**:
> **Usuario**: *"Mueve la reunión con marketing al viernes a la misma hora."*
>
> **Agente**: *"Reunión con marketing pasa del jueves 10:00 al viernes 10:00. ¿Confirmo?"*

> **Usuario**: *"Adelanta la llamada con Juan una hora."*
>
> **Agente**: *"No puedo, a las 10:00 tienes la reunión con marketing. ¿La pongo a las 12:00 en su lugar?"*

---

## Caso 6 — Cancelar un evento

**Qué hace el usuario**: dice que se cancela algo.

**Qué hace el agente**: identifica el evento, pide confirmación (para evitar borrar por error), y lo elimina.

**Ejemplo**:
> **Usuario**: *"Cancela la reunión de mañana con marketing."*
>
> **Agente**: *"¿Confirmas que cancelo 'Reunión con marketing' del jueves 10:00?"* [Sí] [No]

---

## Caso 7 — Clasificación automática: personal vs empresa

**Cuándo aplica**: en toda tarea/evento que crea el usuario.

**Qué hace el agente**: infiere la categoría del contexto:
- **Personal**: dentista, familia, cena con amigos, gym, gestiones personales.
- **Empresa**: reuniones, llamadas con clientes/proveedores, tareas de trabajo.

Si tiene dudas, pregunta. No inventa.

**Ejemplos**:
> *"Cena con Marta el sábado"* → personal (asume familia/pareja).
>
> *"Reunión con el nuevo cliente"* → empresa.
>
> *"Comida con Pedro el martes"* → **ambigüedad** → *"¿Comida de trabajo o personal?"*

---

## Caso 8 — Cálculo automático de prioridad

**Cuándo aplica**: en toda tarea/evento nuevo.

**Qué hace el agente**: aplica una regla simple, transparente:

| Situación | Prioridad |
|-----------|-----------|
| Sin fecha límite, sin involucrar a nadie | Baja |
| Involucra a otra persona | Media (mínimo) |
| Fecha límite en ≤3 días | Media |
| Fecha límite en ≤1 día | Alta |
| Involucra a otra persona **y** fecha límite ≤3 días | Alta |

El usuario puede cambiar la prioridad manualmente si no está de acuerdo:
> *"Marca la llamada con Juan como alta prioridad."*

---

## Caso 9 — Tarea de empresa con persona involucrada

**Cuándo aplica**: cualquier evento de categoría empresa.

**Qué hace el agente**: además de los datos básicos, captura o pregunta:
- **Involucrado**: persona, cliente, proveedor o partner asociado.

Si el usuario no lo menciona explícitamente pero se deduce del texto ("*llamada con Juan*"), lo extrae. Si no se puede deducir, pregunta:
> *"¿Con quién es esta reunión?"*

Este dato aparece luego en el detalle del evento en el visualizador.

---

## Caso 10 — Flujo híbrido de confirmación

**Regla**:
- **Personal + prioridad baja/media** → el agente **agenda directamente** y solo avisa.
- **Empresa** OR **prioridad alta** → el agente **propone 1-2 huecos** y espera confirmación con botones.

**Racional**: minimiza fricción en lo cotidiano, pero pide confirmación en lo que tiene más peso (compromisos con terceros, urgencias).

**Ejemplos**:

*Caso auto-agenda:*
> **Usuario**: *"Recuérdame ir a por pan mañana a media tarde."*
>
> **Agente**: *"Listo, añadido mañana a las 17:00, 15 min."*

*Caso propuesta:*
> **Usuario**: *"Reunión urgente con el cliente principal."*
>
> **Agente**: *"Reunión con cliente (alta prioridad, 1h). Te propongo hoy 16:00, mañana 10:00, mañana 15:00. ¿Cuál?"* [botones]

---

## Caso 11 — Manejo de ambigüedad e información faltante

**Cuándo aplica**: el mensaje del usuario es incompleto o ambiguo.

**Qué hace el agente**: **pregunta, no asume** en lo importante. Sí asume valores por defecto razonables en lo secundario (duración según tipo de actividad, ver Caso 12).

**Ejemplos de qué pregunta**:
- Fecha ambigua: *"el martes"* → *"¿Este martes (día 22) o el siguiente?"*
- Persona no clara: *"reunión con el proveedor"* → *"¿Qué proveedor?"*
- Categoría dudosa: *"comida con Pedro"* → *"¿Es de trabajo o personal?"*

**Ejemplos de qué asume sin preguntar**:
- Duración cuando el tipo de actividad es conocido (Caso 12).
- Prioridad, según la regla del Caso 8.

---

## Caso 12 — Inferencia de tipo de actividad y duración

**Cuándo aplica**: el usuario no especifica cuánto durará el evento.

**Qué hace el agente**: detecta el tipo de actividad por las palabras usadas y aplica una duración por defecto:

| Tipo de actividad | Duración por defecto |
|-------------------|----------------------|
| Llamada           | 10 min               |
| Reunión interna   | 30 min               |
| Reunión con cliente/proveedor | 60 min       |
| Tarea administrativa | 20 min             |
| Email/gestión     | 15 min               |
| Desplazamiento    | 30 min               |

El usuario puede ajustar la duración si no le encaja:
> *"Pon la reunión con marketing a 1h y media."*

Los tipos y duraciones son **configurables** (el cliente puede editarlos según su realidad).

---

## Caso 13 — Respeto de bloques "no negociables"

**Cuándo aplica**: al proponer o agendar cualquier cosa.

**Qué hace el agente**: nunca agenda ni propone huecos que caigan sobre bloques fijos definidos por el usuario:
- Comidas.
- Cenas.
- Tiempo familiar.
- Horas libres declaradas.
- Días libres declarados.

Estos bloques se configuran una vez y se pueden ajustar cuando el usuario quiera:
> *"A partir de ahora, los viernes reservo la tarde para mi hija."*

---

## Caso 14 — Sincronización con el calendario existente

**Cuándo aplica**: siempre. El agente **no** empieza de cero — trabaja sobre lo que el cliente ya tiene.

**Qué hace el agente**:
- **Lee** todos los eventos de Bitrix (que ya incluye Google Calendar, Office y los creados manualmente en Bitrix).
- **Escribe** los nuevos eventos en Bitrix, que los propaga automáticamente a Google y Office gracias a la sincronización que el cliente ya tiene configurada.

**Resultado para el usuario**:
- En el móvil sigue viendo todo en Google Calendar (como siempre).
- En el portátil sigue viendo todo en Bitrix Calendar (como siempre).
- No pierde nada, no duplica nada, no cambia de herramienta.

---

## Caso 15 — Acceso rápido (shortcut)

**Cuándo aplica**: siempre que quiera hablar con el agente o ver la agenda.

**Cómo funciona**:
- En **móvil**: Telegram con acceso directo en la home screen. Un tap → chat con el bot. Botón fijo *"📅 Ver mi agenda"* abre el visualizador.
- En **portátil**: Telegram Desktop. Mismo botón, mismo visualizador.

**No hay que instalar nada extra**, no hay app propia que mantener, no hay login nuevo. Todo vive dentro de Telegram, que el cliente ya usa.

---

## Resumen visual — qué puede hacer el usuario

| Acción                      | Por chat (texto/voz) | Desde el visualizador |
|-----------------------------|----------------------|------------------------|
| Añadir evento               | ✅                    | ❌ (v1)                |
| Consultar agenda            | ✅                    | ✅                     |
| Mover evento                | ✅                    | ❌ (v1)                |
| Cancelar evento             | ✅                    | ❌ (v1)                |
| Cambiar prioridad           | ✅                    | ❌ (v1)                |
| Configurar bloques fijos    | ✅                    | ❌ (v1)                |
| Ver detalle de evento       | ✅                    | ✅                     |
| Ver disponibilidad semanal  | ✅ (limitado)         | ✅ (completo)          |

En v1 el visualizador es solo lectura. En una v2 se puede añadir edición directa desde ahí si el cliente lo pide después de usarlo un tiempo.
