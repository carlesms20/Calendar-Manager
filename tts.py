"""Síntesis de voz con Gemini TTS.
Modelo primario: gemini-3.1-flash-tts-preview.
Fallback: gemini-2.5-flash-tts.

Ambos comparten free tier con RPD=10, así que el fallback solo cubre 503s
del primario (no ayuda si ya agotamos la cuota diaria).

Gemini devuelve PCM raw a 24 kHz mono 16-bit. Envolvemos en WAV para que
el navegador pueda reproducirlo directo con <audio> o new Audio(url).
"""
import asyncio
import io
import wave
from google import genai
from google.genai import types
from os import getenv
from dotenv import load_dotenv

load_dotenv()
TOKEN = getenv("API_GEMINI")
_client = genai.Client(api_key=TOKEN)

MODELO_PRIMARIO = "gemini-3.1-flash-tts-preview"
MODELO_FALLBACK = "gemini-2.5-flash-tts"

# Kore es la voz por defecto usada en los ejemplos de Google, funciona
# razonablemente bien en español. Alternativas: Charon (masc grave),
# Iapetus (masc medio), Puck, Leda, Zephyr. Se puede parametrizar más
# adelante si Alexander pide otra.
VOZ_DEFECTO = "Kore"


async def sintetizar(texto: str, voz: str = VOZ_DEFECTO) -> bytes:
    """Convierte texto en audio WAV. Devuelve los bytes del fichero.

    Aplica reintentos ante 503 y cambia a fallback igual que voice.py.
    Ante 429 (rate limit diario) propaga el error sin reintentar — el
    frontend mostrará el estado de error en el botón de audio.
    """
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voz,
                )
            )
        ),
    )

    ultimo_error = None
    for modelo in (MODELO_PRIMARIO, MODELO_FALLBACK):
        for intento in range(3):
            try:
                response = await _client.aio.models.generate_content(
                    model=modelo,
                    contents=texto,
                    config=config,
                )
                pcm = response.candidates[0].content.parts[0].inline_data.data
                return _envolver_wav(pcm)
            except Exception as e:
                mensaje = str(e).lower()
                es_transitorio = (
                    "503" in mensaje
                    or "unavailable" in mensaje
                    or "overloaded" in mensaje
                )
                if not es_transitorio:
                    # 429 (cuota diaria) y otros errores se propagan
                    # directamente, no tiene sentido reintentar.
                    raise
                ultimo_error = e
                if intento < 2:
                    delay = 2.0 * (2 ** intento)
                    print(f"TTS: 503 con {modelo}, reintento en {delay}s ({intento + 1}/3)")
                    await asyncio.sleep(delay)
                    continue
                if modelo == MODELO_PRIMARIO:
                    print(f"TTS: {MODELO_PRIMARIO} agotado, cambio a {MODELO_FALLBACK}")

    raise ultimo_error


def _envolver_wav(pcm: bytes) -> bytes:
    """Envuelve PCM raw (24 kHz mono 16-bit) en un contenedor WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(24000)
        wf.writeframes(pcm)
    return buf.getvalue()