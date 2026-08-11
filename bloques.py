"""Bloques no negociables del CEO (Caso 13 del PRD, PHASE 3 §6 Protected Deep Work).

Un bloque es una franja horaria recurrente semanal que el usuario declara
intocable: gimnasio, comida familiar, tiempo estratégico, etc. Los bloques
activos se restan como intervalos ocupados en consultar_huecos_libres.

Persistencia en Supabase (tabla bloques_no_negociables). Sigue el mismo
patrón que memory.py y usage.py: cliente sync, funciones async, aislamiento
por user_id, falla suave con logger cuando aplique.

Semántica intencionada:
- Solo bloquea la propuesta de huecos. NO impide que el agente cree un
  evento en esa franja si el usuario lo pide explícitamente (esa decisión
  queda en el system prompt).
- Delete real (crear/eliminar) para operaciones destructivas y desactivar
  para soft delete cuando el usuario quiere pausar el bloque un tiempo.
"""
import os
from datetime import datetime, time, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

import logger

load_dotenv()

_SUPABASE_URL = os.getenv("SUPABASE_URL")
_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not _SUPABASE_URL or not _SUPABASE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_SERVICE_KEY en el entorno. Revisa el .env."
    )

_client: Client = create_client(_SUPABASE_URL, _SUPABASE_KEY)


# --------------------- HELPERS INTERNOS -------------------------------

def _parse_hora(valor) -> time:
    """Acepta time o string 'HH:MM' | 'HH:MM:SS'. Lanza ValueError si no.

    Supabase devuelve time como string 'HH:MM:SS'; el caller puede pasar
    'HH:MM' cuando lo escribe un humano ('07:00'). Normalizamos aquí.
    """
    if isinstance(valor, time):
        return valor
    if not isinstance(valor, str):
        raise ValueError(f"Hora invalida (tipo {type(valor).__name__}): {valor!r}")
    partes = valor.strip().split(":")
    if len(partes) not in (2, 3):
        raise ValueError(f"Hora invalida (formato): {valor!r}")
    try:
        h = int(partes[0])
        m = int(partes[1])
        s = int(partes[2]) if len(partes) == 3 else 0
    except ValueError:
        raise ValueError(f"Hora invalida (no numerica): {valor!r}")
    return time(hour=h, minute=m, second=s)


def _validar_dias(dias_semana: list[int]) -> list[int]:
    """Devuelve la lista deduplicada y ordenada. Lanza ValueError si es
    inválida (vacía o algún dia fuera de 0-6).

    Postgres tambien lo validaria via CHECK constraints, pero fallar antes
    de la BD nos da un mensaje mas claro y ahorra un round trip."""
    if not dias_semana:
        raise ValueError("dias_semana no puede estar vacio")
    dias_unicos = sorted(set(int(d) for d in dias_semana))
    for d in dias_unicos:
        if d < 0 or d > 6:
            raise ValueError(f"dia_semana fuera de rango (0-6): {d}")
    return dias_unicos


def _fila_a_dict(fila: dict) -> dict:
    """Normaliza una fila cruda de Supabase al shape que consume el
    resto del sistema. Convierte times a string 'HH:MM' para simplicidad
    de LLM y frontend (que no consumen time nativos).
    """
    hora_inicio = _parse_hora(fila["hora_inicio"])
    hora_fin = _parse_hora(fila["hora_fin"])
    return {
        "id": fila["id"],
        "user_id": fila["user_id"],
        "nombre": fila["nombre"],
        "dias_semana": list(fila.get("dias_semana") or []),
        "hora_inicio": hora_inicio.strftime("%H:%M"),
        "hora_fin": hora_fin.strftime("%H:%M"),
        "activo": bool(fila.get("activo", True)),
        "descripcion": fila.get("descripcion", "") or "",
    }


# --------------------- INTERFAZ PUBLICA -------------------------------

async def listar(user_id: str, solo_activos: bool = True) -> list[dict]:
    """Devuelve los bloques del usuario, ordenados por (dia_semana, hora).

    Si solo_activos=True (default), filtra activo=true. Cuando el CEO
    pide "qué bloques tengo" queremos ver solo los que aplican.
    Para mantenimiento avanzado, solo_activos=False trae también los
    desactivados.
    """
    query = (
        _client.table("bloques_no_negociables")
        .select("*")
        .eq("user_id", user_id)
    )
    if solo_activos:
        query = query.eq("activo", True)
    query = query.order("hora_inicio")

    respuesta = query.execute()
    return [_fila_a_dict(f) for f in (respuesta.data or [])]


