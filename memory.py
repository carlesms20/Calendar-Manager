"""Memoria conversacional persistida en Supabase.

Dos tablas:
- conversation_history: mensajes activos, se borran al resumirse.
- conversation_summary: resumen acumulado por usuario.

La interfaz publica mantiene los mismos nombres que la version en RAM,
anadiendo user_id como primer parametro. Cuando entre auth real,
user_id vendra del JWT; por ahora lo pasan bot.py y server.py
hardcodeado.

Notas tecnicas:
- Usamos el cliente sincrono de supabase-py (create_client). Las
  funciones se declaran async para mantener la interfaz, pero
  internamente son bloqueantes. Para el volumen actual (un usuario,
  pocos mensajes al dia) es irrelevante.
- Evitamos .maybe_single() porque tiene bugs conocidos cuando la fila
  no existe (issue #1207 y #511 del repo). Usamos .limit(1) y
  comprobamos si respuesta.data esta vacia.
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not _SUPABASE_URL or not _SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en el entorno. "
        "Revisa el .env."
    )

_client: Client = create_client(_SUPABASE_URL, _SUPABASE_KEY)

# Umbral de mensajes por encima del cual se dispara el resumen.
# Cuando check_history devuelva True, agent.procesar_input llama a
# summarize sobre los mas viejos y los borra.
_UMBRAL_RESUMEN = 25

# Cuantos mensajes se resumen en cada disparo.
_MSG_A_RESUMIR = 15


async def save_message(user_id: str, role: str, content: str) -> None:
    """Guarda un mensaje en el historial activo.

    role debe ser 'user' o 'model' (validado tambien por la BD via CHECK).
    """
    _client.table("conversation_history").insert({
        "user_id": user_id,
        "role": role,
        "content": content,
    }).execute()


async def get_history(user_id: str) -> list[dict]:
    """Devuelve todos los mensajes activos del usuario en orden cronologico.

    Formato de retorno compatible con lo que espera agent.process_message
    y agent.summarize: {"role", "fecha" (datetime), "text"}.

    Internamente la BD guarda 'content' y 'created_at', pero traducimos
    aqui para no tocar el contrato con agent.py.
    """
    respuesta = (
        _client.table("conversation_history")
        .select("role, content, created_at")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )

    resultado = []
    for fila in (respuesta.data or []):
        # created_at viene como string ISO 8601 desde Postgrest, hay que parsearlo
        fecha_str = fila.get("created_at")
        try:
            fecha = datetime.fromisoformat(fecha_str) if fecha_str else datetime.now(timezone.utc)
        except ValueError:
            fecha = datetime.now(timezone.utc)

        resultado.append({
            "role": fila["role"],
            "fecha": fecha,
            "text": fila["content"],
        })
    return resultado


async def check_history(user_id: str) -> bool:
    """Devuelve True si el historial supera el umbral y toca resumir.

    Usa count='exact' para contar sin traer los datos (mas eficiente
    que hacer select y contar en Python).
    """
    respuesta = (
        _client.table("conversation_history")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    total = respuesta.count or 0
    return total > _UMBRAL_RESUMEN


async def get_resumen(user_id: str) -> str:
    """Devuelve el resumen acumulado del usuario, o cadena vacia si no hay.

    Evitamos .maybe_single() (bugs conocidos con 0 filas). Usamos limit(1)
    y comprobamos la lista.
    """
    respuesta = (
        _client.table("conversation_summary")
        .select("resumen")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not respuesta.data:
        return ""
    return respuesta.data[0].get("resumen", "") or ""


async def set_resumen(user_id: str, nuevo_resumen: str) -> None:
    """Guarda o actualiza el resumen del usuario (UPSERT).

    Enviamos updated_at explicito porque el default now() de la BD solo
    se aplica en INSERT; en UPDATE (que es lo que UPSERT hace si ya
    existe la fila), el default no se dispara.
    """
    _client.table("conversation_summary").upsert({
        "user_id": user_id,
        "resumen": nuevo_resumen,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


async def del_history(user_id: str, n: int) -> None:
    """Borra los n mensajes mas antiguos del usuario.

    Se llama tras haber generado un resumen que ya los incluye,
    para liberar el historial activo.

    Estrategia en dos pasos: SELECT los ids de los n mas antiguos,
    luego DELETE con IN. Postgrest no soporta DELETE + ORDER + LIMIT
    en una sola operacion, hay que hacerlo asi.
    """
    respuesta = (
        _client.table("conversation_history")
        .select("id")
        .eq("user_id", user_id)
        .order("created_at")
        .limit(n)
        .execute()
    )
    ids = [fila["id"] for fila in (respuesta.data or [])]
    if not ids:
        return
    _client.table("conversation_history").delete().in_("id", ids).execute()

""" CODIGO ANTERIOR, PERSISTENCIA LOCAL EN RAM
from datetime import datetime

historial = []
resumen_previo = ""

async def save_message(role: str, text):
    global historial
    global resumen_previo
    prompt = {}
    fecha = datetime.now()
    
    prompt["role"] = role
    prompt["fecha"] = fecha
    prompt["text"] = text

    historial.append(prompt)
        
def get_history():
    return historial

def del_history():
    del historial[:8]

def check_history():
    return len(historial) >= 15

def get_resumen():
    if resumen_previo:
        return resumen_previo
    else:
        return None

def set_resumen(new_resumen):
    global resumen_previo
    resumen_previo = new_resumen
    """