"""Cliente REST de Bitrix24 para el modulo Tasks + mapping bidireccional
Tarea <-> raw Bitrix. Espejo funcional de bitrix.py (para calendar) pero
con paginacion propia: tasks.task.list devuelve result.tasks (array
anidado con 'next'), no una lista plana como calendar.event.get.

Boundary de tipos (PHASE 1 §12.1):
- Escribiendo: Tarea.field=None -> se omite la clave (no toca ese UF_).
- Leyendo:     UF_="" o clave ausente -> Tarea.field=None internamente.
La sentinela textual '[NO DATA]' NO se usa aqui; ese es problema de la
capa de serializacion al LLM (Tarea.to_llm_dict, en models.py).

Convencion de nombres:
- Bitrix espera UPPERCASE para campos nativos (TITLE, RESPONSIBLE_ID) y
  para UF_* (siempre UPPERCASE por regla propia). Fuente: getFields.
- Aqui los lookups son defensivos: normalizamos claves de la respuesta
  a UPPERCASE por si algun endpoint devuelve camelCase.
"""
import httpx
from datetime import datetime
from enum import Enum

from bitrix import BitrixError
from models import (
    Tarea, EstadoEOS, TipoTarea, RolAlexander, TZ_LOCAL,
    STATUS_BITRIX_POR_EOS,
)
import logger


# ============================================================================
# 1. TABLA DE MAPPING (fuente unica de verdad Tarea <-> Bitrix UF_)
# ============================================================================

# Cada tupla: (atributo_python, clave_bitrix_uppercase, tipo_serializacion).
# tipo: "str" | "bool" | "datetime" | EnumClass.
# El orden aqui = orden de SORT en Bitrix (mantener alineado con el
# bootstrap_bitrix_fields para consistencia visual en la UI Bitrix).
_CAMPOS_UF: list[tuple] = [
    ("status_eos",            "UF_STATUS_EOS",            EstadoEOS),
    ("task_type",             "UF_TASK_TYPE",             TipoTarea),
    ("alexander_role",        "UF_ALEXANDER_ROLE",        RolAlexander),
    ("next_action",           "UF_NEXT_ACTION",           "str"),
    ("expected_result",       "UF_EXPECTED_RESULT",       "str"),
    ("review_date",           "UF_REVIEW_DATE",           "datetime"),
    ("source",                "UF_SOURCE",                "str"),
    ("risk",                  "UF_RISK",                  "str"),
    ("escalation_condition",  "UF_ESCALATION_CONDITION",  "str"),
    ("requires_conversation", "UF_REQUIRES_CONVERSATION", "bool"),
    ("primary_interlocutor",  "UF_PRIMARY_INTERLOCUTOR",  "str"),
    ("conversation_purpose",  "UF_CONVERSATION_PURPOSE",  "str"),
    ("expected_decision",     "UF_EXPECTED_DECISION",     "str"),
    ("meeting_candidate",     "UF_MEETING_CANDIDATE",     "bool"),
    ("related_meeting_id",    "UF_RELATED_MEETING_ID",    "str"),
]

# Select por defecto para list/get. Sin esto tasks.task.list NO devuelve
# los UF_*, solo los nativos por defecto.
_SELECT_TAREA_COMPLETA: list[str] = (
    ["ID", "TITLE", "DESCRIPTION", "STATUS", "RESPONSIBLE_ID",
     "CREATED_BY", "CREATED_DATE", "DEADLINE"]
    + [clave for _, clave, _ in _CAMPOS_UF]
)


# ============================================================================
# 2. CONVERSORES DE TIPO (Python <-> formato Bitrix)
# ============================================================================

