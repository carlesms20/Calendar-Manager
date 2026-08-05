import httpx
from os import getenv
from dotenv import load_dotenv
from datetime import timedelta
from models import Evento, Prioridad
from datetime import datetime, timedelta
import asyncio

load_dotenv()
WEBHOOK = getenv("WEBHOOK_BITRIX")
USER_ID = int(getenv("BITRIX_USER_ID", "0"))
_SECCIONES_CACHE: list[int] | None = None

class BitrixError(Exception):
    """Errores de negocio o red de Bitrix."""
    pass

async def solicitud(metodo: str, params: dict | None = None, es_v3: bool = False):
    """POST paginado al webhook de Bitrix.

    - Si el 'result' es lista, itera con start/next hasta agotar páginas.
    - Si el 'result' es dict/scalar, lo devuelve tal cual.

    Lanza BitrixError en cualquier fallo (red, HTTP, JSON, error de negocio).
    """
    base = WEBHOOK.replace("/rest/", "/rest/api/") if es_v3 else WEBHOOK
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
                raise BitrixError(f"Respuesta no válida en {metodo}: {query.text}")

            if "error" in result:
                raise BitrixError(f"Bitrix rechazó {metodo}, args={params}, error={result['error']}")

            pagina = result["result"]
            if not isinstance(pagina, list):
                return pagina

            resultado.extend(pagina)
            if "next" not in result:
                break
            start = result["next"]

    return resultado

# Mapeo de nuestra prioridad → valor que espera Bitrix.
_MAPEO_IMPORTANCIA = {
    Prioridad.ALTA: "high",
    Prioridad.MEDIA: "normal",
    Prioridad.BAJA: "low",
}


async def crear_evento_bitrix(evento: Evento) -> int:
    """Crea un evento en el calendario personal del usuario Bitrix.

    Devuelve el ID numérico del evento creado.
    Lanza BitrixError si algo falla.
    """
    fecha_fin = evento.fecha_inicio + timedelta(minutes=evento.duracion_min)

    # Componemos la descripción incluyendo el involucrado (Bitrix quiere IDs
    # numéricos en attendees; nosotros manejamos texto libre).
    descripcion = evento.descripcion or ""
    if evento.involucrado:
        prefijo = f"Involucrado: {evento.involucrado}"
        descripcion = f"{prefijo}\n\n{descripcion}" if descripcion else prefijo

    params = {
        "type": "user",
        "ownerId": USER_ID,
        "name": evento.nombre,
        "from": evento.fecha_inicio.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": fecha_fin.strftime("%Y-%m-%dT%H:%M:%S"),
        "auto_detect_section": "Y",
        "description": descripcion,
        "importance": _MAPEO_IMPORTANCIA[evento.prioridad],
        "timezone_from": "Europe/Madrid",
        "timezone_to": "Europe/Madrid",
    }

    return await solicitud("calendar.event.add", params)

async def obtener_secciones_user() -> list[int]:
    """Devuelve las IDs de todas las secciones del calendario del user.

    Se cachea a nivel proceso: la primera llamada consulta a Bitrix,
    el resto son gratis. Si Alexander conecta un nuevo calendario en
    Bitrix mientras el bot corre, hay que reiniciar el proceso.

    Motivo: calendar.event.get sin parámetro 'section' NO devuelve
    algunos tipos de eventos (comprobado: días libres con DATE_FROM
    == DATE_TO). Pasando la lista completa se soluciona.
    """
    global _SECCIONES_CACHE
    if _SECCIONES_CACHE is not None:
        return _SECCIONES_CACHE
    try:
        secciones = await solicitud("calendar.section.get", {"type": "user", "ownerId": USER_ID})
    except Exception as e:
        print(f"BITRIX: no pude listar secciones ({e}), sigo sin filtrar")
        return []
    if not isinstance(secciones, list):
        return []
    _SECCIONES_CACHE = [int(s["ID"]) for s in secciones if "ID" in s]
    print(f"BITRIX: secciones cacheadas para user {USER_ID}: {_SECCIONES_CACHE}")
    return _SECCIONES_CACHE

