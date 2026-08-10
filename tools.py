"""Tools que el brain del agente invoca via Anthropic tool_use. Cada tool
opera sobre un contexto de usuario (user_id) que se pasa como primer
argumento y que agent._ejecutar_tool inyecta en runtime (el modelo LLM
no ve este parametro en el schema).

Buffer de operaciones pendientes: dict indexado por user_id. Dos usuarios
usando el bot en paralelo tienen buffers aislados; el "si, confirma" de
Alexander solo ejecuta las operaciones que EL preparo, no las que Carles
tenga preparadas.

Lookup del contexto Bitrix: cada tool que necesita hablar con Bitrix
resuelve (webhook, bitrix_user_id) via _contexto_bitrix(user_id), que
consulta config_usuarios.USUARIOS_POR_USERNAME.
"""
from datetime import datetime, timedelta
from models import Evento, calcular_prioridad
from bitrix import (
    crear_evento_bitrix,
    consultar_eventos_bitrix,
    modificar_evento_bitrix,
    eliminar_evento_bitrix,
    consultar_ocupacion_bitrix,
    BitrixError,
    _parse_bitrix_date,
)
from huecos import _calcular_huecos
from config_usuarios import USUARIOS_POR_USERNAME


# Buffer unificado de operaciones pendientes de confirmar, POR USUARIO.
# Cada entrada de la lista: {"tipo": "crear"|"modificar"|"eliminar", "payload": ...}
# Cada usuario tiene su propia lista aislada, indexada por user_id ("carles"|"alexander").
_OPERACIONES_PENDIENTES: dict[str, list[dict]] = {}


# --- Helpers privados ---

def _buffer(user_id: str) -> list[dict]:
    """Devuelve la lista de operaciones pendientes del usuario, creandola
    vacia si es la primera vez. Todas las tools que tocan el buffer
    pasan por aqui para evitar duplicar setdefault y hacer explicito el
    aislamiento por usuario."""
    return _OPERACIONES_PENDIENTES.setdefault(user_id, [])


def _contexto_bitrix(user_id: str) -> tuple[str, int]:
    """Extrae (webhook, bitrix_user_id) del contexto de usuario para
    propagarlo a las llamadas a bitrix.py. Si el usuario no existe en
    config o no tiene webhook, lanza ValueError. El caller es
    responsable de convertirlo en un dict de error para el brain."""
    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None:
        raise ValueError(f"Usuario desconocido: '{user_id}'.")
    webhook = usuario.get("webhook_bitrix", "")
    bitrix_uid = usuario.get("bitrix_user_id", 0)
    if not webhook or not bitrix_uid:
        raise ValueError(f"Usuario '{user_id}' sin contexto Bitrix configurado.")
    return webhook, bitrix_uid


# --- Tool terminal ---

def responder_texto(mensaje: str) -> str:
    """Termina el turno respondiendo al usuario con un mensaje.

    Usala para respuestas informativas, resumenes, confirmaciones,
    o cuando no haga falta accion posterior del usuario.

    NOTA: esta tool no necesita user_id porque no toca ni buffer ni
    Bitrix; solo devuelve el mensaje que el bot mandara al usuario.
    """
    print("TOOL: responder_texto ejecutada")
    return mensaje


# --- Tools de preparacion (buffer) ---

