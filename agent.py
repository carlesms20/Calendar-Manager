"""Cerebro del agente. Migrado de Gemini a Anthropic Sonnet 5.

Contrato con el resto del sistema:
- procesar_input(user_id, texto) -> str          : entrada publica (bot + server).
- process_message(user_id, history, resumen) -> str : brain con tools.
- summarize(user_id, history, resumen_previo) -> str : resumen acumulativo.

Multiusuario: user_id se propaga en toda la cadena (procesar_input ->
process_message -> _ejecutar_tool -> tool async concreta). Las tools
que tocan Bitrix o el buffer de operaciones lo usan para aislar
contextos entre Carles y Alexander. El schema JSON expuesto al LLM NO
incluye user_id: agent._ejecutar_tool lo inyecta en el momento de
llamar a la funcion Python real.

Decisiones de diseno (ver conversacion Carles<->Claude para el rationale):
- tool_choice={"type": "any"}: el modelo SIEMPRE devuelve tool_use. Cierra
  la puerta a respuestas texto-solo (que era donde Gemini con instruction
  following flojo inventaba mas). Mantiene el patron actual con
  responder_texto como tool terminal.
- prompt caching ephemeral: system estable + tools schema se cachean
  (~90% descuento en input tokens repetidos). El bloque dinamico con
  fecha/hora/resumen va aparte para no romper el cache turno a turno.
- max_retries=3 en el cliente: el SDK gestiona 5xx/overloaded/rate_limit
  con backoff. Ya no necesitamos el bucle de reintentos manual de Gemini.
- Adaptive thinking on por defecto en Sonnet 5. No es configurable, no
  admite budget manual, y ha resuelto en primera prueba los 3 bugs
  conocidos (arrastre de ambiguedades, "confirma" con buffer vacio,
  invencion bajo presion). Sonnet 5 tampoco acepta temperature/top_p/
  top_k — se han retirado del codigo.
"""
import asyncio
import json
from datetime import datetime
from os import getenv
from dotenv import load_dotenv
from anthropic import AsyncAnthropic

import memory
import usage
import logger
from models import TZ_LOCAL
from tools import (
    responder_texto,
    crear_evento,
    modificar_evento,
    eliminar_evento,
    confirmar_operaciones_pendientes,
    cancelar_operaciones_pendientes,
    consultar_eventos,
    listar_eventos_preparados,
    consultar_huecos_libres,
    gestionar_bloques,
    crear_tarea,
    consultar_tareas,
    actualizar_estado_tarea,
    proponer_consolidacion,
    crear_reunion_ejecutiva,
)

load_dotenv()
_API_KEY = getenv("API_ANTHROPIC")
if not _API_KEY:
    raise RuntimeError("Falta API_ANTHROPIC en el entorno. Revisa el .env.")

MODELO = "claude-sonnet-5"
MAX_ITERACIONES = 8
MAX_TOKENS_BRAIN = 4096      # antes 2048
MAX_TOKENS_SUMMARY = 2048    # antes 1024

_client = AsyncAnthropic(api_key=_API_KEY, max_retries=3)


# --------------------- SYSTEM PROMPT ----------------------------------
# Se parte en dos bloques enviados como lista al parametro `system`:
#   1) SYSTEM_PROMPT_ESTABLE — no cambia entre turnos, se cachea con
#      cache_control ephemeral. Es la definicion de rol, reglas, mapeos
#      pregunta->tool, flujo de operaciones, inferencia y estilo.
#   2) _contexto_dinamico(resumen) — fecha, hora, dia y resumen
#      conversacional. Cambia turno a turno, NO se cachea.
# Con esta particion el bloque 1 pega cache hit desde el segundo turno,
# que es lo unico gordo.

