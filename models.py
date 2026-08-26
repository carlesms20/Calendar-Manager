from pydantic import BaseModel, field_validator, model_validator, Field
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

TZ_LOCAL = ZoneInfo("Europe/Madrid")

class Prioridad(str, Enum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class Categoria(str, Enum):
    PERSONAL = "personal"
    EMPRESA = "empresa"


class Eisenhower(str, Enum):
    URGENTE_IMPORTANTE = "urgente e importante"
    IMPORTANTE = "importante, no urgente"
    URGENTE = "urgente, no importante"
    NINGUNO = "no urgente, no importante"


class Acciones(str, Enum):
    CREAR_EVENTO = "crear evento"
    CONSULTAR = "consultar"
    MOVER = "mover"
    PREGUNTA_ACLARACION = "pregunta aclaracion"
    MENSAJE_TEXTO = "mensaje texto"

class EstadoEOS(str, Enum):
    """Estados permitidos de una tarea (PHASE 1 §6.1). Los valores son
    los strings literales de la spec — es lo que se persiste en
    UF_STATUS_EOS y lo que ve el LLM en tool results."""
    NEW = "New"
    IN_PROGRESS = "In Progress"
    DELEGATED = "Delegated"
    WAITING = "Waiting"
    BLOCKED = "Blocked"
    SCHEDULED = "Scheduled"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class TipoTarea(str, Enum):
    """Type del elemento de trabajo (PHASE 1 §8.1 - campo Type).
    Los 8 valores son los declarados por el spec; ni mas ni menos."""
    PROJECT = "Project"
    TASK = "Task"
    DELEGATED_WORK = "Delegated Work"
    WAITING = "Waiting"
    MEETING = "Meeting"
    DECISION = "Decision"
    RISK = "Risk"
    INFORMATION = "Information"


class RolAlexander(str, Enum):
    """Nivel de involucracion del CEO en la tarea (PHASE 1 §8.1 -
    campo Alexander Role). Determina si Alexander ejecuta, decide,
    aprueba, supervisa o no interviene."""
    EXECUTION = "Execution"
    DECISION = "Decision"
    APPROVAL = "Approval"
    SUPERVISION = "Supervision"
    NO_INVOLVEMENT = "No Involvement"

# Sin uso actual — reservado para futura tool crear_tarea (Bitrix Tasks).
# En eventos no aplica (el responsable es siempre el dueño del calendario).
RESPONSABLES_VALIDOS = ["Alexander", "Sandra", "Stefan", "Carlos"]

NO_DATA = "[NO DATA]"
"""Sentinela textual mandatoria por PHASE 1 §12.1 para datos ausentes.
Solo se emite en la serializacion hacia el LLM (Tarea.to_llm_dict) o
hacia Bitrix (bitrix_tasks.py). Internamente los campos opcionales de
Tarea son None, para que 'if not tarea.next_action' funcione natural."""


class TransicionIlegal(ValueError):
    """Se lanza cuando se intenta cambiar el estado de una tarea a otro
    no permitido por PHASE 1 §6.4. El mensaje contiene el motivo legible
    para que la tool que la capture pueda mostrarlo tal cual al LLM."""
    pass


# --- Transiciones legales de estado (PHASE 1 §6.4) ---
# Fuente unica de verdad. {estado_actual: {estados_destino_legales}}.
# Completed y Cancelled son terminales (sin transiciones salientes).
# "Cualquier estado activo -> Completed | Cancelled" del spec se
# materializa incluyendo COMPLETED y CANCELLED en cada set no terminal.
#
# Si el spec evoluciona (p.ej. permitir Blocked -> In Progress al
# desbloquearse), este es el UNICO sitio donde tocar la matriz.
_TRANSICIONES_LEGALES: dict[EstadoEOS, frozenset[EstadoEOS]] = {
    EstadoEOS.NEW:         frozenset({EstadoEOS.IN_PROGRESS, EstadoEOS.DELEGATED,
                                       EstadoEOS.SCHEDULED, EstadoEOS.COMPLETED,
                                       EstadoEOS.CANCELLED}),
    EstadoEOS.IN_PROGRESS: frozenset({EstadoEOS.SCHEDULED, EstadoEOS.WAITING,
                                       EstadoEOS.BLOCKED, EstadoEOS.COMPLETED,
                                       EstadoEOS.CANCELLED}),
    EstadoEOS.DELEGATED:   frozenset({EstadoEOS.WAITING, EstadoEOS.COMPLETED,
                                       EstadoEOS.CANCELLED}),
    EstadoEOS.WAITING:     frozenset({EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}),
    EstadoEOS.BLOCKED:     frozenset({EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}),
    EstadoEOS.SCHEDULED:   frozenset({EstadoEOS.COMPLETED, EstadoEOS.CANCELLED}),
    EstadoEOS.COMPLETED:   frozenset(),
    EstadoEOS.CANCELLED:   frozenset(),
}

# --- Mapping EOS -> STATUS nativo Bitrix ---
# tasks.task.getFields declara STATUS como enum:
#   2 = Waiting for execution  (default de Bitrix)
#   3 = In progress
#   4 = Awaiting control
#   5 = Completed
#   6 = Deferred
#
# Sincronizar es CRITICO: sin esto, la UI de Bitrix muestra "Pending"
# aunque nuestro EOS diga "Completed" o "Delegated", las tareas
# Cancelled siguen apareciendo en el calendar activo por su DEADLINE,
# y las notificaciones nativas de Bitrix se disparan a destiempo.
# Alexander mira Bitrix directamente algunas veces; ver datos
# contradictorios entre EOS y Bitrix rompe la confianza.
#
# Bitrix no tiene "Cancelled" nativo. Se mapea a 5 (Completed) — la
# saca del calendar activo. La distincion Completed real vs Cancelled
# vive en UF_STATUS_EOS, que sigue siendo la fuente de verdad EOS.
STATUS_BITRIX_POR_EOS: dict[EstadoEOS, int] = {
    EstadoEOS.NEW:         2,  # Waiting for execution
    EstadoEOS.IN_PROGRESS: 3,  # In progress
    EstadoEOS.DELEGATED:   2,  # asignada al responsable, sin empezar
    EstadoEOS.WAITING:     4,  # Awaiting control (esperando respuesta externa)
    EstadoEOS.BLOCKED:     6,  # Deferred (aplazada por bloqueo)
    EstadoEOS.SCHEDULED:   2,  # Waiting for execution con tiempo asignado
    EstadoEOS.COMPLETED:   5,  # Completed
    EstadoEOS.CANCELLED:   5,  # Sin equivalente nativo; ver comentario arriba
}

# Conjunto de STATUS nativos de Bitrix que consideramos "activos" cuando
# UF_STATUS_EOS esta vacio (tareas legacy creadas antes de que existiera
# el EOS, o creadas manualmente en Bitrix sin pasar por el agente).
#
# Diseño alineado con la vista "In progress" del Bitrix UI:
#   1 = Pending (nativo Bitrix)
#   2 = Waiting for execution
#   3 = In progress
# se consideran ACTIVAS. Se excluyen:
#   4 = Awaiting control  (Bitrix las oculta de "In progress")
#   5 = Completed         (terminal)
#   6 = Deferred          (aplazada, Bitrix las oculta)
#   7 = Almost done       (ambiguo; excluir es lo conservador)
#
# Sin este set, el filtro `solo_activos` mostraba 27 tareas cuando
# Bitrix UI mostraba 8, porque cualquier tarea con status_eos=None
# pasaba el check `not in {COMPLETED, CANCELLED}` aunque su STATUS
# nativo fuera 4/5/6 y estuviera efectivamente cerrada o aplazada.
STATUS_BITRIX_ACTIVO: set[int] = {1, 2, 3}

class Evento(BaseModel):
    """Evento de calendario que se sincroniza con Bitrix (calendar.event.add).

    Campos alineados con la sección 4 del PRD 'especificaciones_agente_agenda.md'.
    """
    # obligatorios
    nombre: str
    duracion_min: int = Field(gt=0)
    fecha_inicio: datetime
    categoria: Categoria           # "personal" | "empresa"
    prioridad: Prioridad           # "alta" | "media" | "baja" — la calcula el agente

    # opcional — persona o grupo con quien es el evento
    involucrado: str = ""

    # opcionales
    descripcion: str = ""          # no está en la tabla del PRD pero suele ser útil
    fecha_limite: datetime | None = None
    tipo_actividad: str = ""       # "reunion", "llamada", "tarea admin", etc.
    eisenhower: Eisenhower | None = None

    @field_validator("fecha_inicio")
    @classmethod
    def fecha_inicio_no_pasada(cls, v):
        # si Gemini manda la fecha sin tzinfo, asumimos hora local
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ_LOCAL)
        if v < datetime.now(TZ_LOCAL):
            raise ValueError("La fecha de inicio no puede estar en el pasado")
        return v

    @field_validator("fecha_limite")
    @classmethod
    def fecha_limite_no_pasada(cls, v):
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ_LOCAL)
        if v < datetime.now(TZ_LOCAL):
            raise ValueError("La fecha límite no puede estar en el pasado")
        return v

    @model_validator(mode="after")
    def fecha_limite_posterior_a_inicio(self):
        if self.fecha_limite is not None and self.fecha_limite < self.fecha_inicio:
            raise ValueError("La fecha límite no puede ser anterior a la fecha de inicio")
        return self

