"""Endpoint HTTP para el agente. Sirve la misma logica que el bot
de Telegram pero via HTTP, pensado para que el frontend web del
dashboard (dentro del iframe) hable con el brain.

Ademas sirve el frontend React compilado bajo /app/*.
"""
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import agent
import voice
import tools
import tts
import usage
from bitrix import consultar_eventos_bitrix, BitrixError, consultar_ocupacion_bitrix
import base64
import secrets
from os import getenv
from fastapi import Request
from fastapi.responses import Response as StarletteResponse
from dotenv import load_dotenv
load_dotenv()

APP_USER = getenv("APP_USER", "")
APP_PASSWORD = getenv("APP_PASSWORD", "")

if not APP_USER or not APP_PASSWORD:
    print("SERVER WARN: APP_USER/APP_PASSWORD no configurados. /app abierto (dev mode).")
app = FastAPI(title="Agente SYNCROSFERA")

# CORS: en desarrollo abrimos todo para no pelearse con orígenes locales.
# En produccion se restringe al dominio del dashboard de David.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.middleware("http")
async def basic_auth_para_app(request: Request, call_next):
    """Basic Auth SOLO para rutas bajo /app (la mini-app React).
    /api/*, /health y cualquier otra ruta pasan sin auth: el bot Telegram
    no la necesita y el frontend hace fetch directo a /api desde JS ya
    autenticado en el navegador.

    En dev mode (APP_USER o APP_PASSWORD vacios) no aplica auth.
    """
    path = request.url.path
    if not path.startswith("/app"):
        return await call_next(request)

    if not APP_USER or not APP_PASSWORD:
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return StarletteResponse(
            content="Autenticacion requerida",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Syncrosfera"'},
        )

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, _, password = decoded.partition(":")
    except Exception:
        return StarletteResponse(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Syncrosfera"'},
        )

    user_ok = secrets.compare_digest(user, APP_USER)
    pass_ok = secrets.compare_digest(password, APP_PASSWORD)
    if not (user_ok and pass_ok):
        return StarletteResponse(
            content="Credenciales invalidas",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Syncrosfera"'},
        )

    return await call_next(request)


class MensajeTexto(BaseModel):
    """Payload del endpoint de texto."""
    text: str

class MensajeTTS(BaseModel):
    """Payload del endpoint TTS."""
    text: str

class RespuestaAgente(BaseModel):
    """Respuesta que devuelve el agente al frontend.

    agenda_modificada: True si la respuesta implico una accion confirmada
    sobre Bitrix (crear/modificar/eliminar). El frontend usa este flag
    para decidir si refrescar el calendario o no.
    """
    reply: str
    agenda_modificada: bool = False


class EventoResumen(BaseModel):
    """Evento del calendario, formato consumido por el frontend."""
    id: str
    nombre: str
    fecha_inicio: str  # ISO 8601
    fecha_fin: str
    descripcion: str = ""
    prioridad: str = ""  # "alta" | "media" | "baja" | "" si Bitrix no la trae


class ListaEventos(BaseModel):
    eventos: list[EventoResumen]


@app.get("/health")
async def health():
    """Ping simple para monitorizar que el servicio esta vivo."""
    return {"status": "ok"}


async def _procesar_y_detectar_cambios(texto: str) -> RespuestaAgente:
    """Envuelve agent.procesar_input y detecta si la agenda cambio.

    Estrategia: comparamos el numero de operaciones confirmadas ANTES
    de procesar vs DESPUES. Si hubo un confirmar_operaciones_pendientes
    exitoso, el buffer se vacia — pero eso no lo vemos directamente.
    Lo que si vemos es que si el mensaje del usuario disparo una
    confirmacion, el reply del agente lo va a decir ("he creado...",
    "operacion completada..."). Buscamos esas senales en la respuesta.

    Alternativa mas robusta a futuro: que confirmar_operaciones_pendientes
    guarde un flag en un modulo compartido.
    """
    reply = await agent.procesar_input("alexander", texto)

    # Deteccion heuristica: buscamos frases tipicas en la respuesta que
    # indican que el agente ejecuto algo. No es 100% preciso pero acierta
    # el 95% de las veces. En el peor caso, refrescamos de mas.
    senales = [
        "he creado", "he movido", "he eliminado", "he cancelado",
        "he agendado", "he confirmado", "he reprogramado", "he actualizado",
        "operacion completada", "operación completada",
        "operaciones completadas", "he registrado",
    ]
    reply_lower = reply.lower()
    modificada = any(s in reply_lower for s in senales)

    return RespuestaAgente(reply=reply, agenda_modificada=modificada)


