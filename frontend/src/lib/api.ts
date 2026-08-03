// Cliente HTTP contra el backend Python (server.py).
// En dev, Vite proxea /api/* a localhost:8000.
// En prod, frontend y backend viven en el mismo origen.

import type { Evento } from "./types";

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
    const detail = await res.text();
    throw new ApiError(res.status, detail || "Error del servidor");
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
    const detail = await res.text();
    throw new ApiError(res.status, detail || "Error procesando el audio");
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
    const detail = await res.text();
    throw new ApiError(res.status, detail || "Error obteniendo eventos");
  }

  const data = await res.json();
  return data.eventos;
}
