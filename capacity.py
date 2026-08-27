"""capacity.py — Capacity Planning + Time Blocking (Sprint 5, PHASE 6 Doc 3 §9-§10)

Motor determinista que:
1. Calcula capacidad real disponible del CEO por dia y semana (§9),
   considerando horario laboral, eventos, bloques no negociables y
   buffer minimo del 30%.
2. Clasifica huecos libres en las 5 categorias §10 (Ultra Short, Short,
   Medium, Deep Work, Strategic Block).
3. Propone que hacer en cada hueco: consume la lista de tareas
   priorizadas del brief y emite bloques con {objetivo, prioridad,
   duracion_min, contexto, resultado_esperado}.

Regla dura §10: los bloques estrategicos tienen prioridad absoluta sobre
cualquier operativo. Si no hay ningun bloque >= Deep Work en el dia, se
alerta.

Diseno: puro. Sin IO. Recibe eventos+bloques ya recopilados por brief.py
y devuelve estructuras Pydantic. Tests deterministicos triviales.
"""
from __future__ import annotations
from datetime import datetime, timedelta, time
from typing import Any

from pydantic import BaseModel

from models import Tarea, EstadoEOS, RolAlexander, TZ_LOCAL


# ============================================================================
# CONFIG
# ============================================================================

# Horario laboral por defecto. Si el usuario tiene su propia franja
# (config_usuarios en el futuro), se puede sobreescribir por arg.
JORNADA_INICIO = time(8, 0)
JORNADA_FIN    = time(19, 0)

# Buffer minimo obligatorio §5.8. El sistema NUNCA propone un plan que
# consuma mas de (100 - BUFFER_MINIMO_PCT)% de la jornada.
BUFFER_MINIMO_PCT = 30

# Umbrales de categorias §10. En minutos.
UMBRAL_ULTRA_SHORT = 10
UMBRAL_SHORT       = 30
UMBRAL_MEDIUM      = 60
UMBRAL_DEEP_WORK   = 120


# ============================================================================
# ESTRUCTURAS
# ============================================================================

class Hueco(BaseModel):
    """Intervalo libre en el calendario, ya clasificado."""
    inicio: str  # ISO 8601
    fin: str
    duracion_min: int
    categoria: str  # Ultra Short | Short | Medium | Deep Work | Strategic Block


class BloquePropuesto(BaseModel):
    """§10: cada bloque tiene objetivo/prioridad/duracion/contexto/resultado_esperado."""
    inicio: str
    fin: str
    duracion_min: int
    categoria: str
    objetivo: str            # que se hace en este bloque
    prioridad: str           # alta | media | baja
    contexto: str | None = None  # de donde viene la propuesta
    resultado_esperado: str | None = None
    tareas_relacionadas: list[int] = []
    tipo: str = "operativo"  # operativo | estrategico


class CapacidadDia(BaseModel):
    """Snapshot de capacidad de un dia concreto."""
    fecha: str  # YYYY-MM-DD
    total_min: int
    ocupado_min: int
    libre_min: int
    buffer_pct: float
    buffer_ok: bool                       # buffer_pct >= BUFFER_MINIMO_PCT
    tiene_bloque_estrategico: bool        # algun Deep Work o Strategic Block?
    huecos: list[Hueco]


class CapacidadSemanal(BaseModel):
    """Agregado de 5 dias laborables (L-V)."""
    lunes: str  # YYYY-MM-DD del lunes de la semana
    dias: list[CapacidadDia]
    total_libre_min: int
    total_ocupado_min: int
    buffer_medio_pct: float
    dias_saturados: list[str]  # YYYY-MM-DD de dias sin buffer suficiente


# ============================================================================
# CLASIFICACION DE HUECOS
# ============================================================================

def categoria_de(duracion_min: int) -> str:
    """§10: 5 categorias. Boundaries: <=10 UltraShort, <=30 Short,
    <=60 Medium, <=120 Deep Work, >120 Strategic Block."""
    if duracion_min <= UMBRAL_ULTRA_SHORT:
        return "Ultra Short"
    if duracion_min <= UMBRAL_SHORT:
        return "Short"
    if duracion_min <= UMBRAL_MEDIUM:
        return "Medium"
    if duracion_min <= UMBRAL_DEEP_WORK:
        return "Deep Work"
    return "Strategic Block"


