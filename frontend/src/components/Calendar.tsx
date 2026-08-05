import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import EventPopover from "./EventPopover";
import type { Evento } from "../lib/types";

interface Props {
  eventos: Evento[];
  cargando: boolean;
  error: string | null;
  onRefrescar: () => void;
  semanaOffset: number;
  onCambiarSemana: (delta: number) => void;
  onIrAHoy: () => void;
}

// Constantes del layout
const HORA_INICIO = 7;
const HORA_FIN = 22;
const ALTURA_HORA = 52;
const DIAS_SEMANA = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

export default function Calendar({
  eventos,
  cargando,
  error,
  onRefrescar,
  semanaOffset,
  onCambiarSemana,
  onIrAHoy,
}: Props) {
  // Popover
  const [eventoSeleccionado, setEventoSeleccionado] = useState<{
    evento: Evento;
    posicion: { top: number; left: number; ladoIzquierdo: boolean };
  } | null>(null);

  // Hora actual (para la linea "ahora")
  const [ahora, setAhora] = useState(new Date());
  useEffect(() => {
    const interval = setInterval(() => setAhora(new Date()), 60_000);
    return () => clearInterval(interval);
  }, []);

  // Cerrar popover al cambiar de semana
  useEffect(() => {
    setEventoSeleccionado(null);
  }, [semanaOffset]);

  const cuerpoRef = useRef<HTMLDivElement>(null);

  // Calcular los 7 dias de la semana visible segun el offset
  const dias = useMemo(() => {
    const hoy = new Date();
    const diaSemana = hoy.getDay();
    const diffAlLunes = diaSemana === 0 ? -6 : 1 - diaSemana;

    const lunes = new Date(hoy);
    lunes.setDate(hoy.getDate() + diffAlLunes + semanaOffset * 7);
    lunes.setHours(0, 0, 0, 0);

    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(lunes);
      d.setDate(lunes.getDate() + i);
      return d;
    });
  }, [semanaOffset]);

  const horas = useMemo(
    () => Array.from({ length: HORA_FIN - HORA_INICIO }, (_, i) => HORA_INICIO + i),
    [],
  );

  // Agrupar eventos por dia
  const eventosPorDia = useMemo(() => {
    const grupos: Evento[][] = Array.from({ length: 7 }, () => []);
    for (const ev of eventos) {
      const fecha = new Date(ev.fecha_inicio);
      for (let i = 0; i < 7; i++) {
        if (
          fecha.getFullYear() === dias[i].getFullYear() &&
          fecha.getMonth() === dias[i].getMonth() &&
          fecha.getDate() === dias[i].getDate()
        ) {
          grupos[i].push(ev);
          break;
        }
      }
    }
    return grupos;
  }, [eventos, dias]);

  // Indice del dia "hoy" dentro de la semana visible (-1 si no esta)
  const hoyIdx = dias.findIndex((d) => esHoy(d, ahora));

  // Posicion vertical de la linea "ahora" (solo si hoy esta visible y en rango)
  const posicionAhora = useMemo(() => {
    if (hoyIdx === -1) return null;
    const horaActual = ahora.getHours() + ahora.getMinutes() / 60;
    if (horaActual < HORA_INICIO || horaActual >= HORA_FIN) return null;
    return (horaActual - HORA_INICIO) * ALTURA_HORA;
  }, [ahora, hoyIdx]);

  // Titulo dinamico segun offset
  const titulo = useMemo(() => {
    if (semanaOffset === 0) return "Esta semana";
    if (semanaOffset === -1) return "Semana pasada";
    if (semanaOffset === 1) return "Próxima semana";
    return "Semana del " + dias[0].getDate();
  }, [semanaOffset, dias]);

  // Handlers del popover
  function handleClickEvento(ev: Evento, diaIdx: number, elem: HTMLElement) {
    if (!cuerpoRef.current) return;

    const cuerpoRect = cuerpoRef.current.getBoundingClientRect();
    const eventoRect = elem.getBoundingClientRect();

    const top = eventoRect.top - cuerpoRect.top + cuerpoRef.current.scrollTop;
    const left = eventoRect.right - cuerpoRect.left + 8;
    const ladoIzquierdo = diaIdx >= 4;

    setEventoSeleccionado({
      evento: ev,
      posicion: {
        top,
        left: ladoIzquierdo ? eventoRect.left - cuerpoRect.left - 8 : left,
        ladoIzquierdo,
      },
    });
  }

  function handleClickFuera() {
    setEventoSeleccionado(null);
  }

  return (
    <aside
      className="flex h-full flex-col border-l"
      style={{ borderColor: "var(--color-border)" }}
    >
      {/* Cabecera */}
      <div
        className="flex items-center justify-between border-b px-4 py-3"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="min-w-0">
          <h2
            className="truncate text-lg leading-none"
            style={{
              fontFamily: "var(--font-display)",
              color: "var(--color-text)",
            }}
          >
            {titulo}
          </h2>
          <p
            className="mt-0.5 text-xs"
            style={{ color: "var(--color-text-faint)" }}
          >
            {formatearRango(dias[0], dias[6])}
          </p>
        </div>

        {/* Grupo de controles: navegacion + refresh */}
        <div className="flex flex-shrink-0 items-center gap-1">
          <BotonIcono
            onClick={() => onCambiarSemana(-1)}
            title="Semana anterior"
            icon={<ChevronLeft size={14} />}
          />
          {semanaOffset !== 0 && (
            <button
              onClick={onIrAHoy}
              className="h-8 rounded-lg px-2.5 text-xs transition-opacity"
              style={{
                background: "var(--color-accent-soft)",
                border: "1px solid var(--color-user-bubble-border)",
                color: "var(--color-accent)",
              }}
              title="Volver a esta semana"
            >
              Hoy
            </button>
          )}
          <BotonIcono
            onClick={() => onCambiarSemana(1)}
            title="Semana siguiente"
            icon={<ChevronRight size={14} />}
          />
          <BotonIcono
            onClick={onRefrescar}
            disabled={cargando}
            title="Refrescar"
            icon={<RefreshCw size={14} className={cargando ? "animate-spin" : ""} />}
          />
        </div>
      </div>

      {error && (
        <div className="px-4 py-2 text-xs" style={{ color: "#ef4444" }}>
          {error}
        </div>
      )}

      {/* Cabecera de dias */}
      <div
        className="grid border-b"
        style={{
          gridTemplateColumns: "44px repeat(7, 1fr)",
          borderColor: "var(--color-border)",
        }}
      >
        <div />
        {dias.map((d, i) => (
          <div
            key={i}
            className="border-l px-1 py-2 text-center"
            style={{ borderColor: "var(--color-border)" }}
          >
            <div
              className="text-[10px] uppercase tracking-wider"
              style={{
                color:
                  i === hoyIdx
                    ? "var(--color-accent)"
                    : "var(--color-text-faint)",
              }}
            >
              {DIAS_SEMANA[i]}
            </div>
            <div
              className="text-sm"
              style={{
                color:
                  i === hoyIdx ? "var(--color-accent)" : "var(--color-text)",
                fontWeight: i === hoyIdx ? 600 : 400,
              }}
            >
              {d.getDate()}
            </div>
          </div>
        ))}
      </div>

      {/* Cuerpo con horas y eventos */}
      <div
        ref={cuerpoRef}
        className="relative flex-1 overflow-y-auto"
        onClick={handleClickFuera}
      >
        <div
          className="relative grid"
          style={{
            gridTemplateColumns: "44px repeat(7, 1fr)",
            minHeight: `${(HORA_FIN - HORA_INICIO) * ALTURA_HORA}px`,
          }}
        >
          {/* Columna de horas */}
          <div>
            {horas.map((h) => (
              <div
                key={h}
                className="border-b pr-2 text-right text-[10px]"
                style={{
                  height: `${ALTURA_HORA}px`,
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-faint)",
                }}
              >
                <span className="relative -top-1.5">
                  {String(h).padStart(2, "0")}
                </span>
              </div>
            ))}
          </div>

          {/* Columnas de los dias */}
          {dias.map((_, diaIdx) => (
            <div
              key={diaIdx}
              className="relative border-l"
              style={{ borderColor: "var(--color-border)" }}
            >
              {horas.map((h) => (
                <div
                  key={h}
                  className="border-b"
                  style={{
                    height: `${ALTURA_HORA}px`,
                    borderColor: "var(--color-border)",
                  }}
                />
              ))}

              {diaIdx === hoyIdx && posicionAhora !== null && (
                <LineaAhora top={posicionAhora} hora={ahora} />
              )}

              {eventosPorDia[diaIdx].map((ev) => {
                const rect = calcularRectangulo(ev);
                if (!rect) return null;
                const esSeleccionado = eventoSeleccionado?.evento.id === ev.id;
                const estilos = estilosPorPrioridad(ev.prioridad);
                return (
                  <button
                    key={ev.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleClickEvento(ev, diaIdx, e.currentTarget);
                    }}
                    className="absolute left-0.5 right-0.5 overflow-hidden rounded-md px-1.5 py-1 text-left transition-all hover:brightness-125"
                    style={{
                      top: `${rect.top}px`,
                      height: `${rect.height}px`,
                      background: estilos.background,
                      borderLeft: `2px solid ${estilos.borderColor}`,
                      outline: esSeleccionado
                        ? `1px solid ${estilos.borderColor}`
                        : "none",
                      cursor: "pointer",
                    }}
                    title={ev.nombre}
                  >
                    <div
                      className="truncate text-[11px] font-medium leading-tight"
                      style={{ color: "var(--color-text)" }}
                    >
                      {ev.nombre}
                    </div>
                    <div
                      className="truncate text-[10px]"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      {formatearHora(new Date(ev.fecha_inicio))}
                    </div>
                  </button>
                );
              })}
            </div>
          ))}

          {eventoSeleccionado && (
            <EventPopover
              evento={eventoSeleccionado.evento}
              posicion={eventoSeleccionado.posicion}
              onCerrar={handleClickFuera}
            />
          )}
        </div>
      </div>
    </aside>
  );
}