async def consultar_eventos_bitrix(
    fecha_inicio: datetime | str | None = None,
    fecha_fin: datetime | str | None = None,
) -> list[dict]:
    """Consulta eventos del calendario personal del usuario en Bitrix.

    Devuelve la lista tal cual la da la API: dicts con muchos campos y
    nombres en MAYÚSCULAS. La normalización/filtrado adicional se hace
    en la capa de tools.

    Args:
        fecha_inicio: fecha desde la que buscar. Acepta datetime o string
            ISO 8601 (Gemini puede pasar cualquiera de los dos). Si es
            None, Bitrix usa por defecto un mes antes de hoy.
        fecha_fin: fecha hasta la que buscar. Mismo criterio. Si es None,
            Bitrix usa por defecto tres meses después de hoy.
    """
    def _a_datetime(valor):
        if valor is None or isinstance(valor, datetime):
            return valor
        if isinstance(valor, str):
            try:
                return datetime.fromisoformat(valor)
            except ValueError as e:
                raise BitrixError(f"Fecha inválida '{valor}': {e}") from e
        raise BitrixError(f"Tipo de fecha no soportado: {type(valor).__name__}")

    fecha_inicio = _a_datetime(fecha_inicio)
    fecha_fin = _a_datetime(fecha_fin)

    secciones = await obtener_secciones_user()

    secciones = await obtener_secciones_user()

    # Pedimos a Bitrix con 1 día de margen por cada lado y filtramos en
    # Python. Motivo: Bitrix descarta eventos de duración cero
    # (DATE_FROM == DATE_TO) cuyos timestamps caen justo en el borde
    # del rango pedido — usa comparación estricta, no inclusiva.
    params = {"type": "user", "ownerId": USER_ID}
    if secciones:
        params["section"] = secciones
    if fecha_inicio is not None:
        params["from"] = (fecha_inicio - timedelta(days=1)).strftime("%Y-%m-%d")
    if fecha_fin is not None:
        params["to"] = (fecha_fin + timedelta(days=1)).strftime("%Y-%m-%d")

    eventos = await solicitud("calendar.event.get", params)

    # Filtrado post-Bitrix por fecha de calendario. Robusto frente a
    # eventos de duración cero: usamos .date() para que un all-day en el
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
    """Bitrix devuelve fechas en 'dd.mm.YYYY HH:MM:SS' (formato europeo).
    A veces también en ISO 8601 según el endpoint. Aceptamos ambos."""
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.strptime(s, "%d.%m.%Y %H:%M:%S")

