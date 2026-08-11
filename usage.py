"""Registro y consulta de uso de tokens de Anthropic.

Cada llamada a client.messages.create() devuelve un objeto `usage` con
los tokens facturables. Este modulo persiste esos datos por llamada en
Supabase (tabla token_usage) y calcula el coste en USD segun los precios
vigentes de Sonnet 5.

IMPORTANTE — precios cambian el 1 de septiembre de 2026:
    Hasta 31-ago: intro    $2/MTok input, $10/MTok output, $0.20 cache read
    Desde 1-sept: estandar $3/MTok input, $15/MTok output, $0.30 cache read
Cache write 5min (ephemeral, lo que usamos) es 1.25x el input.
_precio_actual() elige segun la fecha para que no haya que tocar nada
el 1 de septiembre.

Interfaz publica:
    registrar(user_id, usage, modelo, contexto) -> None
    resumen(user_id, desde=None, hasta=None) -> dict
"""
import os
from datetime import datetime, timezone, date
from dotenv import load_dotenv
from supabase import create_client, Client

import logger

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not _SUPABASE_URL or not _SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en el entorno. Revisa el .env."
    )

_client: Client = create_client(_SUPABASE_URL, _SUPABASE_KEY)


# --------------------- PRECIOS ----------------------------------------
# USD por millon de tokens. Los precios cambian el 2026-09-01 (fin del
# periodo introductorio de Sonnet 5). El cache_write que aplicamos es el
# de 5 min (ephemeral), unico que usa el agente. Si en el futuro se usa
# cache de 1h, anadir la clave "cache_write_1h" al dict correspondiente.
_PRECIOS_SONNET_5_INTRO = {
    "input":       2.00,
    "output":     10.00,
    "cache_read":  0.20,
    "cache_write": 2.50,   # 5 min ephemeral, 1.25x input
}

_PRECIOS_SONNET_5_STANDARD = {
    "input":       3.00,
    "output":     15.00,
    "cache_read":  0.30,
    "cache_write": 3.75,
}

_FIN_INTRO_SONNET_5 = date(2026, 9, 1)


def _precio_actual(modelo: str, fecha: date | None = None) -> dict:
    """Devuelve los 4 precios (USD/MTok) del modelo en la fecha dada.

    Si el modelo no es Sonnet 5, cae al precio estandar como default
    conservador para no infravalorar el coste.
    """
    if fecha is None:
        fecha = datetime.now(timezone.utc).date()

    if "sonnet-5" in modelo:
        if fecha < _FIN_INTRO_SONNET_5:
            return _PRECIOS_SONNET_5_INTRO
        return _PRECIOS_SONNET_5_STANDARD

    # Fallback conservador: si un dia se cambia de modelo sin actualizar
    # este dict, no infravaloramos.
    return _PRECIOS_SONNET_5_STANDARD


def _calcular_coste(
    input_tokens: int,
    output_tokens: int,
    cache_write: int,
    cache_read: int,
    modelo: str,
) -> float:
    """Coste en USD de una llamada, redondeado a 6 decimales."""
    p = _precio_actual(modelo)
    coste = (
        (input_tokens   * p["input"])       +
        (output_tokens  * p["output"])      +
        (cache_write    * p["cache_write"]) +
        (cache_read     * p["cache_read"])
    ) / 1_000_000
    return round(coste, 6)


# --------------------- REGISTRO ---------------------------------------

async def registrar(user_id: str, usage, modelo: str, contexto: str) -> None:
    """Registra el consumo de una unica llamada a la API.

    Args:
        user_id: mismo id que memory.py.
        usage: objeto anthropic.types.Usage devuelto por messages.create().
            Se leen: input_tokens, output_tokens, cache_creation_input_tokens,
            cache_read_input_tokens (los dos ultimos pueden ser None).
        modelo: string de modelo, ej "claude-sonnet-5".
        contexto: 'brain' (loop del agente) o 'summary' (memory resume).

    Falla suave: si la BD peta, se loguea y se sigue. Preferimos perder
    una fila de metricas a romperle el turno al usuario.
    """
    input_tokens       = getattr(usage, "input_tokens", 0) or 0
    output_tokens      = getattr(usage, "output_tokens", 0) or 0
    cache_write_tokens = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read_tokens  = getattr(usage, "cache_read_input_tokens", 0) or 0

    coste = _calcular_coste(
        input_tokens, output_tokens, cache_write_tokens, cache_read_tokens, modelo
    )

    try:
        _client.table("token_usage").insert({
            "user_id": user_id,
            "modelo": modelo,
            "contexto": contexto,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_read_tokens": cache_read_tokens,
            "coste_usd": coste,
        }).execute()
    except Exception as e:
        # No propagamos: el brain no puede caer por un log de metricas.
        logger.warn(
            "usage", "registration_failed",
            f"Fallo al registrar en token_usage: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={
                "modelo": modelo,
                "contexto": contexto,
                "coste_usd": coste,
            },
            error=e,
        )