def _parse_bool_bitrix(v) -> bool | None:
    """Bitrix devuelve UF boolean como '1'/'0' (mas comun), a veces
    'Y'/'N' o el bool nativo. Vacio o None -> None (no evaluado)."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "y", "true"):
        return True
    if s in ("0", "n", "false"):
        return False
    return None  # valor raro, tratamos como no evaluado


def _parse_datetime_bitrix(v) -> datetime | None:
    """Acepta ISO 8601 (con o sin tz) y 'dd.mm.YYYY HH:MM:SS' y
    'dd.mm.YYYY'. Vacio -> None. Naive -> se asume TZ_LOCAL.
    Nunca lanza: formato no reconocido -> None + warn log, para no
    romper el parse completo de una tarea por un solo campo raro."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=TZ_LOCAL) if v.tzinfo is None else v
    s = str(v).strip()
    if not s:
        return None
    # ISO 8601 (con Z o offset explicito)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=TZ_LOCAL) if dt.tzinfo is None else dt
    except ValueError:
        pass
    # dd.mm.YYYY HH:MM:SS (formato Bitrix comun en calendar)
    try:
        return datetime.strptime(s, "%d.%m.%Y %H:%M:%S").replace(tzinfo=TZ_LOCAL)
    except ValueError:
        pass
    # dd.mm.YYYY (fecha sin hora)
    try:
        return datetime.strptime(s, "%d.%m.%Y").replace(tzinfo=TZ_LOCAL)
    except ValueError:
        pass
    logger.warn("bitrix_tasks", "datetime_parse_fail",
                f"No pude parsear datetime UF_ '{s}', devolviendo None")
    return None


def _parse_enum_bitrix(v, enum_cls):
    """Devuelve miembro del enum o None. Si el valor externo no cuadra
    con ningun miembro (p.ej. alguien escribio 'Foo' en UF_STATUS_EOS
    desde la UI Bitrix), warn log + None. No lanza."""
    if v is None or v == "":
        return None
    if isinstance(v, enum_cls):
        return v
    s = str(v).strip()
    for member in enum_cls:
        if member.value == s:
            return member
    logger.warn("bitrix_tasks", "enum_parse_fail",
                f"Valor '{s}' no coincide con {enum_cls.__name__}, devolviendo None")
    return None


def _serializar_valor(valor, tipo):
    """Convierte valor Python a formato Bitrix segun el tipo declarado
    en _CAMPOS_UF. None NO se pasa: los callers deben omitir la clave."""
    if tipo == "str":
        return str(valor)
    if tipo == "bool":
        return "1" if valor else "0"
    if tipo == "datetime":
        if isinstance(valor, datetime):
            # Si es naive lo asumimos TZ_LOCAL; Bitrix aguanta ISO con offset.
            if valor.tzinfo is None:
                valor = valor.replace(tzinfo=TZ_LOCAL)
            return valor.isoformat()
        return str(valor)
    if isinstance(tipo, type) and issubclass(tipo, Enum):
        return valor.value if isinstance(valor, Enum) else str(valor)
    raise ValueError(f"Tipo de mapping desconocido: {tipo!r}")


# ============================================================================
# 3. SERIALIZADORES PUBLICOS (Tarea/cambios -> dict Bitrix, y viceversa)
# ============================================================================

def tarea_a_bitrix_fields(tarea: Tarea) -> dict:
    """Convierte Tarea completa a dict 'fields' listo para tasks.task.add.
    Omite claves cuyo valor es None (Bitrix aplicara sus defaults).

    Sincroniza STATUS nativo Bitrix con status_eos (ver mapping en
    models.STATUS_BITRIX_POR_EOS). Sin esta sincronizacion, la UI de
    Bitrix mostraria 'Pending' aunque el EOS diga 'Completed', y las
    tareas Cancelled seguirian apareciendo en el calendar activo.
    """
    fields: dict = {"TITLE": tarea.title}
    for attr, clave, tipo in _CAMPOS_UF:
        val = getattr(tarea, attr)
        if val is None:
            continue
        fields[clave] = _serializar_valor(val, tipo)
    # Sync STATUS nativo cuando conocemos el status EOS
    if tarea.status_eos is not None:
        fields["STATUS"] = STATUS_BITRIX_POR_EOS[tarea.status_eos]
    # DEADLINE es campo NATIVO Bitrix (no UF_). Va aparte de _CAMPOS_UF.
    if tarea.deadline is not None:
        fields["DEADLINE"] = _serializar_valor(tarea.deadline, "datetime")
    return fields


