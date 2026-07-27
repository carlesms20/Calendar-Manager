from pydantic import BaseModel, field_validator, model_validator
from datetime import datetime
from enum import Enum

class Prioridad(str, Enum):
    CRITICA = "critica"
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
    CREAR_TAREA = "crear tarea"
    CONSULTAR = "consultar"
    MOVER = "mover"
    PREGUNTA_ACLARACION = "pregunta aclaracion"
    MENSAJE_TEXTO = "mensaje texto"

RESPONSABLES_VALIDOS = ["Alexander", "Sandra", "Stefan", "Carlos"] #ejemplos, en futuro sera llamada a webhook

class Tarea(BaseModel): #para validar si la tarea tiene un formato correcto para meter en bitrix
    #obligatorios
    nombre: str
    descripcion: str
    responsable: str
    categoria: Categoria           # "personal" | "empresa" 

    #opcionales
    objetivo: str = ""
    resultado_esperado: str = ""
    checklist: list[str] = []
    participantes: list[str] = []
    deadline: datetime | None = None
    prioridad: Prioridad = Prioridad.MEDIA    # "critica" | "alta" | "media" | "baja" 
    departamento: str = ""
    dependencias: list[str] = []
    eisenhower: Eisenhower | None = None    # opcional según PRD, no siempre se clasifica

    @field_validator("deadline") #valida que el datetime no sea pasado, por tanto invalido
    @classmethod
    def deadline_no_pasado(cls, v):
        if v is not None and v < datetime.now():
            raise ValueError("El deadline no puede estar en el pasado")
        return v

    @field_validator("responsable") #valida que responsable sea de lista de coordinadores, jefes...
    @classmethod
    def responsable_en_lista_blanca(cls, v):
        if v not in RESPONSABLES_VALIDOS:
            raise ValueError(f"Responsable '{v}' no válido. Debe ser uno de: {RESPONSABLES_VALIDOS}")
        return v

    @model_validator(mode="after")
    def empresa_requiere_participantes(self):
        if self.categoria == Categoria.EMPRESA and not self.participantes:
            raise ValueError("Las tareas de empresa requieren al menos un participante")
        return self

class RespuestaAgente(BaseModel): #modelo principal, este nos servirá para estructurar la respuesta del agente. YA que tiene que tener campos estrictos para el funcionamiento correcto en bitrix
    tipo_accion: Acciones
    mensaje: str
    tarea: Tarea | None = None

    @model_validator(mode="after")
    def tarea_coherente_con_accion(self):
        if self.tipo_accion == Acciones.CREAR_TAREA and self.tarea == None:
            raise ValueError("Para crear evento necesitas la TAREA")
        if self.tipo_accion != Acciones.CREAR_TAREA and self.tarea is not None:
            raise ValueError("Si NO estas creando evento, NO debe haber tarea")
        return self