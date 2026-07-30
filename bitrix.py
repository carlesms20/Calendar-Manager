import httpx
from os import getenv
from dotenv import load_dotenv
from datetime import timedelta
from models import Evento, Prioridad
from datetime import datetime

load_dotenv()
WEBHOOK = getenv("WEBHOOK_BITRIX")
USER_ID = int(getenv("BITRIX_USER_ID", "0"))

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

    params = {
        "type": "user",
        "ownerId": USER_ID,
    }
    if fecha_inicio is not None:
        params["from"] = fecha_inicio.strftime("%Y-%m-%d")
    if fecha_fin is not None:
        params["to"] = fecha_fin.strftime("%Y-%m-%d")

    return await solicitud("calendar.event.get", params)

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
    fecha_inicio_original = datetime.fromisoformat(evento_actual["DATE_FROM"])
    fecha_fin_original = datetime.fromisoformat(evento_actual["DATE_TO"])
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