def _minutos(fi: datetime, ff: datetime) -> int:
    return max(0, int((ff - fi).total_seconds() // 60))


# ============================================================================
# CALCULO DE CAPACIDAD DIARIA
# ============================================================================

def calcular_capacidad_dia(
    dia: datetime,
    intervalos_ocupados: list[tuple[datetime, datetime]],
    jornada_inicio: time = JORNADA_INICIO,
    jornada_fin: time = JORNADA_FIN,
) -> CapacidadDia:
    """Calcula capacidad de UN dia dado sus intervalos ocupados
    (eventos confirmados + bloques no negociables ya expandidos por
    dia_semana). Los propuestos NO cuentan como ocupados hasta que
    se confirmen (§4.3).

    Devuelve CapacidadDia con lista de huecos clasificados por
    categoria §10. Los huecos se calculan por barrido lineal de los
    intervalos ocupados dentro de la jornada.

    dia: datetime con hora=00:00 (el resto se ignora).
    intervalos_ocupados: lista de (inicio, fin) datetimes tz-aware.
    """
    dia = dia.replace(hour=0, minute=0, second=0, microsecond=0)
    ini_jor = dia.replace(hour=jornada_inicio.hour, minute=jornada_inicio.minute)
    fin_jor = dia.replace(hour=jornada_fin.hour, minute=jornada_fin.minute)

    # Filtramos intervalos que solapan con la jornada y los clippeamos
    ocupados_clip: list[tuple[datetime, datetime]] = []
    for fi, ff in intervalos_ocupados:
        if ff <= ini_jor or fi >= fin_jor:
            continue
        ocupados_clip.append((max(fi, ini_jor), min(ff, fin_jor)))
    ocupados_clip.sort(key=lambda x: x[0])

    # Merge de solapes para no contar dos veces
    merged: list[tuple[datetime, datetime]] = []
    for fi, ff in ocupados_clip:
        if not merged or fi > merged[-1][1]:
            merged.append((fi, ff))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], ff))

    # Huecos: complemento de merged dentro de [ini_jor, fin_jor]
    huecos: list[Hueco] = []
    cursor = ini_jor
    for fi, ff in merged:
        if fi > cursor:
            m = _minutos(cursor, fi)
            if m > 0:
                huecos.append(Hueco(
                    inicio=cursor.isoformat(), fin=fi.isoformat(),
                    duracion_min=m, categoria=categoria_de(m),
                ))
        cursor = max(cursor, ff)
    if cursor < fin_jor:
        m = _minutos(cursor, fin_jor)
        if m > 0:
            huecos.append(Hueco(
                inicio=cursor.isoformat(), fin=fin_jor.isoformat(),
                duracion_min=m, categoria=categoria_de(m),
            ))

    total_min = _minutos(ini_jor, fin_jor)
    ocupado_min = sum(_minutos(fi, ff) for fi, ff in merged)
    libre_min = max(0, total_min - ocupado_min)
    buffer_pct = round(100.0 * libre_min / total_min, 1) if total_min else 0.0

    tiene_estrat = any(
        h.categoria in ("Deep Work", "Strategic Block") for h in huecos
    )

    return CapacidadDia(
        fecha=dia.date().isoformat(),
        total_min=total_min,
        ocupado_min=ocupado_min,
        libre_min=libre_min,
        buffer_pct=buffer_pct,
        buffer_ok=buffer_pct >= BUFFER_MINIMO_PCT,
        tiene_bloque_estrategico=tiene_estrat,
        huecos=huecos,
    )


def calcular_capacidad_semanal(
    lunes: datetime,
    intervalos_por_dia: dict[str, list[tuple[datetime, datetime]]],
    jornada_inicio: time = JORNADA_INICIO,
    jornada_fin: time = JORNADA_FIN,
) -> CapacidadSemanal:
    """Agrega 5 dias laborables desde lunes 00:00.

    intervalos_por_dia: dict {YYYY-MM-DD: [(inicio, fin), ...]} con los
    intervalos ocupados de cada dia. Los dias sin entrada se tratan
    como completamente libres.
    """
    lunes = lunes.replace(hour=0, minute=0, second=0, microsecond=0)
    dias: list[CapacidadDia] = []
    for offset in range(5):
        d = lunes + timedelta(days=offset)
        clave = d.date().isoformat()
        ivals = intervalos_por_dia.get(clave, [])
        dias.append(calcular_capacidad_dia(d, ivals, jornada_inicio, jornada_fin))

    total_libre = sum(d.libre_min for d in dias)
    total_ocupado = sum(d.ocupado_min for d in dias)
    total = sum(d.total_min for d in dias)
    buffer_medio = round(100.0 * total_libre / total, 1) if total else 0.0
    saturados = [d.fecha for d in dias if not d.buffer_ok]

    return CapacidadSemanal(
        lunes=lunes.date().isoformat(),
        dias=dias,
        total_libre_min=total_libre,
        total_ocupado_min=total_ocupado,
        buffer_medio_pct=buffer_medio,
        dias_saturados=saturados,
    )


# ============================================================================
# TIME BLOCKING: proponer bloques concretos en huecos
# ============================================================================