async def consultar_ocupacion_bitrix(
    fecha_inicio: datetime | str | None = None,
    fecha_fin: datetime | str | None = None,
) -> list[dict]:
    """Devuelve TODO lo que hace al usuario busy: eventos + absences.

    Combina en paralelo:
      - calendar.event.get       → eventos con detalle completo (DESCRIPTION,
                                   IMPORTANCE, ATTENDEES...)
      - calendar.accessibility.get → todo lo que ocupa al usuario, incluidas
                                     absences que event.get no ve.

    Deduplica por ID. Los que vienen de event.get tienen prioridad porque
    llevan DESCRIPTION y demás campos ricos; los que solo aparecen en
    accessibility (típicamente absences) se añaden tal cual.

    Si accessibility falla, cae gracefully sobre el resultado de event.get:
    peor caso, comportamiento anterior sin absences.
    """
    def _a_datetime(valor):
        if valor is None or isinstance(valor, datetime):
            return valor
        if isinstance(valor, str):
            try:
                return datetime.fromisoformat(valor)
            except ValueError as e:
                raise BitrixError(f"Fecha inválida '{valor}': {e}") from e
        raise BitrixError(f"Tipo de fecha no soportado: {type(valor).__name__}")

    fecha_inicio = _a_datetime(fecha_inicio)
    fecha_fin = _a_datetime(fecha_fin)

    # Params para calendar.event.get (from/to opcionales; Bitrix usa defaults)
    secciones = await obtener_secciones_user()

    params_event = {"type": "user", "ownerId": USER_ID}
    if secciones:
        params_event["section"] = secciones
    if fecha_inicio is not None:
        params_event["from"] = (fecha_inicio - timedelta(days=1)).strftime("%Y-%m-%d")
    if fecha_fin is not None:
        params_event["to"] = (fecha_fin + timedelta(days=1)).strftime("%Y-%m-%d")

    # Params para calendar.accessibility.get (from/to obligatorios).
    # Si no vienen, usamos el rango por defecto de Bitrix (~1 mes atrás, ~3 adelante).
    ahora = datetime.now()
    fi_acc = fecha_inicio if fecha_inicio is not None else ahora - timedelta(days=30)
    ff_acc = fecha_fin if fecha_fin is not None else ahora + timedelta(days=90)
    params_acc = {
        "users": [USER_ID],
        "from": fi_acc.strftime("%Y-%m-%d"),
        "to": ff_acc.strftime("%Y-%m-%d"),
    }

    # Llamadas en paralelo. return_exceptions=True nos deja hacer fallback.
    eventos_res, ocupacion_res = await asyncio.gather(
        solicitud("calendar.event.get", params_event),
        solicitud("calendar.accessibility.get", params_acc),
        return_exceptions=True,
    )
    # event.get es el principal: si falla, propagamos.
    if isinstance(eventos_res, Exception):
        raise eventos_res

    eventos: list[dict] = eventos_res

    # accessibility es un extra: si falla, avisamos y devolvemos solo event.get.
    if isinstance(ocupacion_res, Exception):
        print(f"BITRIX: aviso, accessibility fallo, sigo sin absences: {ocupacion_res}")
        return eventos

    # accessibility.get devuelve {"user_id_str": [busy_items]}. Extraemos la lista.
    ocupacion_dict = ocupacion_res if isinstance(ocupacion_res, dict) else {}
    ocupacion_lista = ocupacion_dict.get(str(USER_ID), [])

    # Merge por ID, priorizando los eventos ricos de event.get.
    ids_existentes = {str(e.get("ID")) for e in eventos}
    for extra in ocupacion_lista:
        if str(extra.get("ID")) not in ids_existentes:
            eventos.append(extra)

    return eventos

async def modificar_evento_bitrix(id: int, evento_actual: dict, cambios: dict) -> None:
    """Modifica un evento existente en Bitrix. Merge de campos: los que
    están en `cambios` reemplazan, el resto se mantiene desde
    `evento_actual` (el snapshot obtenido en preparación).

    Args:
        id: id Bitrix del evento.
        evento_actual: dict tal cual lo devuelve calendar.event.get
            (mayúsculas: NAME, DATE_FROM, DATE_TO, DESCRIPTION, IMPORTANCE).
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

    # Descripción e importancia
    descripcion = cambios.get("descripcion", evento_actual.get("DESCRIPTION", ""))
    if "prioridad" in cambios:
        importance = _MAPEO_IMPORTANCIA[Prioridad(cambios["prioridad"])]
    else:
        importance = evento_actual.get("IMPORTANCE", "normal")

    params = {
        "id": id,
        "type": "user",
        "ownerId": USER_ID,
        "name": nombre,
        "from": fecha_inicio.strftime("%Y-%m-%dT%H:%M:%S"),
        "to": fecha_fin.strftime("%Y-%m-%dT%H:%M:%S"),
        "description": descripcion,
        "importance": importance,
        "timezone_from": "Europe/Madrid",
        "timezone_to": "Europe/Madrid",
    }

    await solicitud("calendar.event.update", params)


async def eliminar_evento_bitrix(id: int) -> None:
    """Elimina un evento en Bitrix por su id."""
    await solicitud("calendar.event.delete", {"id": id})