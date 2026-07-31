#Canal de entrada / salida de telegram. Orquestador del agente.

import asyncio
import sys
import logging #libreria para implementar un log de events tipo: logging.WARNING("Lo que sea...")
from os import getenv
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram import Dispatcher, Bot, F #F es magic filters, uso: F.text...
from aiogram.filters import CommandStart
from aiogram.types import Message
import agent
import memory
import voice #transcripcion de audio con gemini multimodal

load_dotenv()
TOKEN = getenv("API_BOT")

dp = Dispatcher() #enruta los mensajes que llegan, según el tipo


async def procesar_texto_usuario(texto: str, message: Message):
    """Pipeline comun para texto y audio ya transcrito.
    Antes estaba solo en text_handler; se extrae aqui para que audio_handler
    tambien lo reutilice sin duplicar codigo."""
    await memory.save_message("user", texto) #guarda mensaje en el historial, para memoria de chat
    if memory.check_history():
        history = memory.get_history()
        old_msg = history[:8]
        resumen_previo = memory.get_resumen()
        nuevo_resumen = await agent.summarize(old_msg, resumen_previo)
        memory.set_resumen(nuevo_resumen)
        memory.del_history()

    prompt = memory.get_history()
    resumen = memory.get_resumen()
    respuesta = await agent.process_message(prompt, resumen)
    await memory.save_message("model", respuesta)
    await message.answer(respuesta)


async def text_handler(message: Message):
    await procesar_texto_usuario(message.text, message)


async def audio_handler(message: Message):
    #Descargar el fichero de voz de Telegram usando el file_id
    voice_msg = message.voice
    file = await message.bot.get_file(voice_msg.file_id)
    audio_bytes_io = await message.bot.download_file(file.file_path)
    audio_bytes = audio_bytes_io.read()

    #Transcribir con Gemini (bloque try por si falla la API, para no romper el bot)
    try:
        texto = await voice.transcribir(audio_bytes, mime_type=voice_msg.mime_type or "audio/ogg")
        print(f"BOT: audio transcrito: '{texto}'")
    except Exception as e:
        print(f"BOT: error transcribiendo: {e}")
        await message.answer("No he podido entender el audio, prueba de nuevo por favor.")
        return

    #El texto transcrito entra al mismo pipeline que un mensaje escrito
    await procesar_texto_usuario(texto, message)


async def file_handler(message: Message):
    print("He recibido file.")

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties()) #crea una instancia de bot, con token y propiedades que se usan en toda llamada, es la representacion de "el codigo" en telegram, permite registrar token...
    
    dp.message.register(text_handler, F.text)
    dp.message.register(audio_handler, F.voice)
    dp.message.register(file_handler, F.photo | F.document | F.video)

    await dp.start_polling(bot) #bucle infinito de espera de mensajes, el dispatcher enruta a los respectivos tipos de msg
 
if __name__ == "__main__":
    asyncio.run(main()) #se inicia asi ya que no puedes hacer "await" por que el if no es funcion async -- inicia un bucle gestionado por async para main