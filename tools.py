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

async def _resolver_owner(webhook: str, owner_str: str) -> tuple[int | None, str | None]:
    """Resuelve un nombre de owner a bitrix_user_id via user.search.

    Devuelve (user_id, mensaje_error). Exactamente uno de los dos es
    None:
    - Si hay 1 match activo -> (user_id, None).
    - Si hay 0 matches      -> (None, mensaje legible para el LLM).
    - Si hay N > 1 matches  -> (None, mensaje con la lista de opciones
      para que el LLM pida clarificacion al usuario).

    El caller usa el mensaje tal cual en su return {"ok": False, ...}
    y el LLM lo re-emite al usuario. La estrategia es 'fail loud':
    nunca resolver ambiguedades por nuestra cuenta ni caer al usuario
    actual si no se encuentra.
    """
    from bitrix import buscar_usuarios
    matches = await buscar_usuarios(webhook, owner_str)
    if not matches:
        return None, (
            f"No encontré a '{owner_str}' en Bitrix. Comprueba el nombre "
            f"con el usuario o pídele el email/apellido para volver a probar."
        )
    if len(matches) == 1:
        return matches[0]["id"], None
    # Multiples matches -> lista para que el LLM pregunte cual
    lineas = []
    for m in matches[:8]:  # cap por si acaso
        extras = []
        if m["work_position"]:
            extras.append(m["work_position"])
        if m["email"]:
            extras.append(m["email"])
        sufijo = f" ({', '.join(extras)})" if extras else ""
        lineas.append(f"- {m['nombre_completo']}{sufijo} [id {m['id']}]")
    return None, (
        f"Hay {len(matches)} personas que coinciden con '{owner_str}':\n"
        + "\n".join(lineas)
        + "\n\nPregunta al usuario cuál es y vuelve a llamar con el nombre "
        f"más específico o el id exacto."
    )

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
    usuario NO tiene eventos ni bloques no negociables activos, y que
    duran al menos duracion_min. Respeta horario laboral por defecto
    (L-S 09:00-20:00) y aplica un margen de 5 min entre eventos.

    Los bloques no negociables (gym, comida familiar, tiempo estratégico...)
    se restan automaticamente. NO hay que pedirlos como parametro: se
    consultan de la BD del usuario en cada llamada.

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
    import bloques  # local para evitar ciclos de import en carga

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

    # Consultar bloques no negociables del usuario (falla suave: si la BD
    # peta, seguimos calculando huecos sin ellos y logueamos)
    bloques_activos: list[dict] = []
    try:
        bloques_activos = await bloques.listar_activos_para_calculo(user_id)
    except Exception as e:
        import logger
        logger.warn(
            "tools", "bloques_fetch_failed",
            f"No pude leer bloques no negociables: {type(e).__name__}: {e}",
            user_id=user_id,
            error=e,
        )

    huecos = _calcular_huecos(
        eventos_bitrix=eventos,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        duracion_min=duracion_min,
        incluir_domingo=incluir_domingo,
        incluir_fuera_horario=incluir_fuera_horario,
        ahora=ahora,
        parse_fecha=_parse_bitrix_date,
        bloques_no_negociables=bloques_activos,
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
                f"encontrado and ofrece alternativas si tiene sentido."
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

async def gestionar_bloques(
    user_id: str,
    accion: str,
    id: int | None = None,
    nombre: str | None = None,
    dias_semana: list[int] | None = None,
    hora_inicio: str | None = None,
    hora_fin: str | None = None,
    descripcion: str = "",
) -> dict:
    """Gestiona los BLOQUES NO NEGOCIABLES del usuario.

    Un bloque es una franja horaria recurrente semanal declarada como
    intocable por el CEO: gimnasio, comida familiar, tiempo estratégico
    de trabajo profundo, etc. Los bloques ACTIVOS se restan
    automaticamente de consultar_huecos_libres.

    Semantica intencionada: los bloques bloquean la propuesta de huecos,
    NO impiden crear eventos en esa franja. Si el CEO pide agendar algo
    dentro de un bloque, avisale del solape y pide confirmacion explicita
    antes de meterlo en el buffer con crear_evento.

    Acciones soportadas:
    - "listar": devuelve todos los bloques activos del usuario. No
      requiere mas parametros. Usala cuando pregunte "que bloques tengo",
      "que tengo protegido", "que dias tengo el gym", etc.
    - "añadir": crea un bloque nuevo. Requiere nombre, dias_semana,
      hora_inicio, hora_fin. Ej: "bloqueame el gym L-V 07:00-08:00".
    - "eliminar": borra un bloque. Requiere id. Antes usa "listar"
      para obtener el id correcto. Ej: "quita el bloque del gym".
    - "desactivar": pausa un bloque sin borrarlo. Requiere id.
      Util cuando el CEO quiere saltarse el bloque un tiempo pero no
      perder la configuracion. Ej: "esta semana no voy al gym, quitalo
      pero solo temporal".

    Args:
        accion: una de "listar", "añadir", "eliminar", "desactivar".
        id: identificador del bloque (obligatorio para eliminar/desactivar).
        nombre: titulo corto del bloque (obligatorio para añadir).
            Ej: "Gimnasio", "Comida familiar", "Trabajo profundo".
        dias_semana: lista de dias en formato ISO (0=lunes, 1=martes,
            ..., 6=domingo). Ej: [0,1,2,3,4] para L-V.
        hora_inicio: "HH:MM" en hora local Madrid. Ej: "07:00".
        hora_fin: "HH:MM" en hora local Madrid. Ej: "08:00".
        descripcion: contexto adicional opcional. Ej: "gym con Marc".
    """
    print(f"TOOL[{user_id}]: gestionar_bloques accion={accion}")
    import bloques

    accion_norm = (accion or "").strip().lower()

    if accion_norm == "listar":
        try:
            bloques_activos = await bloques.listar(user_id, solo_activos=True)
        except Exception as e:
            return {"ok": False, "mensaje": f"No pude leer los bloques: {e}"}
        return {
            "ok": True,
            "total": len(bloques_activos),
            "bloques": bloques_activos,
        }

    if accion_norm in ("añadir", "anadir", "add", "crear"):
        # Validacion previa antes de tocar la BD
        if not nombre or not nombre.strip():
            return {"ok": False, "mensaje": "Falta el nombre del bloque."}
        if not dias_semana:
            return {"ok": False, "mensaje": "Faltan los dias de la semana (0=lunes...6=domingo)."}
        if not hora_inicio or not hora_fin:
            return {"ok": False, "mensaje": "Faltan hora_inicio y/o hora_fin (formato HH:MM)."}

        try:
            creado = await bloques.crear(
                user_id=user_id,
                nombre=nombre,
                dias_semana=dias_semana,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
                descripcion=descripcion or "",
            )
        except ValueError as e:
            return {"ok": False, "mensaje": f"Datos invalidos: {e}"}
        except Exception as e:
            return {"ok": False, "mensaje": f"Error creando el bloque: {type(e).__name__}: {e}"}

        return {
            "ok": True,
            "bloque": creado,
            "mensaje": (
                f"Bloque '{creado['nombre']}' creado "
                f"({creado['hora_inicio']}-{creado['hora_fin']} en dias "
                f"{creado['dias_semana']})."
            ),
        }

    if accion_norm in ("eliminar", "borrar", "delete"):
        if id is None:
            return {"ok": False, "mensaje": "Falta el id del bloque. Usa 'listar' antes para obtenerlo."}
        try:
            ok = await bloques.eliminar(user_id, int(id))
        except Exception as e:
            return {"ok": False, "mensaje": f"Error eliminando: {type(e).__name__}: {e}"}
        if not ok:
            return {"ok": False, "mensaje": f"No existe un bloque con id {id} para este usuario."}
        return {"ok": True, "mensaje": f"Bloque {id} eliminado."}

    if accion_norm in ("desactivar", "pausar", "deactivate"):
        if id is None:
            return {"ok": False, "mensaje": "Falta el id del bloque. Usa 'listar' antes para obtenerlo."}
        try:
            ok = await bloques.desactivar(user_id, int(id))
        except Exception as e:
            return {"ok": False, "mensaje": f"Error desactivando: {type(e).__name__}: {e}"}
        if not ok:
            return {"ok": False, "mensaje": f"No existe un bloque con id {id} para este usuario."}
        return {"ok": True, "mensaje": f"Bloque {id} desactivado (pausado, no borrado)."}

    return {
        "ok": False,
        "mensaje": (
            f"Accion desconocida: '{accion}'. Usa una de: "
            f"'listar', 'añadir', 'eliminar', 'desactivar'."
        ),
    }

async def crear_tarea(
    user_id: str,
    title: str,
    owner: str | None = None,
    status_eos: str | None = None,
    task_type: str | None = None,
    alexander_role: str | None = None,
    next_action: str | None = None,
    expected_result: str | None = None,
    review_date: datetime | None = None,
    deadline: datetime | None = None,
    source: str | None = "Bitrix24",
    risk: str | None = None,
    escalation_condition: str | None = None,
    preparation_required: str | None = None,
    next_action_if_missed: str | None = None,
    requires_conversation: bool | None = None,
    primary_interlocutor: str | None = None,
    conversation_purpose: str | None = None,
    expected_decision: str | None = None,
    meeting_candidate: bool | None = None,
    related_meeting_id: str | None = None,
) -> dict:
    """Crea una tarea nueva en Bitrix (tasks) con los UF_* del EOS.

    Ejecucion DIRECTA (no buffer): la tarea se crea inmediatamente en
    Bitrix. Si el usuario cambia de opinion, actualizar_estado_tarea a
    'Cancelled' o edicion en Bitrix son mecanismos suficientes.

    Solo 'title' es obligatorio. Toda tarea nace con status_eos='New'
    salvo que se pase explicitamente otro.

    Owner:
    - Si owner=None (default) -> RESPONSIBLE_ID = el usuario que crea
      (el CEO habitualmente).
    - Si owner=str -> resuelve via user.search por FIND fulltext.
      - 1 match activo    -> se asigna a esa persona.
      - 0 matches         -> devuelve error legible, tarea NO se crea.
      - N > 1 matches     -> devuelve la lista al LLM para que
        pregunte al usuario cual es. Tarea NO se crea.
      Nunca cae silenciosamente al usuario actual: fail loud.
    """
    print(f"TOOL[{user_id}]: crear_tarea title={title!r} owner={owner!r}")

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    from models import Tarea, EstadoEOS, TipoTarea, RolAlexander
    from bitrix_tasks import crear_tarea as crear_tarea_bitrix

    # --- Resolver owner ---
    responsable_id = bitrix_uid  # default: yo mismo
    if owner is not None and owner.strip():
        resuelto, error_msg = await _resolver_owner(webhook, owner)
        if error_msg:
            return {"ok": False, "mensaje": error_msg}
        responsable_id = resuelto

    # --- Coercion de fecha ---
    if isinstance(review_date, str):
        try:
            review_date = datetime.fromisoformat(review_date)
        except ValueError:
            return {"ok": False, "mensaje": f"review_date invalida: '{review_date}'."}
    if isinstance(deadline, str):
        try:
            deadline = datetime.fromisoformat(deadline)
        except ValueError:
            return {"ok": False, "mensaje": f"deadline invalida: '{deadline}'."}

    # --- Validacion de enums ---
    try:
        estado_inicial = EstadoEOS(status_eos) if status_eos else EstadoEOS.NEW
    except ValueError:
        return {"ok": False, "mensaje": (
            f"status_eos invalido: '{status_eos}'. Debe ser uno de: "
            f"{', '.join(e.value for e in EstadoEOS)}.")}
    try:
        type_enum = TipoTarea(task_type) if task_type else None
    except ValueError:
        return {"ok": False, "mensaje": (
            f"task_type invalido: '{task_type}'. Debe ser uno de: "
            f"{', '.join(t.value for t in TipoTarea)}.")}
    try:
        role_enum = RolAlexander(alexander_role) if alexander_role else None
    except ValueError:
        return {"ok": False, "mensaje": (
            f"alexander_role invalido: '{alexander_role}'. Debe ser uno de: "
            f"{', '.join(r.value for r in RolAlexander)}.")}

    try:
        tarea = Tarea(
            title=title.strip(),
            status_eos=estado_inicial,
            task_type=type_enum,
            alexander_role=role_enum,
            next_action=next_action or None,
            expected_result=expected_result or None,
            review_date=review_date,
            deadline=deadline,
            source=source or None,
            risk=risk or None,
            escalation_condition=escalation_condition or None,
            preparation_required=preparation_required or None,
            next_action_if_missed=next_action_if_missed or None,
            requires_conversation=requires_conversation,
            primary_interlocutor=primary_interlocutor or None,
            conversation_purpose=conversation_purpose or None,
            expected_decision=expected_decision or None,
            meeting_candidate=meeting_candidate,
            related_meeting_id=related_meeting_id or None,
        )
    except Exception as e:
        return {"ok": False, "mensaje": f"No pude preparar la tarea: {e}"}

    try:
        task_id = await crear_tarea_bitrix(webhook, responsable_id, tarea)
    except Exception as e:
        return {"ok": False,
                "mensaje": f"Error creando la tarea en Bitrix: {type(e).__name__}: {e}"}

    tarea.id = task_id
    delegada = responsable_id != bitrix_uid
    print(f"TOOL[{user_id}]: crear_tarea creada id={task_id} "
          f"estado={estado_inicial.value} responsable={responsable_id} "
          f"delegada={delegada}")

    return {
        "ok": True,
        "id": task_id,
        "responsable_id": responsable_id,
        "delegada": delegada,
        "mensaje": (
            f"Tarea creada (id={task_id}, estado '{estado_inicial.value}'"
            + (f", asignada a user_id {responsable_id}" if delegada else "")
            + ")."
        ),
        "tarea": tarea.to_llm_dict(),
    }

async def consultar_tareas(
    user_id: str,
    estado: str | None = None,
    task_type: str | None = None,
    primary_interlocutor: str | None = None,
    solo_activos: bool = True,
    limite: int = 50,
    **kwargs_ignorados,
) -> dict:
    """Lista las tareas del usuario en Bitrix con los UF_* del EOS
    materializados.

    Nota Sprint 4: **kwargs_ignorados absorbe args que el LLM pueda
    inventarse pese al prompt (visto texto_libre pese a instruccion
    explicita). Sin esto, cada llamada errada quema un turno completo.
    Si se ignora algo, se logea para tenerlo controlado.

    Por defecto devuelve solo tareas activas (excluye Completed y
    Cancelled), que es lo que el LLM necesita para responder "¿que
    tengo pendiente?". Si el CEO pide historico, pasa solo_activos=False
    o filtra por estado='Completed'.

    Los filtros son AND: pasar estado='Waiting' y task_type='Task'
    devuelve tareas Waiting de tipo Task exclusivamente.

    Args:
        estado: uno de los 8 estados EOS. Sobreescribe solo_activos.
        task_type: uno de los 8 tipos del work item model.
        primary_interlocutor: match exacto (case-insensitive) por nombre.
        solo_activos: default True. Excluye Completed/Cancelled.
            Ignorado si 'estado' esta puesto.
        limite: cap de tareas devueltas (default 50). Si hay mas,
            trunca y avisa via 'truncado'=True para que el LLM
            proponga un filtro mas estrecho.
    """
    print(f"TOOL[{user_id}]: consultar_tareas estado={estado!r} type={task_type!r} "
          f"interlocutor={primary_interlocutor!r} activos={solo_activos}")
    if kwargs_ignorados:
        print(f"TOOL[{user_id}] WARN: consultar_tareas args ignorados: "
              f"{list(kwargs_ignorados.keys())}")

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    from models import EstadoEOS, TipoTarea
    from bitrix_tasks import listar_tareas

    # Validacion de enums (no toca red si el LLM inventa un valor)
    estado_filter = None
    if estado:
        try:
            estado_filter = EstadoEOS(estado)
        except ValueError:
            return {
                "ok": False,
                "mensaje": (f"estado invalido: '{estado}'. Debe ser uno de: "
                            f"{', '.join(e.value for e in EstadoEOS)}."),
            }
    type_filter = None
    if task_type:
        try:
            type_filter = TipoTarea(task_type)
        except ValueError:
            return {
                "ok": False,
                "mensaje": (f"task_type invalido: '{task_type}'. Debe ser uno de: "
                            f"{', '.join(t.value for t in TipoTarea)}."),
            }

    # Filtro en Bitrix: solo tareas del usuario. Los filtros UF_ se
    # aplican client-side porque Bitrix no documenta filtering sobre
    # UF_* arbitrarios en tasks.task.list.
    filtro = {"RESPONSIBLE_ID": bitrix_uid}

    try:
        tareas = await listar_tareas(webhook, filtro=filtro)
    except Exception as e:
        return {"ok": False,
                "mensaje": f"Error consultando Bitrix: {type(e).__name__}: {e}"}

    # Filtros client-side
    if estado_filter is not None:
        tareas = [t for t in tareas if t.status_eos == estado_filter]
    elif solo_activos:
        terminales = {EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}
        # Incluye tareas sin status_eos (pre-existentes) como "activas"
        tareas = [t for t in tareas if t.status_eos not in terminales]

    if type_filter is not None:
        tareas = [t for t in tareas if t.task_type == type_filter]

    if primary_interlocutor:
        needle = primary_interlocutor.strip().lower()
        tareas = [
            t for t in tareas
            if t.primary_interlocutor and t.primary_interlocutor.strip().lower() == needle
        ]

    total_disponibles = len(tareas)
    tareas = tareas[:limite]

    print(f"TOOL[{user_id}]: consultar_tareas devuelve {len(tareas)} "
          f"(disponibles {total_disponibles})")

    return {
        "ok": True,
        "total_devueltas": len(tareas),
        "total_disponibles": total_disponibles,
        "truncado": total_disponibles > limite,
        "tareas": [t.to_llm_dict() for t in tareas],
    }

async def actualizar_estado_tarea(
    user_id: str,
    id: int,
    nuevo_estado: str,
    owner: str | None = None,
    next_action: str | None = None,
    expected_result: str | None = None,
    review_date: datetime | None = None,
    deadline: datetime | None = None,
    escalation_condition: str | None = None,
    preparation_required: str | None = None,
    next_action_if_missed: str | None = None,
) -> dict:
    """Cambia el status_eos de una tarea y, en la misma llamada,
    actualiza campos asociados y opcionalmente el owner.

    La transicion se valida contra la matriz §6.4. Si es ilegal, la
    tool devuelve error con el motivo y NO toca Bitrix.

    Owner:
    - Si owner=None -> no se toca RESPONSIBLE_ID (queda como estaba).
    - Si owner=str  -> se resuelve via user.search (misma logica que
      crear_tarea). Fallo loud si 0 o N matches.

    IMPORTANTE: cambiar owner NO implica automaticamente pasar a
    'Delegated'. Si el usuario dice "delega esto a Sandra", el LLM
    debe pasar nuevo_estado='Delegated' Y owner='Sandra' en la misma
    llamada. Cambiar solo owner deja la tarea en su estado actual con
    otro responsable, lo cual es un caso legitimo (reasignacion).
    """
    print(f"TOOL[{user_id}]: actualizar_estado_tarea id={id} -> {nuevo_estado} "
          f"owner={owner!r}")

    try:
        webhook, _ = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    from models import EstadoEOS, TransicionIlegal
    from bitrix_tasks import obtener_tarea, actualizar_tarea

    try:
        estado_enum = EstadoEOS(nuevo_estado)
    except ValueError:
        return {"ok": False, "mensaje": (
            f"nuevo_estado invalido: '{nuevo_estado}'. Debe ser uno de: "
            f"{', '.join(e.value for e in EstadoEOS)}.")}

    if isinstance(review_date, str):
        try:
            review_date = datetime.fromisoformat(review_date)
        except ValueError:
            return {"ok": False, "mensaje": f"review_date invalida: '{review_date}'."}
    if isinstance(deadline, str):
        try:
            deadline = datetime.fromisoformat(deadline)
        except ValueError:
            return {"ok": False, "mensaje": f"deadline invalida: '{deadline}'."}

    # --- Resolver owner si viene ---
    nuevo_responsable_id: int | None = None
    if owner is not None and owner.strip():
        resuelto, error_msg = await _resolver_owner(webhook, owner)
        if error_msg:
            return {"ok": False, "mensaje": error_msg}
        nuevo_responsable_id = resuelto

    # --- Fetch estado actual (para validar transicion) ---
    try:
        tarea = await obtener_tarea(webhook, int(id))
    except Exception as e:
        return {"ok": False,
                "mensaje": f"No pude leer la tarea {id}: {type(e).__name__}: {e}"}

    try:
        tarea.validar_transicion_a(estado_enum)
    except TransicionIlegal as e:
        return {"ok": False, "mensaje": str(e)}

    # --- Construir cambios ---
    cambios: dict = {"status_eos": estado_enum}
    if next_action is not None:
        cambios["next_action"] = next_action
    if expected_result is not None:
        cambios["expected_result"] = expected_result
    if review_date is not None:
        cambios["review_date"] = review_date
    if deadline is not None:
        cambios["deadline"] = deadline
    if escalation_condition is not None:
        cambios["escalation_condition"] = escalation_condition
    if preparation_required is not None:
        cambios["preparation_required"] = preparation_required
    if next_action_if_missed is not None:
        cambios["next_action_if_missed"] = next_action_if_missed

    # --- Update en Bitrix (con RESPONSIBLE_ID aparte si aplica) ---
    try:
        await actualizar_tarea(
            webhook, int(id), cambios,
            responsable_id=nuevo_responsable_id,
        )
    except Exception as e:
        return {"ok": False,
                "mensaje": f"Error actualizando en Bitrix: {type(e).__name__}: {e}"}

    estado_ant = tarea.status_eos.value if tarea.status_eos else "[NO DATA]"
    campos_ext = [k for k in cambios if k != "status_eos"]
    if nuevo_responsable_id is not None:
        campos_ext.append("responsable_id")
    print(f"TOOL[{user_id}]: actualizar_estado_tarea OK id={id} "
          f"{estado_ant} -> {estado_enum.value} campos_extra={campos_ext}")

    return {
        "ok": True,
        "id": int(id),
        "estado_anterior": estado_ant,
        "estado_nuevo": estado_enum.value,
        "campos_actualizados": list(cambios.keys()) + (
            ["responsable_id"] if nuevo_responsable_id is not None else []
        ),
        "responsable_id": nuevo_responsable_id,
        "mensaje": (
            f"Tarea {id} actualizada. {estado_ant} -> {estado_enum.value}"
            + (f", asignada a user_id {nuevo_responsable_id}"
               if nuevo_responsable_id is not None else "")
            + "."
        ),
    }

# ============================================================================
# CONVERSACION EJECUTIVA - CONSOLIDACION DE REUNIONES (Sprint 1 Bloque A)
# PHASE 1 §2.5-§2.8, PHASE 2 §1-4, PHASE 6 Doc 3 §11.
# ============================================================================

# Marcador que crear_evento_bitrix inserta en DESCRIPTION cuando el Evento
# tiene 'involucrado' (ver bitrix.py:118-123). Lo reutilizamos aqui para
# extraer el interlocutor de eventos existentes en Bitrix sin duplicar
# convencion. Si algun dia se cambia el prefijo, debe cambiarse en ambos
# sitios; hoy es una constante de facto compartida.
_PREFIJO_INVOLUCRADO = "Involucrado:"

# Palabras clave que sugieren que un asunto NO deberia consolidarse con
# otros por confidencialidad. La deteccion es intencionadamente
# conservadora: solo levanta un flag informativo para que el LLM lo
# considere; no bloquea la propuesta por si sola. La decision final
# aplica el juicio semantico del LLM sobre razones_a_evaluar.
_PALABRAS_CONFIDENCIALES = (
    "confidencial", "privado", "off the record", "a solas",
    "secreto", "sensible", "restringido",
)


def _normalizar_interlocutor(nombre: str) -> str:
    """Clave canonica para agrupar. Case-insensitive, sin espacios
    laterales. NO intenta desambiguar "Carlos" vs "Carlos Perez": esa
    logica es responsabilidad de _resolver_owner cuando toque materializar
    la reunion, no de la deteccion. Aqui preferimos falsos positivos
    (proponer consolidar dos "Carlos" distintos y que el CEO lo aclare)
    a falsos negativos (no detectar la agrupacion por diferencias de
    escritura)."""
    return (nombre or "").strip().lower()


def _extraer_involucrado_de_descripcion(descripcion: str) -> str:
    """Recupera el nombre de 'Involucrado: X' que crear_evento_bitrix
    inserta al principio de la descripcion. Devuelve '' si no lo
    encuentra. Tolera mayusculas/minusculas y variantes con : o :\\n."""
    if not descripcion:
        return ""
    for linea in descripcion.splitlines():
        stripped = linea.strip()
        if stripped.lower().startswith(_PREFIJO_INVOLUCRADO.lower()):
            return stripped[len(_PREFIJO_INVOLUCRADO):].strip()
    return ""


def _tiene_palabras_confidenciales(*textos: str) -> bool:
    """True si alguna de las cadenas contiene alguna palabra clave
    de confidencialidad. Case-insensitive."""
    for t in textos:
        if not t:
            continue
        t_low = t.lower()
        for kw in _PALABRAS_CONFIDENCIALES:
            if kw in t_low:
                return True
    return False


async def proponer_consolidacion(
    user_id: str,
    ventana_dias: int = 14,
) -> dict:
    """Detecta candidatos a Reunion Ejecutiva agrupando elementos activos
    por interlocutor principal (PHASE 1 §2.6, PHASE 2 §1-3, PHASE 6 Doc 3 §11).

    NO modifica nada. Solo detecta y devuelve. La creacion efectiva de la
    reunion (con confirmacion del CEO) es responsabilidad de
    crear_reunion_ejecutiva.

    Fuentes que revisa:
    1. Eventos futuros del calendario en la ventana, con el interlocutor
       parseado desde el prefijo 'Involucrado:' de DESCRIPTION.
    2. Tareas activas con requires_conversation=True y primary_interlocutor
       no vacio.
    3. Operaciones 'crear' evento en el buffer del usuario con
       involucrado no vacio. Esto habilita el caso mas comun del Sprint:
       el CEO acaba de preparar N eventos con la misma persona en el
       mismo turno y aun no ha confirmado.

    Agrupa por interlocutor (case-insensitive, sin desambiguacion de
    apellidos) y devuelve solo grupos con >=2 elementos.

    Aplica los checks DETERMINISTAS del Meeting Compatibility Test §2.7:
    - mismo interlocutor: garantizado por definicion de grupo.
    - duracion agregada razonable: flag si suma > 120 min (§2.7).
    - senal heuristica de confidencialidad: flag si aparecen palabras
      clave ('confidencial', 'privado', 'a solas'...) en titulo o
      descripcion de algun elemento.

    Los checks difusos (contexto relacionado, urgencias divergentes,
    conflictos entre Owners, preparacion incompatible) NO se automatizan:
    se exponen como 'razones_a_evaluar' para que el LLM aplique juicio
    antes de proponer al CEO. La regla final la impone §2.8: consolidar
    solo si nada obliga a separar.

    Args:
        ventana_dias: cuantos dias hacia adelante inspeccionar el
            calendario. Default 14. Reducir a 7 cuando el CEO pregunta
            "esta semana".
    """
    print(f"TOOL[{user_id}]: proponer_consolidacion ventana={ventana_dias}d")

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e), "grupos": []}

    from models import TZ_LOCAL, EstadoEOS
    from bitrix_tasks import listar_tareas

    ahora = datetime.now(TZ_LOCAL)
    ventana_fin = ahora + timedelta(days=max(1, ventana_dias))

    # Estructura acumuladora: clave = interlocutor normalizado.
    # Value = {"display": nombre tal cual aparece por primera vez,
    #          "elementos": [dict, ...]}
    grupos: dict[str, dict] = {}

    def _add(interlocutor: str, elemento: dict):
        clave = _normalizar_interlocutor(interlocutor)
        if not clave:
            return
        entrada = grupos.setdefault(clave, {"display": interlocutor.strip(), "elementos": []})
        entrada["elementos"].append(elemento)

    # --- 1. Eventos futuros del calendario ---
    try:
        eventos_bitrix = await consultar_ocupacion_bitrix(
            webhook, bitrix_uid, ahora, ventana_fin,
        )
    except BitrixError as e:
        return {"ok": False, "mensaje": f"Bitrix rechazo la busqueda de eventos: {e}",
                "grupos": []}

    for ev in eventos_bitrix:
        descripcion = ev.get("DESCRIPTION", "") or ""
        involucrado = _extraer_involucrado_de_descripcion(descripcion)
        if not involucrado:
            continue

        date_from = ev.get("DATE_FROM")
        date_to = ev.get("DATE_TO")
        try:
            inicio_dt = _parse_bitrix_date(date_from) if date_from else None
            fin_dt = _parse_bitrix_date(date_to) if date_to else None
        except Exception:
            inicio_dt = fin_dt = None

        duracion_min = None
        if inicio_dt and fin_dt:
            duracion_min = max(0, int((fin_dt - inicio_dt).total_seconds() / 60))

        _add(involucrado, {
            "tipo": "evento",
            "id": ev.get("ID"),
            "titulo": ev.get("NAME", ""),
            "fecha_inicio": date_from,
            "fecha_fin": date_to,
            "duracion_min": duracion_min,
            "descripcion": descripcion[:400],  # snippet, no full body
        })

    # --- 2. Tareas activas con requires_conversation ---
    try:
        tareas = await listar_tareas(webhook, filtro={"RESPONSIBLE_ID": bitrix_uid})
    except Exception as e:
        # No abortamos: si falla la consulta de tareas, aun tenemos
        # eventos y buffer. Registramos y seguimos.
        import logger
        logger.warn(
            "tools", "consolidacion_tareas_fallo",
            f"No pude leer tareas para consolidacion: {type(e).__name__}: {e}",
            user_id=user_id, error=e,
        )
        tareas = []

    terminales = {EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}
    for t in tareas:
        if t.status_eos in terminales:
            continue
        if not t.requires_conversation:
            continue
        if not t.primary_interlocutor:
            continue
        _add(t.primary_interlocutor, {
            "tipo": "tarea",
            "id": t.id,
            "titulo": t.title,
            "status_eos": t.status_eos.value if t.status_eos else None,
            "next_action": t.next_action,
            "expected_result": t.expected_result,
            "conversation_purpose": t.conversation_purpose,
            "review_date": t.review_date.isoformat() if t.review_date else None,
        })

    # --- 3. Buffer de eventos pendientes ---
    for op in _buffer(user_id):
        if op["tipo"] != "crear":
            continue
        payload: Evento = op["payload"]
        if not payload.involucrado:
            continue
        _add(payload.involucrado, {
            "tipo": "buffer_evento",
            "id": None,  # aun no tiene id Bitrix
            "titulo": payload.nombre,
            "fecha_inicio": payload.fecha_inicio.isoformat(),
            "duracion_min": payload.duracion_min,
            "descripcion": payload.descripcion or "",
        })

    # --- Filtrar: solo grupos con >=2 elementos ---
    grupos_finales: list[dict] = []
    for entrada in grupos.values():
        elementos = entrada["elementos"]
        if len(elementos) < 2:
            continue

        # Checks deterministas del §2.7
        duracion_total = sum(
            (el.get("duracion_min") or 0) for el in elementos
        )
        excede_120 = duracion_total > 120

        confidencial = any(
            _tiene_palabras_confidenciales(el.get("titulo", ""), el.get("descripcion", ""))
            for el in elementos
        )

        # Fechas heterogeneas: si los elementos con fecha estan repartidos
        # en dias distintos, es senal fuerte de que consolidar ahorra
        # cambios de contexto (PHASE 6 Doc 3 §11 - Conversation Batching).
        dias_distintos = set()
        for el in elementos:
            fi = el.get("fecha_inicio")
            if isinstance(fi, str) and len(fi) >= 10:
                dias_distintos.add(fi[:10])
        rangos_amplios = len(dias_distintos) >= 2

        grupos_finales.append({
            "interlocutor": entrada["display"],
            "n_elementos": len(elementos),
            "elementos": elementos,
            "duracion_total_estimada_min": duracion_total or None,
            "senales": {
                "duracion_excede_120": excede_120,
                "revisar_confidencialidad": confidencial,
                "elementos_en_dias_distintos": rangos_amplios,
            },
            "razones_a_evaluar": [
                "Compatibilidad de participantes: revisa si los elementos "
                "requieren distintos asistentes (§2.7).",
                "Contexto relacionado: valora si los temas pueden abordarse "
                "en una misma sesion sin degradar la calidad de la decision.",
                "Urgencias divergentes: revisa si algun elemento tiene "
                "fecha limite que obligue a resolverlo antes que el resto.",
                "Preparacion incompatible: comprueba si algun asunto exige "
                "material o decisiones previas que otro no.",
                (
                    "Confidencialidad: se han detectado palabras clave "
                    "sensibles - revisa antes de proponer consolidar."
                ) if confidencial else (
                    "Confidencialidad: sin senales evidentes, pero valora "
                    "si algun tema exige privacidad especifica."
                ),
            ],
        })

    # Ordenar grupos por numero de elementos descendente (mas relevante primero)
    grupos_finales.sort(key=lambda g: g["n_elementos"], reverse=True)

    print(f"TOOL[{user_id}]: proponer_consolidacion detecto "
          f"{len(grupos_finales)} grupo(s) con >=2 elementos")

    if not grupos_finales:
        return {
            "ok": True,
            "total_grupos": 0,
            "ventana_dias": ventana_dias,
            "grupos": [],
            "mensaje": (
                f"No hay grupos con >=2 elementos compartiendo interlocutor "
                f"en los proximos {ventana_dias} dias (revisando eventos "
                f"futuros, tareas activas con requires_conversation, y buffer "
                f"pendiente). No procede proponer consolidacion."
            ),
        }

    return {
        "ok": True,
        "total_grupos": len(grupos_finales),
        "ventana_dias": ventana_dias,
        "grupos": grupos_finales,
        "mensaje": (
            f"{len(grupos_finales)} grupo(s) con >=2 elementos por "
            f"interlocutor. Antes de proponer consolidar al usuario, "
            f"aplica el juicio §2.7 sobre razones_a_evaluar y §2.8 "
            f"(no consolidar si el asunto se resuelve mejor asincrono)."
        ),
    }


