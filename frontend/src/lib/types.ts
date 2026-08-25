// Tipos compartidos del frontend.

export type Role = "user" | "assistant";

export interface Message {
  id: string;
  role: Role;
  content: string;
  timestamp: number;
}

export interface Evento {
  id: string;
  nombre: string;
  fecha_inicio: string; // ISO 8601
  fecha_fin: string;
  descripcion: string;
  prioridad?: "alta" | "media" | "baja" | "";
}

// Estados EOS de una Tarea (PHASE 1 §6.1). Los 8 permitidos.
export type EstadoEOS =
  | "New"
  | "In Progress"
  | "Delegated"
  | "Waiting"
  | "Blocked"
  | "Scheduled"
  | "Completed"
  | "Cancelled";

// Tipos de tarea (PHASE 1 §8.1 campo Type). Enum completo backend
// (models.TipoTarea). Lo mantenemos abierto con `| string` de todas
// formas por si crece antes que el frontend.
export type TaskType =
  | "Project"
  | "Task"
  | "Delegated Work"
  | "Waiting"
  | "Meeting"
  | "Decision"
  | "Risk"
  | "Information"
  | string;

// Roles de Alexander (PHASE 1 §7).
export type AlexanderRole =
  | "Execution"
  | "Decision"
  | "Approval"
  | "Supervision"
  | "No Involvement"
  | string;

// Tarea tal como la devuelve /api/tareas. Refleja models.Tarea con los
// 15 UF_* + nativos. Nulls significan "sin dato en Bitrix" (PHASE 1 §12.1),
// pero en el JSON preferimos null a la sentinela [NO DATA] que sí usamos
// cuando serializamos al LLM.
export interface Tarea {
  id: number;
  title: string;
  status_eos: EstadoEOS | null;
  task_type: TaskType | null;
  alexander_role: AlexanderRole | null;
  deadline: string | null;   // ISO 8601 - fecha limite (nativo Bitrix DEADLINE)
  next_action: string | null;
  expected_result: string | null;
  review_date: string | null; // ISO 8601 - fecha de revision (UF_REVIEW_DATE)
  source: string | null;
  risk: string | null;
  escalation_condition: string | null;
  requires_conversation: boolean | null;
  primary_interlocutor: string | null;
  conversation_purpose: string | null;
  expected_decision: string | null;
  meeting_candidate: boolean | null;
  related_meeting_id: string | null;
}

export interface RespuestaListaTareas {
  tareas: Tarea[];
  total_disponibles: number;
  truncado: boolean;
}

// Payload para PATCH /api/tareas/:id/estado.
export interface CambioEstadoTarea {
  nuevo_estado: EstadoEOS;
  owner?: string;
  next_action?: string;
  expected_result?: string;
  review_date?: string; // ISO 8601
  deadline?: string;    // ISO 8601
  escalation_condition?: string;
}

// -------------------------------------------------------------------------
// Executive Brief (Sprint 3, PHASE 1 §4). Espejo de brief.py BriefEjecutivo.
// -------------------------------------------------------------------------

export type TipoItemCalendario = "confirmado" | "propuesto" | "bloque_protegido";

export interface ItemCalendario {
  id: string;
  nombre: string;
  fecha_inicio: string;
  fecha_fin: string;
  duracion_min: number;
  involucrado: string | null;
  tipo: TipoItemCalendario;
}

export interface CalendarOverview {
  confirmados: ItemCalendario[];
  propuestos: ItemCalendario[];
  bloques_protegidos: ItemCalendario[];
  capacidad_ocupada_min: number;
  capacidad_total_min: number;
  buffer_pct: number;
  conflictos: string[];
  riesgo_fragmentacion: boolean;
}

export interface ItemTareaBrief {
  id: number;
  title: string;
  status_eos: EstadoEOS | null;
  task_type: TaskType | null;
  owner_es_ceo: boolean;
  next_action: string | null;
  deadline: string | null;
  review_date: string | null;
  primary_interlocutor: string | null;
  razon: string | null;
}

export type MecanismoOutcome =
  | "trabajo_ceo"
  | "decision"
  | "aprobacion"
  | "delegacion"
  | "conversacion"
  | "desbloqueo"
  | string;

export interface KeyOutcome {
  resultado: string;
  mecanismo: MecanismoOutcome;
  razon: string;
  items_relacionados: number[];
}

export interface ItemConversacion {
  interlocutor: string;
  temas: string[];
  tareas_relacionadas: number[];
  decisiones_esperadas: string[];
  duracion_estimada_min: number;
  prioridad: "alta" | "media" | "baja" | string;
  horario_propuesto: string | null;
  estado_confirmacion: string;
  impacto_no_celebrarla: string | null;
}

export interface IntegrityFinding {
  check: string;
  ok: boolean;
  detalle: string | null;
}

export interface BriefEjecutivo {
  generado_en: string;
  user_id: string;
  fecha_ref: string;
  executive_summary: string;
  calendar_overview: CalendarOverview;
  three_key_outcomes: KeyOutcome[];
  quick_actions: ItemTareaBrief[];
  people_blocked: ItemTareaBrief[];
  executive_conversations: ItemConversacion[];
  delegated_supervision: ItemTareaBrief[];
  waiting: ItemTareaBrief[];
  proposed_work_blocks: string[];
  not_today: ItemTareaBrief[];
  remaining_inventory_total: number;
  remaining_inventory_por_tipo: Record<string, number>;
  missing_information: string[];
  integrity_check: IntegrityFinding[];
}
