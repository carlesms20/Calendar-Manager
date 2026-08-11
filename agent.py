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
(eventos de calendario) desde Telegram, con Bitrix24 como sistema 
subyacente (sincronizado con Google Calendar y Office).

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
- Tareas mencionadas y su estado.
- Contexto necesario para continuar la conversación.

Descarta: saludos, small talk, repeticiones, aclaraciones resueltas.

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