async def crear_reunion_ejecutiva(
    user_id: str,
    interlocutor: str,
    temas: list[str],
    duracion_min: int,
    fecha_inicio: datetime | str,
    ids_relacionados: list[str] | None = None,
    resultado_esperado: str = "",
    prioridad: str | None = None,
) -> dict:
    """Prepara la creacion de UN evento consolidado que agrupa varios
    temas con el mismo interlocutor principal (PHASE 1 §2.6, §2.8;
    PHASE 2 §4; PHASE 6 Doc 3 §11).

    NO crea nada aun en Bitrix. Se anade al buffer de operaciones
    pendientes del usuario, igual que crear_evento, y se ejecuta cuando
    el CEO confirme con confirmar_operaciones_pendientes. Esto mantiene
    el patron Analyse -> Propose -> Confirm -> Execute.

    IMPORTANTE (§2.8):
    - NO borra ni modifica las tareas o eventos originales que motivaron
      la reunion. Se conservan tal cual.
    - Los ids_relacionados, si vienen, solo se citan en la descripcion
      generada como trazabilidad. La consolidacion es un agregado, no un
      reemplazo. El post-meeting processing (que asuntos cerrar, cuales
      renovar) queda para Sprint 4, no lo tocamos aqui.

    Args:
        interlocutor: persona principal de la reunion. Se guarda en
            'involucrado' del Evento.
        temas: lista de puntos de agenda, uno por asunto a tratar. Cada
            elemento es un titulo corto (una frase). La tool los numera
            y los formatea en la descripcion del evento como agenda
            estructurada.
        duracion_min: duracion total estimada de la reunion.
        fecha_inicio: cuando arranca. ISO 8601 o datetime. Debe venir de
            un hueco real (usa consultar_huecos_libres antes).
        ids_relacionados: opcional. Lista de ids (evento o tarea) que
            esta reunion pretende consolidar. Se incluyen en la descripcion
            para trazabilidad. NO se tocan; solo se referencian.
        resultado_esperado: opcional. Frase breve con el criterio de
            exito de la reunion. Se incluye en la descripcion.
        prioridad: opcional 'alta'|'media'|'baja'. Si no se pasa, se
            calcula automaticamente (categoria empresa + involucrado =>
            al menos media, alta si hay fecha_limite cercana).
    """
    print(f"TOOL[{user_id}]: crear_reunion_ejecutiva interlocutor={interlocutor!r} "
          f"n_temas={len(temas) if temas else 0} duracion={duracion_min}min")

    # Validaciones basicas antes de tocar buffer
    if not interlocutor or not interlocutor.strip():
        return {"ok": False, "mensaje": "Falta el interlocutor de la reunion."}
    if not temas or not isinstance(temas, list):
        return {"ok": False, "mensaje": (
            "Falta 'temas'. Pasa una lista con al menos un asunto a tratar."
        )}
    temas_limpios = [t.strip() for t in temas if isinstance(t, str) and t.strip()]
    if not temas_limpios:
        return {"ok": False, "mensaje": "La lista de temas esta vacia tras limpiar."}
    if duracion_min is None or duracion_min <= 0:
        return {"ok": False, "mensaje": "duracion_min debe ser un entero positivo."}

    # Coercion de fecha_inicio
    if isinstance(fecha_inicio, str):
        try:
            fecha_inicio = datetime.fromisoformat(fecha_inicio)
        except ValueError:
            return {"ok": False, "mensaje": f"fecha_inicio invalida: '{fecha_inicio}'."}

    # Construir nombre del evento
    if len(temas_limpios) == 1:
        nombre = f"Reunion con {interlocutor.strip()}: {temas_limpios[0]}"
    else:
        primer_tema = temas_limpios[0]
        restantes = len(temas_limpios) - 1
        nombre = (
            f"Reunion con {interlocutor.strip()}: {primer_tema} "
            f"+ {restantes} asunto{'s' if restantes != 1 else ''} mas"
        )
    # Cap defensivo de longitud (Bitrix truncara igual, pero por higiene)
    if len(nombre) > 200:
        nombre = nombre[:197] + "..."

    # Construir descripcion con agenda estructurada
    lineas: list[str] = []
    lineas.append(f"Reunion ejecutiva con {interlocutor.strip()}.")
    lineas.append("")
    if resultado_esperado.strip():
        lineas.append(f"Resultado esperado: {resultado_esperado.strip()}")
        lineas.append("")
    lineas.append("Agenda:")
    for i, tema in enumerate(temas_limpios, start=1):
        lineas.append(f"  {i}. {tema}")
    if ids_relacionados:
        ids_str = [str(i).strip() for i in ids_relacionados if str(i).strip()]
        if ids_str:
            lineas.append("")
            lineas.append(
                f"Consolida los siguientes elementos (no se modifican, "
                f"solo se agrupan aqui para trabajarlos juntos): "
                f"{', '.join(ids_str)}"
            )
    descripcion = "\n".join(lineas)

    # Delegar en crear_evento para reutilizar toda la maquinaria: dedup
    # por (nombre, fecha_inicio), calculo automatico de prioridad,
    # validacion del modelo Evento, insercion al buffer, retorno
    # homogeneo. Categoria fija empresa: una reunion ejecutiva por
    # definicion es empresa.
    resultado = await crear_evento(
        user_id=user_id,
        nombre=nombre,
        duracion_min=int(duracion_min),
        fecha_inicio=fecha_inicio,
        categoria="empresa",
        involucrado=interlocutor.strip(),
        descripcion=descripcion,
        tipo_actividad="reunion",
        prioridad=prioridad,
    )

    # Enriquecer el retorno con contexto propio para que el LLM tenga
    # a mano lo que acaba de proponer, sin re-consultar el buffer.
    if not resultado.get("ok"):
        return resultado

    resultado["reunion_ejecutiva"] = True
    resultado["interlocutor"] = interlocutor.strip()
    resultado["n_temas"] = len(temas_limpios)
    resultado["ids_relacionados"] = list(ids_relacionados) if ids_relacionados else []
    resultado["mensaje"] = (
        f"Reunion ejecutiva con {interlocutor.strip()} preparada "
        f"({len(temas_limpios)} tema(s), {duracion_min} min). "
        f"Los elementos originales NO se han tocado. "
        f"Total pendientes: {resultado.get('operaciones_pendientes_total', '?')}."
    )
    return resultado