class Tarea(BaseModel):
    """Tarea del Executive Operating System, mapeada sobre Bitrix Tasks.

    Representa una tarea con:
    - Campos nativos de Bitrix: id, title.
    - 15 UF_* alineados con PHASE 1 §6.1 (estado) y §8 (work item model).

    Los campos opcionales usan None internamente. La conversion a la
    sentinela textual "[NO DATA]" (PHASE 1 §12.1) ocurre solo en la
    serializacion hacia el LLM (to_llm_dict) o hacia Bitrix (Pieza 3,
    bitrix_tasks.py). Asi el codigo Python idiomatico funciona:
    'if tarea.next_action' es False para datos ausentes, sin trampas.

    Diseno de validacion:
    - Constructor laxo: acepta cualquier combinacion valida por tipo,
      para poder reflejar tareas pre-existentes en Bitrix que no tienen
      UF_* poblados (status_eos=None es admisible).
    - Las transiciones de estado se validan solo al aplicarse, via
      validar_transicion_a (dry-run) o transicionar_a (aplica + valida).
      La logica vive aqui, no en las tools, para que ningun caller
      pueda saltarse la comprobacion (§6.4).
    """
    # --- Nativos de Bitrix ---
    id: int | None = None                 # None hasta que Bitrix lo asigna
    title: str

    # STATUS nativo de Bitrix (campo STATUS, entero 1..7). Fallback para
    # decidir si una tarea legacy sin UF_STATUS_EOS es o no activa
    # (ver TAREA_ES_ACTIVA_POR_STATUS_BITRIX abajo). Convivimos con dos
    # fuentes de verdad:
    #   1. UF_STATUS_EOS: fuente EOS canonica (los 8 estados de PHASE 1
    #      §6.1). Puede estar vacio para tareas creadas por Bitrix
    #      directamente sin pasar por el agente.
    #   2. STATUS nativo: siempre presente. Bitrix lo llena con su
    #      propio ciclo de vida (Pending, In Progress, Completed,
    #      Deferred, Awaiting control, Declined, Disapproved).
    # Cuando (1) esta vacio, (2) manda para el filtro solo_activos y
    # para el brief. Ver models.py STATUS_BITRIX_ACTIVO.
    status_bitrix_nativo: int | None = None

    # RESPONSIBLE_ID nativo Bitrix (Sprint 4). Sirve para resolver el
    # nombre del Owner en el Brief via user.get. Lo tratamos como
    # metadata inmutable (no lo modifica el LLM directamente; se usa
    # actualizar_estado_tarea o crear_tarea con arg owner=str).
    responsable_id_bitrix: int | None = None

    # --- UF_* Estado y clasificacion ---
    status_eos: EstadoEOS | None = None
    task_type: TipoTarea | None = None
    alexander_role: RolAlexander | None = None

    # --- Fecha limite nativa Bitrix (campo DEADLINE, no UF_) ---
    # PHASE 1 §8.1 distingue Deadline (cuando hay que terminar) de
    # Review Date (cuando revisar delegacion/waiting). Bitrix tiene
    # DEADLINE nativo; lo mapeamos aqui como campo distinto.
    deadline: datetime | None = None

    # --- UF_* Ejecucion ---
    next_action: str | None = None
    expected_result: str | None = None
    review_date: datetime | None = None
    source: str | None = None

    # --- UF_* Delegacion (Sprint 4, PHASE 1 §7.2) ---
    # preparation_required: que hay que preparar ANTES de que el CEO se
    #   involucre. §7.5 delega la preparacion cuando puede hacerse sin
    #   el CEO. Ej: "recopilar precios de 3 competidores", "presupuesto
    #   marketing Q4 con desglose por canal".
    # next_action_if_missed: que se hace si vence el deadline sin
    #   resolucion. §7.2 lo pide explicitamente para delegaciones.
    #   Ej: "escalar a CEO", "renegociar deadline con owner".
    preparation_required: str | None = None
    next_action_if_missed: str | None = None

    # --- UF_* Riesgo y control ---
    risk: str | None = None
    escalation_condition: str | None = None

    # --- UF_* Conversacion ejecutiva ---
    requires_conversation: bool | None = None
    primary_interlocutor: str | None = None
    conversation_purpose: str | None = None
    expected_decision: str | None = None
    meeting_candidate: bool | None = None
    related_meeting_id: str | None = None

    @field_validator("review_date", "deadline")
    @classmethod
    def _normalizar_tz_fechas(cls, v):
        """Asegura tzinfo en review_date y deadline. Si viene naive,
        asumimos Europe/Madrid para no romper aritmetica de fechas
        contra datetime.now(TZ_LOCAL)."""
        if v is None:
            return v
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ_LOCAL)
        return v

    def validar_transicion_a(self, nuevo: EstadoEOS) -> None:
        """Comprueba que se pueda pasar del estado actual al 'nuevo'
        segun PHASE 1 §6.4. Lanza TransicionIlegal si no.

        No muta el estado. Util para pre-validar antes de aplicar (por
        ejemplo, para pintar un error detallado al LLM antes de tocar
        Bitrix).

        Casos:
        - status_eos actual = None: cualquier estado inicial es legal
          (tarea pre-existente sin UF_STATUS_EOS o tarea recien creada).
        - actual == nuevo: no-op valido, no lanza.
        - actual terminal (Completed/Cancelled): siempre ilegal, con
          mensaje explicando que no hay salida.
        - resto: consulta la matriz _TRANSICIONES_LEGALES.
        """
        actual = self.status_eos

        if actual is None or actual == nuevo:
            return

        legales = _TRANSICIONES_LEGALES.get(actual, frozenset())
        if nuevo not in legales:
            if not legales:
                razon = f"'{actual.value}' es estado terminal, no admite transiciones salientes"
            else:
                permitidos = ", ".join(sorted(e.value for e in legales))
                razon = f"desde '{actual.value}' solo se permite: {permitidos}"
            raise TransicionIlegal(
                f"Transicion ilegal '{actual.value}' -> '{nuevo.value}': "
                f"{razon}. Ver PHASE 1 §6.4."
            )

    def transicionar_a(self, nuevo: EstadoEOS) -> None:
        """Aplica la transicion in-place tras validarla. Lanza
        TransicionIlegal si no es legal.

        Este es el metodo que usan las tools de actualizacion de estado.
        Usarlo garantiza que ningun cambio de estado se guarde sin haber
        pasado por la matriz §6.4.
        """
        self.validar_transicion_a(nuevo)
        self.status_eos = nuevo

    def to_llm_dict(self) -> dict:
        """Serializa la tarea a dict listo para incluir en la respuesta
        de una tool al LLM. Los None se emiten como '[NO DATA]'
        (PHASE 1 §12.1) para que el modelo entienda explicitamente que
        el dato falta, en lugar de recibir null o key ausente y tener
        que inferirlo.

        - Enums: .value (string legible: 'In Progress', 'Delegated'...).
        - datetime: ISO 8601. None -> [NO DATA].
        - bool: True/False como estan. None -> [NO DATA] (distinto de
          False semanticamente: 'no evaluado' vs 'evaluado y no').
        - int (id): tal cual. None -> [NO DATA] (no deberia darse
          post-insert, pero se mantiene la consistencia por si acaso).
        """
        def s(v):
            if v is None:
                return NO_DATA
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, Enum):
                return v.value
            return v

        return {
            "id":                    s(self.id),
            "title":                 self.title,
            "status_eos":            s(self.status_eos),
            "task_type":             s(self.task_type),
            "alexander_role":        s(self.alexander_role),
            "deadline":              s(self.deadline),
            "next_action":           s(self.next_action),
            "expected_result":       s(self.expected_result),
            "review_date":           s(self.review_date),
            "source":                s(self.source),
            "preparation_required":  s(self.preparation_required),
            "next_action_if_missed": s(self.next_action_if_missed),
            "risk":                  s(self.risk),
            "escalation_condition":  s(self.escalation_condition),
            "requires_conversation": s(self.requires_conversation),
            "primary_interlocutor":  s(self.primary_interlocutor),
            "conversation_purpose":  s(self.conversation_purpose),
            "expected_decision":     s(self.expected_decision),
            "meeting_candidate":     s(self.meeting_candidate),
            "related_meeting_id":    s(self.related_meeting_id),
        }

