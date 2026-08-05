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


# Sin uso actual — reservado para futura tool crear_tarea (Bitrix Tasks).
# En eventos no aplica (el responsable es siempre el dueño del calendario).
RESPONSABLES_VALIDOS = ["Alexander", "Sandra", "Stefan", "Carlos"]


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