import { useEffect, useMemo, useState } from "react";
import Calendar from "../components/Calendar";
import { useEventos } from "../lib/useEventos";

interface Props {
  /** Se llama cuando el CEO refresca el calendario manualmente, para que
   *  App tambien refresque su cache de "eventos de la semana actual"
   *  usada por Topbar (contador de eventos hoy). */
  onInvalidarSemanaActual?: () => void;
}

function calcularRangoSemana(offset: number): { desde: Date; hasta: Date } {
  const hoy = new Date();
  const diaSemana = hoy.getDay();
  const diffAlLunes = diaSemana === 0 ? -6 : 1 - diaSemana;

  const lunes = new Date(hoy);
  lunes.setDate(hoy.getDate() + diffAlLunes + offset * 7);
  lunes.setHours(0, 0, 0, 0);

  const domingo = new Date(lunes);
  domingo.setDate(lunes.getDate() + 6);
  domingo.setHours(23, 59, 59, 999);

  return { desde: lunes, hasta: domingo };
}

/**
 * Vista Calendario. Wrapper del componente Calendar existente. Su propio
 * useEventos gestiona la navegacion prev/next semana. Cuando muta la
 * semana actual (offset=0), avisa a App para que refresque su cache de
 * metricas.
 */
export default function Calendario({ onInvalidarSemanaActual }: Props) {
  const [semanaOffset, setSemanaOffset] = useState(0);
  const { desde, hasta } = useMemo(
    () => calcularRangoSemana(semanaOffset),
    [semanaOffset],
  );
  const { eventos, cargando, error, refrescar } = useEventos(desde, hasta);

  // Cuando estemos viendo la semana en curso y el usuario refresque,
  // propagamos hacia App para que la Topbar recuente eventos de hoy.
  useEffect(() => {
    if (semanaOffset === 0 && onInvalidarSemanaActual) {
      onInvalidarSemanaActual();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventos]);

  return (
    <div className="h-full overflow-hidden">
      <Calendar
        eventos={eventos}
        cargando={cargando}
        error={error}
        onRefrescar={refrescar}
        semanaOffset={semanaOffset}
        onCambiarSemana={(delta) => setSemanaOffset((prev) => prev + delta)}
        onIrAHoy={() => setSemanaOffset(0)}
      />
    </div>
  );
}
