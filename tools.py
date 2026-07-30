from datetime import datetime
from models import Evento
from bitrix import (
    crear_evento_bitrix,
    consultar_eventos_bitrix,
    modificar_evento_bitrix,
    eliminar_evento_bitrix,
    BitrixError,
)

# Buffer unificado de operaciones pendientes de confirmar.
# Cada entrada: {"tipo": "crear"|"modificar"|"eliminar", "payload": ...}
_OPERACIONES_PENDIENTES: list[dict] = []


def responder_texto(mensaje: str) -> str:
    """Termina el turno respondiendo al usuario con un mensaje.

    Úsala para respuestas informativas, resúmenes, confirmaciones,
    o cuando no haga falta acción posterior del usuario.
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
    """Prepara la CREACIÓN de un evento. No lo crea aún en Bitrix.

    Se añade al buffer de operaciones pendientes. Puedes llamarla varias
    veces para preparar múltiples eventos. Todos se crearán cuando el
    usuario confirme con confirmar_operaciones_pendientes.
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

    # Dedup: misma creación (nombre + fecha_inicio) ya en buffer → ignorar
    for op in _OPERACIONES_PENDIENTES:
        if op["tipo"] == "crear":
            existente: Evento = op["payload"]
            if existente.nombre == evento.nombre and existente.fecha_inicio == evento.fecha_inicio:
                print(f"TOOL: crear_evento duplicado ignorado ({evento.nombre})")
                return {
                    "ok": True,
                    "duplicado_ignorado": True,
                    "mensaje": f"Ya estaba en la lista. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
                    "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
                }

    _OPERACIONES_PENDIENTES.append({"tipo": "crear", "payload": evento})
    print(f"TOOL: crear_evento añadido ({len(_OPERACIONES_PENDIENTES)} pendientes): {evento.model_dump()}")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Evento preparado. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
        "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
        "evento": evento.model_dump(mode="json"),
    }


async def modificar_evento(
    id: int,
    nombre: str | None = None,
    fecha_inicio: datetime | None = None,
    duracion_min: int | None = None,
    descripcion: str | None = None,
    prioridad: str | None = None,
) -> dict:
    """Prepara la MODIFICACIÓN de un evento existente. No lo modifica aún.

    Antes de llamarla, DEBES usar consultar_eventos para localizar el
    evento y obtener su id. La tool valida que el id existe en Bitrix.
    Se aplicará cuando el usuario confirme con
    confirmar_operaciones_pendientes.

    Args:
        id: identificador Bitrix del evento (obligatorio).
        nombre: nuevo título, si se cambia.
        fecha_inicio: nueva fecha/hora de inicio, si se cambia.
        duracion_min: nueva duración, si se cambia.
        descripcion: nueva descripción, si se cambia.
        prioridad: "alta", "media" o "baja", si se cambia.
    """
    try:
        eventos = await consultar_eventos_bitrix()
    except BitrixError as e:
        return {"ok": False, "mensaje": f"No pude verificar el evento en Bitrix: {e}"}

    evento_actual = next((e for e in eventos if str(e.get("ID")) == str(id)), None)
    if evento_actual is None:
        return {
            "ok": False,
            "mensaje": f"No existe un evento con id {id}. Usa consultar_eventos para verificar.",
        }

    cambios = {}
    if nombre is not None: cambios["nombre"] = nombre
    if fecha_inicio is not None: cambios["fecha_inicio"] = fecha_inicio
    if duracion_min is not None: cambios["duracion_min"] = duracion_min
    if descripcion is not None: cambios["descripcion"] = descripcion
    if prioridad is not None: cambios["prioridad"] = prioridad

    if not cambios:
        return {"ok": False, "mensaje": "No has indicado ningún campo a modificar."}

    # Dedup: si ya había una modificación pendiente al mismo id, la reemplaza.
    for i, op in enumerate(_OPERACIONES_PENDIENTES):
        if op["tipo"] == "modificar" and op["payload"]["id"] == id:
            _OPERACIONES_PENDIENTES[i] = {
                "tipo": "modificar",
                "payload": {"id": id, "evento_actual": evento_actual, "cambios": cambios},
            }
            print(f"TOOL: modificar_evento reemplazado (id={id})")
            return {
                "ok": True,
                "reemplazado": True,
                "mensaje": f"Modificación actualizada. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
                "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
            }

    _OPERACIONES_PENDIENTES.append({
        "tipo": "modificar",
        "payload": {"id": id, "evento_actual": evento_actual, "cambios": cambios},
    })
    print(f"TOOL: modificar_evento añadido (id={id}, cambios={list(cambios.keys())})")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Modificación preparada. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
        "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
        "nombre_actual": evento_actual.get("NAME"),
        "cambios": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in cambios.items()},
    }


