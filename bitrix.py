"""Cliente de Bitrix24 REST. Todas las funciones son async y reciben
el contexto del usuario (webhook + bitrix_user_id) como argumentos
explicitos. No hay estado global de usuario: cada llamada declara
contra que Bitrix opera.

Motivo del diseno: en produccion tenemos dos usuarios (Carles y
Alexander) con calendarios en tenants Bitrix distintos. Antes teniamos
constantes globales USER_ID y WEBHOOK leidas del .env al importar el
modulo, lo que hacia imposible atender a dos usuarios en el mismo
proceso.

Ahora cada tool que llama a Bitrix propaga (webhook, bitrix_user_id)
desde el contexto de usuario resuelto en la capa de entrada (bot.py
para Telegram, server.py para web).
"""
import httpx
import asyncio
from datetime import datetime, timedelta

from models import Evento, Prioridad


class BitrixError(Exception):
    """Errores de negocio o red de Bitrix."""
    pass


# Mapeo de nuestra prioridad -> valor que espera Bitrix.
_MAPEO_IMPORTANCIA = {
    Prioridad.ALTA: "high",
    Prioridad.MEDIA: "normal",
    Prioridad.BAJA: "low",
}


# --- Cache de secciones por usuario ---
# Bitrix devuelve las secciones (calendarios) disponibles al llamar a
# calendar.section.get. Es una lista de IDs que se pasa como parametro
# 'section' en calendar.event.get para filtrar (sin ella, algunos tipos
# de eventos como los dias libres con DATE_FROM == DATE_TO no aparecen).
# No cambia en runtime, asi que la cacheamos por bitrix_user_id tras la
# primera llamada. Dos usuarios = dos entradas en el dict.
_SECCIONES_CACHE: dict[int, list[int]] = {}


async def solicitud(webhook: str, metodo: str, params: dict | None = None, es_v3: bool = False):
    """POST paginado al webhook de Bitrix.

    - Si el 'result' es lista, itera con start/next hasta agotar paginas.
    - Si el 'result' es dict/scalar, lo devuelve tal cual.

    Lanza BitrixError en cualquier fallo (red, HTTP, JSON, error de negocio).

    Args:
        webhook: URL del webhook con user_id embebido, tal cual la da
            Bitrix (ej: https://xxx.bitrix24.es/rest/17/abc123/). Sin
            barra final se acepta igual (concatena bien con el metodo).
        metodo: nombre del metodo REST (ej: 'calendar.event.get').
        params: parametros del body. None equivale a dict vacio.
        es_v3: bandera para el endpoint REST v3 (no usada hoy, se
            mantiene por compat).
    """
    if not webhook:
        raise BitrixError("Webhook Bitrix vacio. Usuario mal configurado.")

    base = webhook.replace("/rest/", "/rest/api/") if es_v3 else webhook
    if params is None:
        params = {}

    start = 0
    resultado = []

    async with httpx.AsyncClient(timeout=120) as client:
        while True:
            params_pagina = {**params, "start": start}
            try:
                query = await client.post(f"{base}{metodo}", json=params_pagina)
            except httpx.RequestError as e:
                raise BitrixError(f"Error de red al llamar {metodo}: {e}") from e

            if not query.is_success:
                raise BitrixError(f"Error {query.status_code} en {metodo}: {query.text}")

            try:
                result = query.json()
            except ValueError:
                raise BitrixError(f"Respuesta no valida en {metodo}: {query.text}")

            if "error" in result:
                raise BitrixError(f"Bitrix rechazo {metodo}, args={params}, error={result['error']}")

            pagina = result["result"]
            if not isinstance(pagina, list):
                return pagina

            resultado.extend(pagina)
            if "next" not in result:
                break
            start = result["next"]

    return resultado


