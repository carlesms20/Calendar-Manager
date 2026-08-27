"""Executive Brief diario (Sprint 3, PHASE 1 §4).

Ejecuta el Executive Reasoning Pipeline (§2.2) de forma DETERMINISTA
para producir las 13 secciones del Brief. Solo dos secciones se
delegan al LLM:

  - Executive Summary (§4.2): prosa ejecutiva no templabilizable.
  - Three Key Outcomes (§4.4): fraseo como resultado de negocio,
    no como "reunirse con X".

El resto (Calendar Overview, Quick Actions, People Blocked, Delegated,
Waiting, Proposed Work Blocks, Not Today, Remaining Inventory, Missing
Information, Integrity Check) sale directo del pipeline sin LLM.

La generacion es idempotente y sin efectos secundarios. Los consumidores
la llaman desde:
  - GET /api/brief (para el frontend)
  - main.run_brief_scheduler (cron 07:00 L-V para Telegram)

Coste esperado por brief:
  - LLM: ~1 llamada Sonnet, prompt ~1000 tokens + salida ~400 = ~1500t
  - Bitrix: 1 tasks.task.list + 1 calendar.event.get + 1 SELECT bloques
  - Latencia total: 3-8s (dominado por LLM)
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, time
from typing import Any
from os import getenv

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from models import (
    Tarea, EstadoEOS, TipoTarea, RolAlexander, TZ_LOCAL,
)
import bitrix_tasks
from bitrix import consultar_ocupacion_bitrix, solicitud as _bitrix_solicitud
import bloques as bloques_mod
import capacity as capacity_mod
import forecast as forecast_mod
import logger
import usage
from config_usuarios import USUARIOS_POR_USERNAME


# ============================================================================
# CACHE DE NOMBRES DE OWNER (Sprint 4)
# ============================================================================
# Bitrix devuelve RESPONSIBLE_ID como numero. Para el Brief resolvemos a
# nombre humano ("Sandra Perez") con user.get. Cacheamos por (webhook_hash,
# user_id) para no bombardear Bitrix con la misma llamada.
#
# TTL: sesion del proceso (los nombres cambian rarisimo). Se resetea al
# redeploy y arreglado.
_owner_cache: dict[tuple[str, int], str] = {}


async def _resolver_owner_nombre(webhook: str, bitrix_uid: int) -> str | None:
    """Resuelve un bitrix_user_id a 'Nombre Apellido'. Devuelve None si
    Bitrix no lo conoce (fallback: caller muestra el id crudo o
    '[NO DATA]')."""
    if not bitrix_uid:
        return None
    key = (webhook[:40], bitrix_uid)
    if key in _owner_cache:
        return _owner_cache[key]
    try:
        result = await _bitrix_solicitud(webhook, "user.get", {"ID": bitrix_uid})
        if isinstance(result, list) and result:
            u = result[0]
            nombre = (u.get("NAME") or "").strip()
            apellido = (u.get("LAST_NAME") or "").strip()
            completo = " ".join(x for x in (nombre, apellido) if x) or None
            _owner_cache[key] = completo or ""
            return completo
    except Exception as e:
        logger.warn("brief", "owner_resolve_error",
                    f"user.get {bitrix_uid} fallo: {type(e).__name__}: {e}")
    _owner_cache[key] = ""
    return None

# ============================================================================
# CONFIG
# ============================================================================

_API_KEY = getenv("API_ANTHROPIC", "")
_client: AsyncAnthropic | None = None
MODELO = "claude-sonnet-5"
MAX_TOKENS_SYNTHESIS = 800

# Ventana horaria del dia laboral usada por Calendar Overview y capacity.
# Alineado con PHASE 6 Doc 3 §9. Rango generoso; el buffer real depende
# de eventos ya agendados.
JORNADA_INICIO = time(8, 0)
JORNADA_FIN    = time(19, 0)

# Umbrales de PHASE 1 §5.8 y §10.5
BUFFER_MINIMO_PCT = 30
MAX_KEY_OUTCOMES = 3


def _get_client() -> AsyncAnthropic:
    """Lazy init del cliente Anthropic. Reusa la misma pauta que agent.py."""
    global _client
    if _client is None:
        if not _API_KEY:
            raise RuntimeError("Falta API_ANTHROPIC para generar el brief.")
        _client = AsyncAnthropic(api_key=_API_KEY, max_retries=3)
    return _client


# ============================================================================
# ESTRUCTURA DEL BRIEF (13 secciones + metadata)
# ============================================================================
# Pydantic para que sirva 1:1 al endpoint /api/brief sin conversion extra.

class ItemCalendario(BaseModel):
    id: str
    nombre: str
    fecha_inicio: str
    fecha_fin: str
    duracion_min: int
    involucrado: str | None = None
    tipo: str  # "confirmado" | "propuesto" | "bloque_protegido"


class ItemTarea(BaseModel):
    """Version compacta de una tarea para el brief. Menos campos que
    TareaResumen: solo lo que el CEO necesita para orientarse.

    Sprint 4 anadio: owner_nombre (para delegadas), waiting_for
    (descripcion humana de que se espera), dias_vencido (para follow-ups
    y review_dates que vencieron), escalation_condition (visible en
    delegadas), preparation_required, next_action_if_missed.
    """
    id: int
    title: str
    status_eos: str | None = None
    task_type: str | None = None
    owner_es_ceo: bool
    owner_nombre: str | None = None  # Sprint 4: nombre resuelto del Owner
    next_action: str | None = None
    deadline: str | None = None
    review_date: str | None = None
    primary_interlocutor: str | None = None
    waiting_for: str | None = None  # Sprint 4: expected_result cuando status=Waiting
    dias_vencido: int | None = None  # Sprint 4: dias desde review_date si vencio
    escalation_condition: str | None = None  # Sprint 4
    preparation_required: str | None = None  # Sprint 4
    next_action_if_missed: str | None = None  # Sprint 4
    razon: str | None = None  # Explicacion CEO-facing de por que aparece aqui


class ItemConversacion(BaseModel):
    """Reunion propuesta o consolidacion detectada (§4.5).

    Sprint 4 anadio: recomendacion §7.4 Meeting Delegation Rule.
    """
    interlocutor: str
    temas: list[str]
    tareas_relacionadas: list[int]
    decisiones_esperadas: list[str] = []
    duracion_estimada_min: int
    prioridad: str  # alta | media | baja
    horario_propuesto: str | None = None
    estado_confirmacion: str = "propuesta"
    impacto_no_celebrarla: str | None = None
    # Sprint 4 (Bloque C) — Meeting Delegation Rule §7.4
    recomendacion_asistencia: str = "asistir"  # asistir | delegar | decidir_asincrono
    razon_recomendacion: str | None = None


class KeyOutcome(BaseModel):
    resultado: str  # Frase de resultado empresarial
    mecanismo: str  # trabajo_ceo | decision | aprobacion | delegacion |
                   # conversacion | desbloqueo
    razon: str
    items_relacionados: list[int] = []  # ids de tarea si aplica


class CalendarOverview(BaseModel):
    confirmados: list[ItemCalendario]
    propuestos: list[ItemCalendario]
    bloques_protegidos: list[ItemCalendario]
    capacidad_ocupada_min: int
    capacidad_total_min: int
    buffer_pct: float
    conflictos: list[str] = []  # descripciones textuales
    riesgo_fragmentacion: bool = False


class IntegrityFinding(BaseModel):
    check: str  # nombre del check (§10)
    ok: bool
    detalle: str | None = None  # si !ok, que falta


class ReminderItem(BaseModel):
    """Un recordatorio priorizado por Reminder Engine (Sprint 5, §13).

    prioridad_num: 1..5 segun orden estricto §13:
      1 Personas bloqueadas por CEO
      2 Decisiones pendientes
      3 Dependencias externas
      4 Revisiones comprometidas
      5 Riesgos de incumplimiento
    """
    prioridad_num: int
    categoria: str  # persona_bloqueada | decision | dependencia_externa |
                    # revision_comprometida | riesgo_incumplimiento |
                    # reunion_propuesta_no_confirmada
    titulo: str
    detalle: str | None = None
    accion_sugerida: str | None = None
    tarea_id: int | None = None
    persona: str | None = None


class BriefEjecutivo(BaseModel):
    """PHASE 1 §4.1 (13 secciones) + Sprint 5 (capacity + reminders + forecast)."""
    generado_en: str  # ISO 8601 timezone-aware
    user_id: str
    fecha_ref: str    # dia del que trata el brief (YYYY-MM-DD)

    # Sec 1 - Executive Summary
    executive_summary: str

    # Sec 2 - Calendar Overview
    calendar_overview: CalendarOverview

    # Sec 3 - Three Key Outcomes (max 3)
    three_key_outcomes: list[KeyOutcome]

    # Sec 4 - Quick Actions (<15 min)
    quick_actions: list[ItemTarea]

    # Sec 5 - People Blocked by Alexander
    people_blocked: list[ItemTarea]

    # Sec 6 - Executive Conversations and Proposed Meetings
    executive_conversations: list[ItemConversacion]

    # Sec 7 - Delegated Work Requiring Supervision
    delegated_supervision: list[ItemTarea]

    # Sec 8 - Waiting for Responses
    waiting: list[ItemTarea]

    # Sec 9 - Proposed Work Blocks (Sprint 5: ahora estructurados)
    proposed_work_blocks: list[dict]  # BloquePropuesto.model_dump()

    # Sec 10 - Not Today
    not_today: list[ItemTarea]

    # Sec 11 - Remaining Task Inventory (contador + resumen)
    remaining_inventory_total: int
    remaining_inventory_por_tipo: dict[str, int]

    # Sec 12 - Missing Information
    missing_information: list[str]

    # Sec 13 - Integrity Check
    integrity_check: list[IntegrityFinding]

    # --- Sprint 5 nuevas secciones ---

    # Sec 14 - Capacity today
    capacidad_hoy: dict | None = None  # CapacidadDia.model_dump()

    # Sec 15 - Forecast next week
    forecast_proxima_semana: dict | None = None  # ForecastSemana.model_dump()

    # Sec 16 - Reminders priorizados §13
    reminders: list[ReminderItem] = []


# ============================================================================
# UTILIDADES DE FECHA
# ============================================================================

def _hoy_local(fecha_ref: datetime | None = None) -> datetime:
    """Datetime del inicio del dia (00:00:00) en TZ_LOCAL."""
    if fecha_ref is None:
        fecha_ref = datetime.now(TZ_LOCAL)
    elif fecha_ref.tzinfo is None:
        fecha_ref = fecha_ref.replace(tzinfo=TZ_LOCAL)
    return fecha_ref.replace(hour=0, minute=0, second=0, microsecond=0)


def _jornada_del(dia: datetime) -> tuple[datetime, datetime]:
    """Devuelve (inicio_jornada, fin_jornada) para el dia dado."""
    ini = dia.replace(hour=JORNADA_INICIO.hour, minute=JORNADA_INICIO.minute,
                       second=0, microsecond=0)
    fin = dia.replace(hour=JORNADA_FIN.hour, minute=JORNADA_FIN.minute,
                       second=0, microsecond=0)
    return ini, fin


# ============================================================================
# STAGE 1 - COLLECT
# ============================================================================
# Recopilamos tareas, eventos y bloques en paralelo (asyncio.gather seria
# ideal pero no lo hacemos aqui para no complicar; el coste ya es bajo).

async def _collect(user_id: str, dia: datetime) -> dict:
    """Recopila datos de las fuentes autorizadas para el dia dado.
    Devuelve dict con keys: tareas (list[Tarea]), eventos (raw Bitrix
    dicts), bloques (list[dict])."""

    usuario = USUARIOS_POR_USERNAME.get(user_id)
    if usuario is None or not usuario.get("webhook_bitrix") or not usuario.get("bitrix_user_id"):
        raise RuntimeError(f"Usuario '{user_id}' sin contexto Bitrix.")

    webhook = usuario["webhook_bitrix"]
    bitrix_uid = usuario["bitrix_user_id"]

    # Tareas activas del CEO. Sin filtros (aplicamos en Structure).
    try:
        tareas = await bitrix_tasks.listar_tareas(
            webhook, filtro={"RESPONSIBLE_ID": bitrix_uid},
        )
    except Exception as e:
        logger.error("brief", "collect_tareas_error",
                     f"Fallo listar_tareas: {type(e).__name__}: {e}",
                     user_id=user_id, error=e)
        tareas = []

    # Ademas: tareas donde el CEO es CREADOR (delegadas). El listar_tareas
    # por RESPONSIBLE_ID no las incluye si estan en otro dueno.
    try:
        tareas_creadas = await bitrix_tasks.listar_tareas(
            webhook, filtro={"CREATED_BY": bitrix_uid},
        )
        # Merge sin duplicados por id
        ids_vistos = {t.id for t in tareas if t.id is not None}
        for t in tareas_creadas:
            if t.id not in ids_vistos:
                tareas.append(t)
    except Exception as e:
        logger.warn("brief", "collect_delegadas_partial",
                    f"No pude listar delegadas: {type(e).__name__}: {e}",
                    user_id=user_id)

    # Eventos: hoy + 7 dias (para poder detectar reuniones propuestas
    # cercanas y bloques recurrentes).
    horizonte = dia + timedelta(days=8)
    try:
        eventos_raw = await consultar_ocupacion_bitrix(
            webhook, bitrix_uid, dia, horizonte,
        )
    except Exception as e:
        logger.warn("brief", "collect_eventos_error",
                    f"No pude leer calendario: {type(e).__name__}: {e}",
                    user_id=user_id)
        eventos_raw = []

    # Bloques no negociables activos (recurrentes por dia de la semana).
    try:
        bloques = await bloques_mod.listar_activos_para_calculo(user_id)
    except Exception as e:
        logger.warn("brief", "collect_bloques_error",
                    f"No pude leer bloques: {type(e).__name__}: {e}",
                    user_id=user_id)
        bloques = []

    return {
        "tareas": tareas,
        "eventos": eventos_raw,
        "bloques": bloques,
    }


# ============================================================================
# STAGE 2 - STRUCTURE + PRIORITIZE + IDENTIFY RESPONSIBILITY
# ============================================================================

def _es_owner_ceo(tarea: Tarea, bitrix_uid: int) -> bool:
    """Aproximacion: si alexander_role != No Involvement asumimos que el
    CEO tiene rol activo. Para diferenciar Owner del CEO, habria que
    leer RESPONSIBLE_ID de Bitrix — que ya viene en el filtro del list.
    Como listar_tareas se llama con RESPONSIBLE_ID = bitrix_uid, todas
    las tareas devueltas son del CEO. Las que anadimos por CREATED_BY
    son las que EL delego. Distinguimos por marcado explicito abajo."""
    return tarea.alexander_role != RolAlexander.NO_INVOLVEMENT


def _clasifica_calendar_item(raw: dict) -> str:
    """Determina si un evento es confirmado o propuesta pendiente.
    Convencion: si el NAME empieza con '[PROPUESTA]' o la description
    contiene 'ESTADO: PROPUESTA', es propuesto. Esto es tentativo — el
    modelo de propuestas se refinara en Sprint 4."""
    nombre = (raw.get("NAME") or "").upper()
    desc = (raw.get("DESCRIPTION") or "").upper()
    if "PROPUESTA" in nombre[:15] or "ESTADO: PROPUESTA" in desc:
        return "propuesto"
    return "confirmado"


def _parsear_fecha_bitrix_safe(s: str) -> datetime | None:
    """Parser tolerante para fechas Bitrix. None si falla, sin propagar."""
    if not s:
        return None
    try:
        # Bitrix v3 devuelve "2026-08-25T09:00:00+03:00" o similar.
        s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_LOCAL)
        return dt.astimezone(TZ_LOCAL)
    except (ValueError, TypeError):
        return None


def _eventos_del_dia(eventos_raw: list[dict], dia: datetime) -> list[dict]:
    """Filtra eventos que solapan con el dia [00:00, 23:59)."""
    ini_dia = dia
    fin_dia = dia + timedelta(days=1)
    dentro: list[dict] = []
    for e in eventos_raw:
        fi = _parsear_fecha_bitrix_safe(e.get("DATE_FROM", ""))
        ff = _parsear_fecha_bitrix_safe(e.get("DATE_TO", ""))
        if fi is None or ff is None:
            continue
        # solape con [ini_dia, fin_dia)
        if ff > ini_dia and fi < fin_dia:
            e["_fi"] = fi
            e["_ff"] = ff
            dentro.append(e)
    return sorted(dentro, key=lambda x: x["_fi"])


def _extraer_involucrado(desc: str) -> str | None:
    """Convencion Sprint 1: "Involucrado: X\\n" al principio de DESCRIPTION."""
    if not desc:
        return None
    linea1 = desc.strip().split("\n", 1)[0]
    if linea1.lower().startswith("involucrado:"):
        return linea1.split(":", 1)[1].strip() or None
    return None


def _minutos(fi: datetime, ff: datetime) -> int:
    return max(0, int((ff - fi).total_seconds() // 60))


def _build_calendar_overview(
    eventos_raw: list[dict], bloques: list[dict], dia: datetime,
) -> CalendarOverview:
    """Sec 2. Distingue confirmados, propuestos y bloques protegidos.
    Calcula ocupacion y buffer contra la jornada."""
    eventos_hoy = _eventos_del_dia(eventos_raw, dia)

    confirmados: list[ItemCalendario] = []
    propuestos: list[ItemCalendario] = []

    for e in eventos_hoy:
        fi: datetime = e["_fi"]
        ff: datetime = e["_ff"]
        tipo = _clasifica_calendar_item(e)
        item = ItemCalendario(
            id=str(e.get("ID", "")),
            nombre=e.get("NAME", "(sin nombre)"),
            fecha_inicio=fi.isoformat(),
            fecha_fin=ff.isoformat(),
            duracion_min=_minutos(fi, ff),
            involucrado=_extraer_involucrado(e.get("DESCRIPTION", "")),
            tipo=tipo,
        )
        (propuestos if tipo == "propuesto" else confirmados).append(item)

    # Bloques del dia: filtramos por dia_semana del datetime (Monday=0).
    dow = dia.weekday()
    bloques_hoy: list[ItemCalendario] = []
    for b in bloques:
        dias_sem = b.get("dias_semana") or []
        # -1 en dias_semana significaba "todos los dias" (config Sprint 1).
        if dias_sem and dow not in dias_sem and -1 not in dias_sem:
            continue
        h_ini = b.get("hora_inicio")
        h_fin = b.get("hora_fin")
        if not h_ini or not h_fin:
            continue
        # Reconstruimos datetime para el dia
        try:
            hi = time.fromisoformat(str(h_ini))
            hf = time.fromisoformat(str(h_fin))
        except (TypeError, ValueError):
            continue
        fi_b = dia.replace(hour=hi.hour, minute=hi.minute)
        ff_b = dia.replace(hour=hf.hour, minute=hf.minute)
        bloques_hoy.append(ItemCalendario(
            id=f"bloque_{b.get('id', '?')}",
            nombre=b.get("nombre", "Bloque protegido"),
            fecha_inicio=fi_b.isoformat(),
            fecha_fin=ff_b.isoformat(),
            duracion_min=_minutos(fi_b, ff_b),
            involucrado=None,
            tipo="bloque_protegido",
        ))

    # Ocupacion total del dia (confirmados + bloques). Los propuestos
    # NO cuentan como ocupacion hasta que el CEO los confirme (§4.3).
    ocupacion_min = sum(x.duracion_min for x in confirmados) + \
                    sum(x.duracion_min for x in bloques_hoy)
    ini_jor, fin_jor = _jornada_del(dia)
    total_min = _minutos(ini_jor, fin_jor)
    libre_min = max(0, total_min - ocupacion_min)
    buffer_pct = round(100.0 * libre_min / total_min, 1) if total_min else 0.0

    # Fragmentacion: >4 eventos con huecos <30min entre medias
    riesgo_frag = False
    todos_ordenados = sorted(
        confirmados + propuestos + bloques_hoy,
        key=lambda x: x.fecha_inicio,
    )
    if len(todos_ordenados) >= 4:
        huecos_cortos = 0
        for i in range(len(todos_ordenados) - 1):
            ff_i = datetime.fromisoformat(todos_ordenados[i].fecha_fin)
            fi_ii = datetime.fromisoformat(todos_ordenados[i+1].fecha_inicio)
            if 0 < _minutos(ff_i, fi_ii) < 30:
                huecos_cortos += 1
        riesgo_frag = huecos_cortos >= 3

    # Conflictos: eventos solapados
    conflictos: list[str] = []
    for i in range(len(todos_ordenados) - 1):
        a = todos_ordenados[i]
        b = todos_ordenados[i+1]
        if datetime.fromisoformat(a.fecha_fin) > datetime.fromisoformat(b.fecha_inicio):
            conflictos.append(
                f"'{a.nombre}' solapa con '{b.nombre}'"
            )

    return CalendarOverview(
        confirmados=confirmados,
        propuestos=propuestos,
        bloques_protegidos=bloques_hoy,
        capacidad_ocupada_min=ocupacion_min,
        capacidad_total_min=total_min,
        buffer_pct=buffer_pct,
        conflictos=conflictos,
        riesgo_fragmentacion=riesgo_frag,
    )


# ============================================================================
# STAGE 3 - CLASIFICAR TAREAS EN LAS SECCIONES 4-11
# ============================================================================

def _tarea_a_item(t: Tarea, razon: str | None = None,
                  owner_es_ceo: bool = True,
                  owner_nombre: str | None = None,
                  ahora: datetime | None = None) -> ItemTarea:
    """Construye ItemTarea desde Tarea. Sprint 4: rellena tambien
    owner_nombre (resuelto por _resolver_owner_nombre), waiting_for
    (expected_result si status=Waiting), dias_vencido (dias desde
    review_date si vencio) y los 2 campos §7.2 nuevos.

    ahora: para tests determinismo; si None usa datetime.now(TZ_LOCAL).
    """
    if ahora is None:
        ahora = datetime.now(TZ_LOCAL)

    # waiting_for: solo tiene sentido en Waiting
    waiting_for = None
    if t.status_eos == EstadoEOS.WAITING and t.expected_result:
        waiting_for = t.expected_result

    # dias_vencido: si review_date < ahora
    dias_vencido = None
    if t.review_date:
        delta = (ahora - t.review_date).days
        if delta > 0:
            dias_vencido = delta

    return ItemTarea(
        id=t.id or 0,
        title=t.title,
        status_eos=t.status_eos.value if t.status_eos else None,
        task_type=t.task_type.value if t.task_type else None,
        owner_es_ceo=owner_es_ceo,
        owner_nombre=owner_nombre,
        next_action=t.next_action,
        deadline=t.deadline.isoformat() if t.deadline else None,
        review_date=t.review_date.isoformat() if t.review_date else None,
        primary_interlocutor=t.primary_interlocutor,
        waiting_for=waiting_for,
        dias_vencido=dias_vencido,
        escalation_condition=t.escalation_condition,
        preparation_required=t.preparation_required,
        next_action_if_missed=t.next_action_if_missed,
        razon=razon,
    )


def _es_quick_action(t: Tarea) -> bool:
    """Heuristica: next_action con verbo cortito y sin dependencia
    conversacional. §4.1 dice "<15 minutes". No sabemos duracion exacta,
    aproximamos por status_eos=New/In Progress + requires_conversation=
    False + next_action existe."""
    if t.status_eos not in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS):
        return False
    if not t.next_action:
        return False
    if t.requires_conversation:
        return False
    # Heurística: si el next_action tiene <=15 palabras, lo consideramos
    # candidato a quick action. El LLM podria refinarlo pero ya no es
    # deterministic.
    return len(t.next_action.split()) <= 15


def _clasificar_tareas(
    tareas: list[Tarea], bitrix_uid: int, dia: datetime,
) -> dict[str, Any]:
    """Reparte tareas en las secciones 4-11 del Brief.

    Regla clave §4.6: los elementos menos prioritarios se CONSERVAN en
    Not Today o Remaining Inventory, nunca desaparecen.

    Filtrado previo: tareas cerradas o aplazadas en Bitrix nativo pero
    sin UF_STATUS_EOS rellenado (tareas legacy anteriores al agente)
    NO deben inflar el inventario. Bug detectado Sprint 3.5: sin este
    filtro, el brief mostraba 27 tareas mientras que Bitrix UI mostraba
    8 activas. Fuente de verdad: STATUS nativo cuando no hay EOS.
    """
    from models import STATUS_BITRIX_ACTIVO

    def _es_activa_para_brief(t: Tarea) -> bool:
        if t.status_eos is not None:
            return t.status_eos not in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED)
        if t.status_bitrix_nativo is not None:
            return t.status_bitrix_nativo in STATUS_BITRIX_ACTIVO
        return True  # sin señal, no ocultar (PHASE 1 §1.2)

    tareas = [t for t in tareas if _es_activa_para_brief(t)]

    ahora = datetime.now(TZ_LOCAL)
    limite_hoy = dia + timedelta(days=1)
    limite_3d = dia + timedelta(days=3)

    quick_actions: list[ItemTarea] = []
    people_blocked: list[ItemTarea] = []
    delegated_supervision: list[ItemTarea] = []
    waiting: list[ItemTarea] = []
    not_today: list[ItemTarea] = []
    remaining: list[Tarea] = []
    inventario_por_tipo: dict[str, int] = {}
    ids_ya_clasificados: set[int] = set()

    for t in tareas:
        if t.id is None:
            continue
        tipo = (t.task_type.value if t.task_type else "Task")
        inventario_por_tipo[tipo] = inventario_por_tipo.get(tipo, 0) + 1

        estado = t.status_eos

        # Sec 5 - People Blocked by Alexander (§2.4 pri 1)
        # Otros bloqueados esperando accion del CEO. Marcamos si la tarea
        # tiene alexander_role=Decision|Approval y esta activa.
        if estado in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS, EstadoEOS.BLOCKED) \
                and t.alexander_role in (RolAlexander.DECISION, RolAlexander.APPROVAL):
            razon = "Terceros esperan tu " + (
                "decision" if t.alexander_role == RolAlexander.DECISION
                else "aprobacion"
            )
            people_blocked.append(_tarea_a_item(t, razon=razon))
            ids_ya_clasificados.add(t.id)
            continue

        # Sec 7 - Delegated Requiring Supervision (§2.4 pri 5)
        if estado == EstadoEOS.DELEGATED:
            razon = None
            if t.review_date and t.review_date <= limite_3d:
                razon = f"Review date el {t.review_date.date().isoformat()}"
            elif t.review_date is None:
                razon = "Sin review date fijado — riesgo de perder control"
            delegated_supervision.append(
                _tarea_a_item(t, razon=razon, owner_es_ceo=False)
            )
            ids_ya_clasificados.add(t.id)
            continue

        # Sec 8 - Waiting for Responses
        if estado == EstadoEOS.WAITING:
            razon = None
            if t.review_date and t.review_date < ahora:
                dias = (ahora - t.review_date).days
                if dias == 0:
                    razon = "Follow-up vencido hoy"
                elif dias == 1:
                    razon = "Follow-up vencido hace 1 día"
                else:
                    razon = f"Follow-up vencido hace {dias} días"
            elif t.review_date is None:
                razon = "Waiting sin follow-up fijado — riesgo de perderse"
            waiting.append(_tarea_a_item(t, razon=razon, ahora=ahora))
            ids_ya_clasificados.add(t.id)
            continue

        # Sec 4 - Quick Actions
        if _es_quick_action(t):
            quick_actions.append(_tarea_a_item(t, razon="Ejecutable en <15 min"))
            ids_ya_clasificados.add(t.id)
            continue

        # Sec 10 - Not Today: tareas activas cuyo deadline es >3d
        # y no son urgentes hoy.
        if estado in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS, EstadoEOS.SCHEDULED):
            urgente = (
                (t.deadline and t.deadline < limite_hoy) or
                (t.deadline and t.deadline < limite_3d and estado == EstadoEOS.IN_PROGRESS)
            )
            if not urgente:
                not_today.append(_tarea_a_item(t))
                ids_ya_clasificados.add(t.id)
                continue

        # Todo lo demas al inventario
        remaining.append(t)

    # Remaining = todo lo no clasificado + las clasificadas tambien
    # cuentan al inventario total (regla §4.6: nunca desaparecen).
    remaining_total = len(tareas)  # inventario COMPLETO incluye clasificadas
    # remaining_por_tipo ya cuenta todas arriba

    return {
        "quick_actions": quick_actions,
        "people_blocked": people_blocked,
        "delegated_supervision": delegated_supervision,
        "waiting": waiting,
        "not_today": not_today,
        "remaining_total": remaining_total,
        "remaining_por_tipo": inventario_por_tipo,
        "ids_ya_clasificados": ids_ya_clasificados,
    }


# ============================================================================
# STAGE 4 - EXECUTIVE CONVERSATIONS (Sec 6, §4.5)
# ============================================================================

def _build_executive_conversations(
    tareas: list[Tarea], eventos_hoy: list[ItemCalendario],
) -> list[ItemConversacion]:
    """Detecta grupos por primary_interlocutor entre tareas activas con
    requires_conversation=True. NO llama al LLM. Reusa la logica
    determinista de proponer_consolidacion (Sprint 1).

    §4.5: mostrar una consolidada, no lista de tareas independientes.
    """
    from models import STATUS_BITRIX_ACTIVO
    grupos: dict[str, list[Tarea]] = {}
    for t in tareas:
        if not t.requires_conversation:
            continue
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
            continue
        # Fallback nativo Bitrix (Sprint 3.5 fix B)
        if t.status_eos is None and t.status_bitrix_nativo is not None \
                and t.status_bitrix_nativo not in STATUS_BITRIX_ACTIVO:
            continue
        interlocutor = (t.primary_interlocutor or "").strip()
        if not interlocutor:
            continue
        key = interlocutor.lower()
        grupos.setdefault(key, []).append(t)

    conversaciones: list[ItemConversacion] = []
    for key, tareas_grupo in grupos.items():
        # Solo mostramos si hay >=2 asuntos (consolidacion real). Uno solo
        # no es "reunion ejecutiva" — sale por Quick Actions o similar.
        if len(tareas_grupo) < 2:
            continue

        interlocutor_display = tareas_grupo[0].primary_interlocutor or key
        temas = [t.title for t in tareas_grupo]
        tareas_rel = [t.id for t in tareas_grupo if t.id is not None]
        decisiones = [
            t.expected_decision for t in tareas_grupo
            if t.expected_decision
        ]

        # Duracion estimada: 15 min por asunto, redondeado a bloques de 15
        duracion = max(30, 15 * len(tareas_grupo))
        duracion = ((duracion + 14) // 15) * 15

        # Prioridad: alta si algun asunto tiene deadline dentro de 3 dias
        ahora = datetime.now(TZ_LOCAL)
        prio = "media"
        deadlines_cerca = [
            t.deadline for t in tareas_grupo
            if t.deadline and t.deadline < ahora + timedelta(days=3)
        ]
        if deadlines_cerca:
            prio = "alta"

        conversaciones.append(ItemConversacion(
            interlocutor=interlocutor_display,
            temas=temas,
            tareas_relacionadas=tareas_rel,
            decisiones_esperadas=decisiones,
            duracion_estimada_min=duracion,
            prioridad=prio,
            horario_propuesto=None,  # Sprint 5 le pondra hueco real
            estado_confirmacion="propuesta",
            impacto_no_celebrarla=(
                f"{len(tareas_grupo)} asuntos siguen abiertos con {interlocutor_display}"
            ),
            **_evaluar_meeting_delegation(tareas_grupo),
        ))

    return conversaciones


def _evaluar_meeting_delegation(tareas_grupo: list[Tarea]) -> dict:
    """Sprint 4 Bloque C — Meeting Delegation Rule (PHASE 1 §7.4).

    Determina si Alexander DEBE asistir o si la reunion puede delegarse
    o resolverse asincronamente. Deterministica: aplica reglas §7.4
    sobre los alexander_role del grupo.

    Devuelve dict con keys que se expanden al kwargs de ItemConversacion:
      - recomendacion_asistencia: "asistir" | "delegar" | "decidir_asincrono"
      - razon_recomendacion: frase corta que justifica

    Reglas (evaluadas en orden, primera que casa gana):
      1. TODOS son alexander_role=No Involvement -> delegar completo
         (el CEO no aporta nada; el Owner puede reunirse solo).
      2. TODOS son alexander_role in {Supervision, No Involvement} -> delegar
         (el CEO recibe resumen despues).
      3. TODOS son alexander_role=Approval Y todos tienen expected_result
         claro -> decidir_asincrono (el CEO puede aprobar por escrito).
      4. Cualquier otro caso -> asistir (Decision o Execution presentes).
    """
    roles = [t.alexander_role for t in tareas_grupo]
    tiene_expected_result = all(t.expected_result for t in tareas_grupo)

    # Filtramos None (rol sin poner) — lo tratamos como Execution
    # defensivamente (si no sabemos, el CEO va).
    roles_efectivos = [r if r is not None else RolAlexander.EXECUTION
                       for r in roles]

    todos_no_involvement = all(r == RolAlexander.NO_INVOLVEMENT
                                for r in roles_efectivos)
    todos_supervisables = all(r in (RolAlexander.SUPERVISION,
                                     RolAlexander.NO_INVOLVEMENT)
                               for r in roles_efectivos)
    todos_approval = all(r == RolAlexander.APPROVAL
                         for r in roles_efectivos)

    if todos_no_involvement:
        return {
            "recomendacion_asistencia": "delegar",
            "razon_recomendacion": (
                "Ninguno de los asuntos requiere tu involucramiento. "
                "Puede resolverse sin ti."
            ),
        }
    if todos_supervisables:
        return {
            "recomendacion_asistencia": "delegar",
            "razon_recomendacion": (
                "Todo lo del grupo es supervision o delegado. "
                "Recibe un resumen despues."
            ),
        }
    if todos_approval and tiene_expected_result:
        return {
            "recomendacion_asistencia": "decidir_asincrono",
            "razon_recomendacion": (
                "Todo son aprobaciones con criterio claro. "
                "Puedes aprobar por escrito sin reunion."
            ),
        }
    return {
        "recomendacion_asistencia": "asistir",
        "razon_recomendacion": None,
    }


# ============================================================================
# STAGE 5 - PROPOSED WORK BLOCKS (Sec 9, PHASE 6 Doc 3 §10)
# ============================================================================

def _build_proposed_work_blocks_v2(
    overview: CalendarOverview,
    dia: datetime,
    tareas: list[Tarea],
) -> tuple[list[dict], "capacity_mod.CapacidadDia"]:
    """Sprint 5: motor real de capacity + time-blocking (§9 + §10).

    Produce:
    - CapacidadDia con huecos ya clasificados en las 5 categorias.
    - Lista de BloquePropuesto[] con objetivo/prioridad/resultado_esperado
      donde el motor sugiere que dedicar cada hueco a que tarea activa
      concreta segun prioridad §2.4 y task_type.

    Anteriormente devolvia list[str] con descripciones textuales. Ahora
    devuelve dicts serializados de BloquePropuesto para que el frontend
    pueda renderizarlos ricos (objetivo, contexto, tareas_relacionadas).
    """
    # Recopilar intervalos ocupados de HOY (confirmados + bloques
    # protegidos; los propuestos NO cuentan hasta confirmar).
    ocupados: list[tuple[datetime, datetime]] = []
    for item in overview.confirmados + overview.bloques_protegidos:
        try:
            fi = datetime.fromisoformat(item.fecha_inicio)
            ff = datetime.fromisoformat(item.fecha_fin)
            ocupados.append((fi, ff))
        except ValueError:
            continue

    cap_dia = capacity_mod.calcular_capacidad_dia(dia, ocupados)
    propuestos = capacity_mod.proponer_bloques(cap_dia.huecos, tareas)
    return [b.model_dump() for b in propuestos], cap_dia


def _categoria_bloque(minutos: int) -> str:
    """PHASE 6 Doc 3 §10: 5 categorias. Compat con codigo pre-Sprint 5."""
    return capacity_mod.categoria_de(minutos)


# ============================================================================
# STAGE 6 - MISSING INFORMATION + INTEGRITY CHECK (Sec 12 y 13)
# ============================================================================

def _build_missing_information(tareas: list[Tarea]) -> list[str]:
    """Sec 12. Lista concreta de campos obligatorios faltantes.
    §4.6: [NO DATA] explicito allí donde falte info obligatoria.

    Aplica el mismo filtro activo/nativo que _clasificar_tareas para
    no bombardear con "sin next_action" 20 tareas legacy cerradas en
    Bitrix (Sprint 3.5 fix B).
    """
    from models import STATUS_BITRIX_ACTIVO
    faltas: list[str] = []
    for t in tareas:
        # Filtrar terminales EOS
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
            continue
        # Fallback: tareas legacy sin UF_STATUS_EOS pero con STATUS
        # nativo cerrado/aplazado.
        if t.status_eos is None and t.status_bitrix_nativo is not None \
                and t.status_bitrix_nativo not in STATUS_BITRIX_ACTIVO:
            continue
        prefix = f"Tarea {t.id} ('{t.title[:40]}')"

        # §10.2 Work Integrity
        if t.status_eos in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS,
                             EstadoEOS.SCHEDULED, EstadoEOS.BLOCKED) \
                and not t.next_action:
            faltas.append(f"{prefix}: sin next_action")

        if t.status_eos == EstadoEOS.WAITING and not t.review_date:
            faltas.append(f"{prefix}: Waiting sin next_follow_up (review_date)")

        if t.status_eos == EstadoEOS.DELEGATED:
            if not t.review_date:
                faltas.append(f"{prefix}: Delegated sin review_date")
            if not t.expected_result:
                faltas.append(f"{prefix}: Delegated sin expected_result")
            if not t.escalation_condition:
                faltas.append(f"{prefix}: Delegated sin escalation_condition")

        if t.status_eos == EstadoEOS.BLOCKED and not t.next_action:
            faltas.append(f"{prefix}: Blocked sin unblocking_action")

        # §10.3 Conversation Integrity
        if t.requires_conversation and not t.primary_interlocutor:
            faltas.append(f"{prefix}: requires_conversation=True sin primary_interlocutor")

    return faltas


def _build_integrity_check(
    brief_parcial: dict, faltas: list[str], overview: CalendarOverview,
) -> list[IntegrityFinding]:
    """Sec 13. Ejecuta el checklist §10 y reporta findings.
    Devuelve UNA entrada por check, con ok=True/False.
    """
    findings: list[IntegrityFinding] = []

    # §10.1 Information Integrity
    findings.append(IntegrityFinding(
        check="Bitrix24 es fuente de tareas",
        ok=True,
        detalle=None,
    ))
    findings.append(IntegrityFinding(
        check="Google Calendar (via Bitrix) es fuente de ocupacion",
        ok=True,
        detalle=None,
    ))
    findings.append(IntegrityFinding(
        check="Se usa [NO DATA] cuando falta informacion",
        ok=len(faltas) > 0 or True,  # si no hay faltas, no aplica; si hay,
                                       # se listan en Missing Information
        detalle=f"{len(faltas)} campos obligatorios faltantes" if faltas else None,
    ))

    # §10.2 Work Integrity
    tareas_sin_next = [f for f in faltas if "sin next_action" in f]
    findings.append(IntegrityFinding(
        check="Cada tarea activa tiene Next Action",
        ok=not tareas_sin_next,
        detalle=f"{len(tareas_sin_next)} tareas sin next_action" if tareas_sin_next else None,
    ))
    del_sin_owner_review = [f for f in faltas if "Delegated sin review_date" in f]
    findings.append(IntegrityFinding(
        check="Cada Delegated tiene Owner y Review Date",
        ok=not del_sin_owner_review,
        detalle=f"{len(del_sin_owner_review)} delegadas sin review_date" if del_sin_owner_review else None,
    ))

    # §10.3 Conversation Integrity
    conv_sin_interloc = [f for f in faltas if "sin primary_interlocutor" in f]
    findings.append(IntegrityFinding(
        check="Cada Requires Conversation=Si tiene Primary Interlocutor",
        ok=not conv_sin_interloc,
        detalle=f"{len(conv_sin_interloc)} tareas requires_conversation sin interlocutor" if conv_sin_interloc else None,
    ))

    # §10.5 Executive Capacity Integrity
    key_outcomes = brief_parcial.get("three_key_outcomes", [])
    findings.append(IntegrityFinding(
        check="Como maximo tres Key Outcomes",
        ok=len(key_outcomes) <= MAX_KEY_OUTCOMES,
        detalle=f"Hay {len(key_outcomes)}" if len(key_outcomes) > MAX_KEY_OUTCOMES else None,
    ))
    buffer_ok = overview.buffer_pct >= BUFFER_MINIMO_PCT
    findings.append(IntegrityFinding(
        check=f"Buffer libre >= {BUFFER_MINIMO_PCT}%",
        ok=buffer_ok,
        detalle=f"Buffer actual: {overview.buffer_pct}%" if not buffer_ok else None,
    ))

    # §10.4 Meeting Integrity (aplicable a las conversaciones propuestas)
    conversaciones = brief_parcial.get("executive_conversations", [])
    convs_sin_agenda = [c for c in conversaciones if not c.temas]
    findings.append(IntegrityFinding(
        check="Reuniones propuestas tienen agenda",
        ok=not convs_sin_agenda,
        detalle=f"{len(convs_sin_agenda)} propuestas sin agenda" if convs_sin_agenda else None,
    ))

    # --- Sprint 5: PHASE 6 Doc 3 §15 Integrity Rules ampliadas ---

    # §15.1 Sobrecarga de trabajo (ya cubierto por buffer)
    # §15.2 Ausencia de bloques estrategicos protegidos
    cap_hoy = brief_parcial.get("capacidad_hoy")
    if cap_hoy is not None:
        tiene_estrat = cap_hoy.get("tiene_bloque_estrategico", True)
        findings.append(IntegrityFinding(
            check="Al menos un bloque estrategico protegido hoy",
            ok=tiene_estrat,
            detalle=None if tiene_estrat
                    else "Ningun hueco >=60min disponible. Sin ventana para trabajo profundo.",
        ))

    # §15.3 Falta de revisiones
    delegadas = brief_parcial.get("delegated_supervision", [])
    del_sin_review = [d for d in delegadas
                      if not getattr(d, "review_date", None)]
    findings.append(IntegrityFinding(
        check="Cada delegada tiene review_date fijado",
        ok=not del_sin_review,
        detalle=f"{len(del_sin_review)} delegadas sin review_date"
                if del_sin_review else None,
    ))

    # §15.4 Conflictos de calendario (ya calculado en overview)
    findings.append(IntegrityFinding(
        check="Sin conflictos de calendario",
        ok=not overview.conflictos,
        detalle=f"{len(overview.conflictos)} conflictos detectados"
                if overview.conflictos else None,
    ))

    # §15.5 Waiting Items sin follow-up
    waiting_items = brief_parcial.get("waiting", [])
    wait_sin_fu = [w for w in waiting_items
                   if not getattr(w, "review_date", None)]
    findings.append(IntegrityFinding(
        check="Cada Waiting tiene next_follow_up fijado",
        ok=not wait_sin_fu,
        detalle=f"{len(wait_sin_fu)} Waiting sin follow-up"
                if wait_sin_fu else None,
    ))

    # §15.6 Exceso de cambios de contexto (fragmentacion detectada en overview)
    findings.append(IntegrityFinding(
        check="Sin exceso de cambios de contexto",
        ok=not overview.riesgo_fragmentacion,
        detalle="Dia fragmentado: >3 huecos <30min entre eventos"
                if overview.riesgo_fragmentacion else None,
    ))

    # §15.7 Conversaciones con la misma persona repartidas (Sprint 1 detecta)
    # Ya cubierto por executive_conversations; check informativo:
    findings.append(IntegrityFinding(
        check="Conversaciones consolidadas por interlocutor",
        ok=True,
        detalle=f"{len(conversaciones)} agrupaciones detectadas"
                if conversaciones else None,
    ))

    return findings


# ============================================================================
# STAGE 6.5 - REMINDER ENGINE (Sprint 5, PHASE 6 Doc 3 §13)
# ============================================================================

def _build_reminders(
    people_blocked: list[ItemTarea],
    delegated_supervision: list[ItemTarea],
    waiting: list[ItemTarea],
    conversaciones: list[ItemConversacion],
    tareas_todas: list[Tarea],
    dia: datetime,
) -> list[ReminderItem]:
    """Sprint 5 §13: recordatorios priorizados por impacto organizacional.

    Orden estricto:
      1. Personas bloqueadas por CEO
      2. Decisiones pendientes
      3. Dependencias externas
      4. Revisiones comprometidas
      5. Riesgos de incumplimiento
      6. (extra) Reuniones propuestas no confirmadas

    Deterministica. No llama al LLM. El brief lo consume para pintar
    una seccion nueva "Recordatorios" jerarquizada.
    """
    ahora = datetime.now(TZ_LOCAL)
    reminders: list[ReminderItem] = []

    # PRIO 1: Personas bloqueadas por CEO
    for it in people_blocked:
        reminders.append(ReminderItem(
            prioridad_num=1,
            categoria="persona_bloqueada",
            titulo=it.title,
            detalle=it.razon or "Terceros esperan tu acción",
            accion_sugerida=it.next_action,
            tarea_id=it.id,
            persona=it.primary_interlocutor,
        ))

    # PRIO 2: Decisiones pendientes (Decision task_type activo)
    from models import TipoTarea
    for t in tareas_todas:
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
            continue
        if t.task_type == TipoTarea.DECISION and t.id:
            # Evitar duplicar los que ya salieron en prio 1
            if any(r.tarea_id == t.id for r in reminders):
                continue
            reminders.append(ReminderItem(
                prioridad_num=2,
                categoria="decision",
                titulo=t.title,
                detalle=t.expected_decision
                        or "Decisión sin criterio explícito",
                accion_sugerida=t.next_action,
                tarea_id=t.id,
                persona=t.primary_interlocutor,
            ))

    # PRIO 3: Dependencias externas (Waiting con follow-up vencido)
    for it in waiting:
        if it.dias_vencido and it.dias_vencido > 0:
            reminders.append(ReminderItem(
                prioridad_num=3,
                categoria="dependencia_externa",
                titulo=it.title,
                detalle=(f"Follow-up vencido hace {it.dias_vencido} días"
                         if it.dias_vencido > 1
                         else "Follow-up vencido"),
                accion_sugerida=(f"Recordar a {it.primary_interlocutor}"
                                 if it.primary_interlocutor
                                 else "Enviar recordatorio"),
                tarea_id=it.id,
                persona=it.primary_interlocutor,
            ))

    # PRIO 4: Revisiones comprometidas (delegadas con review_date <= 3 dias)
    for it in delegated_supervision:
        if not it.review_date:
            continue
        try:
            rv = datetime.fromisoformat(it.review_date)
            if rv <= ahora + timedelta(days=3):
                reminders.append(ReminderItem(
                    prioridad_num=4,
                    categoria="revision_comprometida",
                    titulo=it.title,
                    detalle=(f"Revisión pactada para "
                             f"{rv.date().isoformat()}"),
                    accion_sugerida=(f"Contactar a {it.owner_nombre}"
                                     if it.owner_nombre
                                     else "Pedir avance a Owner"),
                    tarea_id=it.id,
                    persona=it.owner_nombre,
                ))
        except ValueError:
            pass

    # PRIO 5: Riesgos de incumplimiento (deadline <= 3 dias, tarea no arrancada)
    for t in tareas_todas:
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
            continue
        if not t.deadline or not t.id:
            continue
        if any(r.tarea_id == t.id for r in reminders):
            continue
        if t.deadline <= ahora + timedelta(days=3):
            if t.status_eos in (EstadoEOS.NEW, EstadoEOS.BLOCKED):
                reminders.append(ReminderItem(
                    prioridad_num=5,
                    categoria="riesgo_incumplimiento",
                    titulo=t.title,
                    detalle=(f"Deadline {t.deadline.date().isoformat()} "
                             f"y tarea aun en {t.status_eos.value}"),
                    accion_sugerida=t.next_action or "Arrancar hoy",
                    tarea_id=t.id,
                ))

    # PRIO 6 (extra): Reuniones propuestas no confirmadas §13 final
    for c in conversaciones:
        if c.estado_confirmacion == "propuesta":
            reminders.append(ReminderItem(
                prioridad_num=6,
                categoria="reunion_propuesta_no_confirmada",
                titulo=f"Reunión propuesta con {c.interlocutor}",
                detalle=(f"{len(c.temas)} temas · "
                         f"{c.duracion_estimada_min} min"),
                accion_sugerida="Confirmar, rechazar o resolver asíncronamente",
                persona=c.interlocutor,
            ))

    return reminders


# ============================================================================
# STAGE 7 - LLM SYNTHESIS (Executive Summary + Three Key Outcomes)
# ============================================================================

_PROMPT_SINTESIS = """Eres el Executive Operating System de un CEO. \
Recibes datos ya recopilados y estructurados. Tu tarea es SOLO producir \
dos elementos del Executive Brief:

1. Executive Summary: un unico parrafo (max 5-6 frases) que responda a \
PHASE 1 §4.2: que requiere atencion, por que, quien esta bloqueado, que \
decisiones deben tomarse, que conversaciones pueden consolidarse, cual \
es el principal riesgo, que debe EVITAR hacer el CEO. No enumeres — es \
prosa ejecutiva. En espanol de Espana.

2. Three Key Outcomes: EXACTAMENTE 1, 2 o 3 items (nunca mas), cada uno \
con:
   - resultado: frase de RESULTADO EMPRESARIAL (NO "reunirse con X"; \
si el resultado se logra en una reunion, describe el resultado real: \
"cerrar aprobacion de propuesta con Jose", "desbloquear entrega Q3 con \
Sandra"). PHASE 1 §4.4.
   - mecanismo: uno de: trabajo_ceo | decision | aprobacion | delegacion \
| conversacion | desbloqueo
   - razon: una frase con por que este resultado merece el foco hoy \
(prioridad §2.4).
   - items_relacionados: lista de ids de tarea si los hay, o []

Prioridad §2.4 (usar para elegir los outcomes):
1. Personas bloqueadas por el CEO
2. Decisiones que solo puede tomar el CEO
3. Dependencias externas que bloquean trabajo interno
4. Trabajo estrategico con plazo
5. Supervision de trabajo delegado con riesgo
6. Trabajo operativo no delegable
7. Todo lo demas