async def crear_evento(
    user_id: str,
    nombre: str,
    duracion_min: int,
    fecha_inicio: datetime,
    categoria: str,
    involucrado: str = "",
    descripcion: str = "",
    fecha_limite: datetime | None = None,
    tipo_actividad: str = "",
    prioridad: str | None = None,
) -> dict:
    """Prepara la CREACION de un evento. No lo crea aun en Bitrix.

    Se anade al buffer de operaciones pendientes del usuario. Puedes
    llamarla varias veces para preparar multiples eventos. Todos se
    crearan cuando el usuario confirme con confirmar_operaciones_pendientes.

    NOTA sobre prioridad: NO la pases salvo que el usuario la pida
    explicitamente ("marcala como alta"). Si la omites, se calcula
    automaticamente a partir de involucrado y fecha_limite segun la
    regla del PRD (ver models.calcular_prioridad).
    """
    # Coercion de fechas si vienen como string. fecha_limite se usa en
    # calcular_prioridad antes de que Pydantic haga su conversion.
    if isinstance(fecha_inicio, str):
        try:
            fecha_inicio = datetime.fromisoformat(fecha_inicio)
        except ValueError:
            return {"ok": False, "mensaje": f"Fecha de inicio invalida: '{fecha_inicio}'."}
    if isinstance(fecha_limite, str):
        try:
            fecha_limite = datetime.fromisoformat(fecha_limite)
        except ValueError:
            return {"ok": False, "mensaje": f"Fecha limite invalida: '{fecha_limite}'."}

    # Si el modelo no paso prioridad, la calculamos deterministamente.
    if prioridad is None:
        prioridad = calcular_prioridad(involucrado, fecha_limite).value
        print(f"TOOL[{user_id}]: crear_evento prioridad calculada -> {prioridad}")

    try:
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
    except Exception as e:
        print(f"TOOL[{user_id}]: crear_evento validacion fallida: {type(e).__name__}: {e}")
        return {
            "ok": False,
            "mensaje": f"No he podido preparar el evento: {e}. Corrige los datos y vuelve a intentarlo.",
        }

    buffer = _buffer(user_id)

    # Dedup: misma creacion (nombre + fecha_inicio) ya en buffer -> ignorar
    for op in buffer:
        if op["tipo"] == "crear":
            existente: Evento = op["payload"]
            if existente.nombre == evento.nombre and existente.fecha_inicio == evento.fecha_inicio:
                print(f"TOOL[{user_id}]: crear_evento duplicado ignorado ({evento.nombre})")
                return {
                    "ok": True,
                    "duplicado_ignorado": True,
                    "mensaje": f"Ya estaba en la lista. Total pendientes: {len(buffer)}.",
                    "operaciones_pendientes_total": len(buffer),
                }

    buffer.append({"tipo": "crear", "payload": evento})
    print(f"TOOL[{user_id}]: crear_evento anadido ({len(buffer)} pendientes): {evento.model_dump()}")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Evento preparado. Total pendientes: {len(buffer)}.",
        "operaciones_pendientes_total": len(buffer),
        "evento": evento.model_dump(mode="json"),
    }


async def modificar_evento(
    user_id: str,
    id: int,
    nombre: str | None = None,
    fecha_inicio: datetime | None = None,
    duracion_min: int | None = None,
    descripcion: str | None = None,
    prioridad: str | None = None,
) -> dict:
    """Prepara la MODIFICACION de un evento existente. No lo modifica aun.

    Antes de llamarla, DEBES usar consultar_eventos para localizar el
    evento y obtener su id. La tool valida que el id existe en Bitrix.
    Se aplicara cuando el usuario confirme con
    confirmar_operaciones_pendientes.

    Args:
        id: identificador Bitrix del evento (obligatorio).
        nombre: nuevo titulo, si se cambia.
        fecha_inicio: nueva fecha/hora de inicio, si se cambia.
        duracion_min: nueva duracion, si se cambia.
        descripcion: nueva descripcion, si se cambia.
        prioridad: "alta", "media" o "baja", si se cambia.
    """
    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    try:
        eventos = await consultar_eventos_bitrix(webhook, bitrix_uid)
    except BitrixError as e:
        return {"ok": False, "mensaje": f"No pude verificar el evento en Bitrix: {e}"}

    evento_actual = next((e for e in eventos if str(e.get("ID")) == str(id)), None)
    if evento_actual is None:
        return {
            "ok": False,
            "mensaje": f"No existe un evento con id {id}. Usa consultar_eventos para verificar.",
        }

    # Coercion de string a datetime para que en el buffer siempre haya
    # datetime, no str.
    if isinstance(fecha_inicio, str):
        try:
            fecha_inicio = datetime.fromisoformat(fecha_inicio)
        except ValueError:
            return {"ok": False, "mensaje": f"Fecha invalida: '{fecha_inicio}'."}

    cambios = {}
    if nombre is not None: cambios["nombre"] = nombre
    if fecha_inicio is not None: cambios["fecha_inicio"] = fecha_inicio
    if duracion_min is not None: cambios["duracion_min"] = duracion_min
    if descripcion is not None: cambios["descripcion"] = descripcion
    if prioridad is not None: cambios["prioridad"] = prioridad

    if not cambios:
        return {"ok": False, "mensaje": "No has indicado ningun campo a modificar."}

    buffer = _buffer(user_id)

    # Dedup: si ya habia una modificacion pendiente al mismo id, la reemplaza.
    for i, op in enumerate(buffer):
        if op["tipo"] == "modificar" and op["payload"]["id"] == id:
            buffer[i] = {
                "tipo": "modificar",
                "payload": {"id": id, "evento_actual": evento_actual, "cambios": cambios},
            }
            print(f"TOOL[{user_id}]: modificar_evento reemplazado (id={id})")
            return {
                "ok": True,
                "reemplazado": True,
                "mensaje": f"Modificacion actualizada. Total pendientes: {len(buffer)}.",
                "operaciones_pendientes_total": len(buffer),
            }

    buffer.append({
        "tipo": "modificar",
        "payload": {"id": id, "evento_actual": evento_actual, "cambios": cambios},
    })
    print(f"TOOL[{user_id}]: modificar_evento anadido (id={id}, cambios={list(cambios.keys())})")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Modificacion preparada. Total pendientes: {len(buffer)}.",
        "operaciones_pendientes_total": len(buffer),
        "nombre_actual": evento_actual.get("NAME"),
        "cambios": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in cambios.items()},
    }


