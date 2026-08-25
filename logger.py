"""Logs persistentes en Supabase con dual-write a stdout.

Reemplaza los print() dispersos del proyecto por eventos estructurados
que quedan indexados en app_logs. Sigue escribiendo tambien a stdout
para no perder trazabilidad en tiempo real en los logs de Railway.

Interfaz publica:
    info(source, event, message, *, user_id=None, metadata=None) -> None
    warn(source, event, message, *, user_id=None, metadata=None, error=None) -> None
    error(source, event, message, *, user_id=None, metadata=None, error=None) -> None

Notas:
- Falla suave: si Supabase peta, solo queda el stdout. Nunca rompe al caller.
- Sincrono. Como memory.py y usage.py, usamos el cliente sync de supabase-py.
  Para el volumen actual (decenas de logs por conversacion) es irrelevante,
  y evita tener que meter async en modulos que no lo son (bitrix, tools).
- El traceback se guarda solo si se pasa `error=`. Se serializa con
  traceback.format_exception() para tener el stack completo, no solo el str().
- Formato stdout mantiene el estilo actual del proyecto ('AGENT[carles]:',
  'BOT[alexander]:') para no romper grep-patterns en Railway.
"""
import os
import traceback
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# Umbral de nivel para persistencia en Supabase. Los logs por debajo de
# este nivel siguen saliendo en stdout (dual-write) pero NO se insertan
# en app_logs. Objetivo: reducir volumen almacenado. Default 'warn'.
# En local puedes ponerlo a 'info' en el .env para ver todo persistido.
_LOG_LEVEL = os.getenv("LOG_LEVEL", "warn").lower()
_LEVELS = {"info": 0, "warn": 1, "error": 2}
_UMBRAL_SUPABASE = _LEVELS.get(_LOG_LEVEL, 1)

if not _SUPABASE_URL or not _SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en el entorno. Revisa el .env."
    )

_client: Client = create_client(_SUPABASE_URL, _SUPABASE_KEY)


def _prefijo_stdout(level: str, source: str, user_id: str | None) -> str:
    """Formato 'SOURCE[user_id] LEVEL:' consistente con los print()
    originales del proyecto ('AGENT[carles]:', 'BOT[alexander]:').
    Asi los greps existentes en Railway siguen funcionando.
    """
    src = source.upper()
    uid = f"[{user_id}]" if user_id else ""
    lvl = "" if level == "info" else f" {level.upper()}"
    return f"{src}{uid}{lvl}:"


def _serializable(valor):
    """Convierte valores no-JSON-serializables (datetime, exceptions...)
    en strings para meterlos en metadata sin romper la insercion.
    """
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, BaseException):
        return f"{type(valor).__name__}: {valor}"
    return valor


def _limpiar_metadata(metadata: dict | None) -> dict:
    """Recorre metadata una vez y sanea valores no serializables.
    Solo un nivel de profundidad: para logs es mas que suficiente."""
    if not metadata:
        return {}
    return {k: _serializable(v) for k, v in metadata.items()}


def _log(
    level: str,
    source: str,
    event: str,
    message: str,
    user_id: str | None = None,
    metadata: dict | None = None,
    error: BaseException | None = None,
) -> None:
    """Escribe el log en stdout siempre, y en Supabase solo si supera
    el umbral configurado por LOG_LEVEL. Falla suave en la BD."""
    # 1) stdout — siempre. Aunque el nivel este por debajo del umbral,
    # o Supabase falle, este print queda en Railway para debugging vivo.
    print(f"{_prefijo_stdout(level, source, user_id)} [{event}] {message}")

    # 2) Filtro de nivel para Supabase. Si el log esta por debajo del
    # umbral, no lo persistimos.
    if _LEVELS.get(level, 0) < _UMBRAL_SUPABASE:
        return

    # 3) Supabase — best effort
    fila: dict = {
        "level":    level,
        "source":   source,
        "event":    event,
        "message":  message[:2000],
        "user_id":  user_id,
        "metadata": _limpiar_metadata(metadata),
    }
    if error is not None:
        fila["error_type"]  = type(error).__name__
        fila["error_stack"] = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )[:8000]

    try:
        _client.table("app_logs").insert(fila).execute()
    except Exception as e:
        print(f"LOGGER: fallo insertando app_log ({event}): {type(e).__name__}: {e}")


def info(
    source: str,
    event: str,
    message: str,
    *,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Log de nivel info: eventos normales que quieres poder trazar."""
    _log("info", source, event, message, user_id=user_id, metadata=metadata)


def warn(
    source: str,
    event: str,
    message: str,
    *,
    user_id: str | None = None,
    metadata: dict | None = None,
    error: BaseException | None = None,
) -> None:
    """Log de nivel warn: algo raro pero no bloquea. Incluye error si lo hay."""
    _log("warn", source, event, message,
         user_id=user_id, metadata=metadata, error=error)


def error(
    source: str,
    event: str,
    message: str,
    *,
    user_id: str | None = None,
    metadata: dict | None = None,
    error: BaseException | None = None,
) -> None:
    """Log de nivel error: fallo real, con traceback si se pasa la excepcion."""
    _log("error", source, event, message,
         user_id=user_id, metadata=metadata, error=error)