async def crear_evento_bitrix(webhook: str, bitrix_user_id: int, evento: Evento) -> int:
    """Crea un evento en el calendario personal del usuario Bitrix.

    Devuelve el ID numerico del evento creado.
    Lanza BitrixError si algo falla.

    Args:
        webhook, bitrix_user_id: contexto del usuario propietario del
            calendario donde se crea el evento.
        evento: objeto Evento con toda la informacion (nombre, fechas,
            duracion, prioridad, involucrado, descripcion...).
    """
    fecha_fin = evento.fecha_inicio + timedelta(minutes=evento.duracion_min)

    # Componemos la descripcion incluyendo el involucrado (Bitrix quiere IDs
    # numericos en attendees; nosotros manejamos texto libre).
    descripcion = evento.descripcion or ""
    if evento.involucrado:
        prefijo = f"Involucrado: {evento.involucrado}"
        descripcion = f"{prefijo}\n\n{descripcion}" if descripcion else prefijo

    params = {
        "type": "user",
        "ownerId": bitrix_user_id,
        "name": evento.nombre,
        "from": evento.fecha_inicio.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": fecha_fin.strftime("%Y-%m-%dT%H:%M:%S"),
        "auto_detect_section": "Y",
        "description": descripcion,
        "importance": _MAPEO_IMPORTANCIA[evento.prioridad],
        "timezone_from": "Europe/Madrid",
        "timezone_to": "Europe/Madrid",
    }

    return await solicitud(webhook, "calendar.event.add", params)


async def obtener_secciones_user(webhook: str, bitrix_user_id: int) -> list[int]:
    """Devuelve las IDs de todas las secciones del calendario del user.

    Se cachea a nivel proceso por bitrix_user_id: la primera llamada
    consulta a Bitrix, el resto son gratis. Si el usuario conecta un
    nuevo calendario en Bitrix mientras el bot corre, hay que reiniciar
    el proceso.

    Motivo: calendar.event.get sin parametro 'section' NO devuelve
    algunos tipos de eventos (comprobado: dias libres con DATE_FROM
    == DATE_TO). Pasando la lista completa se soluciona.

    Si Bitrix falla al listar secciones, se hace fallback a lista vacia
    (comportamiento degradado, pero el bot sigue funcionando).
    """
    if bitrix_user_id in _SECCIONES_CACHE:
        return _SECCIONES_CACHE[bitrix_user_id]
    try:
        secciones = await solicitud(
            webhook,
            "calendar.section.get",
            {"type": "user", "ownerId": bitrix_user_id},
        )
    except Exception as e:
        print(f"BITRIX: no pude listar secciones para user {bitrix_user_id} ({e}), sigo sin filtrar")
        return []
    if not isinstance(secciones, list):
        return []
    _SECCIONES_CACHE[bitrix_user_id] = [int(s["ID"]) for s in secciones if "ID" in s]
    print(f"BITRIX: secciones cacheadas para user {bitrix_user_id}: {_SECCIONES_CACHE[bitrix_user_id]}")
    return _SECCIONES_CACHE[bitrix_user_id]


async def consultar_eventos_bitrix(
    webhook: str,
    bitrix_user_id: int,
    fecha_inicio: datetime | str | None = None,
    fecha_fin: datetime | str | None = None,
) -> list[dict]:
    """Consulta eventos del calendario personal del usuario en Bitrix.

    Devuelve la lista tal cual la da la API: dicts con muchos campos y
    nombres en MAYUSCULAS. La normalizacion/filtrado adicional se hace
    en la capa de tools.

    Args:
        webhook, bitrix_user_id: contexto del usuario.
        fecha_inicio: fecha desde la que buscar. Acepta datetime o string
            ISO 8601. Si es None, Bitrix usa por defecto un mes antes.
        fecha_fin: fecha hasta la que buscar. Mismo criterio. Si es None,
            Bitrix usa por defecto tres meses despues.
    """
    def _a_datetime(valor):
        if valor is None or isinstance(valor, datetime):
            return valor
        if isinstance(valor, str):
            try:
                return datetime.fromisoformat(valor)
            except ValueError as e:
                raise BitrixError(f"Fecha invalida '{valor}': {e}") from e
        raise BitrixError(f"Tipo de fecha no soportado: {type(valor).__name__}")

    fecha_inicio = _a_datetime(fecha_inicio)
    fecha_fin = _a_datetime(fecha_fin)

    secciones = await obtener_secciones_user(webhook, bitrix_user_id)

    # Pedimos a Bitrix con 1 dia de margen por cada lado y filtramos en
    # Python. Motivo: Bitrix descarta eventos de duracion cero
    # (DATE_FROM == DATE_TO) cuyos timestamps caen justo en el borde
    # del rango pedido — usa comparacion estricta, no inclusiva.
    params = {"type": "user", "ownerId": bitrix_user_id}
    if secciones:
        params["section"] = secciones
    if fecha_inicio is not None:
        params["from"] = (fecha_inicio - timedelta(days=1)).strftime("%Y-%m-%d")
    if fecha_fin is not None:
        params["to"] = (fecha_fin + timedelta(days=1)).strftime("%Y-%m-%d")

    eventos = await solicitud(webhook, "calendar.event.get", params)

    # Filtrado post-Bitrix por fecha de calendario. Robusto frente a
    # eventos de duracion cero: usamos .date() para que un all-day en el
    # borde caiga dentro.
    if fecha_inicio is not None or fecha_fin is not None:
        filtrados = []
        for e in eventos:
            try:
                date_from = _parse_bitrix_date(e["DATE_FROM"])
                date_to = _parse_bitrix_date(e["DATE_TO"])
            except (KeyError, ValueError):
                continue
            if fecha_inicio is not None and date_to.date() < fecha_inicio.date():
                continue
            if fecha_fin is not None and date_from.date() > fecha_fin.date():
                continue
            filtrados.append(e)
        return filtrados

    return eventos