# ============================================================================
# SPRINT 4 — Delegation Model + Waiting Management (PHASE 1 §7)
# ============================================================================
# Las 4 tools de este bloque son THIN WRAPPERS sobre actualizar_estado_tarea
# + logica derivada. No reinventan la rueda: fuerzan los campos obligatorios
# de §7.2, aplican el Delegation Decision Test §7.3 y consumen datos ya
# calculados. La delegacion real ocurre en actualizar_estado_tarea.


async def delegar_tarea(
    user_id: str,
    id: int,
    owner: str,
    review_date: datetime,
    expected_result: str,
    escalation_condition: str,
    preparation_required: str | None = None,
    next_action_if_missed: str | None = None,
    deadline: datetime | None = None,
    next_action: str | None = None,
) -> dict:
    """Delegar una tarea a otro Owner con los campos obligatorios de
    delegacion (§7.2). Wrapper sobre actualizar_estado_tarea que
    fuerza los 4 campos criticos: owner, review_date, expected_result,
    escalation_condition.

    Si falta cualquiera de los 4, la tool devuelve error legible ANTES
    de tocar Bitrix. El agente debe preguntar al usuario y volver a
    llamar con los datos completos.

    Sin esta tool: el agente puede llamar a actualizar_estado_tarea con
    nuevo_estado='Delegated' y omitir review_date, quedando la tarea
    delegada sin fecha de control — invisible en el Brief hasta que sea
    tarde. §7.2 lo prohibe explicitamente.
    """
    print(f"TOOL[{user_id}]: delegar_tarea id={id} owner={owner!r} "
          f"review={review_date} escala={escalation_condition[:30] if escalation_condition else None!r}")

    # Validacion antes de tocar Bitrix. Errores legibles para que el
    # LLM sepa exactamente que preguntar al usuario.
    faltas: list[str] = []
    if not owner or not owner.strip():
        faltas.append("owner (a quien se delega)")
    if not review_date:
        faltas.append("review_date (cuando revisas el avance)")
    if not expected_result or not expected_result.strip():
        faltas.append("expected_result (que constituye 'hecho')")
    if not escalation_condition or not escalation_condition.strip():
        faltas.append("escalation_condition (que dispara alarma)")

    if faltas:
        return {
            "ok": False,
            "mensaje": (
                f"Para delegar la tarea {id} falta: {', '.join(faltas)}. "
                f"PHASE 1 §7.2 los exige. Preguntale al usuario antes de "
                f"volver a llamar."
            ),
            "campos_faltantes": faltas,
        }

    # Delegar: transicion -> Delegated + campos completos.
    return await actualizar_estado_tarea(
        user_id=user_id,
        id=id,
        nuevo_estado="Delegated",
        owner=owner,
        review_date=review_date,
        expected_result=expected_result,
        escalation_condition=escalation_condition,
        preparation_required=preparation_required,
        next_action_if_missed=next_action_if_missed,
        deadline=deadline,
        next_action=next_action,
    )


