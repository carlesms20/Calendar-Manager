from datetime import datetime
from models import Evento
from bitrix import crear_evento_bitrix, BitrixError

_EVENTO_PENDIENTE: Evento | None = None

def responder_texto(mensaje: str) -> str:
    """Termina el turno respondiendo al usuario con un mensaje.

    Úsala para respuestas informativas, resúmenes, confirmaciones,
    o cuando no haga falta acción posterior del usuario.

    Args:
        mensaje: Texto que se envía tal cual al usuario.
    """
    print(f"TOOL: responder_texto ejecutada")
    return mensaje

async def crear_evento(
    nombre: str,
    duracion_min: int,
    fecha_inicio: datetime,
    categoria: str,
    prioridad: str,
    involucrado: str = "",
    descripcion: str = "",
    fecha_limite: datetime | None = None,
    tipo_actividad: str = "",
) -> dict:
    """Prepara un evento para el calendario del usuario.

    IMPORTANTE: esta tool NO crea el evento en Bitrix. Solo lo prepara y lo
    guarda como pendiente de confirmación. Después de llamarla, muestra el
    resumen al usuario con responder_texto y pídele confirmación explícita.
    Solo si el usuario confirma, llama a confirmar_evento_pendiente.

    Args:
        nombre: título corto del evento.
        duracion_min: duración en minutos (>0).
        fecha_inicio: cuándo empieza (ISO 8601).
        categoria: "personal" o "empresa".
        prioridad: "alta", "media" o "baja".
        involucrado: persona involucrada (obligatorio si categoria="empresa").
        descripcion: contexto adicional, opcional.
        fecha_limite: fecha tope opcional, posterior a fecha_inicio.
        tipo_actividad: reunion, llamada, tarea admin, etc.
    """
    global _EVENTO_PENDIENTE

    evento = Evento(
        nombre=nombre,
        duracion_min=duracion_min,
        fecha_inicio=fecha_inicio,
        categoria=categoria,
        prioridad=prioridad,
        involucrado=involucrado,
        descripcion=descripcion,
        fecha_limite=fecha_limite,
        tipo_actividad=tipo_actividad,
    )
    _EVENTO_PENDIENTE = evento
    print(f"TOOL: crear_evento (pendiente): {evento.model_dump()}")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": "Evento preparado. Muestra el resumen al usuario y pide confirmación explícita antes de llamar a confirmar_evento_pendiente.",
        "evento": evento.model_dump(mode="json"),
    }

async def confirmar_evento_pendiente() -> dict:
    """Crea en Bitrix el evento que estaba pendiente de confirmación.

    Úsala SOLO cuando el usuario ha confirmado explícitamente el evento
    que le acabas de proponer (respuestas tipo "sí", "vale", "dale",
    "confirmar", "hazlo", "adelante").

    Si el usuario NO ha confirmado o quiere cambios, NO llames a esta tool.
    """
    global _EVENTO_PENDIENTE

    if _EVENTO_PENDIENTE is None:
        return {
            "ok": False,
            "mensaje": "No hay ningún evento pendiente de confirmar.",
        }

    evento = _EVENTO_PENDIENTE
    _EVENTO_PENDIENTE = None

    print(f"TOOL: confirmar_evento_pendiente ejecutada")

    try:
        event_id = await crear_evento_bitrix(evento)
    except BitrixError as e:
        return {
            "ok": False,
            "mensaje": f"Bitrix rechazó el evento: {e}",
            "evento": evento.model_dump(mode="json"),
        }

    return {
        "ok": True,
        "mensaje": "Evento creado correctamente en Bitrix.",
        "bitrix_id": event_id,
        "evento": evento.model_dump(mode="json"),
    }

#def consultar_eventos(fecha_inicio: datetime | None = None, fecha_fin: datetime | None = None, categoria: str | None = None, texto_libre: str | None = None,) -> dict:

#def modificar_tarea(id, cambios):

#def pedir_aclaracion(pregunta):