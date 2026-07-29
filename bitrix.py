import httpx
from os import getenv
from dotenv import load_dotenv
from datetime import timedelta
from models import Evento, Prioridad

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

