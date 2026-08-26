"""Endpoint HTTP para el agente. Sirve la misma logica que el bot
de Telegram pero via HTTP para la mini-app web.

Ademas sirve el frontend React compilado bajo /app/*.

Autorizacion multiusuario:
- Middleware Basic Auth resuelve credenciales contra USUARIOS_POR_USERNAME.
- El user_id autenticado se guarda en request.state.user_id.
- Los endpoints /api/* lo leen y lo propagan a agent y a bitrix.
- Cada usuario ve/opera SOLO su propio calendario (Carles el suyo,
  Alexander el suyo). Nunca se mezclan datos.
- /health se queda sin auth.
"""
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.responses import Response as StarletteResponse
from pydantic import BaseModel
import base64
from os import getenv
from dotenv import load_dotenv

import agent
import voice
import tools
import tts
import usage
import logger
from bitrix import BitrixError, consultar_ocupacion_bitrix
from config_usuarios import USUARIOS_POR_USERNAME, autenticar_web, USUARIOS_POR_TELEGRAM_ID
import bitrix_tasks
import brief as brief_engine
from models import EstadoEOS, TransicionIlegal

load_dotenv()


app = FastAPI(title="Agente SYNCROSFERA")

# CORS: en desarrollo abrimos todo para no pelearse con origenes locales.
# En produccion se puede restringir si algun dia se necesita.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Basic Auth multiusuario ------------------------------------------------
# Protege /app (mini-app React) y los endpoints /api que requieren identidad
# de usuario (/api/mensaje, /api/audio, /api/eventos).
# /health y /api/usage se dejan abiertos (health para monitorizacion,
# usage porque agrega solo por user_id explicito en query).

_RUTAS_PROTEGIDAS_PREFIJOS = (
    "/app",
    "/api/mensaje",
    "/api/audio",
    "/api/eventos",
    "/api/tareas",
    "/api/brief",
)


@app.middleware("http")
async def basic_auth_multiusuario(request: Request, call_next):
    """Basic Auth contra USUARIOS_POR_USERNAME.

    - /health, /api/tts y /api/usage pasan sin auth (no requieren identidad).
    - /app, /api/mensaje, /api/audio, /api/eventos exigen credenciales.
    - Tras autenticar, guarda user_id en request.state para que el
      endpoint lo use.
    - En dev mode (ningun usuario tiene password configurada) deja pasar
      sin auth pero avisa por consola.
    """
    path = request.url.path
    protegida = any(path.startswith(p) for p in _RUTAS_PROTEGIDAS_PREFIJOS)
    if not protegida:
        return await call_next(request)

    # Dev mode: si NINGUN usuario tiene password, aceptamos sin auth y
    # asignamos un user_id por defecto (el primero disponible). Util
    # cuando pruebas en local sin haber configurado el .env todavia.
    hay_algun_password = any(u.get("app_password") for u in USUARIOS_POR_USERNAME.values())
    if not hay_algun_password:
        logger.warn(
            "server", "auth_dev_mode",
            "Ningun APP_PASSWORD_* configurado. Auth desactivada (dev mode).",
            metadata={"path": path},
        )
        # Asignamos user_id por defecto: prefiere 'carles' (dev), luego 'alexander'
        request.state.user_id = "carles" if "carles" in USUARIOS_POR_USERNAME else "alexander"
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
        username, _, password = decoded.partition(":")
    except Exception:
        return StarletteResponse(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Syncrosfera"'},
        )

    usuario = autenticar_web(username, password)
    if usuario is None:
        return StarletteResponse(
            content="Credenciales invalidas",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Syncrosfera"'},
        )

    request.state.user_id = usuario["user_id"]
    return await call_next(request)


def _user_id_de(request: Request) -> str:
    """Extrae user_id del request state. Solo valido dentro de rutas
    protegidas por el middleware. Si por error se llama desde una ruta
    abierta, cae a 'carles' como default (nunca deberia ocurrir en el
    flujo normal — es defensivo)."""
    return getattr(request.state, "user_id", "carles")


# ---- Modelos Pydantic -------------------------------------------------------

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