async def marcar_waiting(
    user_id: str,
    id: int,
    waiting_for: str,
    next_follow_up: datetime,
    next_action_if_missed: str | None = None,
) -> dict:
    """Marcar una tarea como Waiting (esperando respuesta externa).
    Wrapper que fuerza los 2 campos criticos: descripcion de que se
    espera y cuando hacer el proximo follow-up.

    'waiting_for' se guarda en expected_result (natural para el brief:
    "espero X de Y"). 'next_follow_up' se guarda en review_date
    (fecha en que el sistema volvera a levantar el asunto en el brief
    si no se ha resuelto).

    Sin esto: el LLM pone nuevo_estado='Waiting' sin next_follow_up y
    la tarea queda en un limbo del que el brief no la saca nunca. §12
    Missing Information lo detectaria, pero preferimos prevenir.
    """
    print(f"TOOL[{user_id}]: marcar_waiting id={id} waiting_for={waiting_for[:40]!r} "
          f"next_follow_up={next_follow_up}")

    faltas: list[str] = []
    if not waiting_for or not waiting_for.strip():
        faltas.append("waiting_for (que se espera y de quien)")
    if not next_follow_up:
        faltas.append("next_follow_up (cuando volver a mirar)")

    if faltas:
        return {
            "ok": False,
            "mensaje": (
                f"Para marcar {id} como Waiting falta: {', '.join(faltas)}. "
                f"Preguntale al usuario y vuelve a llamar."
            ),
            "campos_faltantes": faltas,
        }

    return await actualizar_estado_tarea(
        user_id=user_id,
        id=id,
        nuevo_estado="Waiting",
        expected_result=waiting_for.strip(),
        review_date=next_follow_up,
        next_action_if_missed=next_action_if_missed,
    )


