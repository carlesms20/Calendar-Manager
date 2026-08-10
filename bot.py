"""Canal de entrada / salida de Telegram. Orquestador del agente para el
bot de Alexander y Carles.

Autorizacion multiusuario: cada mensaje entrante se resuelve contra
USUARIOS_POR_TELEGRAM_ID de config_usuarios. Si el telegram_id no esta
en el dict, se rechaza con "No autorizado". Si esta, se extrae el
user_id logico ("carles"|"alexander") y se propaga a agent.procesar_input,
que a su vez lo propaga a memoria (Supabase) y a las tools (Bitrix).

Dev mode: si NINGUN usuario esta configurado en el dict (ambos telegram_id
env vars vacios), el bot arranca pero rechaza todo. No hay modo "abierto"
como antes — con multi-usuario tiene que haber al menos una entrada valida.
"""
import asyncio
import sys
import logging
from os import getenv
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram import Dispatcher, Bot, F
from aiogram.filters import CommandStart
from aiogram.types import Message

import agent
import memory
import voice
from config_usuarios import USUARIOS_POR_TELEGRAM_ID, usuario_por_telegram_id

load_dotenv()
TOKEN = getenv("API_BOT")

if not USUARIOS_POR_TELEGRAM_ID:
    print(
        "BOT WARN: USUARIOS_POR_TELEGRAM_ID esta vacio. "
        "Configura CARLES_TELEGRAM_ID y/o ALEXANDER_TELEGRAM_ID en el .env. "
        "Sin ellos, el bot rechazara todos los mensajes."
    )
else:
    print(f"BOT: {len(USUARIOS_POR_TELEGRAM_ID)} usuario(s) autorizado(s) cargados desde config.")

dp = Dispatcher()


def _resolver_usuario(message: Message) -> dict | None:
    """Devuelve el contexto del usuario (dict con user_id, webhook, etc.)
    si el mensaje viene de un telegram_id autorizado, o None si no.
    Loggea rechazos para dejar rastro de intentos no autorizados.
    """
    if not message.from_user:
        # Mensajes de canal o edge cases raros: rechazar por defecto
        return None

    usuario = usuario_por_telegram_id(message.from_user.id)
    if usuario is None:
        print(
            f"BOT: rechazado user_id={message.from_user.id} "
            f"username=@{message.from_user.username} "
            f"nombre={message.from_user.full_name}"
        )
        return None

    return usuario


async def procesar_texto_usuario(user_id: str, texto: str, message: Message):
    """Delega en agent.procesar_input y responde por Telegram.

    user_id es el identificador logico ("carles"|"alexander") ya resuelto
    en el handler, que se usa como clave en Supabase y para el lookup
    del contexto Bitrix dentro de las tools.

    Loggea la respuesta del agente truncada a 200 chars para trazabilidad
    en Railway sin ensuciar con textos larguisimos.
    """
    respuesta = await agent.procesar_input(user_id, texto)
    resp_log = respuesta[:200] + ("..." if len(respuesta) > 200 else "")
    print(f"BOT[{user_id}]: respuesta del agente: '{resp_log}'")
    await message.answer(respuesta)


async def text_handler(message: Message):
    usuario = _resolver_usuario(message)
    if usuario is None:
        await message.answer("No autorizado.")
        return

    user_id = usuario["user_id"]
    print(
        f"BOT[{user_id}]: mensaje texto de {message.from_user.id} "
        f"(@{message.from_user.username}): '{message.text}'"
    )
    await procesar_texto_usuario(user_id, message.text, message)


async def audio_handler(message: Message):
    usuario = _resolver_usuario(message)
    if usuario is None:
        await message.answer("No autorizado.")
        return

    user_id = usuario["user_id"]

    # Descargar el fichero de voz de Telegram usando el file_id
    voice_msg = message.voice
    file = await message.bot.get_file(voice_msg.file_id)
    audio_bytes_io = await message.bot.download_file(file.file_path)
    audio_bytes = audio_bytes_io.read()

    # Transcribir con Gemini (bloque try por si falla la API, para no romper el bot)
    try:
        texto = await voice.transcribir(audio_bytes, mime_type=voice_msg.mime_type or "audio/ogg")
        print(f"BOT[{user_id}]: audio transcrito: '{texto}'")
    except Exception as e:
        print(f"BOT[{user_id}]: error transcribiendo: {e}")
        await message.answer("No he podido entender el audio, prueba de nuevo por favor.")
        return

    # Filtro anti-basura: transcripciones vacias, timestamps sueltos
    # ('00:00'), o cadenas triviales de ruido. Sin esto, un audio mudo
    # o con solo ruido de fondo entra al pipeline del agente gastando
    # tokens y produciendo respuestas erraticas.
    texto_limpio = texto.strip()
    if len(texto_limpio) < 3 or texto_limpio in {"00:00", "0:00", "...", "…"}:
        print(f"BOT[{user_id}]: audio transcrito vacio o irrelevante ('{texto_limpio}'), ignorando")
        await message.answer("No he entendido el audio, intentalo de nuevo por favor.")
        return

    # El texto transcrito entra al mismo pipeline que un mensaje escrito
    await procesar_texto_usuario(user_id, texto_limpio, message)


async def file_handler(message: Message):
    usuario = _resolver_usuario(message)
    if usuario is None:
        await message.answer("No autorizado.")
        return
    user_id = usuario["user_id"]
    print(f"BOT[{user_id}]: recibido file (aun no soportado).")


async def main():
    if not TOKEN:
        raise RuntimeError("Falta API_BOT en el entorno. Revisa el .env.")

    bot = Bot(token=TOKEN, default=DefaultBotProperties())

    dp.message.register(text_handler, F.text)
    dp.message.register(audio_handler, F.voice)
    dp.message.register(file_handler, F.photo | F.document | F.video)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())