// Boton generico de icono para la cabecera de la barra lateral
function BotonIcono({
  onClick,
  icon,
  title,
  disabled,
}: {
  onClick: () => void;
  icon: React.ReactNode;
  title: string;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex h-8 w-8 items-center justify-center rounded-lg transition-opacity disabled:opacity-40"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        color: "var(--color-text-muted)",
      }}
    >
      {icon}
    </button>
  );
}

// Linea horizontal "ahora" estilo Google Calendar
function LineaAhora({ top, hora }: { top: number; hora: Date }) {
  const horaTexto = `${String(hora.getHours()).padStart(2, "0")}:${String(hora.getMinutes()).padStart(2, "0")}`;

  return (
    <div
      className="pointer-events-none absolute left-0 right-0 z-20 flex items-center"
      style={{ top: `${top}px`, transform: "translateY(-50%)" }}
    >
      <div
        className="absolute -left-[42px] rounded px-1 text-[9px] font-medium leading-tight"
        style={{ background: "#ef4444", color: "white" }}
      >
        {horaTexto}
      </div>
      <div
        className="h-2 w-2 flex-shrink-0 rounded-full"
        style={{ background: "#ef4444" }}
      />
      <div className="h-[1.5px] flex-1" style={{ background: "#ef4444" }} />
    </div>
  );
}

