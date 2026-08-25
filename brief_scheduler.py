"""Cron scheduler del Executive Brief (Sprint 3).

Envia el brief diario por Telegram a las 07:00 hora Madrid, de lunes a
viernes, para cada usuario configurado con telegram_id.

Diseno:
- No usamos APScheduler ni cron externo. Un `asyncio.sleep(60)` en bucle
  es suficiente para un cron con precision de minutos.
- Idempotencia via app_logs: marcamos `event='brief_sent'` cuando se envia.
  Antes de generar consultamos si ya hay uno hoy para ese user_id. Sobrevive
  reinicios de Railway.
- Ventana de envio: 07:00-07:15. Si Railway reinicia en ese rango, el brief
  sale en el proximo tick. Fuera de la ventana no se envia (evita spam
  si el proceso arranca a mediodia).
- Fallo suave: si el brief falla para un usuario, se loguea y se sigue con
  el siguiente. Un fallo aislado no bloquea el otro usuario.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
from os import getenv

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from models import TZ_LOCAL
from config_usuarios import USUARIOS_POR_TELEGRAM_ID
import brief as brief_engine
import logger

# Cliente Supabase reutilizado del logger. Es sincrono, pero el volumen
# es 2 queries/dia por usuario — irrelevante.
from logger import _client as _supabase

# --- Config ---

# Ventana de envio: entre estas horas se dispara el brief si no se envio ya.
VENTANA_INICIO_HORA = 7    # 07:00
VENTANA_INICIO_MIN = 0
VENTANA_FIN_HORA = 7       # 07:15
VENTANA_FIN_MIN = 15

# Frecuencia del tick del scheduler.
INTERVALO_TICK_SEG = 60

# Envio semanal solo L-V. Alexander no quiere brief los fines de semana.
DIAS_ACTIVOS = {0, 1, 2, 3, 4}  # Monday=0

# Permite forzar el envio en cualquier momento via env (util para tests).
FORZAR_ENVIO = getenv("BRIEF_FORCE_SEND", "").lower() in ("1", "true", "yes")

# Limite de longitud por mensaje Telegram (Telegram = 4096; margen para markdown).
MAX_MSG_TELEGRAM = 3800


def _dentro_de_ventana(dt: datetime) -> bool:
    """True si dt esta en la ventana [07:00, 07:15] de un dia laborable."""
    if dt.weekday() not in DIAS_ACTIVOS:
        return False
    ini = dt.replace(hour=VENTANA_INICIO_HORA, minute=VENTANA_INICIO_MIN,
                     second=0, microsecond=0)
    fin = dt.replace(hour=VENTANA_FIN_HORA, minute=VENTANA_FIN_MIN,
                     second=0, microsecond=0)
    return ini <= dt <= fin


def _ya_enviado_hoy(user_id: str, dia: datetime) -> bool:
    """Consulta app_logs si ya se envio el brief hoy para este user_id.
    Retorna False en caso de error (mejor duplicar que perder el brief)."""
    try:
        inicio_dia_utc = dia.replace(hour=0, minute=0, second=0, microsecond=0).astimezone().isoformat()
        res = _supabase.table("app_logs") \
            .select("id") \
            .eq("source", "brief_scheduler") \
            .eq("event", "brief_sent") \
            .eq("user_id", user_id) \
            .gte("created_at", inicio_dia_utc) \
            .limit(1) \
            .execute()
        return bool(res.data)
    except Exception as e:
        logger.warn("brief_scheduler", "check_sent_error",
                    f"No pude comprobar app_logs: {type(e).__name__}: {e}")
        return False


def _marcar_enviado(user_id: str, meta: dict | None = None) -> None:
    """Marca en app_logs que el brief se envio. Escribe DIRECTAMENTE a
    Supabase saltandose el umbral LOG_LEVEL: el marcador es la unica
    barrera contra reenvios cada minuto durante la ventana de 15, y
    perderlo por un LOG_LEVEL=warn seria un bug operativo grave.

    Ademas escribimos por logger.info para stdout (railway logs).
    """
    ahora = datetime.now(TZ_LOCAL)
    payload = {
        "level": "info",
        "source": "brief_scheduler",
        "event": "brief_sent",
        "message": f"Brief matutino enviado a {user_id}",
        "user_id": user_id,
        "metadata": {
            **(meta or {}),
            "marcado_en": ahora.isoformat(),
        },
        "created_at": ahora.astimezone().isoformat(),
    }
    try:
        _supabase.table("app_logs").insert(payload).execute()
    except Exception as e:
        # Fallback: si Supabase peta, dejamos rastro en stdout al menos.
        # El siguiente tick reintentara la generacion — es duplicado
        # aceptable frente a perder el brief.
        logger.error("brief_scheduler", "marker_write_failed",
                     f"No pude marcar brief_sent en app_logs: "
                     f"{type(e).__name__}: {e}",
                     user_id=user_id, error=e)
    # Stdout tambien
    logger.info(
        "brief_scheduler", "brief_sent_stdout",
        f"Brief matutino enviado a {user_id}",
        user_id=user_id,
        metadata=meta or {},
    )


def _dividir_mensaje(texto: str, limite: int = MAX_MSG_TELEGRAM) -> list[str]:
    """Parte un mensaje largo en varios respetando saltos de linea.
    Telegram limita a 4096 chars por mensaje."""
    if len(texto) <= limite:
        return [texto]
    partes: list[str] = []
    actual = ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite:
            if actual:
                partes.append(actual)
            actual = linea
        else:
            actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes


async def _enviar_brief_a(bot: Bot, telegram_id: int, user_id: str) -> bool:
    """Genera + envia el brief a un telegram_id. Devuelve True si OK."""
    try:
        brief = await brief_engine.generar_brief(user_id)
    except Exception as e:
        logger.error("brief_scheduler", "brief_generation_failed",
                     f"No pude generar brief para {user_id}: {type(e).__name__}: {e}",
                     user_id=user_id, error=e)
        return False

    try:
        texto = brief_engine.render_telegram(brief)
    except Exception as e:
        logger.error("brief_scheduler", "brief_render_failed",
                     f"No pude renderizar brief para {user_id}: {type(e).__name__}: {e}",
                     user_id=user_id, error=e)
        return False

    try:
        partes = _dividir_mensaje(texto)
        for parte in partes:
            await bot.send_message(chat_id=telegram_id, text=parte)
        return True
    except Exception as e:
        logger.error("brief_scheduler", "brief_send_failed",
                     f"Telegram rechazo el envio a {telegram_id}: {type(e).__name__}: {e}",
                     user_id=user_id, error=e,
                     metadata={"telegram_id": telegram_id})
        return False


async def _procesar_tick(bot: Bot) -> None:
    """Un tick del scheduler: recorre todos los usuarios y envia el
    brief a los que corresponda."""
    ahora = datetime.now(TZ_LOCAL)

    if not FORZAR_ENVIO and not _dentro_de_ventana(ahora):
        return

    for telegram_id, usuario in USUARIOS_POR_TELEGRAM_ID.items():
        user_id = usuario.get("user_id")
        if not user_id:
            continue

        if not FORZAR_ENVIO and _ya_enviado_hoy(user_id, ahora):
            continue

        logger.info("brief_scheduler", "brief_attempt",
                    f"Enviando brief matutino a {user_id} (tg={telegram_id})",
                    user_id=user_id)
        ok = await _enviar_brief_a(bot, telegram_id, user_id)
        if ok:
            _marcar_enviado(user_id, {"telegram_id": telegram_id,
                                      "hora_envio": ahora.isoformat()})


async def run_brief_scheduler() -> None:
    """Bucle infinito. Se lanza en paralelo con bot + server desde main.py.

    Comparte el token de bot con run_bot; instanciamos un Bot dedicado
    aqui para no acoplar al Dispatcher del handler entrante — es un
    canal de salida puro, no consume updates."""
    token = getenv("API_BOT")
    if not token:
        logger.warn("brief_scheduler", "no_token",
                    "Sin API_BOT en el entorno, brief_scheduler no arranca.")
        return

    bot = Bot(token=token, default=DefaultBotProperties())
    logger.info("brief_scheduler", "startup",
                f"Scheduler activo. Ventana: {VENTANA_INICIO_HORA:02d}:{VENTANA_INICIO_MIN:02d}"
                f"-{VENTANA_FIN_HORA:02d}:{VENTANA_FIN_MIN:02d} L-V "
                f"(forzar={FORZAR_ENVIO})")

    while True:
        try:
            await _procesar_tick(bot)
        except Exception as e:
            logger.error("brief_scheduler", "tick_error",
                         f"Tick fallo: {type(e).__name__}: {e}", error=e)
        await asyncio.sleep(INTERVALO_TICK_SEG)