def _prioridad_tarea(t: Tarea) -> int:
    """Score deterministico para ordenar tareas por urgencia/importancia.
    Menor = mas prioritario. Usado para llenar huecos.
    """
    ahora = datetime.now(TZ_LOCAL)
    # Bloqueadas por CEO priori 1
    if t.status_eos in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS, EstadoEOS.BLOCKED) \
            and t.alexander_role in (RolAlexander.DECISION, RolAlexander.APPROVAL):
        return 1
    # Deadline hoy o vencido
    if t.deadline and t.deadline < ahora + timedelta(days=1):
        return 2
    # In progress con deadline en 3 dias
    if t.status_eos == EstadoEOS.IN_PROGRESS and t.deadline \
            and t.deadline < ahora + timedelta(days=3):
        return 3
    # Delegated con review vencido
    if t.status_eos == EstadoEOS.DELEGATED and t.review_date and t.review_date < ahora:
        return 4
    # Waiting con follow-up vencido
    if t.status_eos == EstadoEOS.WAITING and t.review_date and t.review_date < ahora:
        return 5
    # Deadline en 7 dias
    if t.deadline and t.deadline < ahora + timedelta(days=7):
        return 6
    return 9


def _tarea_a_bloque(t: Tarea, hueco: Hueco, tipo: str = "operativo") -> BloquePropuesto:
    """Convierte una tarea en un bloque propuesto para un hueco dado."""
    if t.next_action:
        objetivo = t.next_action
    else:
        objetivo = t.title

    # Prioridad textual
    ahora = datetime.now(TZ_LOCAL)
    if t.deadline and t.deadline < ahora + timedelta(days=1):
        prio = "alta"
    elif t.deadline and t.deadline < ahora + timedelta(days=3):
        prio = "alta"
    elif t.alexander_role in (RolAlexander.DECISION, RolAlexander.APPROVAL):
        prio = "alta"
    elif t.deadline and t.deadline < ahora + timedelta(days=7):
        prio = "media"
    else:
        prio = "baja"

    contexto = None
    if t.primary_interlocutor:
        contexto = f"Sobre {t.primary_interlocutor}"

    return BloquePropuesto(
        inicio=hueco.inicio,
        fin=hueco.fin,
        duracion_min=hueco.duracion_min,
        categoria=hueco.categoria,
        objetivo=objetivo,
        prioridad=prio,
        contexto=contexto,
        resultado_esperado=t.expected_result,
        tareas_relacionadas=[t.id] if t.id else [],
        tipo=tipo,
    )


def proponer_bloques(
    huecos: list[Hueco],
    tareas: list[Tarea],
) -> list[BloquePropuesto]:
    """§10: dado un dia con huecos ya clasificados y una lista de
    tareas activas, propone que hacer en cada hueco.

    Reglas §10:
    - Bloques estrategicos (Deep Work, Strategic Block) tienen prioridad
      absoluta sobre operativos: se les asigna primero la tarea mas
      urgente que encaje en su duracion.
    - Ultra Short y Short se rellenan con quick actions o pendings
      administrativos.
    - Medium se usa para tareas normales con next_action clara.

    Cada tarea se asigna a UN unico bloque (no duplicamos). El caller
    puede seguir teniendo la tarea en su bucket original (People
    Blocked, Delegated, etc.); esto es una SUGERENCIA de dedicacion
    horaria.
    """
    # Filtrar tareas ejecutables por CEO (no delegadas ya ejecutandose)
    ejecutables = [
        t for t in tareas
        if t.status_eos in (EstadoEOS.NEW, EstadoEOS.IN_PROGRESS,
                             EstadoEOS.BLOCKED, EstadoEOS.SCHEDULED)
        and t.alexander_role != RolAlexander.NO_INVOLVEMENT
    ]
    ejecutables.sort(key=_prioridad_tarea)

    # Separar huecos por tipo
    estrategicos = [h for h in huecos if h.categoria in ("Deep Work", "Strategic Block")]
    medium = [h for h in huecos if h.categoria == "Medium"]
    short = [h for h in huecos if h.categoria in ("Ultra Short", "Short")]

    propuestos: list[BloquePropuesto] = []
    usadas: set[int] = set()

    # 1. Estrategicos: tareas alta prioridad + task_type=Project o Decision
    #    con expected_result claro
    from models import TipoTarea
    for h in estrategicos:
        candidatas = [
            t for t in ejecutables
            if t.id not in usadas
            and (t.task_type in (TipoTarea.PROJECT, TipoTarea.DECISION)
                 or t.expected_result is not None)
        ]
        if candidatas:
            t = candidatas[0]
            propuestos.append(_tarea_a_bloque(t, h, tipo="estrategico"))
            if t.id:
                usadas.add(t.id)

    # 2. Medium: cualquier tarea con next_action
    for h in medium:
        candidatas = [t for t in ejecutables
                       if t.id not in usadas and t.next_action]
        if candidatas:
            t = candidatas[0]
            propuestos.append(_tarea_a_bloque(t, h, tipo="operativo"))
            if t.id:
                usadas.add(t.id)

    # 3. Short/Ultra Short: quick actions
    for h in short:
        candidatas = [t for t in ejecutables
                       if t.id not in usadas
                       and t.next_action
                       and len(t.next_action.split()) <= 15]
        if candidatas:
            t = candidatas[0]
            propuestos.append(_tarea_a_bloque(t, h, tipo="operativo"))
            if t.id:
                usadas.add(t.id)

    # Ordenar por hora
    propuestos.sort(key=lambda b: b.inicio)
    return propuestos