async def eliminar_evento(user_id: str, id: int) -> dict:
    """Prepara la ELIMINACION de un evento existente. No lo elimina aun.

    Antes de llamarla, DEBES usar consultar_eventos para localizar el
    evento y obtener su id. Se aplicara cuando el usuario confirme con
    confirmar_operaciones_pendientes.
    """
    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    try:
        eventos = await consultar_eventos_bitrix(webhook, bitrix_uid)
    except BitrixError as e:
        return {"ok": False, "mensaje": f"No pude verificar el evento en Bitrix: {e}"}

    evento_actual = next((e for e in eventos if str(e.get("ID")) == str(id)), None)
    if evento_actual is None:
        return {
            "ok": False,
            "mensaje": f"No existe un evento con id {id}. Usa consultar_eventos para verificar.",
        }

    buffer = _buffer(user_id)

    # Dedup: misma eliminacion ya en buffer -> ignorar
    for op in buffer:
        if op["tipo"] == "eliminar" and op["payload"]["id"] == id:
            print(f"TOOL[{user_id}]: eliminar_evento duplicado ignorado (id={id})")
            return {
                "ok": True,
                "duplicado_ignorado": True,
                "mensaje": f"Ya estaba en la lista. Total pendientes: {len(buffer)}.",
                "operaciones_pendientes_total": len(buffer),
            }

    buffer.append({
        "tipo": "eliminar",
        "payload": {
            "id": id,
            "nombre": evento_actual.get("NAME"),
            "fecha_inicio": evento_actual.get("DATE_FROM"),
        },
    })
    print(f"TOOL[{user_id}]: eliminar_evento anadido (id={id}, nombre={evento_actual.get('NAME')})")

    return {
        "ok": True,
        "pendiente_confirmacion": True,
        "mensaje": f"Eliminacion preparada. Total pendientes: {len(buffer)}.",
        "operaciones_pendientes_total": len(buffer),
        "nombre": evento_actual.get("NAME"),
        "fecha_inicio": evento_actual.get("DATE_FROM"),
    }


# --- Tools de confirmacion ---

async def confirmar_operaciones_pendientes(user_id: str) -> dict:
    """Ejecuta en Bitrix TODAS las operaciones pendientes DEL USUARIO
    (crear, modificar, eliminar).

    Se ejecutan en el orden en que se prepararon. Despues, la lista
    de ESTE usuario queda vacia (las de otros usuarios no se tocan).
    Usala SOLO cuando el usuario haya confirmado explicitamente ("si",
    "vale", "confirma", "adelante").
    """
    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    buffer = _buffer(user_id)

    if not buffer:
        return {"ok": False, "mensaje": "No hay operaciones pendientes de confirmar."}

    pendientes = buffer.copy()
    # Vaciar SOLO el buffer del usuario que confirma (no un global .clear()).
    _OPERACIONES_PENDIENTES[user_id] = []

    creados, modificados, eliminados, fallidos = [], [], [], []

    for op in pendientes:
        tipo, payload = op["tipo"], op["payload"]
        try:
            if tipo == "crear":
                event_id = await crear_evento_bitrix(webhook, bitrix_uid, payload)
                creados.append({"bitrix_id": event_id, "nombre": payload.nombre})
            elif tipo == "modificar":
                await modificar_evento_bitrix(
                    webhook, bitrix_uid,
                    payload["id"], payload["evento_actual"], payload["cambios"],
                )
                modificados.append({
                    "bitrix_id": payload["id"],
                    "cambios": list(payload["cambios"].keys()),
                })
            elif tipo == "eliminar":
                await eliminar_evento_bitrix(webhook, bitrix_uid, payload["id"])
                eliminados.append({"bitrix_id": payload["id"], "nombre": payload["nombre"]})
        except Exception as e:
            fallidos.append({"tipo": tipo, "error": f"{type(e).__name__}: {e}"})

    print(
        f"TOOL[{user_id}]: confirmar_operaciones_pendientes: "
        f"{len(creados)} creados, {len(modificados)} modificados, "
        f"{len(eliminados)} eliminados, {len(fallidos)} fallidos"
    )
    for f in fallidos:
        print(f"  FALLIDO ({f['tipo']}): {f['error']}")

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


