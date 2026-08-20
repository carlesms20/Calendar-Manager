import { useCallback, useEffect, useState } from "react";
import {
  listarTareas,
  actualizarEstadoTarea,
  type FiltrosTareas,
} from "./api";
import type { Tarea, CambioEstadoTarea } from "./types";

/**
 * Hook que gestiona el estado de la lista de tareas.
 * - Fetch inicial al montar.
 * - Refetch cuando cambien los filtros.
 * - `refrescar()` manual (ej: tras que el agente confirme accion).
 * - `mutarEstado()` con mutacion optimista: actualiza la lista local
 *   INMEDIATAMENTE, y si el backend falla, revierte + expone error.
 *
 * Mismo patron que useEventos para mantener consistencia.
 */
export function useTareas(filtros: FiltrosTareas = {}) {
  const [tareas, setTareas] = useState<Tarea[]>([]);
  const [total, setTotal] = useState(0);
  const [truncado, setTruncado] = useState(false);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Estabilizamos las dependencias serializando el filtro. No es la
  // mejor performance del mundo, pero para <100 tareas es irrelevante.
  const filtroKey = JSON.stringify(filtros);

  const refrescar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const data = await listarTareas(filtros);
      setTareas(data.tareas);
      setTotal(data.total_disponibles);
      setTruncado(data.truncado);
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      setError(mensaje);
      console.error("[tareas] error obteniendo:", err);
    } finally {
      setCargando(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtroKey]);

  useEffect(() => {
    refrescar();
  }, [refrescar]);

  /**
   * Mutacion optimista del estado de una tarea.
   * - Aplica el cambio local ANTES de esperar al backend (UI fluida).
   * - Si el backend responde OK: se queda como esta (opcionalmente
   *   refrescamos para pillar cambios colaterales).
   * - Si falla: revierte al snapshot previo, expone el mensaje de error
   *   y devuelve false para que el caller pueda mostrar toast.
   */
  const mutarEstado = useCallback(
    async (id: number, cambios: CambioEstadoTarea): Promise<{ ok: boolean; error?: string }> => {
      const snapshot = tareas;
      setTareas((prev) =>
        prev.map((t) =>
          t.id === id ? { ...t, status_eos: cambios.nuevo_estado } : t,
        ),
      );

      try {
        await actualizarEstadoTarea(id, cambios);
        // Refresh completo para asegurar consistencia con Bitrix (nueva
        // tarea puede haber cambiado, otros campos sincronizados, etc.).
        // Aceptamos el coste porque el usuario no muta N tareas por segundo.
        refrescar();
        return { ok: true };
      } catch (err) {
        // Revert
        setTareas(snapshot);
        const mensaje = err instanceof Error ? err.message : "Error desconocido";
        setError(mensaje);
        console.error("[tareas] error mutando estado:", err);
        return { ok: false, error: mensaje };
      }
    },
    [tareas, refrescar],
  );

  return {
    tareas,
    total,
    truncado,
    cargando,
    error,
    refrescar,
    mutarEstado,
  };
}