class RespuestaAgente(BaseModel):
    """Envoltorio de respuesta con tipo de acción + payload.

    NOTA: con tool calling activo su rol es limitado. Se mantiene por
    compatibilidad y por si sirve como formato interno más adelante.
    """
    tipo_accion: Acciones
    mensaje: str
    evento: Evento | None = None

    @model_validator(mode="after")
    def evento_coherente_con_accion(self):
        if self.tipo_accion == Acciones.CREAR_EVENTO and self.evento is None:
            raise ValueError("Para crear evento necesitas el EVENTO")
        if self.tipo_accion != Acciones.CREAR_EVENTO and self.evento is not None:
            raise ValueError("Si NO estás creando evento, NO debe haber evento")
        return self

def calcular_prioridad(
    involucrado: str,
    fecha_limite: datetime | None,
    ahora: datetime | None = None,
) -> Prioridad:
    """Calcula la prioridad de un evento según la regla del PRD (Caso 8).

    Regla:
        - Sin fecha_limite, sin involucrado → baja
        - Sin fecha_limite, con involucrado → media
        - fecha_limite a ≤1 día → alta (siempre)
        - fecha_limite a 2-3 días sin involucrado → media
        - fecha_limite a 2-3 días con involucrado → alta
        - fecha_limite a >3 días → base (media si involucrado, baja si no)

    Los días se miden en días de calendario (ceil natural), no como float:
    "hoy" = 0, "mañana" = 1, "pasado mañana" = 2.

    Args:
        involucrado: texto libre. Cuenta como "hay involucrado" si no es
            vacío tras strip.
        fecha_limite: si es None, no se aplica la parte de urgencia por tiempo.
        ahora: para tests deterministas. Si es None, usa datetime.now(TZ_LOCAL).
    """
    tiene_involucrado = bool(involucrado and involucrado.strip())
    base = Prioridad.MEDIA if tiene_involucrado else Prioridad.BAJA

    if fecha_limite is None:
        return base

    if ahora is None:
        ahora = datetime.now(TZ_LOCAL)

    # Asegurar tzinfo para evitar TypeError al restar naive vs aware
    if fecha_limite.tzinfo is None:
        fecha_limite = fecha_limite.replace(tzinfo=TZ_LOCAL)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=TZ_LOCAL)

    delta_dias = (fecha_limite.date() - ahora.date()).days

    if delta_dias <= 1:
        return Prioridad.ALTA
    if delta_dias <= 3:
        return Prioridad.ALTA if tiene_involucrado else Prioridad.MEDIA
    return base