async def cancelar_operaciones_pendientes(user_id: str) -> dict:
    """Descarta TODAS las operaciones pendientes DEL USUARIO.

    Usala cuando el usuario diga "cancela todo", "olvidalo", "empecemos
    de nuevo", o cuando pida cambios que requieran rehacer la lista.
    Solo afecta al buffer del usuario que llama, nunca al de otros.
    """
    buffer = _buffer(user_id)
    n = len(buffer)
    _OPERACIONES_PENDIENTES[user_id] = []
    print(f"TOOL[{user_id}]: cancelar_operaciones_pendientes ejecutada ({n} descartadas)")
    return {
        "ok": True,
        "canceladas": n,
        "mensaje": f"{n} operacion(es) pendiente(s) descartada(s).",
    }


# --- Tools de consulta ---

async def consultar_eventos(
    user_id: str,
    fecha_inicio: datetime | None = None,
    fecha_fin: datetime | None = None,
    categoria: str | None = None,
    texto_libre: str | None = None,
) -> dict:
    """Consulta los eventos del calendario del usuario.

    Usala cuando el usuario pregunte por su agenda o quiera saber que
    tiene agendado. Ejemplos: "que tengo manana?", "estoy libre el
    viernes?", "cuando tengo la cita con Juan?".

    Args:
        fecha_inicio: eventos que empiecen desde esta fecha en adelante.
        fecha_fin: eventos que empiecen antes de esta fecha.
        categoria: "personal" o "empresa" (solo si el usuario lo pide).
        texto_libre: busca en nombre y descripcion.
    """
    print(f"TOOL[{user_id}]: consultar_eventos ejecutada")

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e), "eventos": []}

    try:
        eventos = await consultar_ocupacion_bitrix(webhook, bitrix_uid, fecha_inicio, fecha_fin)
    except BitrixError as e:
        return {"ok": False, "mensaje": f"Bitrix rechazo la busqueda: {e}", "eventos": []}

    if texto_libre:
        q = texto_libre.lower()
        eventos = [
            e for e in eventos
            if q in (e.get("NAME") or "").lower()
            or q in (e.get("DESCRIPTION") or "").lower()
        ]

    eventos_normalizados = []
    for e in eventos:
        date_from = e.get("DATE_FROM")
        date_to = e.get("DATE_TO")
        nombre = e.get("NAME")
        if not date_from or not date_to or not nombre:
            print(f"TOOL[{user_id}]: consultar_eventos salto evento incompleto (ID={e.get('ID', '?')})")
            continue
        eventos_normalizados.append({
            "id": e.get("ID", ""),
            "nombre": nombre,
            "fecha_inicio": date_from,
            "fecha_fin": date_to,
            "descripcion": e.get("DESCRIPTION", ""),
            "importancia": e.get("IMPORTANCE", ""),
        })

    return {
        "ok": True,
        "mensaje": f"{len(eventos_normalizados)} eventos encontrados.",
        "eventos": eventos_normalizados,
    }


