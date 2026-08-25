import { useEffect, useState } from "react";

interface Props {
  /** Numero de tareas activas del CEO. Se muestra como contexto sobrio en el
   *  centro de la topbar. Opcional: si no viene, el chip no aparece. */
  tareasActivas?: number;
  /** Numero de eventos del calendario que caen HOY. Idem opcional. */
  eventosHoy?: number;
  /** Numero de tareas que requieren atencion (bloqueadas, vencidas, urgentes).
   *  Si es 0, el chip central pinta verde ("todo bajo control"). Si es >0,
   *  pinta naranja con el contador. */
  atencionRequerida?: number;
}

/**
 * Cabecera fija de la app. Rediseno mas cercano al mockup del jefe
 * pero sin ornamentacion excesiva:
 *
 * - Brand a la izquierda con serif tipografico grande (Instrument Serif
 *   via --font-display) y letter-spacing amplio.
 * - Chip central con contexto operativo: si atencionRequerida > 0 pinta
 *   naranja con el contador; si es 0, pinta verde "Todo bajo control".
 *   Reemplaza la "week-focus" del mockup que era analitica pura (fuera
 *   de phase) por metricas simples que ya calculamos client-side.
 * - A la derecha: fecha corta ("Jue 20 ago") + reloj grande en tabular-nums
 *   con estilo display, y pill de sesion activa.
 *
 * Sin flip-clock ornamental, sin particulas, sin botones de feedback
 * ni automation rules (fuera de scope y de phase).
 */
export default function Topbar({
  tareasActivas,
  eventosHoy,
  atencionRequerida = 0,
}: Props) {
  const [ahora, setAhora] = useState(new Date());

  useEffect(() => {
    const interval = setInterval(() => setAhora(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);

  // Fecha corta tipo "Jue 20 ago". Compuesta a mano porque `capitalize`
  // CSS aplica a cada palabra y deja cosas como "20 De Agosto".
  const diaSemana = ahora
    .toLocaleDateString("es-ES", { weekday: "short" })
    .replace(".", "");
  const diaMes = ahora.getDate();
  const mesCorto = ahora
    .toLocaleDateString("es-ES", { month: "short" })
    .replace(".", "");
  const fechaLabel = `${cap(diaSemana)} ${diaMes} ${mesCorto}`;

  const horaLabel = ahora.toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  });

  // Texto del chip de contexto. Encadenamos las metricas disponibles.
  const contextoBits: string[] = [];
  if (tareasActivas !== undefined) {
    contextoBits.push(`${tareasActivas} ${tareasActivas === 1 ? "tarea" : "tareas"} activas`);
  }
  if (eventosHoy !== undefined) {
    contextoBits.push(`${eventosHoy} ${eventosHoy === 1 ? "evento" : "eventos"} hoy`);
  }
  const contextoLabel = contextoBits.join(" · ");

  return (
    <header
      className="flex items-center justify-between gap-6 border-b px-6"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
        height: 68,
      }}
    >
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div
          className="flex h-10 w-10 items-center justify-center rounded-full"
          style={{
            background: "var(--color-accent-soft)",
            border: "1px solid var(--color-user-bubble-border)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 20,
              color: "var(--color-accent)",
              lineHeight: 1,
            }}
          >
            S
          </span>
        </div>
        <div className="flex flex-col">
          <h1
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 22,
              color: "var(--color-text)",
              letterSpacing: "0.14em",
              lineHeight: 1,
            }}
          >
            SYNCROSFERA
          </h1>
          <span
            className="mt-1 text-[10px] uppercase"
            style={{ color: "var(--color-text-faint)", letterSpacing: "0.18em" }}
          >
            Executive Operating System
          </span>
        </div>
      </div>

      {/* Contexto operativo (solo en pantallas medianas+) */}
      <div className="hidden flex-1 justify-center md:flex">
        {contextoLabel && (
          <ChipContexto
            texto={contextoLabel}
            atencionRequerida={atencionRequerida}
          />
        )}
      </div>

      {/* Fecha + hora + sesion */}
      <div className="flex items-center gap-4">
        <div className="hidden flex-col items-end sm:flex">
          <span
            className="text-[11px] uppercase"
            style={{
              color: "var(--color-text-faint)",
              letterSpacing: "0.16em",
            }}
          >
            {fechaLabel}
          </span>
          <span
            className="tabular-nums"
            style={{
              fontFamily: "var(--font-display)",
              fontSize: 24,
              color: "var(--color-text)",
              lineHeight: 1.1,
              letterSpacing: "0.04em",
            }}
          >
            {horaLabel}
          </span>
        </div>

        <div
          className="flex items-center gap-2 rounded-full border px-3 py-1.5 text-[11px]"
          style={{
            background: "var(--color-surface-hover)",
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{
              background: "var(--color-accent)",
              boxShadow: "0 0 8px var(--color-accent-soft)",
            }}
          />
          <span>Sesión activa</span>
        </div>
      </div>
    </header>
  );
}

/** Chip central. Verde si atencionRequerida=0, naranja si hay pendientes. */
function ChipContexto({
  texto,
  atencionRequerida,
}: {
  texto: string;
  atencionRequerida: number;
}) {
  const hayAtencion = atencionRequerida > 0;
  const color = hayAtencion
    ? "var(--color-prio-media)"
    : "var(--color-accent)";
  const bg = hayAtencion
    ? "var(--color-prio-media-soft)"
    : "var(--color-accent-soft)";
  const label = hayAtencion
    ? `${atencionRequerida} ${atencionRequerida === 1 ? "asunto requiere" : "asuntos requieren"} atención`
    : "Todo bajo control";

  return (
    <div
      className="flex items-center gap-3 rounded-full border px-4 py-1.5"
      style={{
        background: bg,
        borderColor: color,
      }}
    >
      <span
        className="h-2 w-2 rounded-full"
        style={{ background: color, boxShadow: `0 0 6px ${color}` }}
      />
      <span
        className="text-[11px] uppercase"
        style={{ color, letterSpacing: "0.14em" }}
      >
        {label}
      </span>
      <span
        className="border-l pl-3 text-[11px]"
        style={{
          borderColor: color,
          color: "var(--color-text-muted)",
        }}
      >
        {texto}
      </span>
    </div>
  );
}

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