# Indice por atributo Python para cambios_a_bitrix_fields (lookup rapido).
_INDICE_ATTR: dict = {attr: (clave, tipo) for attr, clave, tipo in _CAMPOS_UF}


def cambios_a_bitrix_fields(cambios: dict) -> dict:
    """Convierte dict de cambios parciales (keys = atributos Python de
    Tarea) a dict 'fields' Bitrix. Usado por actualizar_tarea para
    updates que solo tocan un subset de campos.

    - attr desconocido -> ValueError (bug del caller, debe romper).
    - attr con valor None -> se OMITE (no se interpreta como 'borrar').
      Si en el futuro necesitamos borrar campos, se hara con sentinel
      explicito (p.ej. usar "" para strings) porque Bitrix no distingue
      bien null vs unset en el UF_.

    Cuando el cambio incluye status_eos, tambien se sincroniza STATUS
    nativo Bitrix (ver tarea_a_bitrix_fields).
    """
    fields: dict = {}
    for attr, val in cambios.items():
        if attr == "title":
            if val is not None:
                fields["TITLE"] = str(val)
            continue
        # DEADLINE es campo NATIVO Bitrix (no UF_). Se maneja aparte porque
        # no vive en _INDICE_ATTR (que solo cubre UF_*).
        if attr == "deadline":
            if val is not None:
                fields["DEADLINE"] = _serializar_valor(val, "datetime")
            continue
        if attr not in _INDICE_ATTR:
            raise ValueError(
                f"cambios_a_bitrix_fields: atributo Tarea desconocido '{attr}'"
            )
        if val is None:
            continue
        clave, tipo = _INDICE_ATTR[attr]
        fields[clave] = _serializar_valor(val, tipo)
    # Sync STATUS nativo si el cambio incluye status_eos.
    # Aceptamos EstadoEOS o str por defensividad (el caller normalmente
    # pasa el enum, pero si viniera del LLM ya como string, no petamos).
    if "status_eos" in cambios and cambios["status_eos"] is not None:
        val = cambios["status_eos"]
        estado = val if isinstance(val, EstadoEOS) else EstadoEOS(val)
        fields["STATUS"] = STATUS_BITRIX_POR_EOS[estado]
    return fields

def _camel_a_upper_snake(k: str) -> str:
    """Convierte camelCase a UPPER_SNAKE_CASE.
        'ufStatusEos'   -> 'UF_STATUS_EOS'
        'responsibleId' -> 'RESPONSIBLE_ID'
        'id'            -> 'ID'
        'title'         -> 'TITLE'

    Bitrix v3 (tasks.task.get/list) devuelve las respuestas con las
    claves en camelCase AUNQUE el 'select' de la request se envia en
    UPPER_SNAKE_CASE. Sin esta normalizacion todos los lookups por
    UF_STATUS_EOS etc. fallan silenciosamente y las tareas se leen
    como si tuvieran todos los UF_* vacios ([NO DATA]) — con el efecto
    secundario grave de que validar_transicion_a hace early return al
    ver status_eos=None, lo que permite transiciones ilegales sin
    protestar.
    """
    import re
    return re.sub(r'(?<!^)([A-Z])', r'_\1', k).upper()

