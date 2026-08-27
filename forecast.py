"""forecast.py — Forecast Engine (Sprint 5, PHASE 6 Doc 3 §14)

Proyecta la planificacion futura para detectar sobrecarga ANTES de que
ocurra. La regla dura §14: "Nunca deberá esperar a que el conflicto ya
exista".

Alcance de este modulo:
- Proyeccion N semanas hacia delante (default: 1).
- Deteccion de:
  * carga futura vs capacidad
  * saturacion del calendario
  * acumulacion de Waiting
  * exceso de reuniones
  * riesgos de capacidad (deadlines concurrentes)

No hace IO. Consume datos ya recopilados (eventos, tareas, bloques) y
capacity precalculada. Devuelve estructura para renderizar en el brief.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from pydantic import BaseModel

from models import Tarea, EstadoEOS, TZ_LOCAL
from capacity import CapacidadSemanal, BUFFER_MINIMO_PCT


# Umbrales
MAX_REUNIONES_SEMANA_SANO = 15
MAX_WAITING_ACUMULADO_SANO = 8


class RiesgoForecast(BaseModel):
    """Un riesgo detectado por el forecast."""
    categoria: str  # sobrecarga_semanal | saturacion_dia | exceso_reuniones |
                    # acumulacion_waiting | deadlines_concurrentes
    severidad: str  # alta | media | baja
    descripcion: str
    dia_afectado: str | None = None  # YYYY-MM-DD si aplica


class ForecastSemana(BaseModel):
    """Snapshot de la proyeccion de una semana futura."""
    lunes: str
    carga_esperada_min: int
    capacidad_disponible_min: int
    ratio_carga: float  # 1.0 = 100%, >1.0 = sobrecarga
    n_eventos_agendados: int
    n_reuniones_propuestas: int
    n_waiting_acumulado: int
    n_deadlines_esa_semana: int
    riesgos: list[RiesgoForecast]


# ============================================================================
# CALCULO
# ============================================================================

def _dentro_de_semana(dt: datetime, lunes: datetime) -> bool:
    return lunes <= dt < lunes + timedelta(days=7)


def forecast_semana(
    lunes: datetime,
    capacidad: CapacidadSemanal,
    eventos_raw: list[dict],
    reuniones_propuestas: int,
    tareas: list[Tarea],
) -> ForecastSemana:
    """Proyecta una semana concreta. lunes debe ser 00:00 lunes local.

    Estimacion de carga esperada:
      = tiempo de eventos ya agendados esa semana
      + tiempo estimado de tareas con deadline en esa semana (30 min/tarea
        como aproximacion; refinar cuando modelemos esfuerzo)
      + tiempo estimado de reuniones propuestas (30 min c/u por defecto)

    ratio_carga = carga_esperada / capacidad_disponible
    """
    lunes = lunes.replace(hour=0, minute=0, second=0, microsecond=0)

    # 1) Eventos ya agendados en la semana
    n_eventos = 0
    min_eventos = 0
    for e in eventos_raw:
        fi_raw = e.get("DATE_FROM") or e.get("_fi")
        ff_raw = e.get("DATE_TO") or e.get("_ff")
        try:
            if isinstance(fi_raw, datetime):
                fi = fi_raw
            else:
                fi = datetime.fromisoformat(str(fi_raw).replace("Z", "+00:00"))
                if fi.tzinfo is None:
                    fi = fi.replace(tzinfo=TZ_LOCAL)
                fi = fi.astimezone(TZ_LOCAL)
            if isinstance(ff_raw, datetime):
                ff = ff_raw
            else:
                ff = datetime.fromisoformat(str(ff_raw).replace("Z", "+00:00"))
                if ff.tzinfo is None:
                    ff = ff.replace(tzinfo=TZ_LOCAL)
                ff = ff.astimezone(TZ_LOCAL)
        except (ValueError, TypeError):
            continue
        if _dentro_de_semana(fi, lunes):
            n_eventos += 1
            min_eventos += max(0, int((ff - fi).total_seconds() // 60))

    # 2) Tareas con deadline en la semana (aprox 30 min/tarea)
    ESTIMACION_MIN_POR_TAREA = 30
    n_deadlines = 0
    min_tareas = 0
    for t in tareas:
        if t.status_eos in (EstadoEOS.COMPLETED, EstadoEOS.CANCELLED):
            continue
        if t.deadline and _dentro_de_semana(t.deadline, lunes):
            n_deadlines += 1
            min_tareas += ESTIMACION_MIN_POR_TAREA

    # 3) Reuniones propuestas (aprox 30 min c/u)
    min_reuniones = reuniones_propuestas * 30

    # 4) Waiting acumulado (no consume carga pero es señal)
    n_waiting = sum(
        1 for t in tareas if t.status_eos == EstadoEOS.WAITING
    )

    carga_total = min_eventos + min_tareas + min_reuniones
    capacidad_disp = capacidad.total_libre_min + capacidad.total_ocupado_min
    ratio = round(carga_total / capacidad_disp, 2) if capacidad_disp else 0.0

    riesgos: list[RiesgoForecast] = []

    # R1: sobrecarga semanal
    if ratio > 1.0:
        riesgos.append(RiesgoForecast(
            categoria="sobrecarga_semanal",
            severidad="alta",
            descripcion=(
                f"Semana con ratio de carga {ratio}: la demanda estimada "
                f"({carga_total} min) supera la capacidad "
                f"({capacidad_disp} min)."
            ),
        ))
    elif ratio > 0.85:
        riesgos.append(RiesgoForecast(
            categoria="sobrecarga_semanal",
            severidad="media",
            descripcion=(
                f"Semana muy cargada (ratio {ratio}). Menos del 15% de "
                "margen para imprevistos."
            ),
        ))

    # R2: dias saturados (buffer < 30%)
    for d in capacidad.dias_saturados:
        riesgos.append(RiesgoForecast(
            categoria="saturacion_dia",
            severidad="media",
            descripcion=f"Sin buffer del {BUFFER_MINIMO_PCT}% en el dia.",
            dia_afectado=d,
        ))

    # R3: exceso de reuniones
    total_meetings = n_eventos + reuniones_propuestas
    if total_meetings > MAX_REUNIONES_SEMANA_SANO:
        riesgos.append(RiesgoForecast(
            categoria="exceso_reuniones",
            severidad="media",
            descripcion=(
                f"{total_meetings} reuniones en la semana "
                f"(saludable: <={MAX_REUNIONES_SEMANA_SANO}). "
                "Considera consolidar o delegar."
            ),
        ))

    # R4: acumulacion de Waiting
    if n_waiting > MAX_WAITING_ACUMULADO_SANO:
        riesgos.append(RiesgoForecast(
            categoria="acumulacion_waiting",
            severidad="baja",
            descripcion=(
                f"{n_waiting} tareas en Waiting: hay riesgo de perder "
                "seguimiento. Considera cerrar o escalar las mas antiguas."
            ),
        ))

    # R5: deadlines concurrentes
    if n_deadlines > 5:
        riesgos.append(RiesgoForecast(
            categoria="deadlines_concurrentes",
            severidad="media",
            descripcion=(
                f"{n_deadlines} deadlines en esta semana. "
                "Priorizar cual es imprescindible."
            ),
        ))

    return ForecastSemana(
        lunes=lunes.date().isoformat(),
        carga_esperada_min=carga_total,
        capacidad_disponible_min=capacidad_disp,
        ratio_carga=ratio,
        n_eventos_agendados=n_eventos,
        n_reuniones_propuestas=reuniones_propuestas,
        n_waiting_acumulado=n_waiting,
        n_deadlines_esa_semana=n_deadlines,
        riesgos=riesgos,
    )
