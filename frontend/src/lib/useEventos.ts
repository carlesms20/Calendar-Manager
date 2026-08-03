import { useCallback, useEffect, useState } from "react";
import { obtenerEventos } from "./api";
import type { Evento } from "./types";

/**
 * Hook que gestiona el estado del calendario para un rango de fechas dado.
 * - Fetch inicial al montar.
 * - Refetch automatico cuando cambian `desde` o `hasta`.
 * - Funcion `refrescar()` para pedir un refetch manual (ej: tras que el
 *   agente confirme una accion).
 *
 * Si se llama sin argumentos, el backend usa por defecto la semana en curso.
 */
export function useEventos(desde?: Date, hasta?: Date) {
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Estabilizamos las dependencias por timestamp para evitar recrear el
  // callback en cada render si el padre construye nuevas instancias de Date.
  const desdeTs = desde?.getTime();
  const hastaTs = hasta?.getTime();

  const refrescar = useCallback(async () => {
    setCargando(true);
    setError(null);
    try {
      const data = await obtenerEventos(desde, hasta);
      setEventos(data);
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      setError(mensaje);
      console.error("[eventos] error obteniendo:", err);
    } finally {
      setCargando(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desdeTs, hastaTs]);

  useEffect(() => {
    refrescar();
  }, [refrescar]);

  return { eventos, cargando, error, refrescar };
}