async def consultar_huecos_libres(
    user_id: str,
    fecha_desde: datetime | None = None,
    fecha_hasta: datetime | None = None,
    duracion_min: int = 30,
    incluir_domingo: bool = False,
    incluir_fuera_horario: bool = False,
) -> dict:
    """Busca huecos libres en la agenda del usuario.

    Devuelve intervalos de tiempo (con etiqueta legible) donde el
    usuario NO tiene eventos y que duran al menos duracion_min.
    Respeta horario laboral por defecto (L-S 09:00-20:00) y aplica
    un margen de 5 min entre eventos.

    Args:
        fecha_desde: rango a inspeccionar. Si None, usa "ahora".
        fecha_hasta: rango a inspeccionar. Si None, usa "ahora + 48h".
        duracion_min: duracion minima del hueco (default 30 min).
        incluir_domingo: True SOLO si el usuario pide domingos
            explicitamente ("hueco el domingo por la manana").
        incluir_fuera_horario: True SOLO si el usuario pide huecos
            fuera del horario tipico ("por la noche", "a las 7 AM",
            "a las 22:00").

    Usala cuando:
    - "cuando puedo agendar X?"
    - "cuando tengo hueco para Y?"
    - "proponme cuando hacer Z"
    - Antes de proponer huecos ante urgencia (empresa o alta prioridad).
    """
    print(f"TOOL[{user_id}]: consultar_huecos_libres ejecutada")

    from models import TZ_LOCAL  # local para no ensuciar el header

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e), "huecos": []}

    ahora = datetime.now(TZ_LOCAL)

    # Coercion si vienen como string
    if isinstance(fecha_desde, str):
        try:
            fecha_desde = datetime.fromisoformat(fecha_desde)
        except ValueError:
            return {"ok": False, "mensaje": f"fecha_desde invalida: '{fecha_desde}'."}
    if isinstance(fecha_hasta, str):
        try:
            fecha_hasta = datetime.fromisoformat(fecha_hasta)
        except ValueError:
            return {"ok": False, "mensaje": f"fecha_hasta invalida: '{fecha_hasta}'."}

    # Defaults
    if fecha_desde is None:
        fecha_desde = ahora
    if fecha_hasta is None:
        fecha_hasta = fecha_desde + timedelta(days=2)

    # tzinfo
    if fecha_desde.tzinfo is None:
        fecha_desde = fecha_desde.replace(tzinfo=TZ_LOCAL)
    if fecha_hasta.tzinfo is None:
        fecha_hasta = fecha_hasta.replace(tzinfo=TZ_LOCAL)

    # Consultar Bitrix
    try:
        eventos = await consultar_ocupacion_bitrix(webhook, bitrix_uid, fecha_desde, fecha_hasta)
    except BitrixError as e:
        return {"ok": False, "mensaje": f"Bitrix rechazo la busqueda: {e}", "huecos": []}

    huecos = _calcular_huecos(
        eventos_bitrix=eventos,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        duracion_min=duracion_min,
        incluir_domingo=incluir_domingo,
        incluir_fuera_horario=incluir_fuera_horario,
        ahora=ahora,
        parse_fecha=_parse_bitrix_date,
    )
    # Mensaje explicito para el brain cuando no hay huecos: evita que
    # reintente en bucle asumiendo que la llamada fallo.
    if not huecos:
        return {
            "ok": True,
            "total": 0,
            "duracion_solicitada_min": duracion_min,
            "huecos": [],
            "mensaje": (
                f"No hay huecos disponibles de al menos {duracion_min} min "
                f"en el rango solicitado. El dia podria estar completamente "
                f"ocupado por eventos, ser fuera de horario laboral, o caer "
                f"en domingo. Informa al usuario directamente. NO vuelvas a "
                f"llamar esta tool ni consultar_eventos: dile lo que has "
                f"encontrado y ofrece alternativas si tiene sentido."
            ),
        }

    return {
        "ok": True,
        "total": len(huecos),
        "duracion_solicitada_min": duracion_min,
        "huecos": huecos,
    }


async def listar_eventos_preparados(user_id: str) -> dict:
    """Devuelve el BUFFER INTERNO de operaciones preparadas por el
    usuario en el turno actual, que aun no se han ejecutado en Bitrix
    y esperan confirmacion.

    NO consulta el calendario real. Para preguntas sobre la agenda del
    usuario, usa consultar_eventos.

    Cuando usar esta tool:
    - Antes de responder al usuario con un resumen o listado de lo que
      acabas de preparar, para no listar de memoria.
    - Si el usuario pregunta "que tienes preparado?", "que ibas a
      confirmar?", "recuerdame lo que estabas por hacer".
    - Antes de anadir mas operaciones, para saber que hay ya.
    """
    buffer = _buffer(user_id)
    print(f"TOOL[{user_id}]: listar_eventos_preparados ejecutada ({len(buffer)} en buffer)")

    operaciones = []
    for op in buffer:
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