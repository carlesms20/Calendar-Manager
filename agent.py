from google import genai
from os import getenv
from dotenv import load_dotenv
import asyncio
from google.genai import types
from tools import (
    responder_texto,
    crear_evento,
    modificar_evento,
    eliminar_evento,
    confirmar_operaciones_pendientes,
    cancelar_operaciones_pendientes,
    consultar_eventos,
    listar_eventos_preparados,
)

load_dotenv()
TOKEN = getenv("API_GEMINI")
model = "gemini-3.5-flash-lite"
client = genai.Client(api_key=TOKEN)

from datetime import datetime

def get_system_prompt() -> str:
    fecha_actual = datetime.now().strftime('%Y-%m-%d %H:%M')
    dia_semana = datetime.now().strftime('%A')
    
    return f"""# IDENTIDAD
Eres el asistente operativo personal del usuario. Gestionas su agenda 
(eventos de calendario) desde Telegram, con Bitrix24 como sistema 
subyacente (sincronizado con Google Calendar y Office).

# CONTEXTO ACTUAL
- Fecha y hora: {fecha_actual}
- Día de la semana: {dia_semana}
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
- prioridad: asume "media" siempre. NO preguntes por esto. El usuario 
  puede cambiarla explícitamente si quiere.
- categoria: trabajo/cliente/proveedor/empleado → "empresa". Médico, 
  gimnasio, familia, pareja → "personal". Si mencionan a alguien por 
  nombre sin contexto claro, asume "empresa".
- tipo_actividad: infiere de la palabra (reunión, llamada, café...).
- involucrado: si categoria=empresa y no se menciona con quién, 
  PREGUNTA antes de llamar a crear_evento. Es obligatorio.
- descripcion: solo si el usuario da contexto adicional útil.

# ESTILO DE RESPUESTA
- Claro, directivo, sin relleno. Tono de management práctico.
- Prosa natural, como un ejecutivo hablando a otro. No uses headers 
  markdown (###), negritas, ni bullets salvo que el usuario pida 
  explícitamente una lista o comparación.
- No cierres con frases genéricas ("quedo a la espera", "estoy listo").
- No inventes datos que no tienes.
- No inventes sistemas, contadores o protocolos que no existen. Si el 
  usuario dice algo casual, no lo conviertas en mecanismo formal.
"""

async def process_message(history: list, resumen):
    contents = []
    for msg in history:
        if msg["role"] == "user":
            texto = f"[{msg['fecha'].strftime('%Y-%m-%d %H:%M:%S')}] {msg['text']}"
        else:
            texto = msg["text"]
        contents.append({
            "role": msg["role"],
            "parts": [{"text": texto}]
        })

    system_prompt = get_system_prompt()
    system_prompt += f"""
        CONTEXTO CONVERSACIÓN PREVIA, 
        Este es un resumen del sistema sobre partes anteriores de la conversación.
        NO son mensajes del usuario, es contexto para que sepas qué se ha hablado antes::{resumen}
        """
    
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.3,
        max_output_tokens=1024,
        tools=[
            responder_texto,
            crear_evento,
            modificar_evento,
            eliminar_evento,
            confirmar_operaciones_pendientes,
            cancelar_operaciones_pendientes,
            consultar_eventos,
            listar_eventos_preparados,
        ],        
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))
    )        
    #call = response.function_calls[0] #en lugar de .text, como usamos SOLO tools ahora, es .function_calls
    #***function_calls es un atajo, recorre response.candidates[0].content.parts y se queda con .function_calls. Como ahora solo usamos tools (por el mode:ANY) nos sirve
    #function_calls te da una lista de objetos FunctionCall(name='responder_texto', args={'mensaje': 'hola'}) con **call.args accedes a los argumentos de las funciones que gemini quiere ejecutar
    #EN RESUMEN function_calls devuelve una lista de intentos de llamadas a tools, tu ejecutas las que quieras (si no todas, con un for)
    #return responder_texto(**call.args) # <<<--- exactamente, accedes a los argumentos de la funcion que gemini quiso ejecutar y se los pasas a esa misma funcion 
    MAX_ITERACIONES = 8
    for iteracion in range(MAX_ITERACIONES):
        response = await client.aio.models.generate_content(model="gemini-3.1-flash-lite", contents=contents, config=config)

        calls = list(response.function_calls or [])
        # [D] Log de todo lo que Gemini pidió llamar en este turno
        print(f"AGENT it={iteracion}: tool_calls = {[c.name for c in calls]}")

        # [B] Separamos tools no-terminales del responder_texto
        calls_no_terminales = [c for c in calls if c.name != "responder_texto"]
        call_terminal = next((c for c in calls if c.name == "responder_texto"), None)

        # [B] Si hay cualquier tool no-terminal, la ejecutamos y descartamos
        # el responder_texto (si venía mezclado). El modelo re-decidirá en
        # la siguiente iteración con los datos ya en contents.
        if calls_no_terminales:
            function_responses = []
            for call in calls_no_terminales:
                if call.name == "crear_evento":
                    resultado = await crear_evento(**call.args)
                elif call.name == "modificar_evento":
                    resultado = await modificar_evento(**call.args)
                elif call.name == "eliminar_evento":
                    resultado = await eliminar_evento(**call.args)
                elif call.name == "confirmar_operaciones_pendientes":
                    resultado = await confirmar_operaciones_pendientes(**call.args)
                elif call.name == "cancelar_operaciones_pendientes":
                    resultado = await cancelar_operaciones_pendientes(**call.args)
                elif call.name == "consultar_eventos":
                    resultado = await consultar_eventos(**call.args)
                elif call.name == "listar_eventos_preparados":
                    resultado = await listar_eventos_preparados(**call.args)
                else:
                    print(f"AGENT WARN: tool desconocida '{call.name}'")
                    resultado = {
                        "ok": False,
                        "error": f"Tool '{call.name}' no existe. Usa solo las tools registradas.",
                    }
                function_responses.append(
                    types.Part.from_function_response(name=call.name, response=resultado)
                )

            contents.append(response.candidates[0].content)
            contents.append(types.Content(role="user", parts=function_responses))

            if call_terminal is not None:
                print(f"AGENT: responder_texto ignorado (vino mezclado con {[c.name for c in calls_no_terminales]}); se re-decidirá con los datos")
            continue

        # Solo responder_texto (o nada). Cerramos turno.
        if call_terminal is not None:
            return responder_texto(**call_terminal.args)

        # Ni tools ni responder_texto → cortamos limpio
        print(f"AGENT WARN: iteración {iteracion} sin tool_calls, abortando")
        break

    return "El agente no pudo resolver la petición, reformula por favor."

async def summarize(history: list, resumen_previo: str = ""):
    lineas = []
    for msg in history:
        fecha_str = msg['fecha'].strftime('%Y-%m-%d %H:%M:%S')
        linea = f"[{fecha_str}] {msg['role']}: {msg['text']}"
        lineas.append(linea)

    conversacion = "\n".join(lineas) #para que el modelo vea el historial como: [2026-07-23 11:48:12] user: hola *mas sencillo*
    pre_prompt = f"""Eres un asistente de resumen acumulativo de una conversación en curso.

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
        {conversacion}
        """
    contents =[]
    content = {
            "role": "user",
            "parts": [{"text": pre_prompt}] #parts es una lista de diccionarios, por que puede que un mensaje lleve texto, archivo, etc...
        }
    contents.append(content)
    config = types.GenerateContentConfig(
        temperature=0.2,         
        max_output_tokens=512
        )
    return (await client.aio.models.generate_content(model="gemini-3.5-flash-lite", contents=contents, config=config)).text