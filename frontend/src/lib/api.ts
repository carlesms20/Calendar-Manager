// Cliente HTTP contra el backend Python (server.py).
// En dev, Vite proxea /api/* a localhost:8000.
// En prod, frontend y backend viven en el mismo origen.

import type {
  Evento,
  RespuestaListaTareas,
  CambioEstadoTarea,
  EstadoEOS,
  BriefEjecutivo,
} from "./types";

export interface RespuestaAgente {
  reply: string;
  agenda_modificada: boolean;
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

// Helper interno: extrae un mensaje de error legible de una respuesta HTTP
// no-2xx. FastAPI devuelve {"detail": "..."} en errores; fallback a text().
async function _extraerError(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (body?.detail && typeof body.detail === "string") return body.detail;
  } catch {
    // no-op: no era JSON
  }
  try {
    const t = await res.text();
    return t || fallback;
  } catch {
    return fallback;
  }
}

/**
 * Envia un mensaje de texto al agente y devuelve su respuesta.
 */
export async function enviarTexto(text: string): Promise<RespuestaAgente> {
  const res = await fetch("/api/mensaje", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error del servidor"));
  }

  return await res.json();
}

/**
 * Envia un audio al agente. El backend lo transcribe y procesa como si
 * fuera un mensaje de texto.
 */
export async function enviarAudio(blob: Blob): Promise<RespuestaAgente> {
  const formData = new FormData();
  formData.append("audio", blob, "audio.webm");

  const res = await fetch("/api/audio", {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error procesando el audio"));
  }

  return await res.json();
}

/**
 * Obtiene los eventos del calendario para un rango de fechas.
 * Si no se pasa rango, el backend devuelve la semana en curso.
 */
export async function obtenerEventos(
  desde?: Date,
  hasta?: Date,
): Promise<Evento[]> {
  const params = new URLSearchParams();
  if (desde) params.append("desde", desde.toISOString());
  if (hasta) params.append("hasta", hasta.toISOString());

  const qs = params.toString();
  const url = qs ? `/api/eventos?${qs}` : "/api/eventos";

  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error obteniendo eventos"));
  }

  const data = await res.json();
  return data.eventos;
}

/**
 * Convierte un texto en audio hablado usando el TTS del backend (Gemini).
 * Devuelve un Blob de audio/wav listo para reproducir con new Audio().
 * OJO: en free tier el limite es 10 llamadas al dia. En el frontend cacheamos
 * el blob por mensaje para no gastar cuota al pulsar play varias veces.
 */
export async function obtenerTTS(text: string): Promise<Blob> {
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error generando el audio"));
  }

  return await res.blob();
}

// ---------------------------------------------------------------------------
// TAREAS (Sprint 2 backend + Tanda 1 endpoints)
// ---------------------------------------------------------------------------

export interface FiltrosTareas {
  estado?: EstadoEOS;
  task_type?: string;
  primary_interlocutor?: string;
  solo_activos?: boolean;
  limite?: number;
}

/**
 * Lista las tareas del usuario autenticado. Por defecto excluye Completed
 * y Cancelled (solo_activos=true en backend). Pasa filtros para acotar.
 */
export async function listarTareas(
  filtros: FiltrosTareas = {},
): Promise<RespuestaListaTareas> {
  const params = new URLSearchParams();
  if (filtros.estado) params.append("estado", filtros.estado);
  if (filtros.task_type) params.append("task_type", filtros.task_type);
  if (filtros.primary_interlocutor) {
    params.append("primary_interlocutor", filtros.primary_interlocutor);
  }
  if (filtros.solo_activos !== undefined) {
    params.append("solo_activos", String(filtros.solo_activos));
  }
  if (filtros.limite !== undefined) {
    params.append("limite", String(filtros.limite));
  }

  const qs = params.toString();
  const url = qs ? `/api/tareas?${qs}` : "/api/tareas";

  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error obteniendo tareas"));
  }

  return await res.json();
}

/**
 * Cambia el estado EOS de una tarea. El backend valida la transicion
 * contra la matriz §6.4 y devuelve 409 si es ilegal.
 *
 * `cambios` acompaña al nuevo_estado con los campos que la transicion
 * exige conceptualmente (ej: delegar necesita owner + review_date +
 * escalation_condition). Si algo falta y §6.4 lo requiere, el backend
 * lo aplica igual pero deja el campo vacio en Bitrix.
 */
export async function actualizarEstadoTarea(
  id: number,
  cambios: CambioEstadoTarea,
): Promise<{ ok: boolean; id: number; estado_nuevo: string }> {
  const res = await fetch(`/api/tareas/${id}/estado`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cambios),
  });

  if (!res.ok) {
    throw new ApiError(res.status, await _extraerError(res, "Error actualizando la tarea"));
  }

  return await res.json();
}

/**
 * Atajo comun: marcar una tarea como completada. Es el 80% de las
 * mutaciones que hace el CEO desde la UI (tick al finalizar algo).
 */
export function completarTarea(id: number) {
  return actualizarEstadoTarea(id, { nuevo_estado: "Completed" });
}

/**
 * Atajo: cancelar. Segundo caso mas frecuente ("esto ya no aplica").
 */
export function cancelarTarea(id: number) {
  return actualizarEstadoTarea(id, { nuevo_estado: "Cancelled" });
}

// ---------------------------------------------------------------------------
// EXECUTIVE BRIEF (Sprint 3, PHASE 1 §4)
// ---------------------------------------------------------------------------

/**
 * Obtiene el Executive Brief del usuario autenticado para el dia dado.
 * Si se omite `fecha`, el backend genera el brief de hoy en Europe/Madrid.
 *
 * El brief se GENERA cada vez que se llama (no hay cache de servidor).
 * El backend hace 1 llamada Sonnet + 3 fetches Bitrix. Latencia tipica: 3-8s.
 * El frontend deberia mostrar un skeleton/spinner mientras carga.
 */
export async function obtenerBrief(fecha?: Date): Promise<BriefEjecutivo> {
  const params = new URLSearchParams();
  if (fecha) {
    // El backend acepta YYYY-MM-DD (fromisoformat lo parsea).
    const y = fecha.getFullYear();
    const m = String(fecha.getMonth() + 1).padStart(2, "0");
    const d = String(fecha.getDate()).padStart(2, "0");
    params.append("fecha", `${y}-${m}-${d}`);
  }
  const qs = params.toString();
  const url = qs ? `/api/brief?${qs}` : "/api/brief";

  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(
      res.status,
      await _extraerError(res, "Error generando el brief"),
    );
  }

  return await res.json();
}