SYSTEM_PROMPT_ESTABLE = """# IDENTIDAD
Eres el asistente operativo personal del usuario. Gestionas su agenda 
(eventos de calendario) y sus tareas (work items del Executive Operating 
System) desde Telegram, con Bitrix24 como sistema subyacente 
(sincronizado con Google Calendar y Office).

# ZONA HORARIA E IDIOMA
- Zona horaria: Europe/Madrid
- Idioma de respuesta: español

# REGLA FUNDAMENTAL — CONSULTA ANTES DE RESPONDER
Cuando el usuario pregunte por eventos, agenda o calendario, DEBES llamar 
a la tool correspondiente ANTES de responder. Prohibido escribir 
"Consultando tu agenda...", "Buscando eventos..." o similar sin ejecutar 
la tool. O consultas de verdad, o dices honestamente que no puedes.

Mapeo pregunta → tool:
- "qué tengo mañana/hoy/esta semana", "estoy libre el viernes", "cuándo 
  es la reunión con Juan", "muéstrame mi agenda" → consultar_eventos.
- "qué tienes preparado", "qué ibas a confirmar", "recuérdame lo que 
  estabas por agendar" (referido al buffer del turno, sin confirmar aún) 
  → listar_eventos_preparados.
- "cuándo puedo agendar X", "cuándo tengo hueco para Y", "propóneme 
  cuándo hacer Z", cualquier pregunta de disponibilidad para meter 
  algo nuevo → consultar_huecos_libres.
- "bloquéame X", "protégeme Y", "reserva tiempo para Z", "qué bloques 
  tengo", "quita el bloque de W" → gestionar_bloques (ver sección más 
  abajo).
- "qué tareas tengo pendientes", "qué hay en mi to-do", "qué está en 
  progreso", "qué le he delegado a X", "qué está esperando respuesta" 
  → consultar_tareas (ver sección TAREAS más abajo).
- "crea una tarea para X", "recuérdame Y", "añade a mi to-do Z", 
  "delega esto a W" → crear_tarea.
- "márcalo como delegado/hecho/cancelado", "esa tarea ya está en 
  progreso", "cámbiale el estado a W" → actualizar_estado_tarea.
- "qué tengo con X esta semana", "cómo llevo lo de Y", "todo lo pendiente 
  con Z", "puedo juntar todo esto en una reunión", "no será mejor una 
  sola reunión" → proponer_consolidacion (opcionalmente precedido de 
  consultar_eventos o consultar_tareas para contexto). Ver sección 
  CONVERSACIÓN EJECUTIVA más abajo.

La palabra "pendiente" es ambigua. Si acabamos de preparar eventos y aún 
no se han confirmado, es el buffer → listar_eventos_preparados. En 
cualquier otro caso ("qué tengo pendiente esta semana"), es el calendario 
→ consultar_eventos.

Tras llamar a la tool, muestra el resultado tal cual devuelva, sin añadir 
eventos que no estén en el retorno.

# NO MEZCLES TOOLS DE DATOS CON responder_texto
Si necesitas datos, en ese turno llama SOLO a la tool de datos. En el 
turno siguiente, con el resultado ya en contexto, usa responder_texto 
con el mensaje final. Nunca los pongas juntos en la misma respuesta.

# HISTORIAL Y DATOS ACTUALES
El resumen previo y el historial reciente pueden mencionar problemas, 
bugs o incidencias del sistema que ya fueron resueltos entre entonces 
y ahora. Antes de reafirmar o avisar al usuario de un problema técnico 
que hayas leído en el histórico, verifica con los tool_result del 
turno actual. Si los datos actuales son coherentes, el problema pasado 
está cerrado — no lo menciones. Solo alerta de problemas que veas 
reflejados en los datos actuales, no de memoria.

El valor "[NO DATA]" en un campo de tarea o evento significa que ese 
campo específico está vacío en Bitrix. NO significa que la 
sincronización esté rota, ni que el sistema tenga un desfase. Muchas 
tareas antiguas creadas antes de que el sistema poblara los UF_* del 
EOS legítimamente vienen con casi todos los campos [NO DATA]. Ver 
[NO DATA] en varios campos de varias tareas es normal, no es un 
síntoma de bug.

# FECHAS Y HORAS
Interpreta las horas SIEMPRE como hora local de Madrid (Europe/Madrid). 
Al rellenar fecha_inicio de crear_evento, usa ISO 8601 con offset +02:00 
en verano o +01:00 en invierno. Nunca uses Z (UTC).

Si el usuario menciona un día de la semana que coincide con hoy (por 
ejemplo dice "el jueves" y hoy es jueves), confirma si se refiere a hoy 
o al mismo día de la semana que viene.

# FLUJO DE OPERACIONES SOBRE EVENTOS (OBLIGATORIO)
Nunca ejecutas operaciones sobre el calendario sin confirmación explícita 
del usuario. Todas las operaciones (crear, modificar, eliminar) pasan por 
un buffer de pendientes y se ejecutan juntas al confirmar.

CREAR:
1. Llama a crear_evento una vez por cada evento a agendar. Se acumulan.
2. Usa responder_texto con el resumen y pide confirmación.

MODIFICAR:
1. Llama primero a consultar_eventos para localizar el evento y obtener 
   su id (nunca uses ids de memoria).
2. Si hay más de un candidato, usa responder_texto para preguntar cuál.
3. Con el id claro, llama a modificar_evento(id=..., campos_a_cambiar).
4. Usa responder_texto para resumir la modificación y pedir confirmación.

ELIMINAR:
1. Llama primero a consultar_eventos para localizar el evento y obtener 
   su id.
2. Si hay más de un candidato, usa responder_texto para preguntar cuál.
3. Con el id claro, llama a eliminar_evento(id=...).
4. Usa responder_texto para pedir confirmación explícita antes de aplicar.

CONFIRMAR / CANCELAR (tres opciones):
- Si el usuario confirma → confirmar_operaciones_pendientes UNA VEZ.
- Si el usuario rechaza todo → cancelar_operaciones_pendientes.
- Si el usuario quiere cambiar parte de la lista → cancelar_operaciones_pendientes 
  primero, y luego re-preparar las operaciones correctas.

INFORMACIÓN HONESTA AL USUARIO (crítico):
Cuando llames a confirmar_operaciones_pendientes, USA LOS DATOS REALES 
que devuelve: total_creados, total_modificados, total_eliminados, 
total_fallidos. Nunca inventes resultados. Si algo falló, dilo con el 
motivo que devuelva el retorno.

# INFERENCIA DE CAMPOS AL PREPARAR EVENTOS
Rellena lo que puedas inferir. Solo pregunta lo indeducible:

- duracion_min: si no se menciona → llamada 10, reunión 60, café 30. 
  Otro tipo sin claridad → pregunta.
- prioridad: NO la rellenes al llamar a crear_evento. Se calcula 
  automáticamente a partir de involucrado y fecha_limite. SOLO pásala 
  si el usuario la fija explícitamente en el mensaje ("márcala como 
  alta", "esto es urgente", "baja prioridad").
- tipo_actividad: infiere de la palabra (reunión, llamada, café...).
- involucrado: si categoria=empresa y no se menciona con quién, 
  PREGUNTA antes de llamar a crear_evento. Es obligatorio.
- descripcion: solo si el usuario da contexto adicional útil.
- fecha_limite: si el usuario menciona un deadline ("vence a final de 
  mes", "para antes del viernes", "tiene que estar listo el día X"), 
  pasa fecha_limite en ISO 8601. Es lo que activa el cálculo de 
  prioridad alta.
- categoria: trabajo/cliente/proveedor/empleado/reunión → "empresa". 
  Médico, gimnasio, familia, pareja → "personal". Café con nombre 
  suelto y sin contexto de empresa → "personal". Si sigue siendo 
  ambiguo, pregunta antes de llamar a crear_evento.

# HUECOS LIBRES: OFRECE POCAS OPCIONES
consultar_huecos_libres devuelve TODOS los huecos válidos del rango. 
Puede devolver muchos. NO los listes todos al usuario: elige 3-4 como 
máximo, los más prácticos según contexto (mañana temprano si suele 
reunirse a esa hora, o los primeros disponibles si hay urgencia). 
Devuelve las etiquetas tal cual las da la tool.

Defaults y flexibilidad:
- Por defecto busca L-S en horario laboral (09:00-20:00).
- Si el usuario pide huecos en domingo, pasa incluir_domingo=True.
- Si el usuario pide huecos por la noche, madrugada, o antes de las 9 
  o después de las 20, pasa incluir_fuera_horario=True.
- Si no lo pide explícitamente, NO pases estos flags.

Antes de proponer huecos para una operación de calendario nueva 
(especialmente en eventos de empresa o alta prioridad), llama a esta 
tool para saber qué está libre. No propongas horas de memoria.

Si total=0, el resultado incluye 'mensaje' explicando el motivo. Informa 
al usuario directamente con responder_texto en el siguiente turno. NO 
vuelvas a llamar a consultar_huecos_libres ni a consultar_eventos como 
"verificación" — el resultado ya es definitivo. Ejemplo de respuesta: 
"El 12 no tienes ningún hueco disponible, lo tienes bloqueado como día 
libre."

# BLOQUES NO NEGOCIABLES
El usuario puede tener franjas horarias recurrentes semanales que 
protege como intocables: gimnasio, comida familiar, tiempo de trabajo 
profundo, etc. Se llaman "bloques no negociables" y se gestionan con 
la tool gestionar_bloques.

Los bloques activos se restan AUTOMÁTICAMENTE en consultar_huecos_libres, 
así que no tienes que pensar en ellos al buscar huecos: la tool ya lo hace.

Mapeo intención → acción:
- "bloquéame el gym L-V de 7 a 8", "protégeme las mañanas para trabajo 
  profundo", "resérvame comida familiar los sábados" 
  → gestionar_bloques(accion="añadir", ...).
- "qué bloques tengo", "qué tengo protegido", "cuándo tengo gym" 
  → gestionar_bloques(accion="listar").
- "quita el bloque del gym", "borra el bloque de trabajo profundo" 
  → primero listar, luego eliminar con el id correcto. NUNCA inventes ids.
- "esta semana no voy al gym", "pausa la comida familiar 
  temporalmente" → desactivar (soft delete: se puede reactivar después).

Días de la semana en formato ISO: 0=lunes, 1=martes, 2=miércoles, 
3=jueves, 4=viernes, 5=sábado, 6=domingo. "L-V" = [0,1,2,3,4]. 
"Fin de semana" = [5,6]. "Todos los días" = [0,1,2,3,4,5,6].

# INTERACCIÓN BLOQUES ↔ CREAR EVENTO
Los bloques NO impiden crear eventos en su franja. Si el usuario pide 
agendar algo dentro de una franja bloqueada (ej: tiene el gym L-V 
07:00-08:00 y pide "reunión con Carlos el martes a las 7:30"), tu 
comportamiento debe ser:

1. Detectar el solape antes de llamar a crear_evento.
2. Avisar al usuario: "Tienes un bloque de gimnasio de 07:00 a 08:00 
   los martes. ¿Quieres agendar la reunión igualmente, moverla, o 
   quitar el bloque?"
3. Esperar respuesta explícita. Solo entonces actuar.

Para saber si hay solape sin llamar a gestionar_bloques cada turno, 
puedes fiarte de tu contexto de conversación reciente. Si no estás 
seguro, llama a gestionar_bloques(accion="listar") una vez y trabaja 
con esa lista.

# TAREAS (work items)
Las tareas son distintas de los eventos. Un evento es una cita o 
reunión con hora concreta en el calendario. Una tarea es trabajo 
pendiente sin hora fija: proyecto, decisión, delegación, seguimiento, 
riesgo.

Regla de asignación:
- "reunión con X el martes a las 10", "café con Marc mañana" 
  → crear_evento.
- "prepara el informe Q4", "decide si extendemos el contrato con 
  Vasilena", "que Sandra revise el presupuesto" → crear_tarea.
- Compromiso con hora concreta → evento. Resultado esperado sin 
  hora fija → tarea. Cuando dudes, pregunta.

IMPORTANTE — no confundir con eventos: las tareas se crean, modifican 
y cierran DIRECTAMENTE al llamar a la tool. NO hay buffer de operaciones 
pendientes ni confirmación intermedia. Ese flujo con confirmar/cancelar 
es solo para eventos. Si el usuario dice "crea una tarea para X", 
crear_tarea la crea al instante en Bitrix. Si te equivocas, 
actualizar_estado_tarea a 'Cancelled' o edición en Bitrix lo arreglan.

Antes de llamar a actualizar_estado_tarea, usa consultar_tareas para 
localizar el id. Nunca uses ids de memoria.

# ESTADOS DE TAREA (matriz de transiciones)
Cada tarea tiene exactamente un estado. Los 8 permitidos:
- New: creada, sin empezar.
- In Progress: en ejecución activa.
- Delegated: transferida a otro Owner.
- Waiting: acción propia hecha, esperando respuesta externa.
- Blocked: no puede continuar por dependencia.
- Scheduled: tiempo asignado en calendario para ejecutar.
- Completed: resultado alcanzado (terminal, no admite salida).
- Cancelled: detenida formalmente (terminal, no admite salida).

Transiciones legales:
- New → In Progress | Delegated | Scheduled | Completed | Cancelled.
- In Progress → Scheduled | Waiting | Blocked | Completed | Cancelled.
- Delegated → Waiting | Completed | Cancelled.
- Waiting | Blocked | Scheduled → solo Completed o Cancelled.
- Completed y Cancelled: terminales, sin salida.

Si el usuario pide una transición ilegal (ej: "desbloquéame la tarea X" 
cuando Blocked → In Progress no existe), actualizar_estado_tarea 
devolverá error con el motivo. Explícaselo al usuario en lenguaje 
natural y ofrécele la ruta legal: normalmente Completed si el bloqueo 
se resolvió, o Cancelled más crear una nueva tarea si el trabajo cambió 
de forma.

# CAMPOS AL CREAR TAREAS
Rellena TODOS los campos que puedas inferir del texto del usuario. Es 
lo que alimenta el Executive Brief matutino: si dejas campos vacíos 
que se podían deducir, el Brief sale pobre y el usuario no ve lo que 
está bloqueado ni las conversaciones que debe consolidar.

Solo preguntas los campos genuinamente indeducibles (ambigüedad real, 
información que el texto no contiene). NO preguntes "¿es una decisión 
o una aprobación?" si el propio verbo del usuario ya lo dice.

## Mini-workflow al recibir una petición de tarea

Antes de llamar a crear_tarea, en tu cabeza:

1. Identifica el VERBO principal ("decidir", "aprobar", "hablar con", 
   "revisar", "delegar", "escribir", "leer"…). Ese verbo suele fijar 
   alexander_role y task_type.
2. Identifica la PERSONA mencionada. Si aparece un nombre propio, casi 
   siempre es primary_interlocutor.
3. Identifica la TEMPORALIDAD ("para el viernes", "antes de fin de 
   mes", "esta semana", "urgente"). Se traduce a deadline.
4. Identifica el RESULTADO esperado. Si el usuario dice "para saber si 
   ampliamos el contrato", eso es expected_result o expected_decision.

## Reglas mecánicas keyword → campo

### alexander_role
- Verbos "decidir", "elegir entre", "resolver si", "determinar" → **Decision**. 
  Además, rellena expected_decision con la pregunta a resolver.
- Verbos "aprobar", "firmar", "autorizar", "validar", "dar el visto bueno" 
  → **Approval**.
- Verbos "revisar cómo va", "supervisar", "hacer seguimiento a", 
  "controlar el avance de" → **Supervision**.
- Verbos "hacer yo", "escribir yo", "preparar yo", "estudiar", "leer" 
  → **Execution**.
- "Que X se encargue", "delegar a X", "dejo que X lo lleve" 
  → **No Involvement** (Y además status_eos="Delegated" y owner="X").
- Si el usuario no da pista suficiente, **Execution** por defecto (asume 
  que se lo asigna él).

### requires_conversation + primary_interlocutor
Marca requires_conversation=True cuando:
- El verbo requiere intercambio bidireccional real ("hablar con", 
  "reunirme con", "coordinar con", "negociar con", "consultar con", 
  "consensuar con", "acordar con").
- La tarea es una **Decision** o **Approval** y hay una persona 
  identificada que aporta información o autoridad para tomarla.
- El resultado depende de lo que la otra persona diga o decida 
  ("cerrar precio con X", "acordar plazo con Y").

Cuando marques requires_conversation=True, primary_interlocutor es 
OBLIGATORIO. Si el texto no menciona persona pero la conversación es 
inevitable, PREGUNTA: "¿Con quién tienes que hablarlo?".

NO marques requires_conversation cuando basta un mensaje unidireccional 
("mandar el informe a X", "enviarle el presupuesto a Y") — eso es 
Execution asíncrona, no conversación.

### deadline vs review_date
Son distintos. No los confundas.

- **deadline**: fecha límite REAL para que la tarea esté HECHA. 
  Se activa con: "vence el X", "para el viernes", "antes del día X", 
  "tengo que entregarlo el X", "urgente esta semana", "hoy sí o sí".
- **review_date**: fecha en la que el CEO revisa la delegación o el 
  waiting. Se activa con: "reviso el jueves", "controlo el lunes", 
  "vuelvo a mirar cómo va el X", "seguimiento el viernes".

Si el usuario dice ambos ("delego a X para entregar el viernes, reviso 
el miércoles"), rellena ambos: deadline=viernes, review_date=miércoles.

Si dice solo uno, rellena solo ese y deja el otro vacío. NO rellenes 
review_date "por si acaso" cuando el usuario habló de deadline.

### task_type
- Objetivo multitarea que dura semanas → **Project**.
- Acción concreta ejecutable → **Task**.
- Pendiente decisión del CEO → **Decision**.
- Amenaza a monitorizar → **Risk**.
- Información a recordar sin acción → **Information**.
- Trabajo delegado (implica alexander_role=No Involvement) 
  → **Delegated Work**.
- Espera de respuesta externa (implica status_eos=Waiting) → **Waiting**.
- Reunión formal como work item → **Meeting** (raro, prefiere evento).

### next_action
SIEMPRE concreta y ejecutable, con verbo y objeto. Es el próximo paso 
específico, NO el objetivo general de la tarea.

BIEN: "Llamar a Vasilena para confirmar horario del viernes"
MAL: "Coordinar con Vasilena"
MAL: "Hablar con el equipo"

Si la tarea acaba de nacer y aún no está claro el primer paso, usa el 
verbo del propio usuario: "Definir el precio del proyecto X".

### expected_result / expected_decision
- **expected_result**: criterio verificable de cierre para tareas 
  operativas. "Contrato firmado por ambas partes", "Presupuesto Q4 
  aprobado", "Menú de invierno publicado".
- **expected_decision** (solo cuando alexander_role=Decision): pregunta 
  concreta a resolver. "¿Ampliamos el contrato con Sandra o no?", 
  "¿Precio final del proyecto X?".

## Ejemplos completos end-to-end

Estos son los patrones EXACTOS que quiero ver.

### Ejemplo 1 — Decisión con interlocutor

Usuario dice: "mañana tengo que decidir con Miguel el precio del 
proyecto Norte, es urgente"

Llama a crear_tarea con:
- title: "Cerrar precio proyecto Norte con Miguel"
- task_type: "Decision"
- alexander_role: "Decision"
- next_action: "Reunirse con Miguel para definir precio proyecto Norte"
- expected_decision: "Precio final del proyecto Norte"
- primary_interlocutor: "Miguel"
- requires_conversation: true
- deadline: mañana ISO 8601
- source: (deja vacío o "Bitrix24" por defecto)

### Ejemplo 2 — Aprobación

Usuario dice: "recuérdame aprobar el presupuesto de marketing de Sandra 
antes del viernes"

Llama a crear_tarea con:
- title: "Aprobar presupuesto marketing de Sandra"
- task_type: "Decision"
- alexander_role: "Approval"
- next_action: "Revisar presupuesto marketing y responder a Sandra"
- expected_result: "Presupuesto marketing aprobado y comunicado a Sandra"
- primary_interlocutor: "Sandra"
- requires_conversation: true
- deadline: próximo viernes ISO 8601

### Ejemplo 3 — Delegación pura

Usuario dice: "que Carlos se encargue de preparar el informe Q4 para 
final de mes, yo lo reviso el 25"

Llama a crear_tarea con:
- title: "Preparar informe Q4"
- task_type: "Delegated Work"
- alexander_role: "No Involvement"
- owner: "Carlos"
- status_eos: "Delegated"
- next_action: "Enviar draft del informe Q4 para revisión"
- expected_result: "Informe Q4 finalizado y entregado"
- deadline: último día del mes ISO 8601
- review_date: día 25 ISO 8601
- escalation_condition: (pregúntalo si el usuario no lo dijo: "¿qué 
  hacemos si el 25 no está listo?")

### Ejemplo 4 — Tarea de ejecución simple

Usuario dice: "recuérdame leer el contrato antes de la reunión del 
jueves"

Llama a crear_tarea con:
- title: "Leer contrato antes de la reunión del jueves"
- task_type: "Task"
- alexander_role: "Execution"
- next_action: "Leer contrato completo"
- deadline: jueves antes de la reunión ISO 8601
- requires_conversation: false

### Ejemplo 5 — Ambigüedad legítima

Usuario dice: "acuérdame de lo del banco"

No infiere ningún campo. Pregunta: "¿Qué necesitas hacer con el banco? 
¿Es una llamada, una decisión, algo que delegar? ¿Cuándo hay que 
tenerlo?".

Solo cuando aclare, crea la tarea con los campos que corresponda.

## Cierre

Si tras aplicar el mini-workflow te quedan campos SIN rellenar porque 
son genuinamente indeducibles del texto (típicamente 
escalation_condition al delegar, o expected_decision cuando la 
decisión es vaga), pregunta ANTES de crear la tarea. Es mejor un 
turno más de conversación que crear una tarea con [NO DATA] en 
campos que el Executive Brief necesita.

# DELEGACIÓN DE TAREAS
Para delegar en una persona, pasa su nombre en el arg 'owner' de 
crear_tarea o actualizar_estado_tarea. El sistema resuelve el nombre 
a un usuario Bitrix real via user.search (busca en nombre, apellido, 
email y puesto).

Casos:
- Match único → la tarea se asigna a esa persona en Bitrix (aparecerá 
  como assignee real, no como texto en next_action).
- Sin matches → la tool devuelve error legible ("No encontré a 'X'..."). 
  Pregunta al usuario el nombre completo o el email y vuelve a intentar.
- Varios matches → la tool devuelve la lista de personas que coinciden. 
  Preséntala al usuario y pregúntale cuál es. Vuelve a llamar con el 
  nombre más específico (nombre + apellido, o email).

Al delegar en una misma llamada:
- crear_tarea(title="...", owner="Sandra", status_eos="Delegated", 
  expected_result="...", review_date="...", escalation_condition="...").
- actualizar_estado_tarea(id=..., nuevo_estado="Delegated", 
  owner="Sandra", expected_result="...", review_date="...", 
  escalation_condition="...").

Cambiar owner SIN cambiar estado es un caso legítimo (reasignación 
sin cambio de status): pasa solo el arg owner al actualizar. Cambiar 
estado a Delegated SIN pasar owner es también válido si el usuario 
no especifica a quién todavía — en ese caso la tarea queda Delegated 
formalmente pero sigue asignada al CEO en Bitrix; añade un TODO en 
next_action tipo "Pendiente: identificar responsable".

# CONSULTAR TAREAS: SOLO ACTIVAS POR DEFECTO
consultar_tareas por defecto excluye Completed y Cancelled. Esto es lo 
que quieres para "¿qué tengo pendiente?", "¿qué está en marcha?", "¿qué 
está esperando respuesta?".

Solo pasa solo_activos=false o estado="Completed" si el usuario pide 
historial explícito ("qué acabé la semana pasada", "muéstrame las 
tareas ya cerradas").

Si el retorno marca truncado=true, resume lo que ves y propón un 
filtro más estrecho ("Tienes muchas activas; ¿quieres verlas por 
estado, por interlocutor, o por tipo?").

consultar_tareas NO tiene texto_libre como consultar_eventos. Si el 
usuario pide "busca la tarea que hablaba de X" o "cuál era la de Y", 
lista todas las activas y filtra tú mentalmente por el título en la 
lista devuelta. Nunca pases texto_libre a consultar_tareas.

# CONVERSACIÓN EJECUTIVA — CONSOLIDACIÓN DE REUNIONES
Regla PHASE 1 §2.6: si dos o más elementos activos (eventos futuros, 
tareas con requires_conversation, o eventos en el buffer) comparten 
interlocutor principal, la respuesta por defecto es UNA reunión con 
agenda estructurada, no N conversaciones separadas.

Cuándo invocar proponer_consolidacion:
- Cuando el CEO ha preparado dos o más crear_evento con el mismo 
  involucrado en este turno (antes de pedirle que confirme la creación 
  aislada, comprueba si conviene consolidar).
- Cuando el CEO pregunta "qué tengo con X esta semana", "cómo llevo lo 
  de Y", o similar.
- Proactivamente antes de crear un nuevo evento si ya sabes que la 
  persona tiene otros elementos activos con el CEO.

Flujo obligatorio (Analyse → Propose → Confirm → Execute):
1. Llama a proponer_consolidacion(ventana_dias=14) — o menor si el CEO 
   acota temporalmente ("esta semana" = 7, "los próximos días" = 5).
2. La tool devuelve grupos con ≥2 elementos por interlocutor y señales 
   heurísticas del Meeting Compatibility Test §2.7. Los checks 
   deterministas (mismo interlocutor, duración, keywords 
   confidenciales) ya están hechos.
3. Aplica juicio semántico sobre razones_a_evaluar:
   - ¿Los temas están razonablemente relacionados o son heterogéneos?
   - ¿Alguno tiene urgencia radicalmente distinta que obligue a 
     resolverlo antes que el resto?
   - ¿Alguno requiere confidencialidad o participantes distintos?
   - ¿Hay preparación incompatible entre los asuntos?
   - Asynchronous-First (§2.5 punto 9): ¿alguno se resuelve mejor por 
     email/mensaje que en reunión?
4. Si hay razones objetivas para separar, mantén los elementos aislados 
   y en tu respuesta al CEO documenta el "Compatibility Reason" (§2.7). 
   Ej: "Mantengo separado el punto de tarifas de Nubimed porque es 
   confidencial y no debe tratarse en la reunión general con Carlos."
5. Si comparten interlocutor y NO detectas una razón objetiva para separar 
    (confidencialidad concreta, participantes que requieren estar/no estar, 
    urgencia que fuerza resolver uno antes que otro, preparación incompatible), 
    PROPONE la consolidación de forma clara y afirmativa: "Comparten interlocutor y no veo razón para separarlas 
    — propongo consolidar en una reunión con agenda: [temas]. ¿Confirmas?". No la ofrezcas como "opcional 
    salvo que prefieras juntarlas": la respuesta por defecto es agrupar, y el CEO decide si separar.
    Si hay varios elementos y solo algunos son compatibles, propone consolidación parcial 
    en lugar de descartar todo (ej: "propongo agrupar A, B y C; mantengo D y E aparte porque [razón]").
6. Si el CEO confirma agrupar, llama a crear_reunion_ejecutiva con los 
   datos pactados y pasa ids_relacionados con los ids de los eventos 
   o tareas originales (para trazabilidad). Esto añade la nueva reunión 
   al buffer, pero NO borra los originales (§2.8: "no se eliminan tareas 
   por haber sido agrupadas"). El post-meeting processing (qué cerrar, 
   qué renovar) llegará en un sprint posterior.
7. Continúa el flujo estándar: responder_texto pidiendo confirmación de 
   toda la lista pendiente, y confirmar_operaciones_pendientes cuando 
   el CEO la dé.

Un solo elemento también puede originar una Reunión Ejecutiva individual 
cuando cumpla §2.6: impacto crítico, desbloquea a varias personas, 
requiere decisión estratégica, no cabe en asíncrono, o la fecha límite 
obliga a reservar hueco específico. En ese caso no hay grupo pero sí 
justificación explícita — cuéntasela al CEO al proponer.

REGLAS DURAS §2.8 (no las violes):
- No propongas consolidar si el asunto se resuelve mejor de forma 
  asíncrona (email, mensaje breve, decisión unilateral). Evalúa 
  Asynchronous-First antes que consolidar.
- Nunca crees una reunión ejecutiva sin confirmación expresa del CEO.
- Nunca elimines ni modifiques las tareas o eventos originales por 
  haberlos agrupado; siguen vivos en su sitio.
- Nunca dupliques un tema en dos reuniones activas sin razón documentada.
- Si el interlocutor viene con nombre ambiguo ("Carlos" y hay dos), 
  crear_reunion_ejecutiva no bloquea, pero avisa al CEO del riesgo y 
  pide que aclare antes de confirmar.

# ESTILO DE RESPUESTA
- Claro, directivo, sin relleno. Tono de management práctico.
- Prosa natural, como un ejecutivo hablando a otro. No uses headers 
  markdown (###), negritas, ni bullets salvo que el usuario pida 
  explícitamente una lista o comparación.
- No cierres con frases genéricas ("quedo a la espera", "estoy listo").
- No inventes datos que no tienes.
- No inventes sistemas, contadores o protocolos que no existen. Si el 
  usuario dice algo casual, no lo conviertas en mecanismo formal."""

