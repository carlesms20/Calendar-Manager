"""Transcripción de audio con Gemini multimodal.
El módulo solo devuelve texto; toda la lógica del agente sigue en agent.py.
"""
import asyncio
from google import genai
from google.genai import types
from os import getenv
from dotenv import load_dotenv

load_dotenv()
TOKEN = getenv("API_GEMINI")
_client = genai.Client(api_key=TOKEN)

MODELO_PRIMARIO = "gemini-3.5-flash-lite"
MODELO_FALLBACK = "gemini-3.1-flash-lite"


async def transcribir(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe un audio y devuelve el texto tal cual dijo el usuario.
    
    Aplica fallback y reintentos ante 503 igual que agent.py.
    NO interpreta ni resume — eso es trabajo del agente después.
    """
    prompt = (
        "Transcribe este audio exactamente como se dice, en español. "
        "Devuelve SOLO el texto transcrito, sin comentarios, sin comillas, "
        "sin interpretación. Si hay silencios o ruidos, ignóralos."
    )
    config = types.GenerateContentConfig(
        temperature=0.0,
        max_output_tokens=512,
    )

    ultimo_error = None
    for modelo in (MODELO_PRIMARIO, MODELO_FALLBACK):
        for intento in range(3):
            try:
                response = await _client.aio.models.generate_content(
                    model=modelo,
                    contents=[
                        types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                        prompt,
                    ],
                    config=config,
                )
                return response.text.strip()
            except Exception as e:
                mensaje = str(e).lower()
                es_transitorio = (
                    "503" in mensaje
                    or "unavailable" in mensaje
                    or "overloaded" in mensaje
                )
                if not es_transitorio:
                    raise
                ultimo_error = e
                if intento < 2:
                    delay = 2.0 * (2 ** intento)
                    print(f"VOICE: 503 con {modelo}, reintento en {delay}s ({intento + 1}/3)")
                    await asyncio.sleep(delay)
                    continue
                if modelo == MODELO_PRIMARIO:
                    print(f"VOICE: {MODELO_PRIMARIO} agotado, cambio a {MODELO_FALLBACK}")
    
    raise ultimo_error