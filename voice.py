from google import genai
from google.genai import types
from os import getenv
from dotenv import load_dotenv

load_dotenv()
TOKEN = getenv("API_GEMINI")
_client = genai.Client(api_key=TOKEN)

async def transcribir(audio_bytes: bytes, mime_type: str = "audio/ogg") -> str:
    """Transcribe un audio y devuelve el texto tal cual dijo el usuario.
    
    NO interpreta ni resume — eso es trabajo del agente después.
    """
    response = await _client.aio.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            "Transcribe este audio exactamente como se dice, en español. "
            "Devuelve SOLO el texto transcrito, sin comentarios, sin comillas, "
            "sin interpretación. Si hay silencios o ruidos, ignóralos."
        ],
        config=types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=512,
        )
    )
    print(response.text)
    return response.text.strip()