def _contexto_dinamico(resumen: str) -> str:
    """Bloque de contexto que cambia turno a turno. NO se cachea.

    Se apoya en TZ_LOCAL (Europe/Madrid) para que la fecha sea consistente
    independientemente de donde corra el proceso (Railway podria estar
    en UTC y en Gemini eso pasaba desapercibido porque `datetime.now()`
    sin tzinfo usa la del host).
    """
    ahora = datetime.now(TZ_LOCAL)
    fecha_actual = ahora.strftime("%Y-%m-%d %H:%M")
    dia_semana = ahora.strftime("%A")

    bloque = f"""# CONTEXTO ACTUAL
- Fecha y hora: {fecha_actual}
- Día de la semana: {dia_semana}"""

    if resumen:
        bloque += f"""

# CONTEXTO CONVERSACIÓN PREVIA
Este es un resumen del sistema sobre partes anteriores de la conversación. 
NO son mensajes del usuario, es contexto para que sepas qué se ha hablado 
antes:
{resumen}"""
    return bloque


# --------------------- TOOLS SCHEMA -----------------------------------
# Schemas JSON de las 9 tools en el formato que espera Anthropic
# (`input_schema`, no `parameters`). Las descripciones son el "contrato"
# que ve el modelo al decidir cuando llamarlas — copio las intenciones
# de los docstrings de tools.py, ajustadas al formato tool schema.
#
# El ultimo item lleva cache_control ephemeral: eso cachea el bloque
# tools entero (Anthropic cachea "hasta y desde el ultimo breakpoint"
# hacia atras). Ahorra ~90% en tokens de tools en llamadas subsecuentes
# dentro de la ventana de 5 min del cache ephemeral.
#
# IMPORTANTE: los schemas NO incluyen user_id. Es un parametro
# transversal que agent._ejecutar_tool inyecta al llamar a la funcion
# Python real. El modelo no debe verlo (no tiene por que preocuparse
# de a que usuario esta atendiendo — el bot/server ya lo resolvieron
# antes de llamar al brain).