class TareaResumen(BaseModel):
    """Tarea del EOS serializada para el frontend.

    Refleja Tarea.to_llm_dict() de models.py pero sin la sentinela textual
    [NO DATA]: en el JSON del API preferimos null para que el frontend haga
    los checks con `if (tarea.field)` de forma idiomatica. La sentinela
    solo tiene sentido dentro del contexto del LLM.
    """
    id: int
    title: str
    status_eos: str | None = None
    task_type: str | None = None
    alexander_role: str | None = None
    deadline: str | None = None
    next_action: str | None = None
    expected_result: str | None = None
    review_date: str | None = None
    source: str | None = None
    risk: str | None = None
    escalation_condition: str | None = None
    requires_conversation: bool | None = None
    primary_interlocutor: str | None = None
    conversation_purpose: str | None = None
    expected_decision: str | None = None
    meeting_candidate: bool | None = None
    related_meeting_id: str | None = None


class ListaTareas(BaseModel):
    tareas: list[TareaResumen]
    total_disponibles: int
    truncado: bool = False


class CambioEstadoTarea(BaseModel):
    """Payload para PATCH /api/tareas/{id}/estado.

    Los campos opcionales acompañan al cambio de estado: p.ej. delegar
    exige owner + expected_result + review_date + escalation_condition.
    El frontend los pasa juntos y el backend los aplica todos en una
    llamada Bitrix (misma semantica que la tool actualizar_estado_tarea
    del agente, pero saltandose el LLM porque es accion directa).
    """
    nuevo_estado: str
    owner: str | None = None
    next_action: str | None = None
    expected_result: str | None = None
    review_date: str | None = None
    deadline: str | None = None
    escalation_condition: str | None = None


# ---- Root redirect ---------------------------------------------------------

@app.get("/")
async def root_redirect():
    """La raiz redirige a /app (donde vive la mini-app React).

    Evita que un curl o un click en la URL raiz devuelva 404 y da una
    experiencia limpia al abrir el dominio sin sufijo. La proteccion
    Basic Auth de /app la aplica el middleware al hacer follow del 302.
    """
    return RedirectResponse(url="/app", status_code=302)


# ---- Health ----------------------------------------------------------------

@app.get("/health")
async def health():
    """Ping simple para monitorizar que el servicio esta vivo. Sin auth."""
    return {"status": "ok"}


# ---- Pipeline compartido: texto -> agente -> respuesta ---------------------

async def _procesar_y_detectar_cambios(user_id: str, texto: str) -> RespuestaAgente:
    """Envuelve agent.procesar_input y detecta si la agenda cambio.

    Deteccion heuristica: buscamos frases tipicas en la respuesta que
    indican que el agente ejecuto algo. No es 100% preciso pero acierta
    el 95% de las veces. En el peor caso, refrescamos de mas.

    Alternativa mas robusta a futuro: que confirmar_operaciones_pendientes
    guarde un flag en un modulo compartido indexado por user_id.
    """
    reply = await agent.procesar_input(user_id, texto)

    senales = [
        "he creado", "he movido", "he eliminado", "he cancelado",
        "he agendado", "he confirmado", "he reprogramado", "he actualizado",
        "operacion completada", "operación completada",
        "operaciones completadas", "he registrado",
    ]
    reply_lower = reply.lower()
    modificada = any(s in reply_lower for s in senales)

    return RespuestaAgente(reply=reply, agenda_modificada=modificada)


# ---- Endpoints principales -------------------------------------------------

@app.post("/api/mensaje", response_model=RespuestaAgente)
async def mensaje_texto(payload: MensajeTexto, request: Request):
    """Recibe un mensaje escrito y devuelve la respuesta del agente,
    procesado contra el contexto del usuario autenticado."""
    user_id = _user_id_de(request)

    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacio")

    try:
        return await _procesar_y_detectar_cambios(user_id, payload.text)
    except Exception as e:
        logger.error(
            "server", "request_error",
            f"Error procesando mensaje texto: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={"endpoint": "/api/mensaje", "texto_input": (payload.text or "")[:500]},
            error=e,
        )
        raise HTTPException(status_code=500, detail="Error procesando el mensaje")


