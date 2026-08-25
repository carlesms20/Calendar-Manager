import { useCallback, useEffect, useState } from "react";
import { obtenerBrief } from "./api";
import type { BriefEjecutivo } from "./types";

/**
 * Hook para el Executive Brief diario (Sprint 3, PHASE 1 §4).
 *
 * - Fetch lazy: NO carga al montar; solo cuando el consumer llama a
 *   `cargar()`. Motivo: el brief cuesta ~3-8s + ~500 tokens Anthropic;
 *   no queremos generarlo en cada apertura de MiDia por si el usuario
 *   solo viene a ver el calendario.
 * - `recargar()` refresca el brief actual sin cambiar la fecha.
 * - `cargar(fecha)` fuerza fecha distinta (para ver briefs pasados).
 *
 * Distinto patron que useTareas/useEventos porque el brief NO es una
 * lista mutable — es una foto puntual que el CEO consume y descarta.
 */
export function useBrief() {
  const [brief, setBrief] = useState<BriefEjecutivo | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fechaActual, setFechaActual] = useState<Date | undefined>(undefined);

  const cargar = useCallback(async (fecha?: Date) => {
    setCargando(true);
    setError(null);
    setFechaActual(fecha);
    try {
      const data = await obtenerBrief(fecha);
      setBrief(data);
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      setError(mensaje);
      console.error("[brief] error generando:", err);
    } finally {
      setCargando(false);
    }
  }, []);

  const recargar = useCallback(() => {
    return cargar(fechaActual);
  }, [cargar, fechaActual]);

  const limpiar = useCallback(() => {
    setBrief(null);
    setError(null);
    setFechaActual(undefined);
  }, []);

  // Sin useEffect de carga automatica — es intencional (ver docstring).
  useEffect(() => {
    return () => {
      // Al desmontar, dejamos el estado como esta. Si el componente se
      // vuelve a montar (usuario cierra y reabre modal), la siguiente
      // apertura reutiliza el brief cacheado hasta que llame a recargar.
    };
  }, []);

  return {
    brief,
    cargando,
    error,
    fechaActual,
    cargar,
    recargar,
    limpiar,
  };
}
