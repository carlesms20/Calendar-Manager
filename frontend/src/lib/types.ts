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
  preparation_required: string | null;   // Sprint 4 §7.2
  next_action_if_missed: string | null;  // Sprint 4 §7.2
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
  preparation_required?: string;    // Sprint 4 §7.2
  next_action_if_missed?: string;   // Sprint 4 §7.2
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
  owner_nombre: string | null;      // Sprint 4: nombre resuelto Owner
  next_action: string | null;
  deadline: string | null;
  review_date: string | null;
  primary_interlocutor: string | null;
  waiting_for: string | null;       // Sprint 4: expected_result si status=Waiting
  dias_vencido: number | null;      // Sprint 4: dias desde review_date si vencio
  escalation_condition: string | null;   // Sprint 4
  preparation_required: string | null;   // Sprint 4
  next_action_if_missed: string | null;  // Sprint 4
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

// Sprint 4 - Meeting Delegation Rule §7.4
export type RecomendacionAsistencia = "asistir" | "delegar" | "decidir_asincrono" | string;

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
  recomendacion_asistencia: RecomendacionAsistencia;  // Sprint 4
  razon_recomendacion: string | null;                  // Sprint 4
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
  proposed_work_blocks: BloquePropuesto[];  // Sprint 5: ahora estructurados
  not_today: ItemTareaBrief[];
  remaining_inventory_total: number;
  remaining_inventory_por_tipo: Record<string, number>;
  missing_information: string[];
  integrity_check: IntegrityFinding[];

  // --- Sprint 5 nuevas secciones ---
  capacidad_hoy: CapacidadDia | null;
  forecast_proxima_semana: ForecastSemana | null;
  reminders: ReminderItem[];
}

// Sprint 5 - Capacity Planning + Time Blocking (PHASE 6 Doc 3 §9-§10)

export interface Hueco {
  inicio: string;
  fin: string;
  duracion_min: number;
  categoria: string;  // Ultra Short | Short | Medium | Deep Work | Strategic Block
}

export interface CapacidadDia {
  fecha: string;
  total_min: number;
  ocupado_min: number;
  libre_min: number;
  buffer_pct: number;
  buffer_ok: boolean;
  tiene_bloque_estrategico: boolean;
  huecos: Hueco[];
}

export interface BloquePropuesto {
  inicio: string;
  fin: string;
  duracion_min: number;
  categoria: string;
  objetivo: string;
  prioridad: "alta" | "media" | "baja" | string;
  contexto: string | null;
  resultado_esperado: string | null;
  tareas_relacionadas: number[];
  tipo: "operativo" | "estrategico" | string;
}

// Sprint 5 - Reminder Engine (§13)

export type CategoriaReminder =
  | "persona_bloqueada"
  | "decision"
  | "dependencia_externa"
  | "revision_comprometida"
  | "riesgo_incumplimiento"
  | "reunion_propuesta_no_confirmada"
  | string;

export interface ReminderItem {
  prioridad_num: number;  // 1..6
  categoria: CategoriaReminder;
  titulo: string;
  detalle: string | null;
  accion_sugerida: string | null;
  tarea_id: number | null;
  persona: string | null;
}

// Sprint 5 - Forecast Engine (§14)

export interface RiesgoForecast {
  categoria: string;
  severidad: "alta" | "media" | "baja" | string;
  descripcion: string;
  dia_afectado: string | null;
}

export interface ForecastSemana {
  lunes: string;
  carga_esperada_min: number;
  capacidad_disponible_min: number;
  ratio_carga: number;
  n_eventos_agendados: number;
  n_reuniones_propuestas: number;
  n_waiting_acumulado: number;
  n_deadlines_esa_semana: number;
  riesgos: RiesgoForecast[];
}