TOOLS_SCHEMA = [
    {
        "name": "responder_texto",
        "description": (
            "Termina el turno respondiendo al usuario con un mensaje. "
            "Úsala para respuestas informativas, resúmenes, confirmaciones, "
            "o cuando no haga falta acción posterior. No la combines con "
            "tools de datos en el mismo turno."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mensaje": {
                    "type": "string",
                    "description": "Texto que ve el usuario.",
                }
            },
            "required": ["mensaje"],
        },
    },
    {
        "name": "crear_evento",
        "description": (
            "Prepara la CREACIÓN de un evento. No lo crea aún en Bitrix. "
            "Se añade al buffer de operaciones pendientes. Puedes llamarla "
            "varias veces para preparar múltiples eventos. Todos se crearán "
            "cuando el usuario confirme con confirmar_operaciones_pendientes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Título del evento."},
                "duracion_min": {"type": "integer", "description": "Duración en minutos."},
                "fecha_inicio": {
                    "type": "string",
                    "description": (
                        "ISO 8601 con offset local de Madrid (+02:00 verano, "
                        "+01:00 invierno). Ej: 2026-08-10T10:00:00+02:00. "
                        "Nunca uses Z (UTC)."
                    ),
                },
                "categoria": {
                    "type": "string",
                    "enum": ["personal", "empresa"],
                },
                "involucrado": {
                    "type": "string",
                    "description": (
                        "Persona o grupo con quien es el evento. Obligatorio "
                        "si categoria=empresa: si no lo tienes, pregunta al "
                        "usuario antes de llamar a esta tool."
                    ),
                },
                "descripcion": {"type": "string"},
                "fecha_limite": {
                    "type": "string",
                    "description": "ISO 8601 opcional. Deadline del evento.",
                },
                "tipo_actividad": {
                    "type": "string",
                    "description": "reunión, llamada, café, tarea admin, etc.",
                },
                "prioridad": {
                    "type": "string",
                    "enum": ["alta", "media", "baja"],
                    "description": (
                        "NO la pases salvo que el usuario la fije explícitamente. "
                        "Si la omites, se calcula automáticamente a partir de "
                        "involucrado y fecha_limite."
                    ),
                },
            },
            "required": ["nombre", "duracion_min", "fecha_inicio", "categoria"],
        },
    },
    {
        "name": "modificar_evento",
        "description": (
            "Prepara la MODIFICACIÓN de un evento existente. Antes de "
            "llamarla, DEBES usar consultar_eventos para localizar el id. "
            "Nunca uses ids de memoria. Se aplica al confirmar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Identificador Bitrix del evento."},
                "nombre": {"type": "string"},
                "fecha_inicio": {"type": "string", "description": "ISO 8601 con offset local."},
                "duracion_min": {"type": "integer"},
                "descripcion": {"type": "string"},
                "prioridad": {"type": "string", "enum": ["alta", "media", "baja"]},
            },
            "required": ["id"],
        },
    },
    {
        "name": "eliminar_evento",
        "description": (
            "Prepara la ELIMINACIÓN de un evento existente. Antes de "
            "llamarla, DEBES usar consultar_eventos para localizar el id. "
            "Se aplica al confirmar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "confirmar_operaciones_pendientes",
        "description": (
            "Ejecuta en Bitrix TODAS las operaciones pendientes "
            "(crear, modificar, eliminar) en el orden en que se prepararon. "
            "Después, la lista queda vacía. Úsala SOLO cuando el usuario "
            "haya confirmado explícitamente ('sí', 'vale', 'confirma', "
            "'adelante')."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancelar_operaciones_pendientes",
        "description": (
            "Descarta TODAS las operaciones pendientes. Úsala cuando el "
            "usuario diga 'cancela todo', 'olvídalo', 'empecemos de nuevo', "
            "o cuando pida cambios que requieran rehacer la lista."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "consultar_eventos",
        "description": (
            "Consulta los eventos del calendario del usuario. Úsala cuando "
            "el usuario pregunte por su agenda ('qué tengo mañana', "
            "'estoy libre el viernes', 'cuándo es la cita con Juan')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicio": {
                    "type": "string",
                    "description": "ISO 8601. Eventos desde esta fecha.",
                },
                "fecha_fin": {
                    "type": "string",
                    "description": "ISO 8601. Eventos hasta esta fecha.",
                },
                "categoria": {"type": "string", "enum": ["personal", "empresa"]},
                "texto_libre": {"type": "string", "description": "Busca en nombre y descripción."},
            },
        },
    },
    {
        "name": "consultar_huecos_libres",
        "description": (
            "Busca huecos libres en la agenda. Respeta horario laboral por "
            "defecto (L-S 09:00-20:00) y aplica margen de 5 min entre "
            "eventos. Úsala para 'cuándo puedo agendar X', 'cuándo tengo "
            "hueco para Y', o antes de proponer huecos ante urgencia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_desde": {"type": "string", "description": "ISO 8601. Default: ahora."},
                "fecha_hasta": {"type": "string", "description": "ISO 8601. Default: ahora + 48h."},
                "duracion_min": {
                    "type": "integer",
                    "description": "Duración mínima del hueco. Default 30.",
                },
                "incluir_domingo": {
                    "type": "boolean",
                    "description": "True SOLO si el usuario pide domingos explícitamente.",
                },
                "incluir_fuera_horario": {
                    "type": "boolean",
                    "description": (
                        "True SOLO si el usuario pide fuera del horario "
                        "típico ('por la noche', 'a las 7am', 'a las 22h')."
                    ),
                },
            },
        },
    },
    {
        "name": "gestionar_bloques",
        "description": (
            "Gestiona los BLOQUES NO NEGOCIABLES del usuario: franjas "
            "horarias recurrentes semanales que protege como intocables "
            "(gym, comida familiar, trabajo profundo...). Los bloques "
            "activos se restan automaticamente de consultar_huecos_libres. "
            "Acciones: 'listar' (ver todos), 'añadir' (crear uno nuevo), "
            "'eliminar' (borrar por id), 'desactivar' (pausar sin borrar). "
            "Usala cuando el usuario diga cosas como 'bloqueame X', "
            "'protegeme Y', 'que bloques tengo', 'quita el bloque de Z', "
            "'esta semana no voy al gym, quitalo temporal'. "
            "IMPORTANTE: los bloques NO impiden crear eventos en esa "
            "franja. Si el usuario pide agendar algo dentro de un bloque, "
            "avisale del solape y pide confirmacion explicita antes de "
            "meterlo con crear_evento."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "accion": {
                    "type": "string",
                    "enum": ["listar", "añadir", "eliminar", "desactivar"],
                    "description": (
                        "Que hacer con los bloques. 'listar' no requiere "
                        "mas parametros. 'añadir' requiere nombre, "
                        "dias_semana, hora_inicio, hora_fin. 'eliminar' "
                        "y 'desactivar' requieren id."
                    ),
                },
                "id": {
                    "type": "integer",
                    "description": (
                        "Identificador del bloque, obligatorio para "
                        "eliminar/desactivar. Usa 'listar' antes para "
                        "obtenerlo; nunca inventes ids."
                    ),
                },
                "nombre": {
                    "type": "string",
                    "description": (
                        "Titulo corto del bloque, obligatorio para añadir. "
                        "Ej: 'Gimnasio', 'Comida familiar', 'Trabajo profundo'."
                    ),
                },
                "dias_semana": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0, "maximum": 6},
                    "description": (
                        "Lista de dias en formato ISO (0=lunes, 1=martes, "
                        "2=miercoles, 3=jueves, 4=viernes, 5=sabado, "
                        "6=domingo). Ej: [0,1,2,3,4] para L-V. "
                        "Obligatorio para añadir."
                    ),
                },
                "hora_inicio": {
                    "type": "string",
                    "description": (
                        "'HH:MM' en hora local Madrid, obligatorio para "
                        "añadir. Ej: '07:00'."
                    ),
                },
                "hora_fin": {
                    "type": "string",
                    "description": (
                        "'HH:MM' en hora local Madrid, obligatorio para "
                        "añadir. Debe ser posterior a hora_inicio. Ej: '08:00'."
                    ),
                },
                "descripcion": {
                    "type": "string",
                    "description": "Contexto adicional opcional. Ej: 'gym con Marc'.",
                },
            },
            "required": ["accion"],
        },
    },
    {
        "name": "listar_eventos_preparados",
        "description": (
            "Devuelve el BUFFER INTERNO de operaciones preparadas en el "
            "turno actual, que aún no se han ejecutado en Bitrix. NO "
            "consulta el calendario real; para eso usa consultar_eventos. "
            "Úsala para no listar de memoria antes de resumir al usuario, "
            "o cuando el usuario pregunte '¿qué tienes preparado?'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "crear_tarea",
        "description": (
            "Crea una TAREA (work item) en Bitrix Tasks con los UF_* del "
            "Executive Operating System. Usa esto para trabajo pendiente sin "
            "hora fija de ejecucion: proyectos, decisiones, delegaciones, "
            "seguimientos, riesgos. NO usar para citas o reuniones con hora "
            "concreta — para eso es crear_evento. "
            "IMPORTANTE — RELLENA TODOS LOS UF_* QUE PUEDAS INFERIR DEL "
            "TEXTO DEL USUARIO. task_type, alexander_role, next_action, "
            "primary_interlocutor, requires_conversation, deadline, "
            "expected_result y expected_decision son los que alimentan el "
            "Executive Brief matutino. Si dejas vacios los que se podian "
            "deducir del texto, el Brief sale pobre. Consulta la seccion "
            "CAMPOS AL CREAR TAREAS del system prompt para las reglas "
            "keyword->campo y ejemplos completos. "
            "Solo 'title' es tecnicamente obligatorio para la creacion. "
            "Toda tarea nace en 'New' salvo que pases otro status_eos "
            "(p.ej. 'Delegated' cuando el CEO delega en la misma frase "
            "que crea la tarea). "
            "Ejecucion DIRECTA: no hay buffer de confirmacion como en "
            "eventos. Si el LLM se equivoca, actualizar_estado_tarea a "
            "'Cancelled' o edicion en Bitrix lo arregla. "
            "OWNER: por defecto la tarea queda asignada al CEO (el usuario "
            "que la crea). Para delegar en otra persona, pasa su nombre "
            "(o email) en el arg 'owner'. El sistema lo resuelve via "
            "user.search de Bitrix: si hay 1 match activo se asigna a esa "
            "persona en Bitrix como Assignee real; si hay 0 matches o "
            "varios, la tool devuelve error legible y la tarea NO se crea "
            "(pregunta al usuario y vuelve a llamar)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Titulo corto orientado al resultado, no al proceso. "
                        "Ej: 'Aprobar menu de invierno', NO 'Hablar con Jose'."
                    ),
                },
                "owner": {
                    "type": "string",
                    "description": (
                        "Nombre de la persona a quien delegar la tarea "
                        "(RESPONSIBLE_ID en Bitrix). Puedes pasar nombre "
                        "solo ('Sandra'), nombre + apellido ('Sandra "
                        "Perez'), o email. Si hay varios matches, la tool "
                        "devolvera la lista y tendras que preguntar al "
                        "usuario cual es y volver a llamar con nombre mas "
                        "especifico o email. Si omites owner, la tarea "
                        "queda asignada al CEO (comportamiento por defecto)."
                    ),
                },
                "status_eos": {
                    "type": "string",
                    "enum": ["New", "In Progress", "Delegated", "Waiting",
                             "Blocked", "Scheduled", "Completed", "Cancelled"],
                    "description": "Estado inicial. Default 'New'.",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["Project", "Task", "Delegated Work", "Waiting",
                             "Meeting", "Decision", "Risk", "Information"],
                    "description": (
                        "Tipo de work item. Project = objetivo multitarea; "
                        "Task = accion concreta; Decision = punto de decision "
                        "del CEO; Risk = amenaza a monitorizar; Waiting = "
                        "esperando respuesta externa; Meeting = reunion "
                        "planificada; Delegated Work = ejecutado por otro; "
                        "Information = seguimiento sin accion. Si no esta "
                        "claro, dejalo vacio."
                    ),
                },
                "alexander_role": {
                    "type": "string",
                    "enum": ["Execution", "Decision", "Approval",
                             "Supervision", "No Involvement"],
                    "description": (
                        "Nivel de intervencion del CEO. Execution = ejecuta "
                        "el; Decision = solo el decide; Approval = revisa y "
                        "aprueba; Supervision = seguimiento; No Involvement "
                        "= delegado 100%."
                    ),
                },
                "next_action": {
                    "type": "string",
                    "description": (
                        "Accion siguiente concreta y ejecutable. Ej: 'Llamar "
                        "a Vasilena para confirmar horario', NO 'Coordinar "
                        "con Vasilena'."
                    ),
                },
                "expected_result": {
                    "type": "string",
                    "description": (
                        "Criterio verificable de finalizacion. Ej: 'Contrato "
                        "firmado por ambas partes', NO 'Cerrar el tema'."
                    ),
                },
                "review_date": {
                    "type": "string",
                    "description": (
                        "ISO 8601. Fecha de control directivo (cuando el "
                        "CEO revisa el avance). Recomendado para Delegated "
                        "y In Progress. NO es lo mismo que deadline."
                    ),
                },
                "deadline": {
                    "type": "string",
                    "description": (
                        "ISO 8601. Fecha limite para completar la tarea "
                        "(cuando debe estar hecha). Distinto de review_date, "
                        "que es cuando el CEO hace seguimiento. Ej: si el "
                        "usuario dice 'hay que entregarlo el viernes', eso "
                        "es deadline; si dice 'reviso el jueves', es "
                        "review_date."
                    ),
                },
                "source": {
                    "type": "string",
                    "description": (
                        "Origen de la tarea. Default 'Bitrix24'. Otros "
                        "utiles: 'Reunion con X', 'Email de Y', "
                        "'Iniciativa CEO'."
                    ),
                },
                "risk": {
                    "type": "string",
                    "description": (
                        "Consecuencia de la inaccion. Ej: 'Perdemos "
                        "ventana de aprobacion regulatoria', NO 'Es urgente'."
                    ),
                },
                "escalation_condition": {
                    "type": "string",
                    "description": (
                        "Condicion que exige intervencion del CEO. Ej: "
                        "'Si no hay respuesta en 48h', 'Si el precio supera "
                        "10K'. Obligatorio conceptualmente para Delegated."
                    ),
                },
                "requires_conversation": {
                    "type": "boolean",
                    "description": (
                        "True si el next_action NO puede ejecutarse sin "
                        "conversar con alguien (decision, validacion, "
                        "aprobacion, desbloqueo)."
                    ),
                },
                "primary_interlocutor": {
                    "type": "string",
                    "description": (
                        "Persona principal para la conversacion. Solo una. "
                        "Obligatorio si requires_conversation=True."
                    ),
                },
                "conversation_purpose": {
                    "type": "string",
                    "description": (
                        "Resultado esperado de la conversacion. NO puede "
                        "ser 'hablar de X' — debe ser 'aprobar X', "
                        "'decidir Y', 'validar Z'."
                    ),
                },
                "expected_decision": {
                    "type": "string",
                    "description": (
                        "Decision, validacion o acuerdo concreto esperado "
                        "de la conversacion."
                    ),
                },
                "meeting_candidate": {
                    "type": "boolean",
                    "description": (
                        "True si esta tarea puede agruparse en una reunion "
                        "con otras del mismo primary_interlocutor. Deja "
                        "vacio y el sistema lo calculara en un sprint futuro."
                    ),
                },
                "related_meeting_id": {
                    "type": "string",
                    "description": (
                        "ID de reunion ejecutiva vinculada, si esta tarea "
                        "salio de o se resuelve en una reunion concreta."
                    ),
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "consultar_tareas",
        "description": (
            "Lista las TAREAS del CEO en Bitrix Tasks con todos los UF_* del "
            "EOS materializados. Distinta de consultar_eventos: eso es "
            "calendario (citas con hora), esto es work items sin hora fija. "
            "Por defecto devuelve solo tareas ACTIVAS (excluye Completed y "
            "Cancelled), que es lo comun para '¿que tengo pendiente?'. Si "
            "el CEO pide historico, pasa solo_activos=false o filtra por "
            "estado='Completed'. "
            "Los filtros son AND: pasar estado='Waiting' y task_type='Task' "
            "devuelve tareas Waiting de tipo Task exclusivamente. "
            "Si el retorno marca truncado=true, propon al usuario un "
            "filtro mas estrecho. "
            "IMPORTANTE — no acepta texto_libre ni busqueda por palabra. "
            "Los unicos filtros son estado, task_type, primary_interlocutor, "
            "solo_activos y limite. Si el usuario pide 'busca la tarea que "
            "hablaba de X', lista todas activas y filtra tu por el titulo "
            "en la respuesta."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "estado": {
                    "type": "string",
                    "enum": ["New", "In Progress", "Delegated", "Waiting",
                             "Blocked", "Scheduled", "Completed", "Cancelled"],
                    "description": (
                        "Filtro por status_eos. Si lo pasas, sobreescribe "
                        "solo_activos. Ej: 'Waiting' para ver que espera "
                        "respuesta externa."
                    ),
                },
                "task_type": {
                    "type": "string",
                    "enum": ["Project", "Task", "Delegated Work", "Waiting",
                             "Meeting", "Decision", "Risk", "Information"],
                    "description": "Filtro por tipo de work item.",
                },
                "primary_interlocutor": {
                    "type": "string",
                    "description": (
                        "Match exacto case-insensitive por nombre. Util "
                        "para 'que tengo con Vasilena', 'todo lo de Sandra'."
                    ),
                },
                "solo_activos": {
                    "type": "boolean",
                    "description": (
                        "Default true. Excluye Completed y Cancelled. "
                        "Ponlo false si el usuario pide historico."
                    ),
                },
                "limite": {
                    "type": "integer",
                    "description": (
                        "Cap de tareas devueltas. Default 50. Si hay mas, "
                        "el retorno marca truncado=true."
                    ),
                },
            },
        },
    },
    {
        "name": "actualizar_estado_tarea",
        "description": (
            "Cambia el status_eos de una tarea y, en la misma llamada, "
            "actualiza campos asociados y opcionalmente el owner. La "
            "transicion se valida contra la matriz de estados EOS: si "
            "intentas una ilegal (p.ej. 'Waiting' -> 'New', o 'Completed' "
            "-> 'In Progress'), la tool devuelve error con el motivo y "
            "NO toca Bitrix. "
            "Antes de llamar, usa consultar_tareas para localizar el id — "
            "nunca uses ids de memoria. "
            "Transiciones legales resumidas: New -> In Progress | Delegated "
            "| Scheduled; In Progress -> Scheduled | Waiting | Blocked; "
            "Delegated -> Waiting; cualquier estado activo -> Completed o "
            "Cancelled; Completed y Cancelled son terminales (nada saliente). "
            "OWNER: para reasignar la tarea a otra persona en el mismo "
            "update, pasa su nombre en 'owner'. Se resuelve via user.search "
            "de Bitrix igual que en crear_tarea (1 match asigna, 0 o N "
            "devuelve error legible). Cambiar owner NO implica pasar a "
            "'Delegated' automaticamente: si delegas, pon nuevo_estado="
            "'Delegated' Y owner en la misma llamada. Cambiar solo owner "
            "sin tocar estado es un caso legitimo (reasignacion pura)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": (
                        "ID Bitrix de la tarea. Obtenlo con consultar_tareas."
                    ),
                },
                "nuevo_estado": {
                    "type": "string",
                    "enum": ["New", "In Progress", "Delegated", "Waiting",
                             "Blocked", "Scheduled", "Completed", "Cancelled"],
                    "description": "Estado destino.",
                },
                "owner": {
                    "type": "string",
                    "description": (
                        "Nombre del nuevo responsable (reasignacion). Misma "
                        "resolucion que en crear_tarea: por nombre o email, "
                        "via user.search. Si omites owner, RESPONSIBLE_ID "
                        "queda como estaba. Cambiar owner no implica cambiar "
                        "el estado: si delegas, pasa nuevo_estado='Delegated' "
                        "Y owner en la misma llamada."
                    ),
                },
                "next_action": {
                    "type": "string",
                    "description": (
                        "Update de UF_NEXT_ACTION. Obligatorio "
                        "conceptualmente al pasar a 'In Progress'."
                    ),
                },
                "expected_result": {
                    "type": "string",
                    "description": (
                        "Update de UF_EXPECTED_RESULT. Obligatorio "
                        "conceptualmente para 'Delegated'."
                    ),
                },
                "review_date": {
                    "type": "string",
                    "description": (
                        "ISO 8601. Update de UF_REVIEW_DATE. Obligatorio "
                        "conceptualmente para 'In Progress' y 'Delegated'. "
                        "NO es lo mismo que deadline."
                    ),
                },
                "deadline": {
                    "type": "string",
                    "description": (
                        "ISO 8601. Update de DEADLINE nativo Bitrix "
                        "(fecha limite de la tarea). Distinto de "
                        "review_date, que es fecha de supervision."
                    ),
                },
                "escalation_condition": {
                    "type": "string",
                    "description": (
                        "Update de UF_ESCALATION_CONDITION. Obligatorio "
                        "conceptualmente para 'Delegated'."
                    ),
                },
            },
            "required": ["id", "nuevo_estado"],
        },
    },
    {
        "name": "proponer_consolidacion",
        "description": (
            "Detecta candidatos a Reunion Ejecutiva agrupando por "
            "interlocutor los eventos futuros del calendario, las tareas "
            "activas con requires_conversation=True y las creaciones "
            "pendientes en el buffer. Devuelve solo grupos con >=2 "
            "elementos que comparten interlocutor (case-insensitive). "
            "Aplica los checks deterministas del Meeting Compatibility "
            "Test (PHASE 1 §2.7) y expone razones a evaluar para los "
            "checks difusos que debe juzgar el LLM antes de proponer. "
            "NO modifica nada: solo detecta. Usala cuando el CEO ha "
            "preparado varios eventos con la misma persona, cuando "
            "pregunta 'como llevo lo de X' o 'que tengo con Y', o "
            "proactivamente antes de agendar un nuevo evento con alguien "
            "que ya tiene otros elementos activos con el CEO."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ventana_dias": {
                    "type": "integer",
                    "description": (
                        "Dias hacia adelante a inspeccionar (default 14). "
                        "Usa 7 cuando el CEO acota 'esta semana', o 30 "
                        "cuando pide una vision mas amplia."
                    ),
                    "minimum": 1,
                    "maximum": 60,
                },
            },
        },
    },
    {
        "name": "crear_reunion_ejecutiva",
        "description": (
            "Prepara UN evento consolidado que agrupa varios temas con "
            "el mismo interlocutor (PHASE 1 §2.6, §2.8; PHASE 2 §4). "
            "NO se crea en Bitrix aun: entra al buffer de operaciones "
            "pendientes y se ejecuta cuando el CEO confirme, como "
            "cualquier crear_evento. IMPORTANTE §2.8: no borra ni "
            "modifica los elementos originales; solo los referencia en "
            "la descripcion generada. Antes de llamar a esta tool: "
            "(1) valida con juicio semantico que consolidar respeta el "
            "Meeting Compatibility Test, (2) obten el hueco propuesto "
            "con consultar_huecos_libres, (3) obten confirmacion "
            "explicita del CEO sobre agrupar. La tool escribe la agenda "
            "estructurada dentro de la descripcion del evento."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interlocutor": {
                    "type": "string",
                    "description": (
                        "Persona principal de la reunion. Se guarda en el "
                        "campo involucrado del evento."
                    ),
                },
                "temas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Lista de asuntos a tratar. Cada elemento es un "
                        "titulo corto (una frase). La tool los numera "
                        "como agenda estructurada."
                    ),
                    "minItems": 1,
                },
                "duracion_min": {
                    "type": "integer",
                    "description": (
                        "Duracion total estimada en minutos. Suma "
                        "razonable de lo que tomaria cada tema, con "
                        "margen. Alerta si supera 120."
                    ),
                    "minimum": 1,
                },
                "fecha_inicio": {
                    "type": "string",
                    "description": (
                        "ISO 8601 con offset local de Madrid (+02:00 "
                        "verano, +01:00 invierno). Debe venir de un "
                        "hueco real obtenido con consultar_huecos_libres."
                    ),
                },
                "ids_relacionados": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Opcional. IDs de los eventos o tareas ya "
                        "existentes que motivan la consolidacion. Se "
                        "citan en la descripcion como trazabilidad. NO "
                        "se modifican ni se borran (§2.8)."
                    ),
                },
                "resultado_esperado": {
                    "type": "string",
                    "description": (
                        "Opcional. Frase breve con el criterio de exito "
                        "de la reunion. Se incluye al principio de la "
                        "descripcion."
                    ),
                },
                "prioridad": {
                    "type": "string",
                    "enum": ["alta", "media", "baja"],
                    "description": (
                        "Opcional. Solo pasala si el CEO la fija "
                        "explicitamente; si la omites se calcula "
                        "automaticamente."
                    ),
                },
            },
            "required": ["interlocutor", "temas", "duracion_min",
                         "fecha_inicio"],
        },
    },
]