def _parse_bitrix_date(s: str) -> datetime:
    """Bitrix devuelve fechas en tres formatos:
    - ISO 8601 completo ('2026-08-10T09:00:00+02:00')
    - 'dd.mm.YYYY HH:MM:SS' formato europeo con hora
    - 'dd.mm.YYYY' formato europeo sin hora (eventos all-day)

    Los all-day quedan como datetime a 00:00:00 del dia. El caller es
    responsable de expandir DATE_TO a 23:59:59 si necesita representar
    "todo el dia". El filtrado por .date() en consultar_eventos_bitrix
    y la heuristica _es_evento_todo_el_dia() en huecos.py ya lo manejan.
    """
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%d.%m.%Y %H:%M:%S")
    except ValueError:
        pass
    return datetime.strptime(s, "%d.%m.%Y")


async def consultar_ocupacion_bitrix(
    webhook: str,
    bitrix_user_id: int,
    fecha_inicio: datetime | str | None = None,
    fecha_fin: datetime | str | None = None,
) -> list[dict]:
    """Devuelve TODO lo que hace al usuario busy: eventos + absences.

    Combina en paralelo:
      - calendar.event.get         -> eventos con detalle completo (DESCRIPTION,
                                       IMPORTANCE, ATTENDEES...)
      - calendar.accessibility.get -> todo lo que ocupa al usuario, incluidas
                                       absences que event.get no ve.

    Deduplica por ID. Los que vienen de event.get tienen prioridad porque
    llevan DESCRIPTION y demas campos ricos; los que solo aparecen en
    accessibility (tipicamente absences) se anaden tal cual.

    Si accessibility falla, cae gracefully sobre el resultado de event.get:
    peor caso, comportamiento anterior sin absences.

    Args:
        webhook, bitrix_user_id: contexto del usuario.
        fecha_inicio, fecha_fin: rango a inspeccionar.
    """
    def _a_datetime(valor):
        if valor is None or isinstance(valor, datetime):
            return valor
        if isinstance(valor, str):
            try:
                return datetime.fromisoformat(valor)
            except ValueError as e:
                raise BitrixError(f"Fecha invalida '{valor}': {e}") from e
        raise BitrixError(f"Tipo de fecha no soportado: {type(valor).__name__}")

    fecha_inicio = _a_datetime(fecha_inicio)
    fecha_fin = _a_datetime(fecha_fin)

    # Params para calendar.event.get (from/to opcionales; Bitrix usa defaults)
    secciones = await obtener_secciones_user(webhook, bitrix_user_id)

    params_event = {"type": "user", "ownerId": bitrix_user_id}
    if secciones:
        params_event["section"] = secciones
    if fecha_inicio is not None:
        params_event["from"] = (fecha_inicio - timedelta(days=1)).strftime("%Y-%m-%d")
    if fecha_fin is not None:
        params_event["to"] = (fecha_fin + timedelta(days=1)).strftime("%Y-%m-%d")

    # Params para calendar.accessibility.get (from/to obligatorios).
    # Si no vienen, usamos el rango por defecto de Bitrix (~1 mes atras, ~3 adelante).
    ahora = datetime.now()
    fi_acc = fecha_inicio if fecha_inicio is not None else ahora - timedelta(days=30)
    ff_acc = fecha_fin if fecha_fin is not None else ahora + timedelta(days=90)
    params_acc = {
        "users": [bitrix_user_id],
        "from": fi_acc.strftime("%Y-%m-%d"),
        "to": ff_acc.strftime("%Y-%m-%d"),
    }

    # Llamadas en paralelo. return_exceptions=True nos deja hacer fallback.
    eventos_res, ocupacion_res = await asyncio.gather(
        solicitud(webhook, "calendar.event.get", params_event),
        solicitud(webhook, "calendar.accessibility.get", params_acc),
        return_exceptions=True,
    )
    # event.get es el principal: si falla, propagamos.
    if isinstance(eventos_res, Exception):
        raise eventos_res

    eventos: list[dict] = eventos_res

    # accessibility es un extra: si falla, avisamos y devolvemos solo event.get.
    if isinstance(ocupacion_res, Exception):
        print(f"BITRIX: aviso, accessibility fallo para user {bitrix_user_id}, sigo sin absences: {ocupacion_res}")
        return eventos

    # accessibility.get devuelve {"user_id_str": [busy_items]}. Extraemos la lista.
    ocupacion_dict = ocupacion_res if isinstance(ocupacion_res, dict) else {}
    ocupacion_lista = ocupacion_dict.get(str(bitrix_user_id), [])

    # Merge por ID, priorizando los eventos ricos de event.get.
    ids_existentes = {str(e.get("ID")) for e in eventos}
    for extra in ocupacion_lista:
        if str(extra.get("ID")) not in ids_existentes:
            eventos.append(extra)

    return eventos