@app.post("/api/mensaje", response_model=RespuestaAgente)
async def mensaje_texto(payload: MensajeTexto):
    """Recibe un mensaje escrito y devuelve la respuesta del agente."""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacio")

    try:
        return await _procesar_y_detectar_cambios(payload.text)
    except Exception as e:
        print(f"SERVER: error procesando mensaje: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error procesando el mensaje")


@app.post("/api/audio", response_model=RespuestaAgente)
async def mensaje_audio(audio: UploadFile = File(...)):
    """Recibe un audio del navegador, lo transcribe con Gemini, y procesa
    el texto resultante por el mismo pipeline que un mensaje escrito."""
    audio_bytes = await audio.read()
    mime_type = audio.content_type or "audio/webm"

    try:
        texto = await voice.transcribir(audio_bytes, mime_type=mime_type)
        print(f"SERVER: audio transcrito: '{texto}'")
    except Exception as e:
        print(f"SERVER: error transcribiendo: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="No he podido entender el audio")

    try:
        return await _procesar_y_detectar_cambios(texto)
    except Exception as e:
        print(f"SERVER: error procesando: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error procesando el mensaje")

from fastapi.responses import Response

@app.post("/api/tts")
async def sintetizar_voz(payload: MensajeTTS):
    """Convierte un texto en audio WAV usando Gemini TTS.

    Free tier: 10 llamadas al dia. El frontend cachea el blob por mensaje
    para no gastar cuota al pulsar play varias veces sobre el mismo.
    """
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacio")

    if len(payload.text) > 5000:
        # Limite arbitrario para no gastar tokens en respuestas larguisimas.
        # Gemini acepta hasta 8000 bytes de input, dejamos margen.
        raise HTTPException(status_code=413, detail="Texto demasiado largo para TTS")

    try:
        wav_bytes = await tts.sintetizar(payload.text)
    except Exception as e:
        mensaje = str(e).lower()
        if "429" in mensaje or "resource_exhausted" in mensaje or "quota" in mensaje:
            print(f"SERVER: cuota TTS agotada: {e}")
            raise HTTPException(status_code=429, detail="Cuota diaria de TTS agotada")
        print(f"SERVER: error TTS: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error generando el audio")

    return Response(content=wav_bytes, media_type="audio/wav")

def _parsear_fecha_bitrix(s: str) -> datetime:
    """Bitrix devuelve fechas en tres formatos:
    - ISO 8601 completo
    - 'dd.mm.YYYY HH:MM:SS' formato europeo con hora
    - 'dd.mm.YYYY' formato europeo sin hora (eventos all-day)
    Los all-day quedan como datetime a 00:00:00 del dia.
    """
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%d.%m.%Y %H:%M:%S")
    except ValueError:
        pass
    return datetime.strptime(s, "%d.%m.%Y")


@app.get("/api/eventos", response_model=ListaEventos)
async def listar_eventos(
    desde: str | None = Query(None, description="ISO 8601, ej: 2026-08-03T00:00:00"),
    hasta: str | None = Query(None, description="ISO 8601, ej: 2026-08-10T00:00:00"),
):
    """Devuelve los eventos del calendario en el rango dado.
    Si no se pasan fechas, devuelve la semana en curso (lunes-domingo).
    """
    # Rango por defecto: semana en curso
    if desde is None or hasta is None:
        hoy = datetime.now()
        lunes = hoy - timedelta(days=hoy.weekday())
        lunes = lunes.replace(hour=0, minute=0, second=0, microsecond=0)
        domingo_23 = lunes + timedelta(days=6, hours=23, minutes=59)
        desde_dt = lunes
        hasta_dt = domingo_23
    else:
        try:
            desde_dt = datetime.fromisoformat(desde)
            hasta_dt = datetime.fromisoformat(hasta)
        except ValueError:
            raise HTTPException(status_code=400, detail="Fechas invalidas, usa ISO 8601")

    try:
        raw = await consultar_ocupacion_bitrix(desde_dt, hasta_dt)
    except BitrixError as e:
        raise HTTPException(status_code=502, detail=f"Bitrix rechazo la peticion: {e}")

    # Normalizamos al formato que consume el frontend
    _IMPORTANCE_A_PRIORIDAD = {"high": "alta", "normal": "media", "low": "baja"}

    eventos = []
    for e in raw:
        date_from = e.get("DATE_FROM")
        date_to = e.get("DATE_TO")
        if not date_from or not date_to:
            print(f"SERVER: evento sin fechas, salto (ID={e.get('ID', '?')}, NAME={e.get('NAME', '?')})")
            continue
        try:
            fi = _parsear_fecha_bitrix(date_from)
            ff = _parsear_fecha_bitrix(date_to)
        except ValueError as err:
            print(f"SERVER: fecha invalida en evento {e.get('ID', '?')}: {err}")
            continue

        # Eventos all-day: Bitrix suele darlos como DATE_FROM == DATE_TO
        # (misma fecha, ambos parseados a 00:00:00). Sin esta correccion
        # el frontend pintaria un tick de duracion cero y el evento no
        # se veria. Estiramos DATE_TO a las 23:59:59 del mismo dia.
        if fi == ff and fi.hour == 0 and fi.minute == 0 and fi.second == 0:
            ff = fi.replace(hour=23, minute=59, second=59)

        prioridad = _IMPORTANCE_A_PRIORIDAD.get(
            (e.get("IMPORTANCE") or "").lower(), ""
        )

        eventos.append(EventoResumen(
            id=str(e.get("ID", "")),
            nombre=e.get("NAME", "(sin nombre)"),
            fecha_inicio=fi.isoformat(),
            fecha_fin=ff.isoformat(),
            descripcion=e.get("DESCRIPTION", "") or "",
            prioridad=prioridad,
        ))

    return ListaEventos(eventos=eventos)


@app.get("/api/usage")
async def obtener_uso(
    user_id: str | None = Query(None, description="Filtra por usuario. Si se omite, agrega todos."),
    desde: str | None = Query(None, description="ISO 8601, ej: 2026-08-01T00:00:00"),
    hasta: str | None = Query(None, description="ISO 8601, ej: 2026-08-31T23:59:59"),
):
    """Totales agregados de consumo de tokens y coste USD.

    Sin params, devuelve el total historico agregado de todos los usuarios.
    Con user_id="alexander" y sin rango, devuelve el total de Alexander.
    Con rango, filtra por fecha.

    El campo cache_hit_ratio indica cuanto del input viene del cache:
    >0.5 significa que el prompt caching esta pegando bien en conversaciones
    de multiples turnos. Si es 0, el cache no esta funcionando.
    """
    desde_dt: datetime | None = None
    hasta_dt: datetime | None = None
    try:
        if desde is not None:
            desde_dt = datetime.fromisoformat(desde)
        if hasta is not None:
            hasta_dt = datetime.fromisoformat(hasta)
    except ValueError:
        raise HTTPException(status_code=400, detail="Fechas invalidas, usa ISO 8601")

    try:
        return await usage.resumen(user_id=user_id, desde=desde_dt, hasta=hasta_dt)
    except Exception as e:
        print(f"SERVER: error consultando usage: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Error consultando uso")


# ---- Servir el frontend compilado ------------------------------------------
# Solo se activa si existe la carpeta frontend/dist (creada por `npm run build`).
# En desarrollo, el frontend corre en localhost:5173 con Vite dev server.

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount(
        "/app/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/app")
    @app.get("/app/{path:path}")
    async def servir_frontend(path: str = ""):
        """Sirve index.html para cualquier ruta bajo /app."""
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    print("SERVER: aviso - frontend/dist no existe. En dev usa 'npm run dev' aparte.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)