@app.post("/api/audio", response_model=RespuestaAgente)
async def mensaje_audio(request: Request, audio: UploadFile = File(...)):
    """Recibe un audio del navegador, lo transcribe con Gemini, y procesa
    el texto resultante por el mismo pipeline que un mensaje escrito.
    Contra el contexto del usuario autenticado."""
    user_id = _user_id_de(request)

    audio_bytes = await audio.read()
    mime_type = audio.content_type or "audio/webm"

    try:
        texto = await voice.transcribir(audio_bytes, mime_type=mime_type)
        logger.info(
            "server", "audio_transcribed",
            f"Audio transcrito: '{texto[:200]}'",
            user_id=user_id,
            metadata={"mime_type": mime_type, "bytes": len(audio_bytes)},
        )
    except Exception as e:
        logger.error(
            "server", "audio_transcription_error",
            f"Error transcribiendo audio: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={"mime_type": mime_type, "bytes": len(audio_bytes)},
            error=e,
        )
        raise HTTPException(status_code=500, detail="No he podido entender el audio")

    # Filtro anti-basura (mismo criterio que bot.py)
    texto_limpio = texto.strip()
    if len(texto_limpio) < 3 or texto_limpio in {"00:00", "0:00", "...", "…"}:
        logger.info(
            "server", "audio_filtered_as_noise",
            f"Audio transcrito vacio o irrelevante ('{texto_limpio}'), ignorando",
            user_id=user_id,
            metadata={"texto_transcrito": texto_limpio},
        )
        return RespuestaAgente(
            reply="No he entendido el audio, intentalo de nuevo por favor.",
            agenda_modificada=False,
        )

    try:
        return await _procesar_y_detectar_cambios(user_id, texto_limpio)
    except Exception as e:
        logger.error(
            "server", "request_error",
            f"Error procesando audio->texto: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={"endpoint": "/api/audio", "texto_input": texto_limpio[:500]},
            error=e,
        )
        raise HTTPException(status_code=500, detail="Error procesando el mensaje")


@app.post("/api/tts")
async def sintetizar_voz(payload: MensajeTTS):
    """Convierte un texto en audio WAV usando Gemini TTS.

    Free tier: 10 llamadas al dia. El frontend cachea el blob por mensaje
    para no gastar cuota al pulsar play varias veces sobre el mismo.

    NO requiere auth: el texto que se sintetiza es la respuesta del
    agente que el propio frontend ya tiene, no filtra datos ajenos.
    """
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="El texto no puede estar vacio")

    if len(payload.text) > 5000:
        # Limite arbitrario para no gastar tokens en respuestas larguisimas.
        raise HTTPException(status_code=413, detail="Texto demasiado largo para TTS")

    try:
        wav_bytes = await tts.sintetizar(payload.text)
    except Exception as e:
        mensaje = str(e).lower()
        if "429" in mensaje or "resource_exhausted" in mensaje or "quota" in mensaje:
            logger.warn(
                "server", "tts_quota_exhausted",
                f"Cuota TTS agotada: {e}",
                metadata={"texto_len": len(payload.text)},
                error=e,
            )
            raise HTTPException(status_code=429, detail="Cuota diaria de TTS agotada")
        logger.error(
            "server", "tts_error",
            f"Error TTS: {type(e).__name__}: {e}",
            metadata={"texto_len": len(payload.text)},
            error=e,
        )
        raise HTTPException(status_code=500, detail="Error generando el audio")

    return Response(content=wav_bytes, media_type="audio/wav")


# ---- /api/eventos ----------------------------------------------------------

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
    request: Request,
    desde: str | None = Query(None, description="ISO 8601, ej: 2026-08-03T00:00:00"),
    hasta: str | None = Query(None, description="ISO 8601, ej: 2026-08-10T00:00:00"),
):
    """Devuelve los eventos del calendario del usuario autenticado en el
    rango dado. Si no se pasan fechas, devuelve la semana en curso
    (lunes-domingo).

    El calendario que se lee es el del usuario que hizo el Basic Auth:
    si Carles se autentica ve su calendario, si Alexander se autentica
    ve el suyo. Nunca se mezclan.
    """
    user_id = _user_id_de(request)

    # Resolver contexto Bitrix del usuario autenticado
    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=500, detail=f"Usuario '{user_id}' no configurado.")
    webhook = usuario.get("webhook_bitrix", "")
    bitrix_uid = usuario.get("bitrix_user_id", 0)
    if not webhook or not bitrix_uid:
        raise HTTPException(
            status_code=500,
            detail=f"Usuario '{user_id}' sin contexto Bitrix (falta webhook o bitrix_user_id).",
        )

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
        raw = await consultar_ocupacion_bitrix(webhook, bitrix_uid, desde_dt, hasta_dt)
    except BitrixError as e:
        raise HTTPException(status_code=502, detail=f"Bitrix rechazo la peticion: {e}")

    # Normalizamos al formato que consume el frontend
    _IMPORTANCE_A_PRIORIDAD = {"high": "alta", "normal": "media", "low": "baja"}

    eventos = []
    eventos_sin_fecha = 0
    eventos_fecha_invalida = 0
    for e in raw:
        date_from = e.get("DATE_FROM")
        date_to = e.get("DATE_TO")
        if not date_from or not date_to:
            eventos_sin_fecha += 1
            continue
        try:
            fi = _parsear_fecha_bitrix(date_from)
            ff = _parsear_fecha_bitrix(date_to)
        except ValueError as err:
            eventos_fecha_invalida += 1
            logger.warn(
                "server", "event_invalid_date",
                f"Fecha invalida en evento {e.get('ID', '?')}: {err}",
                user_id=user_id,
                metadata={
                    "bitrix_id": str(e.get("ID", "")),
                    "date_from": date_from,
                    "date_to": date_to,
                },
            )
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

    # Un unico log agregado al final del batch en vez de un print por
    # evento skipeado, para no spamear stdout ni la tabla app_logs.
    if eventos_sin_fecha or eventos_fecha_invalida:
        logger.info(
            "server", "events_skipped_summary",
            f"Eventos skipeados: {eventos_sin_fecha} sin fechas, "
            f"{eventos_fecha_invalida} con fecha invalida",
            user_id=user_id,
            metadata={
                "total_raw": len(raw),
                "sin_fecha": eventos_sin_fecha,
                "fecha_invalida": eventos_fecha_invalida,
                "aceptados": len(eventos),
            },
        )

    return ListaEventos(eventos=eventos)