async def crear(
    user_id: str,
    nombre: str,
    dias_semana: list[int],
    hora_inicio,
    hora_fin,
    descripcion: str = "",
) -> dict:
    """Crea un bloque nuevo y devuelve el dict normalizado.

    Valida días y horas antes de tocar la BD. Si la BD rechaza (ej: horas
    invertidas por un caller que se saltó la validación), propaga la
    excepción — el caller la traduce a mensaje para el LLM.
    """
    dias_norm = _validar_dias(dias_semana)
    h_ini = _parse_hora(hora_inicio)
    h_fin = _parse_hora(hora_fin)
    if h_ini >= h_fin:
        raise ValueError(f"hora_inicio ({h_ini}) debe ser anterior a hora_fin ({h_fin})")

    fila = {
        "user_id":     user_id,
        "nombre":      nombre.strip(),
        "dias_semana": dias_norm,
        "hora_inicio": h_ini.strftime("%H:%M:%S"),
        "hora_fin":    h_fin.strftime("%H:%M:%S"),
        "descripcion": descripcion.strip(),
    }
    respuesta = _client.table("bloques_no_negociables").insert(fila).execute()
    if not respuesta.data:
        raise RuntimeError("Supabase no devolvio la fila creada; revisa RLS o el schema.")
    creado = _fila_a_dict(respuesta.data[0])
    logger.info(
        "bloques", "created",
        f"Bloque '{creado['nombre']}' creado ({creado['hora_inicio']}-{creado['hora_fin']})",
        user_id=user_id,
        metadata={
            "bloque_id": creado["id"],
            "dias_semana": creado["dias_semana"],
        },
    )
    return creado


async def eliminar(user_id: str, bloque_id: int) -> bool:
    """Borra un bloque del usuario. Devuelve True si borro fila, False si
    el id no existia o pertenecia a otro user (por seguridad, filtramos
    tambien por user_id: nunca dejamos a un user tocar bloques ajenos)."""
    respuesta = (
        _client.table("bloques_no_negociables")
        .delete()
        .eq("id", bloque_id)
        .eq("user_id", user_id)
        .execute()
    )
    borrado = bool(respuesta.data)
    if borrado:
        logger.info(
            "bloques", "deleted",
            f"Bloque id={bloque_id} eliminado",
            user_id=user_id,
            metadata={"bloque_id": bloque_id},
        )
    return borrado


async def desactivar(user_id: str, bloque_id: int) -> bool:
    """Soft delete: pone activo=false. Devuelve True si actualizo fila.

    Util cuando el CEO quiere pausar un bloque temporalmente (ej: 'quita
    el gym esta semana que estoy de viaje') sin perder la configuracion.
    """
    respuesta = (
        _client.table("bloques_no_negociables")
        .update({"activo": False, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", bloque_id)
        .eq("user_id", user_id)
        .execute()
    )
    desactivado = bool(respuesta.data)
    if desactivado:
        logger.info(
            "bloques", "deactivated",
            f"Bloque id={bloque_id} desactivado",
            user_id=user_id,
            metadata={"bloque_id": bloque_id},
        )
    return desactivado


async def listar_activos_para_calculo(user_id: str) -> list[dict]:
    """Optimizacion: devuelve solo los campos que _calcular_huecos necesita
    (dias_semana, hora_inicio, hora_fin) para todos los bloques activos.

    Se llama en cada consultar_huecos_libres, asi que evitamos traer
    'descripcion' y 'nombre' que no se usan en el calculo.
    """
    respuesta = (
        _client.table("bloques_no_negociables")
        .select("id, nombre, dias_semana, hora_inicio, hora_fin")
        .eq("user_id", user_id)
        .eq("activo", True)
        .execute()
    )
    return [
        {
            "id":          fila["id"],
            "nombre":      fila["nombre"],
            "dias_semana": list(fila.get("dias_semana") or []),
            "hora_inicio": _parse_hora(fila["hora_inicio"]),
            "hora_fin":    _parse_hora(fila["hora_fin"]),
        }
        for fila in (respuesta.data or [])
    ]