async def modificar_evento_bitrix(
    webhook: str,
    bitrix_user_id: int,
    id: int,
    evento_actual: dict,
    cambios: dict,
) -> None:
    """Modifica un evento existente en Bitrix. Merge de campos: los que
    estan en `cambios` reemplazan, el resto se mantiene desde
    `evento_actual` (el snapshot obtenido en preparacion).

    Args:
        webhook, bitrix_user_id: contexto del usuario.
        id: id Bitrix del evento.
        evento_actual: dict tal cual lo devuelve calendar.event.get
            (mayusculas: NAME, DATE_FROM, DATE_TO, DESCRIPTION, IMPORTANCE).
        cambios: dict con nuestras keys de dominio (nombre, fecha_inicio,
            duracion_min, descripcion, prioridad). Solo las que cambian.
    """
    # Nombre
    nombre = cambios.get("nombre", evento_actual.get("NAME"))

    # Fechas: recalculamos siempre from y to a partir de lo disponible
    fecha_inicio_original = _parse_bitrix_date(evento_actual["DATE_FROM"])
    fecha_fin_original = _parse_bitrix_date(evento_actual["DATE_TO"])
    duracion_original_min = int((fecha_fin_original - fecha_inicio_original).total_seconds() / 60)

    fecha_inicio = cambios.get("fecha_inicio", fecha_inicio_original)
    duracion_min = cambios.get("duracion_min", duracion_original_min)
    fecha_fin = fecha_inicio + timedelta(minutes=duracion_min)

    # Descripcion e importancia
    descripcion = cambios.get("descripcion", evento_actual.get("DESCRIPTION", ""))
    if "prioridad" in cambios:
        importance = _MAPEO_IMPORTANCIA[Prioridad(cambios["prioridad"])]
    else:
        importance = evento_actual.get("IMPORTANCE", "normal")

    params = {
        "id": id,
        "type": "user",
        "ownerId": bitrix_user_id,
        "name": nombre,
        "from": fecha_inicio.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": fecha_fin.strftime("%Y-%m-%dT%H:%M:%S"),
        "description": descripcion,
        "importance": importance,
        "timezone_from": "Europe/Madrid",
        "timezone_to": "Europe/Madrid",
    }

    await solicitud(webhook, "calendar.event.update", params)


async def eliminar_evento_bitrix(webhook: str, bitrix_user_id: int, id: int) -> None:
    """Elimina un evento en Bitrix por su id.

    Args:
        webhook, bitrix_user_id: contexto del usuario propietario.
        id: id Bitrix del evento a borrar.
    """
    await solicitud(webhook, "calendar.event.delete", {"id": id})