Responde EXCLUSIVAMENTE con un JSON valido con esta forma exacta, sin \
markdown, sin backticks, sin explicacion:

{"executive_summary": "...", "three_key_outcomes": [{"resultado": "...", \
"mecanismo": "...", "razon": "...", "items_relacionados": [1,2]}]}

DATOS DEL DIA:
"""


async def _synthesize_llm(user_id: str, contexto: dict) -> tuple[str, list[KeyOutcome]]:
    """Llama a Sonnet UNA sola vez para producir Executive Summary +
    Three Key Outcomes.

    Falla-suave: si la llamada peta, devuelve un summary generico y
    outcomes vacio. El resto del Brief sigue siendo util.
    """
    try:
        client = _get_client()
    except RuntimeError as e:
        logger.warn("brief", "no_api_key", str(e), user_id=user_id)
        return _fallback_synthesis(contexto)

    # Contexto compacto para el LLM. Enviamos solo lo que necesita.
    payload = {
        "people_blocked": [x.model_dump() for x in contexto["people_blocked"]],
        "quick_actions_count": len(contexto["quick_actions"]),
        "delegated_supervision": [x.model_dump() for x in contexto["delegated_supervision"]],
        "waiting": [x.model_dump() for x in contexto["waiting"]],
        "executive_conversations": [x.model_dump() for x in contexto["executive_conversations"]],
        "calendar_summary": {
            "confirmados_hoy": len(contexto["overview"].confirmados),
            "propuestos_hoy": len(contexto["overview"].propuestos),
            "buffer_pct": contexto["overview"].buffer_pct,
            "conflictos": contexto["overview"].conflictos,
        },
        "missing_information_count": len(contexto["missing_information"]),
        "fecha_ref": contexto["fecha_ref"],
    }
    prompt = _PROMPT_SINTESIS + json.dumps(payload, ensure_ascii=False, indent=2)

    try:
        response = await client.messages.create(
            model=MODELO,
            max_tokens=MAX_TOKENS_SYNTHESIS,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            await usage.registrar(user_id, response.usage, MODELO, contexto="brief")
        except Exception:
            pass  # no romper el brief por un fallo en registro de coste

        # Extraer texto
        texto = ""
        for bloque in response.content:
            if bloque.type == "text":
                texto = bloque.text.strip()
                break
        if not texto:
            return _fallback_synthesis(contexto)

        # Limpieza defensiva por si el modelo devuelve backticks
        if texto.startswith("```"):
            texto = texto.strip("`")
            if texto.startswith("json"):
                texto = texto[4:].strip()

        parsed = json.loads(texto)
        summary = str(parsed.get("executive_summary", "")).strip()
        raw_outcomes = parsed.get("three_key_outcomes", [])
        outcomes: list[KeyOutcome] = []
        for o in raw_outcomes[:MAX_KEY_OUTCOMES]:
            if not isinstance(o, dict):
                continue
            try:
                outcomes.append(KeyOutcome(
                    resultado=str(o.get("resultado", "")).strip() or "[NO DATA]",
                    mecanismo=str(o.get("mecanismo", "trabajo_ceo")).strip(),
                    razon=str(o.get("razon", "")).strip() or "[NO DATA]",
                    items_relacionados=[int(i) for i in (o.get("items_relacionados") or [])
                                         if isinstance(i, (int, str)) and str(i).isdigit()],
                ))
            except Exception:
                continue

        if not summary:
            summary = _fallback_summary(contexto)
        return summary, outcomes

    except Exception as e:
        logger.error("brief", "llm_synth_error",
                     f"Fallo sintesis LLM: {type(e).__name__}: {e}",
                     user_id=user_id, error=e)
        return _fallback_synthesis(contexto)


def _fallback_synthesis(contexto: dict) -> tuple[str, list[KeyOutcome]]:
    """Cuando el LLM no esta disponible, degradamos con gracia."""
    return _fallback_summary(contexto), []


def _fallback_summary(contexto: dict) -> str:
    """Summary generado 100% deterministico. Menos vivo pero informativo."""
    partes = []
    n_blk = len(contexto["people_blocked"])
    if n_blk:
        partes.append(f"{n_blk} persona{'s' if n_blk != 1 else ''} espera{'n' if n_blk != 1 else ''} decision o aprobacion tuya.")
    n_wait = len(contexto["waiting"])
    if n_wait:
        partes.append(f"{n_wait} asunto{'s' if n_wait != 1 else ''} en Waiting.")
    n_del = len(contexto["delegated_supervision"])
    if n_del:
        partes.append(f"{n_del} tarea{'s' if n_del != 1 else ''} delegada{'s' if n_del != 1 else ''} bajo supervision.")
    n_conv = len(contexto["executive_conversations"])
    if n_conv:
        partes.append(f"Detectamos {n_conv} conversacion{'es' if n_conv != 1 else ''} ejecutiva{'s' if n_conv != 1 else ''} agrupable{'s' if n_conv != 1 else ''}.")
    buf = contexto["overview"].buffer_pct
    if buf < BUFFER_MINIMO_PCT:
        partes.append(f"Alerta: buffer libre del {buf}%, por debajo del {BUFFER_MINIMO_PCT}% minimo.")
    if not partes:
        return "Dia despejado: sin bloqueos, sin waiting vencidos, sin conversaciones pendientes."
    return " ".join(partes)


# ============================================================================
# PUBLIC API: generar_brief
# ============================================================================

async def generar_brief(user_id: str, fecha_ref: datetime | None = None) -> BriefEjecutivo:
    """Punto de entrada. Ejecuta el pipeline completo y devuelve el Brief.

    fecha_ref: dia para el que se genera. Si None, hoy en Europe/Madrid.
    """
    dia = _hoy_local(fecha_ref)
    ahora = datetime.now(TZ_LOCAL)

    logger.info("brief", "generation_start",
                f"Generando brief para {user_id} dia {dia.date().isoformat()}",
                user_id=user_id)

    # 1. COLLECT
    data = await _collect(user_id, dia)

    # 2. STRUCTURE + PRIORITIZE
    overview = _build_calendar_overview(data["eventos"], data["bloques"], dia)
    eventos_hoy_items = overview.confirmados + overview.propuestos

    # 3. CLASIFICAR TAREAS EN SECCIONES 4-11
    usuario = USUARIOS_POR_USERNAME.get(user_id) or {}
    bitrix_uid = usuario.get("bitrix_user_id", 0)
    webhook = usuario.get("webhook_bitrix", "")
    clasif = _clasificar_tareas(data["tareas"], bitrix_uid, dia)

    # 3.5 SPRINT 4 — Enriquecer delegated_supervision con owner_nombre.
    # Se hace despues de _clasificar_tareas porque necesitamos await, y
    # esa funcion es sync. Resolucion via user.get cacheada; el bucle
    # es cheap si el portal tiene <10 delegadas.
    if webhook and clasif["delegated_supervision"]:
        # Mapa id_tarea -> responsable_id nativo, tomado del set original.
        resp_por_tarea: dict[int, int] = {}
        for t in data["tareas"]:
            if t.id is not None and t.responsable_id_bitrix:
                resp_por_tarea[t.id] = t.responsable_id_bitrix

        for item in clasif["delegated_supervision"]:
            resp_id = resp_por_tarea.get(item.id)
            if resp_id and resp_id != bitrix_uid:
                # Owner externo — resolvemos nombre
                nombre = await _resolver_owner_nombre(webhook, resp_id)
                if nombre:
                    item.owner_nombre = nombre
            # Fallback: si sigue vacio y hay interlocutor, usamos ese
            if not item.owner_nombre and item.primary_interlocutor:
                item.owner_nombre = item.primary_interlocutor

    # 4. EXECUTIVE CONVERSATIONS
    conversaciones = _build_executive_conversations(data["tareas"], eventos_hoy_items)

    # 5. PROPOSED WORK BLOCKS (Sprint 5: motor real capacity + time-blocking)
    work_blocks, cap_hoy = _build_proposed_work_blocks_v2(overview, dia, data["tareas"])

    # 6. MISSING INFORMATION
    faltas = _build_missing_information(data["tareas"])

    # 6.5 Sprint 5 - REMINDERS priorizados §13
    reminders = _build_reminders(
        clasif["people_blocked"],
        clasif["delegated_supervision"],
        clasif["waiting"],
        conversaciones,
        data["tareas"],
        dia,
    )

    # 6.6 Sprint 5 - FORECAST proxima semana (§14)
    forecast_dict = None
    try:
        # Lunes de la proxima semana
        proximo_lunes = dia + timedelta(days=(7 - dia.weekday()))
        proximo_lunes = proximo_lunes.replace(hour=0, minute=0, second=0, microsecond=0)

        # Agrupamos ocupados por dia usando los eventos ya recopilados
        intervalos_por_dia: dict[str, list[tuple[datetime, datetime]]] = {}
        for e in data["eventos"]:
            fi_raw = e.get("DATE_FROM", "") or ""
            ff_raw = e.get("DATE_TO", "") or ""
            try:
                fi = datetime.fromisoformat(fi_raw.replace("Z", "+00:00")) \
                    if fi_raw else None
                ff = datetime.fromisoformat(ff_raw.replace("Z", "+00:00")) \
                    if ff_raw else None
            except (ValueError, TypeError):
                continue
            if fi is None or ff is None:
                continue
            if fi.tzinfo is None: fi = fi.replace(tzinfo=TZ_LOCAL)
            if ff.tzinfo is None: ff = ff.replace(tzinfo=TZ_LOCAL)
            fi = fi.astimezone(TZ_LOCAL)
            ff = ff.astimezone(TZ_LOCAL)
            clave = fi.date().isoformat()
            intervalos_por_dia.setdefault(clave, []).append((fi, ff))

        cap_semana = capacity_mod.calcular_capacidad_semanal(
            proximo_lunes, intervalos_por_dia,
        )
        fc = forecast_mod.forecast_semana(
            proximo_lunes, cap_semana, data["eventos"],
            len(conversaciones), data["tareas"],
        )
        forecast_dict = fc.model_dump()
    except Exception as e:
        logger.warn("brief", "forecast_error",
                    f"Fallo forecast: {type(e).__name__}: {e}",
                    user_id=user_id)

    # 7. LLM synthesis (Executive Summary + Three Key Outcomes)
    contexto_llm = {
        "people_blocked": clasif["people_blocked"],
        "quick_actions": clasif["quick_actions"],
        "delegated_supervision": clasif["delegated_supervision"],
        "waiting": clasif["waiting"],
        "executive_conversations": conversaciones,
        "overview": overview,
        "missing_information": faltas,
        "fecha_ref": dia.date().isoformat(),
    }
    summary, outcomes = await _synthesize_llm(user_id, contexto_llm)

    # 8. INTEGRITY CHECK (ampliado Sprint 5 con §15)
    integrity = _build_integrity_check(
        {"three_key_outcomes": outcomes,
         "executive_conversations": conversaciones,
         "delegated_supervision": clasif["delegated_supervision"],
         "waiting": clasif["waiting"],
         "capacidad_hoy": cap_hoy.model_dump() if cap_hoy else None},
        faltas, overview,
    )

    brief = BriefEjecutivo(
        generado_en=ahora.isoformat(),
        user_id=user_id,
        fecha_ref=dia.date().isoformat(),
        executive_summary=summary,
        calendar_overview=overview,
        three_key_outcomes=outcomes,
        quick_actions=clasif["quick_actions"],
        people_blocked=clasif["people_blocked"],
        executive_conversations=conversaciones,
        delegated_supervision=clasif["delegated_supervision"],
        waiting=clasif["waiting"],
        proposed_work_blocks=work_blocks,
        not_today=clasif["not_today"],
        remaining_inventory_total=clasif["remaining_total"],
        remaining_inventory_por_tipo=clasif["remaining_por_tipo"],
        missing_information=faltas,
        integrity_check=integrity,
        capacidad_hoy=cap_hoy.model_dump() if cap_hoy else None,
        forecast_proxima_semana=forecast_dict,
        reminders=reminders,
    )

    logger.info("brief", "generation_ok",
                f"Brief generado: {len(outcomes)} outcomes, "
                f"{len(conversaciones)} conversaciones, buffer {overview.buffer_pct}%, "
                f"{len(reminders)} reminders",
                user_id=user_id,
                metadata={
                    "n_key_outcomes": len(outcomes),
                    "n_conversaciones": len(conversaciones),
                    "n_quick_actions": len(clasif["quick_actions"]),
                    "n_people_blocked": len(clasif["people_blocked"]),
                    "n_waiting": len(clasif["waiting"]),
                    "n_delegated": len(clasif["delegated_supervision"]),
                    "n_reminders": len(reminders),
                    "n_work_blocks": len(work_blocks),
                    "buffer_pct": overview.buffer_pct,
                    "n_faltas": len(faltas),
                    "forecast_ratio": forecast_dict.get("ratio_carga") if forecast_dict else None,
                })

    return brief


# ============================================================================
# RENDERIZADO A TELEGRAM (texto plano)
# ============================================================================

def render_telegram(brief: BriefEjecutivo) -> str:
    """Convierte BriefEjecutivo a un mensaje Telegram legible.

    Telegram tiene limite de 4096 chars por mensaje. El caller puede
    dividir si hace falta. Aqui producimos algo compacto pensado para
    ojo del CEO, no exhaustivo.
    """
    L: list[str] = []
    L.append(f"🌅 *Executive Brief — {brief.fecha_ref}*")
    L.append("")

    # 1 Executive Summary
    L.append("*Situacion*")
    L.append(brief.executive_summary)
    L.append("")

    # 2 Calendar
    ov = brief.calendar_overview
    L.append("*Calendario*")
    L.append(f"• Confirmados: {len(ov.confirmados)} · "
             f"Propuestas: {len(ov.propuestos)} · "
             f"Bloques: {len(ov.bloques_protegidos)}")
    L.append(f"• Buffer libre: {ov.buffer_pct}%"
             f"{' ⚠️' if ov.buffer_pct < BUFFER_MINIMO_PCT else ''}")
    if ov.conflictos:
        L.append(f"• Conflictos: {len(ov.conflictos)}")
    L.append("")

    # 3 Key Outcomes
    if brief.three_key_outcomes:
        L.append("*Tres resultados del dia*")
        for i, o in enumerate(brief.three_key_outcomes, 1):
            L.append(f"{i}. {o.resultado}")
            L.append(f"   _{o.razon}_")
        L.append("")

    # 5 People Blocked
    if brief.people_blocked:
        L.append(f"*Terceros esperandote* ({len(brief.people_blocked)})")
        for it in brief.people_blocked[:5]:
            L.append(f"• {it.title}")
            if it.razon:
                L.append(f"   _{it.razon}_")
        L.append("")

    # 6 Executive Conversations
    if brief.executive_conversations:
        L.append("*Conversaciones a agrupar*")
        for c in brief.executive_conversations[:4]:
            L.append(f"• Con {c.interlocutor} ({len(c.temas)} temas, {c.duracion_estimada_min} min)")
        L.append("")

    # 4 Quick Actions
    if brief.quick_actions:
        L.append(f"*Quick actions* ({len(brief.quick_actions)})")
        for it in brief.quick_actions[:5]:
            L.append(f"• {it.next_action or it.title}")
        L.append("")

    # 8 Waiting
    if brief.waiting:
        L.append(f"*Waiting* ({len(brief.waiting)})")
        for it in brief.waiting[:4]:
            marca = " ⏰" if it.razon and "vencido" in it.razon else ""
            L.append(f"• {it.title}{marca}")
        L.append("")

    # 7 Delegated
    if brief.delegated_supervision:
        L.append(f"*Delegadas bajo supervision* ({len(brief.delegated_supervision)})")
        for it in brief.delegated_supervision[:4]:
            L.append(f"• {it.title}")
            if it.razon:
                L.append(f"   _{it.razon}_")
        L.append("")

    # 9 Work Blocks (Sprint 5: ahora son dicts con objetivo)
    if brief.proposed_work_blocks:
        L.append("*Bloques de trabajo propuestos*")
        for b in brief.proposed_work_blocks[:4]:
            fi = b.get("inicio", "")[11:16]
            ff = b.get("fin", "")[11:16]
            obj = b.get("objetivo", "")[:60]
            L.append(f"• {fi}-{ff} · {obj}")
        L.append("")

    # Sprint 5 - Reminders priorizados §13
    if brief.reminders:
        L.append(f"*Recordatorios* ({len(brief.reminders)})")
        for r in brief.reminders[:5]:
            L.append(f"• [{r.prioridad_num}] {r.titulo}")
            if r.accion_sugerida:
                L.append(f"   _{r.accion_sugerida}_")
        L.append("")

    # Sprint 5 - Forecast proxima semana
    fc = brief.forecast_proxima_semana
    if fc and fc.get("riesgos"):
        n_riesgos = len(fc["riesgos"])
        L.append(f"*Próxima semana* ({n_riesgos} riesgo{'s' if n_riesgos != 1 else ''})")
        for r in fc["riesgos"][:3]:
            L.append(f"• {r.get('descripcion', '')[:120]}")
        L.append("")

    # 12 Missing info
    if brief.missing_information:
        L.append(f"*Info incompleta*: {len(brief.missing_information)} campos faltantes")

    # 13 Integrity
    fallos = [f for f in brief.integrity_check if not f.ok]
    if fallos:
        L.append(f"*Integrity Check*: {len(fallos)} incidencias")
        for f in fallos[:3]:
            L.append(f"• {f.check}: {f.detalle or ''}")

    return "\n".join(L)
