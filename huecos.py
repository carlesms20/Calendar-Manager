"""Lógica de cálculo de huecos libres (Caso 3 del PRD).

Separada del resto de tools por dos razones:
1. La función pura _calcular_huecos es testeable sin tocar Bitrix.
2. El código de negocio (constantes, helpers) queda agrupado y explícito.
"""
import re
from datetime import datetime, timedelta, time
from models import TZ_LOCAL

# --- Constantes de configuración ---
HORA_LABORAL_INICIO = 9        # inclusive
HORA_LABORAL_FIN = 20          # exclusive (hasta 19:59:59)
MARGEN_MIN = 5                 # buffer entre eventos, en minutos
DURACION_DEFECTO_MIN = 30
DIA_DOMINGO = 6                # weekday(): lun=0..dom=6

_PALABRAS_DIA_LIBRE = {
    "libre", "descanso", "vacaciones", "vacacion", "vacación",
    "off", "festivo", "asueto", "baja",
}

_NOMBRES_DIAS = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo",
]


def _es_evento_todo_el_dia(inicio: datetime, fin: datetime) -> bool:
    """Heurística all-day: dura >= 20 horas.

    Bitrix marca los all-day con DATE_FROM 00:00:00 y DATE_TO 23:59:59;
    algunos clientes usan variantes, así que damos margen.
    """
    return (fin - inicio).total_seconds() >= 20 * 3600


def _es_dia_libre(nombre: str) -> bool:
    """True si el título del evento sugiere descanso/vacaciones/día off.

    Un cumpleaños o aniversario all-day NO bloquea; sí bloquea un evento
    llamado 'libre', 'descanso', 'vacaciones', 'off', etc.
    """
    tokens = set(re.findall(r"\b\w+\b", nombre.lower()))
    return bool(tokens & _PALABRAS_DIA_LIBRE)


def _fusionar_ocupados(ocupados: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Ordena y fusiona intervalos que se solapan o se tocan."""
    if not ocupados:
        return []
    ocupados = sorted(ocupados, key=lambda x: x[0])
    fusionados = [ocupados[0]]
    for inicio, fin in ocupados[1:]:
        ultimo_inicio, ultimo_fin = fusionados[-1]
        if inicio <= ultimo_fin:
            fusionados[-1] = (ultimo_inicio, max(ultimo_fin, fin))
        else:
            fusionados.append((inicio, fin))
    return fusionados


def _huecos_dia(
    dia_inicio: datetime,
    dia_fin: datetime,
    ocupados: list[tuple[datetime, datetime]],
    duracion_min: int,
) -> list[tuple[datetime, datetime]]:
    """Devuelve los huecos ≥ duracion_min entre dia_inicio y dia_fin,
    dados los intervalos ocupados (ya fusionados)."""
    huecos = []
    cursor = dia_inicio
    for op_inicio, op_fin in ocupados:
        if op_fin <= cursor:
            continue
        if op_inicio >= dia_fin:
            break
        if op_inicio > cursor:
            top = min(op_inicio, dia_fin)
            if (top - cursor).total_seconds() / 60 >= duracion_min:
                huecos.append((cursor, top))
        cursor = max(cursor, op_fin)

    if cursor < dia_fin and (dia_fin - cursor).total_seconds() / 60 >= duracion_min:
        huecos.append((cursor, dia_fin))

    return huecos


def _calcular_huecos(
    eventos_bitrix: list[dict],
    fecha_desde: datetime,
    fecha_hasta: datetime,
    duracion_min: int,
    incluir_domingo: bool,
    incluir_fuera_horario: bool,
    ahora: datetime,
    parse_fecha,
) -> list[dict]:
    """Núcleo del algoritmo, testeable sin red.

    Args:
        eventos_bitrix: la lista tal cual la devuelve consultar_eventos_bitrix.
        fecha_desde, fecha_hasta: rango a inspeccionar (ambos aware).
        duracion_min: duración mínima del hueco, en minutos.
        incluir_domingo: True para no saltar domingos.
        incluir_fuera_horario: True para usar ventana 00:00-23:59 en vez
            del horario laboral.
        ahora: para tests deterministas.
        parse_fecha: fn (str) -> datetime, para parsear DATE_FROM/TO de Bitrix.
    """
    # 1) Recortar al presente: no proponemos huecos en el pasado
    if fecha_desde < ahora:
        fecha_desde = ahora
    if fecha_desde >= fecha_hasta:
        return []

    # 2) Convertir eventos a intervalos ocupados con margen de MARGEN_MIN
    ocupados: list[tuple[datetime, datetime]] = []
    for ev in eventos_bitrix:
        try:
            inicio = parse_fecha(ev["DATE_FROM"])
            fin = parse_fecha(ev["DATE_TO"])
        except (KeyError, ValueError):
            continue
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=TZ_LOCAL)
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=TZ_LOCAL)

        # Bitrix marca algunos all-day con DATE_FROM == DATE_TO (duración cero)
        # o con DT_SKIP_TIME='Y'. Expandimos a día completo para que la
        # lógica de all-day funcione uniformemente.
        if fin <= inicio or str(ev.get("DT_SKIP_TIME", "")).upper() == "Y":
            fin = inicio.replace(hour=23, minute=59, second=59)

        # All-day: solo bloquea si el título indica "día libre / descanso"
        if _es_evento_todo_el_dia(inicio, fin) and not _es_dia_libre(ev.get("NAME", "")):
            continue

        # Margen de MARGEN_MIN por cada lado
        ocupados.append((
            inicio - timedelta(minutes=MARGEN_MIN),
            fin + timedelta(minutes=MARGEN_MIN),
        ))

    ocupados = _fusionar_ocupados(ocupados)

    # 3) Iterar día a día
    if incluir_fuera_horario:
        h_ini, h_fin = 0, 24
    else:
        h_ini, h_fin = HORA_LABORAL_INICIO, HORA_LABORAL_FIN

    resultados: list[dict] = []
    dia = fecha_desde.date()
    dia_fin_iter = fecha_hasta.date()

    while dia <= dia_fin_iter:
        if dia.weekday() == DIA_DOMINGO and not incluir_domingo:
            dia += timedelta(days=1)
            continue

        # Ventana del día
        ventana_ini = datetime.combine(dia, time(h_ini, 0), tzinfo=TZ_LOCAL)
        if h_fin >= 24:
            ventana_fin_dt = datetime.combine(dia, time(23, 59, 59), tzinfo=TZ_LOCAL)
        else:
            ventana_fin_dt = datetime.combine(dia, time(h_fin, 0), tzinfo=TZ_LOCAL)

        # Recortar por el rango solicitado
        ventana_ini = max(ventana_ini, fecha_desde)
        ventana_fin_dt = min(ventana_fin_dt, fecha_hasta)

        if ventana_ini < ventana_fin_dt:
            for hueco_ini, hueco_fin in _huecos_dia(ventana_ini, ventana_fin_dt, ocupados, duracion_min):
                duracion = int((hueco_fin - hueco_ini).total_seconds() / 60)
                dia_nombre = _NOMBRES_DIAS[hueco_ini.weekday()]
                etiqueta = (
                    f"{dia_nombre.capitalize()} "
                    f"{hueco_ini.strftime('%H:%M')}–{hueco_fin.strftime('%H:%M')}"
                )
                resultados.append({
                    "inicio": hueco_ini.isoformat(),
                    "fin": hueco_fin.isoformat(),
                    "duracion_min": duracion,
                    "dia_semana": dia_nombre,
                    "etiqueta": etiqueta,
                })

        dia += timedelta(days=1)

    return resultados