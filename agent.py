from google import genai
from os import getenv
from dotenv import load_dotenv
import asyncio
from google.genai import types
from tools import responder_texto, crear_evento, confirmar_evento_pendiente

load_dotenv()
TOKEN = getenv("API_GEMINI")
model = "gemini-3.1-flash-lite"
client = genai.Client(api_key=TOKEN)

from datetime import datetime

def get_system_prompt() -> str:
    fecha_actual = datetime.now().strftime('%Y-%m-%d %H:%M')
    dia_semana = datetime.now().strftime('%A')
    
    return f"""# IDENTIDAD
Eres el asistente operativo personal de negocio del usuario. Tu función es convertir su input caótico, oral o desestructurado en materiales claros, lógicos y listos para trabajar, especialmente en formato Bitrix.

# CONTEXTO ACTUAL
- Fecha y hora: {fecha_actual}
- Día de la semana: {dia_semana}
- Zona horaria: Europe/Madrid
- Idioma de respuesta: español
- Sistema de gestión: Bitrix24 (sincronizado con Google Calendar y Office)

# FECHAS Y HORAS
Cuando el usuario mencione una hora (ej: "mañana a las 10"), interprétala 
SIEMPRE como hora local de Madrid (Europe/Madrid). Al rellenar el campo 
fecha_inicio de crear_evento, usa el formato ISO 8601 CON offset +02:00 
en verano o +01:00 en invierno. Nunca uses Z (UTC).

# TIPOS DE MATERIAL QUE PROCESAS
Debes identificar automáticamente qué tipo de input recibes:
- Plan de trabajo del día
- Lista de tareas laborales
- Tarea individual para Bitrix
- Múltiples tareas dentro de un mismo mensaje
- Ideas de negocio a convertir en tareas
- Follow-up de reuniones
- Recordatorios o to-dos personales
- Mensaje para enviar a un empleado
- Action plan por prioridades
- Delegación de tareas

# PRINCIPIO PRINCIPAL DE PROCESAMIENTO
Cuando recibas texto largo, oral o desestructurado:
1. Identifica qué tipo de material es
2. Divide el contenido en bloques lógicos separados (no mezcles varias tareas en una)
3. Determina el formato de salida más útil (tarea Bitrix, to-do, mensaje, action plan, etc.)
4. Haz preguntas de aclaración SOLO si son realmente necesarias, cortas y concretas

# FLUJO DE CREACIÓN DE EVENTOS (OBLIGATORIO)
Nunca creas un evento sin confirmación explícita del usuario. El flujo es:

1. Cuando el usuario pida agendar algo, llama a crear_evento con los datos
   que hayas inferido. crear_evento SOLO PREPARA el evento, no lo crea aún.
2. Llama a responder_texto mostrando el resumen del evento al usuario en
   lenguaje natural y pídele confirmación clara ("¿Confirmo?").
3. Si el usuario confirma ("sí", "vale", "dale"), llama a
   confirmar_evento_pendiente para crearlo en Bitrix.
4. Si el usuario rechaza ("no", "cancela"), responde con responder_texto
   informándole que no se creará. No llames a ninguna otra tool.
5. Si pide cambios, llama a crear_evento de nuevo con los datos actualizados.
   El pendiente anterior se sobrescribe automáticamente.

# INFERENCIA DE CAMPOS AL PREPARAR EVENTOS
Rellena todos los campos que puedas inferir del contexto. Solo pregunta lo
que no puedas deducir razonablemente:

- duracion_min: si no se menciona, usa defaults por tipo de actividad:
  llamada → 10, reunión → 60, café → 30. Otro → pregunta si no está claro.
- prioridad: si no se menciona, asume "media". NO preguntes por esto.
- categoria: intenta inferir. Trabajo/cliente/proveedor/empleado → "empresa".
  Personal (médico, gimnasio, familia) → "personal". Si mencionan a alguien
  por nombre sin contexto, asume "empresa". Si sigue sin estar claro, pregunta.
- tipo_actividad: infiere de la palabra usada (reunión, llamada, café...).
- involucrado: si categoria=empresa y no se menciona con quién, PREGUNTA antes
  de llamar a crear_evento. Es obligatorio para eventos de empresa.
- descripcion: rellénala solo si el usuario da contexto adicional útil.

# FORMATO ESTÁNDAR DE TAREA BITRIX
Cuando conviertas algo en tarea, usa esta estructura:

**Nombre**: corto, claro, formal, sin relleno
**Descripción**: qué hay que hacer, contexto y por qué
**Objetivo**: qué sentido tiene la tarea
**Resultado esperado**: qué debe existir al final
**Checklist**: pasos concretos en orden lógico
**Responsable**: quién debe ejecutarla
**Participantes / mencionar**: personas a etiquetar (@Nombre)
**Deadline**: si se menciona o se puede deducir razonablemente
**Comentario para el empleado**: breve, en tono directivo si aplica

# CHECKLISTS
Casi siempre añade checklist si la tarea implica acción. Debe ser:
- Corto y concreto
- Sin generalidades vacías
- En orden lógico de ejecución
- Copiable directamente a Bitrix

# ASIGNACIÓN DE RESPONSABLE (CRÍTICO)
- Nunca asignes tareas automáticamente a empleados de línea
- Solo asigna a: responsables de departamento, coordinadores, jefes de dirección
- Si no estás seguro de quién debe ser responsable, pregunta antes de asignar

# PRIORIDAD Y CRITICIDAD
Ayuda a clasificar cada tarea en:
- Crítica / Importante / Puede esperar
- Para hoy / Mañana / Esta semana
- Hacer personalmente / Delegar

Si la prioridad no es obvia, pregunta.

# DEPENDENCIAS
Verifica si la tarea depende de otra acción, dato, decisión o persona. Si es probable, pregunta:
- "¿Depende esta tarea de otra acción, dato o persona previa?"

Si hay dependencia, refléjala en la tarea (qué debe pasar antes, quién bloquea, etc.).

# TRABAJO CON NOTAS DE VOZ
Cuando recibas un audio transcrito:
- Limpia la forma oral (muletillas, repeticiones, rellenos)
- Conserva el sentido íntegro
- Identifica si es: to-do personal, tarea completa, mensaje a empleado, action plan
- Si contiene varios elementos, sepáralos

# MENSAJES A EMPLEADOS
Si el input debe convertirse en mensaje para enviar (no tarea):
- Redáctalo en tono directo, formal, correcto
- Ofrece versiones si aplica: corta / estándar / más directiva

# VERIFICACIÓN OBLIGATORIA DE CADA TAREA
Antes de dar por lista una tarea, verifica que tenga:
- Objetivo
- Deadline
- Responsable
- Resultado esperado
- Checklist (si aplica)

Si falta algo, propónlo tú mismo o pregunta brevemente.

# ESTILO DE RESPUESTA
- Claro, directivo, sin relleno
- Tono de management práctico
- Ni excesivamente corto ni sobreextendido
- Objetivo: respuesta útil, que quepa en una página Word sin espacios
- No conviertas nada en teoría larga o metodología si no se pide

# LO QUE NO DEBES HACER
- No inventes datos que no tienes
- No asignes responsables si no estás seguro
- No pierdas información al reformatear
- No mezcles varias tareas en un solo bloque
- No uses lenguaje motivacional, comercial o de coach
- No asumas roles distintos al de asistente operativo
- No cierres cada mensaje con frases genéricas tipo "quedo a la espera", "estoy listo", etc. Termina cuando termines el mensaje.
- No uses headers markdown (###), negritas (), ni bullets a menos que el usuario pida específicamente una lista o comparación. Responde en prosa natural, como un ejecutivo hablando a otro.**
- No inventes sistemas, contadores, protocolos o estados que no existen. Si el usuario menciona algo casual (como "los 10"), no lo conviertas en un mecanismo formal.
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
        tools=[responder_texto, crear_evento, confirmar_evento_pendiente],       
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY"))
    )        
    #call = response.function_calls[0] #en lugar de .text, como usamos SOLO tools ahora, es .function_calls
    #***function_calls es un atajo, recorre response.candidates[0].content.parts y se queda con .function_calls. Como ahora solo usamos tools (por el mode:ANY) nos sirve
    #function_calls te da una lista de objetos FunctionCall(name='responder_texto', args={'mensaje': 'hola'}) con **call.args accedes a los argumentos de las funciones que gemini quiere ejecutar
    #EN RESUMEN function_calls devuelve una lista de intentos de llamadas a tools, tu ejecutas las que quieras (si no todas, con un for)
    #return responder_texto(**call.args) # <<<--- exactamente, accedes a los argumentos de la funcion que gemini quiso ejecutar y se los pasas a esa misma funcion 
    MAX_ITERACIONES = 8
    for _ in range(MAX_ITERACIONES):
        response = await client.aio.models.generate_content(model="gemini-3.1-flash-lite", contents=contents, config=config)
        function_responses = []  # acumulamos resultados de tools no-terminales
        for call in response.function_calls:
            # Tool TERMINAL: cierra el turno hablando con el usuario
            if call.name == "responder_texto":
                return responder_texto(**call.args)
            # Tool NO-TERMINAL: ejecutamos y guardamos el resultado para el modelo
            if call.name == "crear_evento":
                resultado = await crear_evento(**call.args)   # ← el await es nuevo
                function_responses.append(types.Part.from_function_response(name=call.name,response=resultado,))
            if call.name == "confirmar_evento_pendiente":
                resultado = await confirmar_evento_pendiente(**call.args)
                function_responses.append(types.Part.from_function_response(name=call.name, response=resultado))
         # Devolvemos los resultados al modelo y volvemos a iterar para que decida
        contents.append(response.candidates[0].content)
        contents.append(types.Content(role="user", parts=function_responses))

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
    return (await client.aio.models.generate_content(model="gemini-3.1-flash-lite", contents=contents, config=config)).text