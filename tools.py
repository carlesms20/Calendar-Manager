from datetime import datetime
from models import Evento

def responder_texto(mensaje: str) -> str:
    """Termina el turno respondiendo al usuario con un mensaje.

    Úsala para respuestas informativas, resúmenes, confirmaciones,
    o cuando no haga falta acción posterior del usuario.

    Args:
        mensaje: Texto que se envía tal cual al usuario.
    """
    print(f"TOOL: responder_texto ejecutada")
    return mensaje

def crear_evento(nombre: str, duracion_min: int, fecha_inicio: datetime, categoria: str, prioridad: str, involucrado: str = "", descripcion: str = "", fecha_limite: datetime | None = None, tipo_actividad: str = "",) -> dict:
    """Crea un evento en el calendario del usuario.

    Úsala cuando el usuario pida agendar algo (reunión, llamada, tarea puntual).
    Rellena todos los campos que puedas inferir del contexto. Si falta algún
    dato crítico (fecha, duración, o involucrado en eventos de empresa),
    llama antes a pedir_aclaracion.

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
    print(f"TOOL: crear_evento ejecutada: {evento.model_dump()}")
    return {
    "ok": True,
    "mensaje": "Evento validado correctamente (STUB — aún no se ha creado en Bitrix).",
    "evento": evento.model_dump(mode="json"),
    }

#def consultar_tareas(filtros):

#def modificar_tarea(id, cambios):

#def pedir_aclaracion(pregunta):

#def responder_texto(mensaje):