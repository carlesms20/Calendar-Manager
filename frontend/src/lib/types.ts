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