# --------------------- CONSULTA ---------------------------------------

async def resumen(
    user_id: str | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
) -> dict:
    """Devuelve totales agregados de uso.

    Args:
        user_id: filtra por usuario. Si None, agrega todos.
        desde/hasta: rango temporal (ambos opcionales).

    Retorno:
        {
            "total_llamadas": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "total_cache_write_tokens": int,
            "total_cache_read_tokens": int,
            "total_coste_usd": float,
            "por_contexto": {
                "brain":   {"llamadas": N, "coste_usd": X, "input_tokens": ..., ...},
                "summary": {"llamadas": N, "coste_usd": X, "input_tokens": ..., ...},
            },
            "rango": {"desde": ISO | None, "hasta": ISO | None},
            "cache_hit_ratio": float,   # cache_read / (cache_read + input) — [0, 1]
        }

    Nota: los agregados se hacen en Python porque Postgrest no expone
    SUM() directamente. Para el volumen esperado (un usuario, decenas de
    llamadas al dia) es aceptable. Si escala a miles de usuarios, mover
    a una view SQL en Supabase.
    """
    query = _client.table("token_usage").select(
        "contexto, input_tokens, output_tokens, "
        "cache_write_tokens, cache_read_tokens, coste_usd"
    )
    if user_id:
        query = query.eq("user_id", user_id)
    if desde:
        query = query.gte("created_at", desde.isoformat())
    if hasta:
        query = query.lte("created_at", hasta.isoformat())

    respuesta = query.execute()
    filas = respuesta.data or []

    # Agregacion
    total = {
        "llamadas": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "coste_usd": 0.0,
    }
    por_contexto: dict[str, dict] = {}

    for f in filas:
        ctx = f.get("contexto", "desconocido")
        if ctx not in por_contexto:
            por_contexto[ctx] = {
                "llamadas": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_write_tokens": 0,
                "cache_read_tokens": 0,
                "coste_usd": 0.0,
            }
        for grupo in (total, por_contexto[ctx]):
            grupo["llamadas"]           += 1
            grupo["input_tokens"]       += f.get("input_tokens", 0) or 0
            grupo["output_tokens"]      += f.get("output_tokens", 0) or 0
            grupo["cache_write_tokens"] += f.get("cache_write_tokens", 0) or 0
            grupo["cache_read_tokens"]  += f.get("cache_read_tokens", 0) or 0
            grupo["coste_usd"]          += float(f.get("coste_usd", 0) or 0)

    # cache_hit_ratio: cuanto del input total pego caché. Si es alto (>0.5
    # en conversaciones de 2+ turnos) el prompt caching esta pegando bien.
    denom = total["input_tokens"] + total["cache_read_tokens"]
    cache_hit_ratio = round(total["cache_read_tokens"] / denom, 4) if denom else 0.0

    return {
        "total_llamadas":            total["llamadas"],
        "total_input_tokens":        total["input_tokens"],
        "total_output_tokens":       total["output_tokens"],
        "total_cache_write_tokens":  total["cache_write_tokens"],
        "total_cache_read_tokens":   total["cache_read_tokens"],
        "total_coste_usd":           round(total["coste_usd"], 6),
        "por_contexto":              por_contexto,
        "cache_hit_ratio":           cache_hit_ratio,
        "rango": {
            "desde": desde.isoformat() if desde else None,
            "hasta": hasta.isoformat() if hasta else None,
        },
    }