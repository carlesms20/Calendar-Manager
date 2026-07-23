# Especificaciones — Agente de Agenda

## 1. Resumen ejecutivo

Agente conversacional que ayuda al cliente a gestionar su agenda personal y profesional mediante mensajes de texto y voz. El agente lee la agenda existente del cliente desde Bitrix24 (que ya agrega Google Calendar, Office y eventos manuales), permite crear nuevos eventos/tareas por voz o texto, los clasifica automáticamente por prioridad y los agenda en el hueco adecuado. La interacción ocurre desde Telegram, y la visualización se hace mediante una Mini App de Telegram (integrada en la propia app, accesible tanto en móvil como en portátil con un shortcut).

---

## 2. Arquitectura general

```
[Usuario]
   │
   │  (texto / audio)
   ▼
[Bot de Telegram] ──── [Mini App de Telegram] (visualizador)
   │                        │
   └──────────┬─────────────┘
              ▼
     [Backend Python (FastAPI)]
              │
     ┌────────┼──────────┐
     ▼        ▼          ▼
  [LLM]  [BD local]  [Bitrix24 API]
 (Haiku    (SQLite)     │
  4.5)                  ▼
                  Google Calendar + Office
                  (vía sincronización nativa
                   de Bitrix, ya configurada)
```

**Principio clave**: el backend nunca habla directamente con Google Calendar u Office. Solo con Bitrix24, que ya centraliza todo. Esto reduce integraciones y evita duplicados.

---

## 3. Componentes técnicos

### 3.1 Bot de Telegram
- Librería: `python-telegram-bot` o `aiogram` (async, mejor para manejar audio).
- Recibe mensajes de texto y audio del usuario.
- Los audios se transcriben antes de pasarlos al LLM (ver sección 6).
- Envía respuestas de confirmación, propuestas de hueco con botones inline, y notificaciones.
- Expone un botón de menú fijo tipo **"📅 Ver mi agenda"** que abre la Mini App.

### 3.2 Mini App de Telegram (visualizador)
- Frontend web (HTML/JS/CSS o React ligero) servido desde el backend.
- Registrado como Web App del bot vía BotFather.
- Se abre dentro de Telegram, tanto en móvil como en Telegram Desktop.
- Muestra:
  - Vista semanal/diaria del calendario (leído desde Bitrix).
  - Colores por prioridad y por categoría (personal vs empresa).
  - Detalle al hacer tap/click sobre un evento.
- **Solo lectura en la v1** (la creación/edición sigue siendo por chat con el bot). Se puede añadir edición en una v2 si el cliente lo pide.

### 3.3 Backend Python
- Framework: **FastAPI** (async, ligero, buena para APIs y para servir la Mini App).
- Módulos principales:
  - `telegram_handler.py` — recibe eventos del bot.
  - `voice.py` — transcripción de audio (Whisper API o local).
  - `agent.py` — orquesta la conversación con el LLM y las tool calls.
  - `bitrix_client.py` — wrapper de la API de Bitrix24.
  - `scheduler.py` — lógica de asignación de huecos, prioridad, tipos de actividad.
  - `models.py` — esquemas Pydantic.
  - `db.py` — SQLite local para configuración, tipos de actividad, historial.

### 3.4 Base de datos local (SQLite)
No almacena eventos (esos viven en Bitrix). Sí almacena:
- Configuración del usuario (horarios laborales, bloqueos fijos, etc.).
- Diccionario de tipos de actividad con duraciones por defecto.
- Historial de conversaciones (opcional, para contexto en sesiones largas).
- Cache de eventos recientes (para respuestas rápidas del visualizador).

### 3.5 Integración con Bitrix24
- API REST de Bitrix24 vía webhook entrante (más simple que OAuth para uso personal).
- Endpoints principales usados:
  - `calendar.event.get` — leer eventos existentes.
  - `calendar.event.add` — crear nuevos eventos.
  - `calendar.event.update` — modificar existentes.
- La sincronización Bitrix ↔ Google ↔ Office ya está montada por el cliente y no se toca.

---

## 4. Modelo de datos (evento/tarea)

Cada evento manejado por el agente tiene esta estructura mínima:

| Campo             | Tipo         | Requerido | Notas                                          |
|-------------------|--------------|-----------|------------------------------------------------|
| `nombre`          | string       | Sí        | Título del evento.                             |
| `duracion_min`    | int          | Sí        | Se infiere del tipo de actividad si no se da. |
| `fecha_inicio`    | datetime     | Sí        | La asigna el agente si el usuario no la fija. |
| `fecha_limite`    | datetime     | No        | Solo si el usuario la menciona.               |
| `categoria`       | enum         | Sí        | `personal` \| `empresa`                        |
| `tipo_actividad`  | string       | No        | Ej: reunión, llamada, tarea admin.            |
| `involucrado`     | string       | Condicional | Obligatorio si `categoria = empresa`.        |
| `prioridad`       | enum         | Sí        | `alta` \| `media` \| `baja` (calculada).      |

Validación mediante **Pydantic** para garantizar consistencia independientemente del output del LLM.

---

## 5. Criterios de organización

### 5.1 Clasificación personal vs empresa
El agente infiere la categoría del contenido del mensaje. Si duda, pregunta.

### 5.2 Cálculo de prioridad (v1)
Regla simple, ampliable después:

```
prioridad_base = "baja"

si involucra_a_otra_persona:
    prioridad_base = "media"

si fecha_limite:
    dias_hasta_limite = (fecha_limite - hoy).days
    si dias_hasta_limite <= 1: prioridad = "alta"
    si dias_hasta_limite <= 3: prioridad = max(prioridad_base, "media")
    si dias_hasta_limite > 7:  prioridad = prioridad_base
```

### 5.3 Tipos de actividad con duración por defecto
Configurables por el usuario. Valores iniciales sugeridos:

```python
TIPOS_ACTIVIDAD = {
    "llamada":              10,
    "reunión interna":      30,
    "reunión con cliente":  60,
    "tarea administrativa": 20,
    "email/gestión":        15,
    "desplazamiento":       30,
}
```
Si el usuario dice "reunión con Pepe mañana", el agente asume 60 min salvo indicación contraria.

### 5.4 Flujo de confirmación híbrido
- **Personal + prioridad baja/media** → agenda directo, avisa por chat.
  > *"Listo, puse 'dentista' el jueves a las 17h."*
- **Empresa OR prioridad alta** → propone 1-2 huecos con botones inline, espera confirmación.
  > *"Reunión con proveedor X (60 min, urgente). ¿Cuándo? [Mañana 10h] [Mañana 16h] [Otro]"*

---

## 6. LLM y procesamiento

### 6.1 Modelo elegido: Claude Haiku 4.5
- Coste: ~$1/M input, $5/M output — insignificante para un solo usuario.
- Muy consistente con **tool use** (function calling), que es lo que evita los errores de formato que había con Gemini.
- Baja latencia (crítico para conversación por chat).

### 6.2 Estrategia anti-errores de formato
- **No se pide al LLM que devuelva JSON en texto libre.** Se definen tools con schema:
  - `crear_evento(nombre, duracion_min, fecha_inicio, categoria, ...)`
  - `consultar_agenda(fecha_desde, fecha_hasta)`
  - `mover_evento(id, nueva_fecha)`
  - `pedir_aclaracion(pregunta)` — cuando falta info.
- Los parámetros los valida Pydantic antes de ejecutar.
- Si validación falla → reintento con feedback al modelo, o fallback a preguntar al usuario.

### 6.3 Transcripción de voz
- Opción A: **OpenAI Whisper API** (barato, muy buena precisión en español).
- Opción B: `whisper.cpp` corriendo local en el servidor (gratis pero exige recursos).
- Recomendación v1: Whisper API para no complicarse.

---

## 7. Stack técnico completo

| Capa            | Tecnología                                  |
|-----------------|---------------------------------------------|
| Bot             | `python-telegram-bot` o `aiogram`           |
| Backend         | FastAPI + Uvicorn                           |
| LLM             | Anthropic API (Claude Haiku 4.5)            |
| Transcripción   | OpenAI Whisper API                          |
| Validación      | Pydantic v2                                 |
| BD local        | SQLite + SQLAlchemy                         |
| Frontend Mini App | HTML + JS vanilla (o React si se complica) |
| Calendario ext. | Bitrix24 REST API (webhook entrante)        |
| Hosting         | VPS pequeño (2-4€/mes) o Railway/Render     |

---

## 8. Flujos principales (ejemplos)

### 8.1 Añadir evento por voz
1. Usuario manda audio: *"Recuérdame llamar al proveedor Juan mañana antes de comer."*
2. Bot descarga audio → Whisper transcribe → texto pasa al LLM.
3. LLM llama a `crear_evento(nombre="Llamar a Juan (proveedor)", tipo_actividad="llamada", duracion_min=10, categoria="empresa", involucrado="Juan (proveedor)", fecha_limite="mañana 14:00")`.
4. Scheduler calcula prioridad (media: involucra a otra persona, límite <3 días).
5. Como es empresa → propone hueco: *"Puedo agendarlo mañana a las 11:00 o 12:30. ¿Cuál?"* con botones.
6. Usuario confirma → evento se crea vía `calendar.event.add` en Bitrix.
7. Bitrix sincroniza a Google/Office automáticamente.

### 8.2 Consultar agenda
1. Usuario abre la Mini App desde el botón del bot.
2. Mini App hace fetch a `/api/events?from=...&to=...` del backend.
3. Backend consulta Bitrix (o cache si es reciente) y devuelve JSON.
4. Frontend renderiza vista semanal con colores por prioridad/categoría.

---

## 9. Riesgos y consideraciones

- **Latencia de sincronización Bitrix ↔ Google/Office**: confirmar con el cliente que es aceptable. Si es >5 min, informar al usuario que el evento "aparecerá pronto en Google Calendar".
- **Interpretación ambigua de fechas** ("el martes" → ¿este o el que viene?): el agente pregunta si hay duda, no asume.
- **Whisper con audios muy ruidosos o dialectos marcados**: monitorizar precisión las primeras semanas.
- **Coste de API**: prácticamente nulo para un usuario. Escalar tendría que revisarse si se abre a más gente.

---

## 10. Siguientes pasos

1. Confirmar con el cliente:
   - Días/horas bloqueadas fijas (comidas, cenas, familia, horario laboral).
   - Si quiere respuesta del agente también por voz o solo texto.
   - Acceso/credenciales a Bitrix24 (webhook entrante).
2. Setup inicial:
   - Bot creado en BotFather.
   - Mini App registrada.
   - Endpoint de Bitrix probado.
3. Desarrollo iterativo:
   - v1: bot funcional con crear/consultar/mover eventos por texto.
   - v2: añadir audio (Whisper).
   - v3: Mini App con visualizador semanal.
   - v4: ajustes de prioridad, tipos de actividad personalizados por el uso real.