# ---- /api/usage ------------------------------------------------------------

@app.get("/api/usage")
async def obtener_uso(
    user_id: str | None = Query(None, description="Filtra por usuario. Si se omite, agrega todos."),
    desde: str | None = Query(None, description="ISO 8601, ej: 2026-08-01T00:00:00"),
    hasta: str | None = Query(None, description="ISO 8601, ej: 2026-08-31T23:59:59"),
):
    """Totales agregados de consumo de tokens y coste USD.

    Sin params, devuelve el total historico agregado de todos los usuarios.
    Con user_id="carles" o user_id="alexander" filtra por usuario.
    Con rango, filtra por fecha.

    El campo cache_hit_ratio indica cuanto del input viene del cache:
    >0.5 significa que el prompt caching esta pegando bien en conversaciones
    de multiples turnos. Si es 0, el cache no esta funcionando.

    NO requiere Basic Auth: es un endpoint de metricas agregadas.
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
        logger.error(
            "server", "usage_query_error",
            f"Error consultando usage: {type(e).__name__}: {e}",
            user_id=user_id,
            metadata={
                "desde": desde_dt.isoformat() if desde_dt else None,
                "hasta": hasta_dt.isoformat() if hasta_dt else None,
            },
            error=e,
        )
        raise HTTPException(status_code=500, detail="Error consultando uso")

def _tarea_a_resumen(t) -> TareaResumen:
    """Convierte models.Tarea -> TareaResumen. Los None se conservan
    como None en el JSON (no como '[NO DATA]', que es solo para el LLM).
    """
    return TareaResumen(
        id=t.id or 0,
        title=t.title,
        status_eos=t.status_eos.value if t.status_eos else None,
        task_type=t.task_type.value if t.task_type else None,
        alexander_role=t.alexander_role.value if t.alexander_role else None,
        deadline=t.deadline.isoformat() if t.deadline else None,
        next_action=t.next_action,
        expected_result=t.expected_result,
        review_date=t.review_date.isoformat() if t.review_date else None,
        source=t.source,
        risk=t.risk,
        escalation_condition=t.escalation_condition,
        requires_conversation=t.requires_conversation,
        primary_interlocutor=t.primary_interlocutor,
        conversation_purpose=t.conversation_purpose,
        expected_decision=t.expected_decision,
        meeting_candidate=t.meeting_candidate,
        related_meeting_id=t.related_meeting_id,
    )


@app.get("/api/tareas", response_model=ListaTareas)
async def listar_tareas_endpoint(
    request: Request,
    estado: str | None = Query(None, description="Filtro EOS. Vacio: solo activas."),
    task_type: str | None = Query(None),
    primary_interlocutor: str | None = Query(None, description="Match exacto case-insensitive."),
    solo_activos: bool = Query(True, description="Excluye Completed/Cancelled. Ignorado si estado esta puesto."),
    limite: int = Query(100, ge=1, le=500),
):
    """Lista tareas del usuario autenticado con filtros opcionales.

    Va DIRECTO a bitrix_tasks.listar_tareas sin pasar por el agente:
    es una lectura, no hace falta LLM. Sprint 2 dejo el backend Tareas
    listo, este endpoint es la ventana HTTP para el frontend.

    Requiere Basic Auth (esta ruta protegida por el middleware).
    """
    user_id = _user_id_de(request)
    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None or not usuario.get("webhook_bitrix") or not usuario.get("bitrix_user_id"):
        raise HTTPException(status_code=500, detail=f"Usuario '{user_id}' sin contexto Bitrix configurado.")

    webhook = usuario["webhook_bitrix"]
    bitrix_uid = usuario["bitrix_user_id"]

    # Validacion de enum si viene
    estado_filter: EstadoEOS | None = None
    if estado:
        try:
            estado_filter = EstadoEOS(estado)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"estado invalido: '{estado}'. Debe ser uno de: "
                       f"{', '.join(e.value for e in EstadoEOS)}",
            )

    try:
        tareas = await bitrix_tasks.listar_tareas(
            webhook,
            filtro={"RESPONSIBLE_ID": bitrix_uid},
        )
    except Exception as e:
        logger.error(
            "server", "tareas_query_error",
            f"Error listando tareas: {type(e).__name__}: {e}",
            user_id=user_id,
            error=e,
        )
        raise HTTPException(status_code=502, detail=f"Bitrix rechazo la lectura: {e}")

    # Filtros client-side (Bitrix no filtra UF_* arbitrarios en tasks.task.list)
    if estado_filter is not None:
        tareas = [t for t in tareas if t.status_eos == estado_filter]
    elif solo_activos:
        # solo_activos combina dos fuentes de verdad para no dejar
        # tareas fantasma en el listado (bug Sprint 3.5, ver fix B):
        #   - Si status_eos esta puesto: excluir terminales EOS.
        #   - Si status_eos es None (tareas legacy sin UF_STATUS_EOS):
        #     mirar el STATUS nativo Bitrix. Solo consideramos activas
        #     las que Bitrix tambien considera "In progress" (statuses
        #     1/2/3). Awaiting control (4), Completed (5), Deferred (6)
        #     y Almost done (7) se filtran fuera.
        #   - Si tampoco tenemos STATUS nativo: incluimos por defecto
        #     para no ocultar trabajo (PHASE 1 §1.2). Deberia ser
        #     rarisimo, es fallback del fallback.
        from models import STATUS_BITRIX_ACTIVO
        terminales = {EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}
        def _es_activa(t) -> bool:
            if t.status_eos is not None:
                return t.status_eos not in terminales
            if t.status_bitrix_nativo is not None:
                return t.status_bitrix_nativo in STATUS_BITRIX_ACTIVO
            return True  # sin ninguna senyal, no ocultamos
        tareas = [t for t in tareas if _es_activa(t)]

    if task_type:
        tareas = [t for t in tareas if t.task_type and t.task_type.value == task_type]

    if primary_interlocutor:
        needle = primary_interlocutor.strip().lower()
        tareas = [
            t for t in tareas
            if t.primary_interlocutor and t.primary_interlocutor.strip().lower() == needle
        ]

    total_disponibles = len(tareas)
    tareas = tareas[:limite]

    return ListaTareas(
        tareas=[_tarea_a_resumen(t) for t in tareas],
        total_disponibles=total_disponibles,
        truncado=total_disponibles > limite,
    )


@app.patch("/api/tareas/{task_id}/estado")
async def actualizar_estado_tarea_endpoint(
    task_id: int,
    payload: CambioEstadoTarea,
    request: Request,
):
    """Cambia el estado EOS de una tarea. Valida transicion legal (§6.4)
    antes de aplicar. Va directo a Bitrix sin pasar por el agente.

    Semantica identica a la tool actualizar_estado_tarea del agente:
    si owner viene, se resuelve por user.search; si la transicion es
    ilegal, devuelve 409 con el motivo.
    """
    user_id = _user_id_de(request)
    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None or not usuario.get("webhook_bitrix"):
        raise HTTPException(status_code=500, detail=f"Usuario '{user_id}' sin contexto Bitrix.")

    webhook = usuario["webhook_bitrix"]

    # Validar enum destino
    try:
        estado_enum = EstadoEOS(payload.nuevo_estado)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"nuevo_estado invalido: '{payload.nuevo_estado}'. Debe ser uno de: "
                   f"{', '.join(e.value for e in EstadoEOS)}",
        )

    # Parsear review_date si viene
    review_dt: datetime | None = None
    if payload.review_date:
        try:
            review_dt = datetime.fromisoformat(payload.review_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"review_date invalida: '{payload.review_date}'")

    # Parsear deadline si viene
    deadline_dt: datetime | None = None
    if payload.deadline:
        try:
            deadline_dt = datetime.fromisoformat(payload.deadline)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"deadline invalida: '{payload.deadline}'")

    # Resolver owner si viene (fail loud si 0 o N matches)
    responsable_id: int | None = None
    if payload.owner and payload.owner.strip():
        # Reusa el resolver que ya vive en tools.py
        resuelto, error_msg = await tools._resolver_owner(webhook, payload.owner)
        if error_msg:
            raise HTTPException(status_code=400, detail=error_msg)
        responsable_id = resuelto

    # Fetch tarea actual para validar transicion
    try:
        tarea = await bitrix_tasks.obtener_tarea(webhook, task_id)
    except Exception as e:
        logger.error(
            "server", "tarea_fetch_error",
            f"No pude leer tarea {task_id}: {type(e).__name__}: {e}",
            user_id=user_id, error=e,
        )
        raise HTTPException(status_code=404, detail=f"No pude leer la tarea {task_id}: {e}")

    try:
        tarea.validar_transicion_a(estado_enum)
    except TransicionIlegal as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Construir cambios
    cambios: dict = {"status_eos": estado_enum}
    if payload.next_action is not None:
        cambios["next_action"] = payload.next_action
    if payload.expected_result is not None:
        cambios["expected_result"] = payload.expected_result
    if review_dt is not None:
        cambios["review_date"] = review_dt
    if deadline_dt is not None:
        cambios["deadline"] = deadline_dt
    if payload.escalation_condition is not None:
        cambios["escalation_condition"] = payload.escalation_condition

    try:
        await bitrix_tasks.actualizar_tarea(
            webhook, task_id, cambios,
            responsable_id=responsable_id,
        )
    except Exception as e:
        logger.error(
            "server", "tarea_update_error",
            f"Error actualizando tarea {task_id}: {type(e).__name__}: {e}",
            user_id=user_id, error=e,
        )
        raise HTTPException(status_code=502, detail=f"Error actualizando en Bitrix: {e}")

    return {
        "ok": True,
        "id": task_id,
        "estado_anterior": tarea.status_eos.value if tarea.status_eos else None,
        "estado_nuevo": estado_enum.value,
        "responsable_id": responsable_id,
    }


# ---- /api/brief ------------------------------------------------------------
# Executive Brief diario (Sprint 3, PHASE 1 §4).

@app.get("/api/brief")
async def obtener_brief(
    request: Request,
    fecha: str | None = Query(None, description="YYYY-MM-DD. Si vacio, hoy."),
):
    """Devuelve el Executive Brief del usuario autenticado para el dia
    indicado (por defecto hoy). Se regenera en cada llamada — no hay
    cache. El coste dominante es la sintesis LLM (~2s + ~500 tokens).

    Response: BriefEjecutivo (13 secciones + metadata). El frontend lo
    consume tal cual desde useBrief.

    Requiere Basic Auth (ruta protegida por el middleware).
    """
    user_id = _user_id_de(request)
    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None or not usuario.get("webhook_bitrix"):
        raise HTTPException(status_code=500, detail=f"Usuario '{user_id}' sin contexto Bitrix.")

    fecha_dt: datetime | None = None
    if fecha:
        try:
            fecha_dt = datetime.fromisoformat(fecha)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"fecha invalida: '{fecha}' (usa YYYY-MM-DD)")

    try:
        b = await brief_engine.generar_brief(user_id, fecha_ref=fecha_dt)
    except Exception as e:
        logger.error(
            "server", "brief_generation_error",
            f"Fallo generar_brief: {type(e).__name__}: {e}",
            user_id=user_id, error=e,
        )
        raise HTTPException(status_code=502, detail=f"Error generando el brief: {e}")

    return b.model_dump()



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
        """Sirve index.html para cualquier ruta bajo /app.
        La proteccion Basic Auth la aplica el middleware antes de llegar aqui.
        """
        return FileResponse(str(FRONTEND_DIST / "index.html"))
else:
    logger.warn(
        "server", "frontend_dist_missing",
        "frontend/dist no existe. En dev usa 'npm run dev' aparte.",
        metadata={"expected_path": str(FRONTEND_DIST)},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)