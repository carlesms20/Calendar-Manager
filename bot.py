#Canal de entrada / salida de telegram. Orquestador del agente.

import asyncio
import sys
import logging  # libreria para implementar un log de events tipo: logging.WARNING("Lo que sea...")
from os import getenv
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram import Dispatcher, Bot, F  # F es magic filters, uso: F.text...
from aiogram.filters import CommandStart
from aiogram.types import Message
import agent
import memory
import voice  # transcripcion de audio con gemini multimodal

load_dotenv()
TOKEN = getenv("API_BOT")

# Filtro de autorizacion: solo Alexander puede usar el bot. Si el env no
# esta seteado (dev local), abrimos la puerta pero avisamos por consola
# para que en produccion no se olvide de configurarlo.
try:
    ALEXANDER_TELEGRAM_ID = int(getenv("ALEXANDER_TELEGRAM_ID", "0"))
except ValueError:
    print("BOT WARN: ALEXANDER_TELEGRAM_ID no es un entero valido, tratando como 0 (dev mode)")
    ALEXANDER_TELEGRAM_ID = 0

if ALEXANDER_TELEGRAM_ID == 0:
    print("BOT WARN: ALEXANDER_TELEGRAM_ID no configurado. Bot abierto a cualquier user_id (dev mode).")

dp = Dispatcher()  # enruta los mensajes que llegan, según el tipo


def _es_autorizado(message: Message) -> bool:
    """True si el mensaje viene de Alexander (o si estamos en dev mode
    con el env sin configurar). Loggea rechazos para dejar rastro de
    intentos no autorizados."""
    if not message.from_user:
        # Mensajes de canal o edge cases raros: rechazar por defecto
        return False
    if ALEXANDER_TELEGRAM_ID == 0:
        # Dev mode: sin filtro
        return True
    autorizado = message.from_user.id == ALEXANDER_TELEGRAM_ID
    if not autorizado:
        print(
            f"BOT: rechazado user_id={message.from_user.id} "
            f"username=@{message.from_user.username} "
            f"nombre={message.from_user.full_name}"
        )
    return autorizado

async def procesar_texto_usuario(texto: str, message: Message):
    """Delega en agent.procesar_input y responde por Telegram.
    Loggea la respuesta del agente truncada a 200 chars para trazabilidad
    en Railway sin ensuciar con textos larguisimos.
    """
    respuesta = await agent.procesar_input("alexander", texto)
    resp_log = respuesta[:200] + ("..." if len(respuesta) > 200 else "")
    print(f"BOT: respuesta del agente: '{resp_log}'")
    await message.answer(respuesta)


async def text_handler(message: Message):
    if not _es_autorizado(message):
        await message.answer("No autorizado.")
        return
    print(
        f"BOT: mensaje texto de {message.from_user.id} "
        f"(@{message.from_user.username}): '{message.text}'"
    )
    await procesar_texto_usuario(message.text, message)


async def audio_handler(message: Message):
    if not _es_autorizado(message):
        await message.answer("No autorizado.")
        return

    # Descargar el fichero de voz de Telegram usando el file_id
    voice_msg = message.voice
    file = await message.bot.get_file(voice_msg.file_id)
    audio_bytes_io = await message.bot.download_file(file.file_path)
    audio_bytes = audio_bytes_io.read()

    # Transcribir con Gemini (bloque try por si falla la API, para no romper el bot)
    try:
        texto = await voice.transcribir(audio_bytes, mime_type=voice_msg.mime_type or "audio/ogg")
        print(f"BOT: audio transcrito: '{texto}'")
    except Exception as e:
        print(f"BOT: error transcribiendo: {e}")
        await message.answer("No he podido entender el audio, prueba de nuevo por favor.")
        return

    # Filtro anti-basura: transcripciones vacias, timestamps sueltos
    # ('00:00'), o cadenas triviales de ruido. Sin esto, un audio mudo
    # o con solo ruido de fondo entra al pipeline del agente gastando
    # tokens y produciendo respuestas erraticas.
    texto_limpio = texto.strip()
    if len(texto_limpio) < 3 or texto_limpio in {"00:00", "0:00", "...", "…"}:
        print(f"BOT: audio transcrito vacio o irrelevante ('{texto_limpio}'), ignorando")
        await message.answer("No he entendido el audio, intentalo de nuevo por favor.")
        return

    # El texto transcrito entra al mismo pipeline que un mensaje escrito
    await procesar_texto_usuario(texto_limpio, message)

async def file_handler(message: Message):
    if not _es_autorizado(message):
        await message.answer("No autorizado.")
        return
    print("He recibido file.")


async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties())  # crea una instancia de bot, con token y propiedades que se usan en toda llamada, es la representacion de "el codigo" en telegram, permite registrar token...

    dp.message.register(text_handler, F.text)
    dp.message.register(audio_handler, F.voice)
    dp.message.register(file_handler, F.photo | F.document | F.video)

    await dp.start_polling(bot)  # bucle infinito de espera de mensajes, el dispatcher enruta a los respectivos tipos de msg


if __name__ == "__main__":
    asyncio.run(main())  # se inicia asi ya que no puedes hacer "await" por que el if no es funcion async -- inicia un bucle gestionado por async para main