def bitrix_dict_a_tarea(raw: dict) -> Tarea:
    """Convierte objeto raw de Bitrix (una task del array de list, o el
    result.task de get/add) a instancia Tarea. Robusto:
    - Normaliza claves de camelCase (formato de respuesta v3) a
      UPPER_SNAKE_CASE (formato de nuestro _CAMPOS_UF). Sin esto los
      lookups por UF_STATUS_EOS fallan y todo sale como [NO DATA].
    - Trata "" y null como None (Bitrix devuelve string vacio o null
      para UF unset).
    - Valores enum externos raros -> None + warn (via _parse_enum).
    - datetime malformado -> None + warn.
    """
    d: dict = {}
    for k, v in raw.items():
        if isinstance(k, str):
            d[_camel_a_upper_snake(k)] = v
        else:
            d[k] = v

    id_raw = d.get("ID")
    try:
        id_val = int(id_raw) if id_raw not in (None, "") else None
    except (TypeError, ValueError):
        id_val = None

    kwargs: dict = {
        "id":    id_val,
        "title": str(d.get("TITLE") or ""),
    }

    # DEADLINE nativo Bitrix. Lo parseamos aparte de los UF_ porque no
    # vive en _CAMPOS_UF. Si Bitrix lo devuelve "" o null, deadline=None.
    dl_raw = d.get("DEADLINE")
    if dl_raw not in (None, ""):
        kwargs["deadline"] = _parse_datetime_bitrix(dl_raw)
    # (si es None/vacio, dejamos que el default None del modelo aplique)

    for attr, clave, tipo in _CAMPOS_UF:
        val = d.get(clave)
        if val is None or val == "":
            kwargs[attr] = None
            continue
        if tipo == "str":
            kwargs[attr] = str(val)
        elif tipo == "bool":
            kwargs[attr] = _parse_bool_bitrix(val)
        elif tipo == "datetime":
            kwargs[attr] = _parse_datetime_bitrix(val)
        elif isinstance(tipo, type) and issubclass(tipo, Enum):
            kwargs[attr] = _parse_enum_bitrix(val, tipo)

    return Tarea(**kwargs)


# ============================================================================
# 4. HTTP HELPER (para los 4 metodos REST de tasks; no paginado)
# ============================================================================

def _normalizar_webhook(webhook: str) -> str:
    """Igual que en bootstrap: garantiza trailing slash para poder
    concatenar limpio con el nombre del metodo."""
    webhook = webhook.strip()
    if not webhook.endswith("/"):
        webhook += "/"
    return webhook

# --- Cache local anti-lag de tasks.task.list ---
# Bitrix crea tareas sincronamente (tasks.task.add devuelve id
# inmediatamente) pero el indice de tasks.task.list tarda ~1-3s en
# verlas. Sin este cache: crear + consultar en el mismo turno del LLM
# devuelve la lista sin la tarea recien creada, el LLM se lia y
# responde cosas raras al usuario.
#
# Formato: {task_id: (timestamp_creacion, responsable_id, Tarea)}.
# TTL 30s: pasado ese margen asumimos que el indice Bitrix ya la ve.
# Actualizado tambien por actualizar_tarea para no servir estados
# stale (p.ej. si cancelas dentro de la ventana de 30s).
#
# NO se persiste: si el proceso se reinicia (Railway redeploy), cache
# vacio. Es un buffer de robustez, no una fuente de verdad. La fuente
# de verdad es Bitrix.
_CACHE_TAREAS_RECIENTES: dict[int, tuple[float, int, Tarea]] = {}
_TTL_CACHE_SEG = 30.0


def _cache_guardar(task_id: int, responsable_id: int, tarea: Tarea) -> None:
    """Registra una tarea recien creada en el cache local."""
    import time
    _CACHE_TAREAS_RECIENTES[task_id] = (time.time(), responsable_id, tarea)


def _cache_actualizar(task_id: int, cambios: dict) -> None:
    """Aplica cambios a una tarea que este en el cache, si existe.
    Si no esta cacheada (>30s desde su creacion), no hace nada — el
    proximo listar leera el estado actualizado directo de Bitrix.
    """
    if task_id not in _CACHE_TAREAS_RECIENTES:
        return
    ts, r_id, tarea = _CACHE_TAREAS_RECIENTES[task_id]
    for attr, val in cambios.items():
        if hasattr(tarea, attr):
            setattr(tarea, attr, val)
    _CACHE_TAREAS_RECIENTES[task_id] = (ts, r_id, tarea)