async def evaluar_delegacion(user_id: str, tarea_id: int) -> dict:
    """Aplica el Delegation Decision Test §7.3 a una tarea existente y
    devuelve un diagnostico DETERMINISTA con nivel sugerido, razones y
    campos que faltan para poder delegarla.

    NO delega automaticamente. Solo devuelve el analisis para que el
    LLM lo cuente al usuario y este decida.

    Nivel sugerido segun §7.1:
      1 CEO Execution     - CEO ejecuta personalmente (competencias unicas)
      2 CEO Decision      - equipo prepara, CEO decide
      3 CEO Approval      - equipo ejecuta, CEO aprueba
      4 CEO Supervision   - delegado, CEO recibe KPI/desviaciones
      5 No CEO Involvement - Owner ejecuta y cierra

    Heuristica (deterministica, sin LLM):
      - alexander_role=Execution + task_type=Project -> Nivel 1
      - alexander_role=Decision -> Nivel 2
      - alexander_role=Approval -> Nivel 3
      - alexander_role=Supervision -> Nivel 4
      - alexander_role=No Involvement -> Nivel 5
      - Sin alexander_role: heuristica por otros campos
    """
    print(f"TOOL[{user_id}]: evaluar_delegacion id={tarea_id}")

    try:
        webhook, _ = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    from bitrix_tasks import obtener_tarea
    try:
        tarea = await obtener_tarea(webhook, tarea_id)
    except Exception as e:
        return {"ok": False,
                "mensaje": f"No pude leer la tarea {tarea_id}: {type(e).__name__}: {e}"}

    if not tarea:
        return {"ok": False, "mensaje": f"Tarea {tarea_id} no existe."}

    # --- Determinar nivel sugerido ---
    from models import RolAlexander, TipoTarea, EstadoEOS

    nivel_num: int
    nivel_txt: str
    razones: list[str] = []

    role = tarea.alexander_role
    if role == RolAlexander.EXECUTION:
        if tarea.task_type == TipoTarea.PROJECT:
            nivel_num, nivel_txt = 1, "CEO Execution"
            razones.append("alexander_role=Execution en un Project: requiere criterio propio del CEO.")
        else:
            nivel_num, nivel_txt = 1, "CEO Execution"
            razones.append("alexander_role=Execution: la tarea esta marcada como trabajo directo del CEO.")
    elif role == RolAlexander.DECISION:
        nivel_num, nivel_txt = 2, "CEO Decision"
        razones.append("alexander_role=Decision: el equipo puede preparar contexto; el CEO decide.")
    elif role == RolAlexander.APPROVAL:
        nivel_num, nivel_txt = 3, "CEO Approval"
        razones.append("alexander_role=Approval: el equipo ejecuta; el CEO aprueba el resultado.")
    elif role == RolAlexander.SUPERVISION:
        nivel_num, nivel_txt = 4, "CEO Supervision"
        razones.append("alexander_role=Supervision: trabajo delegado; el CEO recibe reportes.")
    elif role == RolAlexander.NO_INVOLVEMENT:
        nivel_num, nivel_txt = 5, "No CEO Involvement"
        razones.append("alexander_role=No Involvement: el Owner ejecuta y cierra autonomamente.")
    else:
        # Sin rol asignado — inferir por otros campos
        if tarea.requires_conversation and tarea.expected_decision:
            nivel_num, nivel_txt = 2, "CEO Decision"
            razones.append(
                "Sin alexander_role, pero requires_conversation=True + "
                "expected_decision presente sugiere Decision."
            )
        elif tarea.status_eos == EstadoEOS.DELEGATED:
            nivel_num, nivel_txt = 4, "CEO Supervision"
            razones.append(
                "Sin alexander_role, pero status=Delegated implica al menos supervision."
            )
        else:
            nivel_num, nivel_txt = 3, "CEO Approval"
            razones.append(
                "Sin alexander_role ni pistas fuertes: por defecto sugiero Approval "
                "(el equipo puede ejecutar y el CEO revisa)."
            )

    # --- Campos faltantes para poder delegar (§7.2) ---
    campos_faltantes: list[str] = []
    if nivel_num >= 3:  # A partir de Approval ya hay delegacion
        if not tarea.expected_result:
            campos_faltantes.append("expected_result")
        if not tarea.deadline:
            campos_faltantes.append("deadline")
        if nivel_num >= 4:  # Supervision o superior — necesita mas
            if not tarea.review_date:
                campos_faltantes.append("review_date")
            if not tarea.escalation_condition:
                campos_faltantes.append("escalation_condition")

    # --- Puede evitarse la conversacion? §7.4 ---
    puede_evitarse_conv = (
        nivel_num >= 3
        and tarea.expected_result is not None
        and tarea.deadline is not None
    )

    # --- Puede otro reemplazar al CEO en la conversacion? §7.4 ---
    alguien_puede_reemplazarlo = nivel_num >= 4

    return {
        "ok": True,
        "id": tarea_id,
        "title": tarea.title,
        "nivel_sugerido_num": nivel_num,
        "nivel_sugerido": nivel_txt,
        "razones": razones,
        "campos_faltantes_para_delegar": campos_faltantes,
        "puede_evitarse_conversacion": puede_evitarse_conv,
        "alguien_puede_reemplazarlo": alguien_puede_reemplazarlo,
        "mensaje": (
            f"Tarea {tarea_id} '{tarea.title[:40]}': sugerido {nivel_txt}. "
            + (f"Faltan campos: {', '.join(campos_faltantes)}. "
               if campos_faltantes else "Lista para delegar.")
        ),
    }