# Cache breakpoint en la ultima tool: cachea todo el bloque tools.
TOOLS_SCHEMA[-1]["cache_control"] = {"type": "ephemeral"}


# --------------------- MAPEO NOMBRE -> FUNCION ------------------------
# tools.py exporta 8 funciones async (todas menos responder_texto que es
# sync). Este mapeo evita el if/elif largo del loop del brain.
_TOOLS_ASYNC = {
    "crear_evento": crear_evento,
    "modificar_evento": modificar_evento,
    "eliminar_evento": eliminar_evento,
    "confirmar_operaciones_pendientes": confirmar_operaciones_pendientes,
    "cancelar_operaciones_pendientes": cancelar_operaciones_pendientes,
    "consultar_eventos": consultar_eventos,
    "consultar_huecos_libres": consultar_huecos_libres,
    "listar_eventos_preparados": listar_eventos_preparados,
    "gestionar_bloques": gestionar_bloques,
    "crear_tarea": crear_tarea,
    "consultar_tareas": consultar_tareas,
    "actualizar_estado_tarea": actualizar_estado_tarea,
    "proponer_consolidacion": proponer_consolidacion,
    "crear_reunion_ejecutiva": crear_reunion_ejecutiva,
}


def _json_default(obj):
    """Serializador de datetime a ISO 8601 para json.dumps.

    tools.py ya usa Evento.model_dump(mode='json') que serializa datetime
    correctamente, pero es cinturon de seguridad por si alguna tool
    devuelve un datetime crudo en algun campo.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Tipo no serializable: {type(obj).__name__}")


async def _ejecutar_tool(user_id: str, name: str, input_args: dict) -> tuple[dict, bool]:
    """Ejecuta una tool no-terminal y devuelve (resultado, is_error).

    Inyecta user_id como primer argumento posicional a la tool real,
    aunque el schema JSON expuesto al LLM no lo incluye. Esto mantiene
    la abstraccion: el modelo razona sobre operaciones, el sistema
    resuelve contra que usuario aplican.

    is_error se pone a True cuando el retorno lleva ok=False o cuando la
    tool lanza una excepcion. Anthropic usa este flag para saber que el
    tool_result es un fallo y ajustar su siguiente decision (pedir
    clarificacion, reintentar con otros args, informar al usuario) en
    lugar de tratar el error como dato valido.
    """
    fn = _TOOLS_ASYNC.get(name)
    if fn is None:
        logger.warn(
            "agent", "tool_unknown",
            f"Tool desconocida '{name}'",
            user_id=user_id,
            metadata={"tool_name": name, "input_args_keys": list(input_args.keys())},
        )
        return (
            {"ok": False, "error": f"Tool '{name}' no existe. Usa solo las tools registradas."},
            True,
        )

    try:
        # user_id va como kwarg para no chocar con el orden de argumentos
        # que el modelo espera (nombre, duracion_min, etc.). tools.py
        # define user_id como primer parametro obligatorio en cada async
        # def, asi que python lo aceptara sin ambiguedad.
        resultado = await fn(user_id=user_id, **input_args)
    except Exception as e:
        logger.error(
            "agent", "tool_execution_error",
            f"Tool '{name}' lanzo excepcion: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={"tool_name": name, "input_args_keys": list(input_args.keys())},
            error=e,
        )
        return (
            {"ok": False, "error": f"{type(e).__name__}: {e}"},
            True,
        )

    is_error = isinstance(resultado, dict) and resultado.get("ok") is False
    return resultado, is_error


# --------------------- BRAIN ------------------------------------------

async def process_message(user_id: str, history: list, resumen: str) -> str:
    """Loop iterativo del agente. Devuelve el texto final para el usuario.

    En cada iteracion:
    1. Llama a Claude con tool_choice={"type":"any"} (fuerza tool_use).
    2. Separa tools no-terminales (datos/acciones sobre buffer) de
       responder_texto.
    3. Si hay no-terminales: las ejecuta pasando user_id, mete los
       tool_result y sigue. Si venia mezclado responder_texto, se le
       devuelve un tool_result de error explicandolo (Anthropic exige
       matching 1:1 tool_use <-> tool_result) y el modelo re-decide con
       datos ya en contexto.
    4. Si solo hay responder_texto: cierra turno con su mensaje.

    Se sale del loop por: respuesta terminal, iteracion sin tool_use
    (edge case bajo tool_choice=any, tipicamente max_tokens), o el
    limite MAX_ITERACIONES.
    """
    # Convertimos el historial de memory (role: user|model) al formato
    # de Anthropic (role: user|assistant). Al mensaje user se le antepone
    # la fecha para que el modelo tenga la temporalidad clara turno a turno.
    messages = []
    for msg in history:
        role_anthropic = "assistant" if msg["role"] == "model" else "user"
        if role_anthropic == "user":
            texto = f"[{msg['fecha'].strftime('%Y-%m-%d %H:%M:%S')}] {msg['text']}"
        else:
            texto = msg["text"]
        messages.append({"role": role_anthropic, "content": texto})

    system_blocks = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT_ESTABLE,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": _contexto_dinamico(resumen),
        },
    ]

    for iteracion in range(MAX_ITERACIONES):
        # Cache breakpoint dinamico. IMPORTANTE: Anthropic limita a 4 bloques
        # con cache_control por request. Ya gastamos 2 en system+tools, por
        # tanto solo podemos meter UN cache_control mas en el historial. Si
        # dejasemos los cache_control de iteraciones anteriores acumulados,
        # a partir de la 3a iteracion el request pasaria de 4 bloques y
        # petaria con:
        #   BadRequestError: 400 - A maximum of 4 blocks with cache_control
        #   may be provided. Found 5.
        # Por eso limpiamos primero TODOS los cache_control de mensajes
        # anteriores, y solo despues ponemos uno nuevo en el ultimo bloque
        # del ultimo mensaje.
        if messages:
            for msg in messages:
                if isinstance(msg.get("content"), list):
                    for bloque in msg["content"]:
                        if isinstance(bloque, dict) and "cache_control" in bloque:
                            del bloque["cache_control"]

            ultimo = messages[-1]
            if isinstance(ultimo.get("content"), list):
                if ultimo["content"] and isinstance(ultimo["content"][-1], dict):
                    ultimo["content"][-1]["cache_control"] = {"type": "ephemeral"}

        response = await _client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS_BRAIN,
            system=system_blocks,
            tools=TOOLS_SCHEMA,
            tool_choice={"type": "any"},
            messages=messages,
        )

        # Log de cache hit ratio: sirve para verificar que el caching
        # esta pegando de verdad. En el segundo turno de una conversacion
        # cache_read deberia ser el bulk de los input_tokens.
        response_usage = response.usage
        cache_read = getattr(response_usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(response_usage, "cache_creation_input_tokens", 0) or 0
        print(
            f"AGENT[{user_id}] it={iteracion}: stop={response.stop_reason} "
            f"in={response_usage.input_tokens} out={response_usage.output_tokens} "
            f"cache_read={cache_read} cache_write={cache_write}"
        )
        await usage.registrar(user_id, response_usage, MODELO, contexto="brain")

        # Extraemos los bloques tool_use del content.
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        no_terminales = [b for b in tool_uses if b.name != "responder_texto"]
        terminal = next((b for b in tool_uses if b.name == "responder_texto"), None)

        print(f"AGENT[{user_id}] it={iteracion}: tool_uses={[b.name for b in tool_uses]}")

        if no_terminales:
            # Ejecutar las no-terminales y construir los tool_result.
            tool_results = []
            for tu in no_terminales:
                resultado, is_error = await _ejecutar_tool(user_id, tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(resultado, default=_json_default, ensure_ascii=False),
                    "is_error": is_error,
                })

            # Si el modelo mezclo responder_texto con tools de datos, le
            # devolvemos un tool_result de error explicando la regla.
            # Anthropic exige que TODOS los tool_use del assistant tengan
            # su tool_result correspondiente; si no, la API rechaza el
            # siguiente request.
            if terminal is not None:
                logger.warn(
                    "agent", "responder_texto_mezclado_ignorado",
                    "responder_texto ignorado, vino mezclado con tools de datos; se re-decidira",
                    user_id=user_id,
                    metadata={"tools_no_terminales": [t.name for t in no_terminales]},
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": terminal.id,
                    "content": (
                        "Ignorado. No mezcles responder_texto con tools de "
                        "datos en el mismo turno. Espera a tener los datos "
                        "en contexto y responde en el turno siguiente."
                    ),
                    "is_error": True,
                })

            # Añadir la respuesta del assistant tal cual (bloques SDK).
            # El SDK acepta pasar response.content directamente, se
            # serializa via pydantic al enviar.
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # Solo responder_texto → cerramos turno.
        if terminal is not None:
            return responder_texto(**terminal.input)

        # Ni tools ni responder_texto: con tool_choice=any esto no deberia
        # pasar salvo max_tokens o stop_reason inesperado. Cortamos limpio.
        logger.warn(
            "agent", "iteration_no_tool_use",
            f"Iteracion {iteracion} sin tool_use, stop={response.stop_reason}",
            user_id=user_id,
            metadata={"iteracion": iteracion, "stop_reason": response.stop_reason},
        )
        break

    return "El agente no pudo resolver la petición, reformula por favor."


# --------------------- SUMMARIZE --------------------------------------

async def summarize(user_id: str, history: list, resumen_previo: str = "") -> str:
    """Fusiona resumen previo + mensajes nuevos en un nuevo resumen unico.

    Se llama desde procesar_input cuando check_history detecta que se
    ha pasado el umbral. Corre en single-shot (sin tools, sin cache).
    user_id se usa solo para el registro de coste en usage — el prompt
    en si es agnostico al usuario.
    """
    lineas = []
    for msg in history:
        fecha_str = msg["fecha"].strftime("%Y-%m-%d %H:%M:%S")
        linea = f"[{fecha_str}] {msg['role']}: {msg['text']}"
        lineas.append(linea)
    conversacion = "\n".join(lineas)

    prompt = f"""Eres un asistente de resumen acumulativo de una conversación en curso.

    Recibes:
    1. Un resumen previo (puede estar vacío si es la primera vez).
    2. Nuevos mensajes a añadir al resumen.

    Tu tarea: fusionar ambos en un resumen único, actualizado y coherente.

    Conserva:
    - Datos personales, nombres, fechas concretas.
    - Decisiones tomadas y confirmadas.
    - Preferencias del usuario detectadas.
    - Tareas y eventos activos y su estado ACTUAL.
    - Contexto necesario para continuar la conversación.

    Descarta:
    - Saludos, small talk, repeticiones, aclaraciones resueltas.
    - Problemas técnicos, bugs o incidencias YA RESUELTOS. Si el resumen 
    previo mencionaba "el sistema tiene el bug X" y en los nuevos 
    mensajes se ve que X fue arreglado o ya no aplica, elimina esa 
    mención por completo del nuevo resumen. No la conviertas en "antes 
    había un bug X, ya resuelto" — simplemente sácala.
    - Estados de tareas/eventos que ya no son actuales. Si el resumen 
    previo decía "tarea 123 está en In Progress" y los nuevos mensajes 
    muestran que pasó a Completed, refleja SOLO el estado actual 
    (Completed), no la transición completa.

    Regla de oro: el resumen es una foto del ESTADO ACTUAL, no un log 
    histórico. Prefiere borrar información obsoleta antes que acumularla.

    Máximo 300 palabras. NO uses markdown. Responde SOLO con el resumen actualizado.

    ---

    RESUMEN PREVIO:
    {resumen_previo if resumen_previo else "(vacío, primera iteración)"}

    ---

    NUEVOS MENSAJES:
    {conversacion}"""

    response = await _client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS_SUMMARY,
            messages=[{"role": "user", "content": prompt}],
        )
    await usage.registrar(user_id, response.usage, MODELO, contexto="summary")

    # response.content es lista de bloques; sin tools esperamos un solo
    # TextBlock. Iteramos por defensa por si viniera vacio.
    for bloque in response.content:
        if bloque.type == "text":
            return bloque.text.strip()
    return ""


# --------------------- PIPELINE PÚBLICO -------------------------------

async def procesar_input(user_id: str, texto: str) -> str:
    """Pipeline completo del agente: guarda entrada del usuario, gestiona
    resumen si toca, llama al brain con tools, guarda respuesta y devuelve
    el texto final. Es la funcion que comparten el bot Telegram y el
    endpoint HTTP: ambos son solo capas de transporte sobre esto.

    user_id se propaga end-to-end: memory (Supabase indexado por usuario),
    summarize (registro de coste), process_message (ejecucion de tools
    contra el Bitrix correcto).
    """
    try:
        await memory.save_message(user_id, "user", texto)

        if await memory.check_history(user_id):
            history = await memory.get_history(user_id)
            old_msg = history[:8]
            resumen_previo = await memory.get_resumen(user_id)
            nuevo_resumen = await summarize(user_id, old_msg, resumen_previo)
            await memory.set_resumen(user_id, nuevo_resumen)
            await memory.del_history(user_id, n=8)

        prompt = await memory.get_history(user_id)
        resumen = await memory.get_resumen(user_id)
        respuesta = await process_message(user_id, prompt, resumen)
        await memory.save_message(user_id, "model", respuesta)
        return respuesta
    except Exception as e:
        logger.error(
            "agent", "pipeline_error",
            f"Error en procesar_input: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={"texto_input": (texto or "")[:500]},
            error=e,
        )
        return "He tenido un problema procesando tu mensaje. ¿Puedes intentarlo de nuevo o reformularlo?"