def _cache_leer_frescas(filtro_responsable_id: int | None) -> list[Tarea]:
    """Devuelve tareas cacheadas que:
    - No han expirado (< TTL)
    - Machean el filtro de responsable si viene

    De paso, purga las expiradas para no acumular basura.
    """
    import time
    ahora = time.time()
    frescas: list[Tarea] = []
    expirados: list[int] = []
    for tid, (ts, r_id, tarea) in _CACHE_TAREAS_RECIENTES.items():
        if ahora - ts > _TTL_CACHE_SEG:
            expirados.append(tid)
            continue
        if filtro_responsable_id is None or r_id == filtro_responsable_id:
            frescas.append(tarea)
    for tid in expirados:
        del _CACHE_TAREAS_RECIENTES[tid]
    return frescas

async def _llamar(client: httpx.AsyncClient, webhook: str,
                  metodo: str, params: dict) -> dict:
    """POST simple a un metodo Bitrix. Devuelve el contenido de
    result[] entero (dict), sin paginar. Los metodos de tasks devuelven
    objetos anidados (result.task, result.tasks, result.item), no listas
    planas — por eso no reutilizamos bitrix.solicitud().

    Errores Bitrix con 'error_description' quedan en el mensaje de
    BitrixError para diagnostico directo.
    """
    try:
        resp = await client.post(f"{webhook}{metodo}", json=params)
    except httpx.RequestError as e:
        raise BitrixError(f"Red fallo llamando {metodo}: {e}") from e

    try:
        data = resp.json()
    except ValueError as e:
        raise BitrixError(
            f"{metodo} devolvio no-JSON (HTTP {resp.status_code}): "
            f"{resp.text[:300]}"
        ) from e

    if "error" in data:
        err = data.get("error", "")
        desc = data.get("error_description", "")
        raise BitrixError(f"{metodo} rechazado: [{err}] {desc}")

    if not resp.is_success:
        # 4xx/5xx sin 'error' en body: raro pero posible
        raise BitrixError(
            f"{metodo} HTTP {resp.status_code} sin error en body: "
            f"{resp.text[:300]}"
        )

    return data.get("result", {})


# ============================================================================
# 5. API PUBLICA: 4 funciones REST
# ============================================================================

async def crear_tarea(webhook: str, responsable_id: int, tarea: Tarea) -> int:
    """Crea una tarea en Bitrix. Devuelve el ID numerico creado.

    RESPONSIBLE_ID es obligatorio en Bitrix (una tarea sin executor no
    se crea). Se pasa como arg suelto para no persistirlo en el modelo
    Tarea (fuera del scope Sprint 2). CREATED_BY se deja por defecto:
    Bitrix asignara el usuario del webhook.

    Tras crear, guarda la tarea en el cache local anti-lag para que
    consultar/listar inmediato en el mismo turno del LLM la vea, aunque
    el indice de tasks.task.list todavia no la haya propagado.
    """
    webhook = _normalizar_webhook(webhook)
    fields = tarea_a_bitrix_fields(tarea)
    fields["RESPONSIBLE_ID"] = responsable_id
    params = {"fields": fields}

    async with httpx.AsyncClient(timeout=60) as client:
        result = await _llamar(client, webhook, "tasks.task.add", params)

    # tasks.task.add devuelve {"task": {"id": N, ...}} en v1/v3.
    # Fallback a "item" por si algun endpoint devuelve otra forma.
    task_obj = result.get("task") or result.get("item") or {}
    task_id_raw = task_obj.get("id") or task_obj.get("ID")
    if task_id_raw is None:
        raise BitrixError(
            f"tasks.task.add exito pero sin id parseable en result: {result!r}"
        )
    task_id = int(task_id_raw)

    # Poblar cache anti-lag con la Tarea ya con id asignado
    tarea.id = task_id
    _cache_guardar(task_id, responsable_id, tarea)

    return task_id