async def eliminar_evento(id: int) -> dict:
    """Prepara la ELIMINACIÓN de un evento existente. No lo elimina aún.

    Antes de llamarla, DEBES usar consultar_eventos para localizar el
    evento y obtener su id. Se aplicará cuando el usuario confirme con
    confirmar_operaciones_pendientes.
    """
    try:
        eventos = await consultar_eventos_bitrix()
    except BitrixError as e:
        return {"ok": False, "mensaje": f"No pude verificar el evento en Bitrix: {e}"}

    evento_actual = next((e for e in eventos if str(e.get("ID")) == str(id)), None)
    if evento_actual is None:
        return {
            "ok": False,
            "mensaje": f"No existe un evento con id {id}. Usa consultar_eventos para verificar.",
        }

    # Dedup: misma eliminación ya en buffer → ignorar
    for op in _OPERACIONES_PENDIENTES:
        if op["tipo"] == "eliminar" and op["payload"]["id"] == id:
            print(f"TOOL: eliminar_evento duplicado ignorado (id={id})")
            return {
                "ok": True,
                "duplicado_ignorado": True,
                "mensaje": f"Ya estaba en la lista. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
                "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
            }

    _OPERACIONES_PENDIENTES.append({
        "tipo": "eliminar",
        "payload": {
            "id": id,
            "nombre": evento_actual.get("NAME"),
            "fecha_inicio": evento_actual.get("DATE_FROM"),
        },
    })
    print(f"TOOL: eliminar_evento añadido (id={id}, nombre={evento_actual.get('NAME')})")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Eliminación preparada. Total pendientes: {len(_OPERACIONES_PENDIENTES)}.",
        "operaciones_pendientes_total": len(_OPERACIONES_PENDIENTES),
        "nombre": evento_actual.get("NAME"),
        "fecha_inicio": evento_actual.get("DATE_FROM"),
    }


async def confirmar_operaciones_pendientes() -> dict:
    """Ejecuta en Bitrix TODAS las operaciones pendientes (crear, modificar, eliminar).

    Se ejecutan en el orden en que se prepararon. Después, la lista queda
    vacía. Úsala SOLO cuando el usuario haya confirmado explícitamente
    ("sí", "vale", "confirma", "adelante").
    """
    global _OPERACIONES_PENDIENTES

    if not _OPERACIONES_PENDIENTES:
        return {"ok": False, "mensaje": "No hay operaciones pendientes de confirmar."}

    pendientes = _OPERACIONES_PENDIENTES.copy()
    _OPERACIONES_PENDIENTES = []

    creados, modificados, eliminados, fallidos = [], [], [], []

    for op in pendientes:
        tipo, payload = op["tipo"], op["payload"]
        try:
            if tipo == "crear":
                event_id = await crear_evento_bitrix(payload)
                creados.append({"bitrix_id": event_id, "nombre": payload.nombre})
            elif tipo == "modificar":
                await modificar_evento_bitrix(
                    payload["id"], payload["evento_actual"], payload["cambios"]
                )
                modificados.append({
                    "bitrix_id": payload["id"],
                    "cambios": list(payload["cambios"].keys()),
                })
            elif tipo == "eliminar":
                await eliminar_evento_bitrix(payload["id"])
                eliminados.append({"bitrix_id": payload["id"], "nombre": payload["nombre"]})
        except BitrixError as e:
            fallidos.append({"tipo": tipo, "error": str(e)})

    print(
        f"TOOL: confirmar_operaciones_pendientes: "
        f"{len(creados)} creados, {len(modificados)} modificados, "
        f"{len(eliminados)} eliminados, {len(fallidos)} fallidos"
    )

    return {
        "ok": len(fallidos) == 0,
        "total_creados": len(creados),
        "total_modificados": len(modificados),
        "total_eliminados": len(eliminados),
        "total_fallidos": len(fallidos),
        "creados": creados,
        "modificados": modificados,
        "eliminados": eliminados,
        "fallidos": fallidos,
    }


