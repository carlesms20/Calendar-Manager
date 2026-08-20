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

// Tipos de tarea (PHASE 1 §8.1 campo Type). El backend puede devolver
// mas valores; los mantenemos abiertos con `| string` para no romper
// la app si el enum crece antes que el frontend.
export type TaskType =
  | "Project"
  | "Task"
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
  next_action: string | null;
  expected_result: string | null;
  review_date: string | null; // ISO 8601
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
  escalation_condition?: string;
}
