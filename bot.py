#Canal de entrada / salida de telegram.

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

load_dotenv()
TOKEN = getenv("API_BOT")

dp = Dispatcher() #enruta los mensajes que llegan, según el tipo

async def text_handler(message: Message):
    await memory.save_message("user",message.text) #guarda mensaje en el historial, para memoria de chat
    prompt = memory.get_history()
    respuesta = await agent.process_message(prompt)
    await message.answer(respuesta)

async def audio_handler(message: Message):
    print("He recibido audio.")
    
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