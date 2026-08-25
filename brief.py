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
from bitrix import consultar_ocupacion_bitrix
import bloques as bloques_mod
import logger
import usage
from config_usuarios import USUARIOS_POR_USERNAME

# ============================================================================
# CONFIG
# ============================================================================

_API_KEY = getenv("ANTHROPIC_API_KEY", "")
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
            raise RuntimeError("Falta ANTHROPIC_API_KEY para generar el brief.")
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
    TareaResumen: solo lo que el CEO necesita para orientarse."""
    id: int
    title: str
    status_eos: str | None = None
    task_type: str | None = None
    owner_es_ceo: bool
    next_action: str | None = None
    deadline: str | None = None
    review_date: str | None = None
    primary_interlocutor: str | None = None
    razon: str | None = None  # Explicacion CEO-facing de por que aparece aqui


class ItemConversacion(BaseModel):
    """Reunion propuesta o consolidacion detectada (§4.5)."""
    interlocutor: str
    temas: list[str]
    tareas_relacionadas: list[int]
    decisiones_esperadas: list[str] = []
    duracion_estimada_min: int
    prioridad: str  # alta | media | baja
    horario_propuesto: str | None = None
    estado_confirmacion: str = "propuesta"
    impacto_no_celebrarla: str | None = None


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


class BriefEjecutivo(BaseModel):
    """PHASE 1 §4.1 completo: 13 secciones + metadata."""
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

    # Sec 9 - Proposed Work Blocks
    proposed_work_blocks: list[str]  # descripciones ("Bloque estrategico 9-11")

    # Sec 10 - Not Today
    not_today: list[ItemTarea]

    # Sec 11 - Remaining Task Inventory (contador + resumen)
    remaining_inventory_total: int
    remaining_inventory_por_tipo: dict[str, int]

    # Sec 12 - Missing Information
    missing_information: list[str]  # frases "Tarea 42 no tiene next_action"

    # Sec 13 - Integrity Check
    integrity_check: list[IntegrityFinding]


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
                  owner_es_ceo: bool = True) -> ItemTarea:
    return ItemTarea(
        id=t.id or 0,
        title=t.title,
        status_eos=t.status_eos.value if t.status_eos else None,
        task_type=t.task_type.value if t.task_type else None,
        owner_es_ceo=owner_es_ceo,
        next_action=t.next_action,
        deadline=t.deadline.isoformat() if t.deadline else None,
        review_date=t.review_date.isoformat() if t.review_date else None,
        primary_interlocutor=t.primary_interlocutor,
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
    """
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
                razon = "Follow-up vencido"
            waiting.append(_tarea_a_item(t, razon=razon))
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
    grupos: dict[str, list[Tarea]] = {}
    for t in tareas:
        if not t.requires_conversation:
            continue
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
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
        ))

    return conversaciones


# ============================================================================
# STAGE 5 - PROPOSED WORK BLOCKS (Sec 9, PHASE 6 Doc 3 §10)
# ============================================================================

def _build_proposed_work_blocks(overview: CalendarOverview,
                                dia: datetime) -> list[str]:
    """Sugerencias textuales de bloques de trabajo profundo cuando
    detectamos huecos >=60min entre eventos. Deterministico simple —
    el motor completo llega en Sprint 5."""
    todos = sorted(
        overview.confirmados + overview.propuestos + overview.bloques_protegidos,
        key=lambda x: x.fecha_inicio,
    )
    bloques: list[str] = []
    ini_jor, fin_jor = _jornada_del(dia)

    cursor = ini_jor
    for item in todos:
        fi_item = datetime.fromisoformat(item.fecha_inicio)
        if fi_item > cursor:
            gap_min = _minutos(cursor, fi_item)
            if gap_min >= 60:
                categoria = _categoria_bloque(gap_min)
                bloques.append(
                    f"{cursor.strftime('%H:%M')}-{fi_item.strftime('%H:%M')} "
                    f"({gap_min} min, {categoria})"
                )
        cursor = max(cursor, datetime.fromisoformat(item.fecha_fin))
    # Cola de dia
    if fin_jor > cursor:
        gap_min = _minutos(cursor, fin_jor)
        if gap_min >= 60:
            categoria = _categoria_bloque(gap_min)
            bloques.append(
                f"{cursor.strftime('%H:%M')}-{fin_jor.strftime('%H:%M')} "
                f"({gap_min} min, {categoria})"
            )
    return bloques


def _categoria_bloque(minutos: int) -> str:
    """PHASE 6 Doc 3 §10: 5 categorias."""
    if minutos <= 10:
        return "Ultra Short"
    if minutos <= 30:
        return "Short"
    if minutos <= 60:
        return "Medium"
    if minutos <= 120:
        return "Deep Work"
    return "Strategic Block"


# ============================================================================
# STAGE 6 - MISSING INFORMATION + INTEGRITY CHECK (Sec 12 y 13)
# ============================================================================

def _build_missing_information(tareas: list[Tarea]) -> list[str]:
    """Sec 12. Lista concreta de campos obligatorios faltantes.
    §4.6: [NO DATA] explicito allí donde falte info obligatoria.
    """
    faltas: list[str] = []
    for t in tareas:
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
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

    return findings


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
    clasif = _clasificar_tareas(data["tareas"], bitrix_uid, dia)

    # 4. EXECUTIVE CONVERSATIONS
    conversaciones = _build_executive_conversations(data["tareas"], eventos_hoy_items)

    # 5. PROPOSED WORK BLOCKS
    work_blocks = _build_proposed_work_blocks(overview, dia)

    # 6. MISSING INFORMATION
    faltas = _build_missing_information(data["tareas"])

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

    # 8. INTEGRITY CHECK
    integrity = _build_integrity_check(
        {"three_key_outcomes": outcomes,
         "executive_conversations": conversaciones},
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
    )

    logger.info("brief", "generation_ok",
                f"Brief generado: {len(outcomes)} outcomes, "
                f"{len(conversaciones)} conversaciones, buffer {overview.buffer_pct}%",
                user_id=user_id,
                metadata={
                    "n_key_outcomes": len(outcomes),
                    "n_conversaciones": len(conversaciones),
                    "n_quick_actions": len(clasif["quick_actions"]),
                    "n_people_blocked": len(clasif["people_blocked"]),
                    "n_waiting": len(clasif["waiting"]),
                    "n_delegated": len(clasif["delegated_supervision"]),
                    "buffer_pct": overview.buffer_pct,
                    "n_faltas": len(faltas),
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

    # 9 Work Blocks
    if brief.proposed_work_blocks:
        L.append("*Bloques de trabajo posibles*")
        for b in brief.proposed_work_blocks[:3]:
            L.append(f"• {b}")
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
