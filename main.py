"""Orquestador de produccion: arranca bot Telegram + FastAPI en el mismo
proceso asyncio. Punto de entrada unico para Railway.

En desarrollo local puedes seguir arrancando bot.py y server.py por
separado si te resulta mas comodo (mas logs limpios, reinicio independiente).
main.py es el que ejecuta Railway.

Si cualquiera de las dos tareas cae, asyncio.gather cancela la otra y el
proceso muere. Railway lo relanza automaticamente segun su politica de
restart.
"""
import asyncio
from os import getenv
import uvicorn
from aiogram import Dispatcher, Bot, F
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Importamos el objeto `app` de FastAPI ya montado con middlewares, rutas
# y mount de frontend/dist. server.py no expone main(), solo el `app`.
from server import app as fastapi_app

# Los handlers del bot viven en bot.py. Los registramos en un Dispatcher
# nuevo aqui para no acoplarnos al `dp` global de bot.py (aunque tambien
# funcionaria usarlo directamente).
from bot import text_handler, audio_handler, file_handler
from brief_scheduler import run_brief_scheduler

load_dotenv()

TOKEN_BOT = getenv("API_BOT")
if not TOKEN_BOT:
    raise RuntimeError("Falta API_BOT en el entorno.")

# Railway y otros PaaS inyectan el puerto por env PORT. En local usamos 8000.
PORT = int(getenv("PORT", "8000"))


async def run_bot():
    """Polling infinito del bot Telegram."""
    bot = Bot(token=TOKEN_BOT, default=DefaultBotProperties())
    dp = Dispatcher()
    dp.message.register(text_handler, F.text)
    dp.message.register(audio_handler, F.voice)
    dp.message.register(file_handler, F.photo | F.document | F.video)
    print("MAIN: bot Telegram iniciado")
    await dp.start_polling(bot)


async def run_server():
    """Uvicorn embebido en el event loop actual, no como proceso aparte."""
    config = uvicorn.Config(
        fastapi_app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    print(f"MAIN: FastAPI iniciado en 0.0.0.0:{PORT}")
    await server.serve()


async def main():
    """Corre bot, server y scheduler del brief en paralelo. Si uno lanza
    excepcion, gather cancela los otros y propaga el error. Railway
    relanzara el proceso."""
    await asyncio.gather(
        run_bot(),
        run_server(),
        run_brief_scheduler(),
    )


if __name__ == "__main__":
    asyncio.run(main())