async def follow_up_waiting(user_id: str) -> dict:
    """Lista tareas en estado Waiting con review_date (next_follow_up)
    vencido. Devuelve items con dias_vencido, waiting_for y una
    next_action sugerida deterministica.

    El brief la usa para poblar la seccion "Waiting for Responses" con
    llamada a la accion concreta. El LLM la puede invocar tambien on-demand:
    "que estoy esperando?" -> la muestra ordenada por urgencia.
    """
    print(f"TOOL[{user_id}]: follow_up_waiting")

    try:
        webhook, bitrix_uid = _contexto_bitrix(user_id)
    except ValueError as e:
        return {"ok": False, "mensaje": str(e)}

    from bitrix_tasks import listar_tareas
    from models import EstadoEOS, TZ_LOCAL

    try:
        tareas = await listar_tareas(
            webhook, filtro={"RESPONSIBLE_ID": bitrix_uid},
        )
    except Exception as e:
        return {"ok": False,
                "mensaje": f"Error listando tareas: {type(e).__name__}: {e}"}

    ahora = datetime.now(TZ_LOCAL)
    vencidos: list[dict] = []
    proximos: list[dict] = []

    for t in tareas:
        if t.status_eos != EstadoEOS.WAITING:
            continue
        if not t.review_date:
            # Waiting sin next_follow_up: es una anomalia que §12
            # reporta como Missing Information. Aqui la incluimos con
            # marca especial para que el LLM la pueda mencionar.
            vencidos.append({
                "id": t.id,
                "title": t.title,
                "waiting_for": t.expected_result or "[NO DATA]",
                "dias_vencido": None,
                "sin_follow_up": True,
                "next_action_sugerida": (
                    f"Fijar next_follow_up y decidir a quien recordar."
                ),
                "primary_interlocutor": t.primary_interlocutor,
            })
            continue

        dias_diff = (t.review_date - ahora).days
        item = {
            "id": t.id,
            "title": t.title,
            "waiting_for": t.expected_result or "[NO DATA]",
            "next_follow_up": t.review_date.isoformat(),
            "dias_vencido": -dias_diff if dias_diff < 0 else 0,
            "dias_hasta": dias_diff if dias_diff > 0 else 0,
            "primary_interlocutor": t.primary_interlocutor,
            "sin_follow_up": False,
        }

        # Sugerir accion segun quien es el interlocutor
        interlocutor = t.primary_interlocutor
        if interlocutor:
            item["next_action_sugerida"] = f"Recordar a {interlocutor}"
        else:
            item["next_action_sugerida"] = "Enviar recordatorio (sin destinatario claro)"

        if dias_diff < 0:
            vencidos.append(item)
        elif dias_diff <= 2:
            proximos.append(item)

    vencidos.sort(key=lambda x: x.get("dias_vencido") or 0, reverse=True)
    proximos.sort(key=lambda x: x.get("dias_hasta") or 0)

    return {
        "ok": True,
        "n_vencidos": len(vencidos),
        "n_proximos": len(proximos),
        "vencidos": vencidos,
        "proximos": proximos,
        "mensaje": (
            f"{len(vencidos)} Waiting vencido(s), "
            f"{len(proximos)} vence(n) en <=2 dias."
        ),
    }