async def obtener_tarea(webhook: str, task_id: int) -> Tarea:
    """Fetch de una tarea por ID con todos los UF_* poblados. Lanza
    BitrixError si Bitrix devuelve error (incluye task no existente)."""
    webhook = _normalizar_webhook(webhook)
    params = {
        "taskId": task_id,
        "select": _SELECT_TAREA_COMPLETA,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        result = await _llamar(client, webhook, "tasks.task.get", params)

    task_obj = result.get("task") or result.get("item") or result
    return bitrix_dict_a_tarea(task_obj)


async def listar_tareas(
    webhook: str,
    filtro: dict | None = None,
    orden: dict | None = None,
) -> list[Tarea]:
    """Lista de tareas paginada. filtro y orden usan claves UPPERCASE
    de Bitrix (RESPONSIBLE_ID, DEADLINE, TITLE, etc. — ver docs de
    tasks.task.list para el conjunto valido).

    Merge con el cache anti-lag: las tareas recien creadas (<30s) que
    Bitrix aun no devuelve por lag de indice, se inyectan del cache.
    Dedup por id — si Bitrix ya la ve, gana Bitrix.

    Ejemplo:
        listar_tareas(webhook, filtro={"RESPONSIBLE_ID": 42,
                                       "!REAL_STATUS": 5})

    Pagina en bloques de 50. Sin cap explicito: para tenants con
    miles de tareas activas, el caller deberia acotar con filtro
    (fecha, responsable, estado).
    """
    webhook = _normalizar_webhook(webhook)
    todas: list[dict] = []
    start = 0

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            params: dict = {
                "select": _SELECT_TAREA_COMPLETA,
                "start":  start,
            }
            if filtro:
                params["filter"] = filtro
            if orden:
                params["order"] = orden

            result = await _llamar(client, webhook, "tasks.task.list", params)
            pagina = result.get("tasks") or result.get("items") or []
            todas.extend(pagina)

            siguiente = result.get("next")
            if siguiente is None or siguiente == start:
                break
            start = siguiente

    tareas_bitrix = [bitrix_dict_a_tarea(t) for t in todas]

    # Merge con cache anti-lag: añadir tareas cacheadas que Bitrix
    # aun no devuelve por lag de indice (dedup por id).
    resp_id_filtro = None
    if filtro:
        r_id = filtro.get("RESPONSIBLE_ID")
        if isinstance(r_id, int):
            resp_id_filtro = r_id
    cacheadas = _cache_leer_frescas(resp_id_filtro)
    if cacheadas:
        ids_ya_vistos = {t.id for t in tareas_bitrix if t.id is not None}
        nuevas = [t for t in cacheadas if t.id not in ids_ya_vistos]
        if nuevas:
            tareas_bitrix.extend(nuevas)

    return tareas_bitrix


async def actualizar_tarea(
    webhook: str,
    task_id: int,
    cambios: dict,
    responsable_id: int | None = None,
) -> None:
    """Update parcial de una tarea. 'cambios' usa nombres Python
    (status_eos, next_action, review_date...); la conversion a UF_* y
    tipos Bitrix se hace aqui.

    'responsable_id', si viene, se inyecta como RESPONSIBLE_ID nativo
    (reasignacion de owner). Pasa como arg separado porque no forma
    parte del modelo Tarea (owner no persiste en la instancia, ver
    scope Sprint 2).

    Si la tarea esta en el cache anti-lag (creada hace <30s), aplica
    los mismos cambios sobre la copia cacheada para no servir estados
    stale en listar_tareas dentro de la misma ventana.

    IMPORTANTE: para cambios de estado (status_eos), el caller debe
    haber pasado antes por Tarea.transicionar_a() o validar_transicion_a()
    para respetar PHASE 1 §6.4. Esta funcion NO valida transiciones:
    es un dumb setter de campos.
    """
    if not cambios and responsable_id is None:
        return
    webhook = _normalizar_webhook(webhook)
    fields = cambios_a_bitrix_fields(cambios) if cambios else {}
    if responsable_id is not None:
        fields["RESPONSIBLE_ID"] = responsable_id
    if not fields:
        return
    params = {"taskId": task_id, "fields": fields}

    async with httpx.AsyncClient(timeout=60) as client:
        await _llamar(client, webhook, "tasks.task.update", params)

    # Refrescar cache anti-lag si esta tarea aun esta dentro de la
    # ventana de 30s desde su creacion
    if cambios:
        _cache_actualizar(task_id, cambios)