async def cancelar_operaciones_pendientes() -> dict:
    """Descarta TODAS las operaciones pendientes (crear, modificar, eliminar).

    Úsala cuando el usuario diga "cancela todo", "olvídalo", "empecemos
    de nuevo", o cuando pida cambios que requieran rehacer la lista.
    """
    global _OPERACIONES_PENDIENTES
    n = len(_OPERACIONES_PENDIENTES)
    _OPERACIONES_PENDIENTES = []
    print(f"TOOL: cancelar_operaciones_pendientes ejecutada ({n} descartadas)")
    return {
        "ok": True,
        "canceladas": n,
        "mensaje": f"{n} operación(es) pendiente(s) descartada(s).",
    }


async def consultar_eventos(
    fecha_inicio: datetime | None = None,
    fecha_fin: datetime | None = None,
    categoria: str | None = None,
    texto_libre: str | None = None,
) -> dict:
    """Consulta los eventos del calendario del usuario.

    Úsala cuando el usuario pregunte por su agenda o quiera saber qué
    tiene agendado. Ejemplos: "¿qué tengo mañana?", "¿estoy libre el
    viernes?", "¿cuándo tengo la cita con Juan?".

    Args:
        fecha_inicio: eventos que empiecen desde esta fecha en adelante.
        fecha_fin: eventos que empiecen antes de esta fecha.
        categoria: "personal" o "empresa" (solo si el usuario lo pide).
        texto_libre: busca en nombre y descripción.
    """
    print(f"TOOL: consultar_eventos ejecutada")

    try:
        eventos = await consultar_eventos_bitrix(fecha_inicio, fecha_fin)
    except BitrixError as e:
        return {"ok": False, "mensaje": f"Bitrix rechazó la búsqueda: {e}", "eventos": []}

    if texto_libre:
        q = texto_libre.lower()
        eventos = [
            e for e in eventos
            if q in (e.get("NAME") or "").lower()
            or q in (e.get("DESCRIPTION") or "").lower()
        ]

    eventos_normalizados = [
        {
            "id": e["ID"],
            "nombre": e["NAME"],
            "fecha_inicio": e["DATE_FROM"],
            "fecha_fin": e["DATE_TO"],
            "descripcion": e.get("DESCRIPTION", ""),
            "importancia": e.get("IMPORTANCE", ""),
        }
        for e in eventos
    ]

    return {
        "ok": True,
        "mensaje": f"{len(eventos_normalizados)} eventos encontrados.",
        "eventos": eventos_normalizados,
    }


async def listar_eventos_preparados() -> dict:
    """Devuelve el BUFFER INTERNO de operaciones preparadas en el turno
    actual, que aún no se han ejecutado en Bitrix y esperan confirmación.

    NO consulta el calendario real. Para preguntas sobre la agenda del
    usuario, usa consultar_eventos.

    Cuándo usar esta tool:
    - Antes de responder al usuario con un resumen o listado de lo que
      acabas de preparar, para no listar de memoria.
    - Si el usuario pregunta "¿qué tienes preparado?", "¿qué ibas a
      confirmar?", "recuérdame lo que estabas por hacer".
    - Antes de añadir más operaciones, para saber qué hay ya.
    """
    print(f"TOOL: listar_eventos_preparados ejecutada ({len(_OPERACIONES_PENDIENTES)} en buffer)")

    operaciones = []
    for op in _OPERACIONES_PENDIENTES:
        if op["tipo"] == "crear":
            operaciones.append({
                "tipo": "crear",
                "evento": op["payload"].model_dump(mode="json"),
            })
        elif op["tipo"] == "modificar":
            cambios_json = {
                k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in op["payload"]["cambios"].items()
            }
            operaciones.append({
                "tipo": "modificar",
                "id": op["payload"]["id"],
                "nombre_actual": op["payload"]["evento_actual"].get("NAME"),
                "cambios": cambios_json,
            })
        elif op["tipo"] == "eliminar":
            operaciones.append({
                "tipo": "eliminar",
                "id": op["payload"]["id"],
                "nombre": op["payload"]["nombre"],
                "fecha_inicio": op["payload"]["fecha_inicio"],
            })

    return {"ok": True, "total": len(operaciones), "operaciones": operaciones}