function calcularRectangulo(ev: Evento): { top: number; height: number } | null {
  const inicio = new Date(ev.fecha_inicio);
  const fin = new Date(ev.fecha_fin);

  const horaInicio = inicio.getHours() + inicio.getMinutes() / 60;
  const horaFin = fin.getHours() + fin.getMinutes() / 60;

  if (horaFin <= HORA_INICIO || horaInicio >= HORA_FIN) return null;

  const top = Math.max(0, horaInicio - HORA_INICIO) * ALTURA_HORA;
  const bottom = Math.min(HORA_FIN, horaFin) - HORA_INICIO;
  const height = Math.max(20, bottom * ALTURA_HORA - top);

  return { top, height };
}

function esHoy(d: Date, ahora: Date): boolean {
  return (
    d.getFullYear() === ahora.getFullYear() &&
    d.getMonth() === ahora.getMonth() &&
    d.getDate() === ahora.getDate()
  );
}

function formatearRango(inicio: Date, fin: Date): string {
  const meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  const mesInicio = meses[inicio.getMonth()];
  const mesFin = meses[fin.getMonth()];
  if (mesInicio === mesFin) {
    return `${inicio.getDate()}–${fin.getDate()} ${mesFin}`;
  }
  return `${inicio.getDate()} ${mesInicio} – ${fin.getDate()} ${mesFin}`;
}

function formatearHora(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// Devuelve los colores CSS para pintar un evento segun su prioridad.
// Si no viene prioridad (evento externo sin importance), cae en "media"
// como neutro razonable.
function estilosPorPrioridad(prioridad?: string): {
  background: string;
  borderColor: string;
} {
  switch (prioridad) {
    case "alta":
      return {
        background: "var(--color-prio-alta-soft)",
        borderColor: "var(--color-prio-alta)",
      };
    case "baja":
      return {
        background: "var(--color-prio-baja-soft)",
        borderColor: "var(--color-prio-baja)",
      };
    case "media":
    default:
      return {
        background: "var(--color-prio-media-soft)",
        borderColor: "var(--color-prio-media)",
      };
  }
}
