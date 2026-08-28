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

  // Sprint 5.4: separacion de eventos por dia + calculo del layout de
  // columnas para solapados. Ademas separa "todo el dia" (>=8h consecutivas
  // o abarca toda la jornada) para pintar arriba en su propia franja y
  // no invadir la parrilla horaria haciendo eventos ilegibles.
  type PorDia = { todoElDia: Evento[]; layout: EventoConLayout[] };
  const eventosPorDia = useMemo<PorDia[]>(() => {
    const grupos: PorDia[] = Array.from({ length: 7 }, () => ({
      todoElDia: [],
      layout: [],
    }));
    for (const ev of eventos) {
      const fecha = new Date(ev.fecha_inicio);
      for (let i = 0; i < 7; i++) {
        if (
          fecha.getFullYear() === dias[i].getFullYear() &&
          fecha.getMonth() === dias[i].getMonth() &&
          fecha.getDate() === dias[i].getDate()
        ) {
          // Umbral: si el evento dura mas de la jornada visible o
          // empieza a 00:00 y dura >=6h -> "todo el dia".
          const inicio = new Date(ev.fecha_inicio);
          const fin = new Date(ev.fecha_fin);
          const durH = (fin.getTime() - inicio.getTime()) / 3_600_000;
          const empiezaAlba = inicio.getHours() === 0 && inicio.getMinutes() === 0;
          const abarcaJornada = durH >= HORA_FIN - HORA_INICIO;
          if ((empiezaAlba && durH >= 6) || abarcaJornada) {
            grupos[i].todoElDia.push(ev);
          } else {
            // Se procesará en el layout
            grupos[i].layout.push(ev as any);
          }
          break;
        }
      }
    }
    // Reemplazar layout crudo por el organizado
    return grupos.map((g) => ({
      todoElDia: g.todoElDia,
      layout: organizarPorColumnas(g.layout as unknown as Evento[]),
    }));
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
        {dias.map((d, i) => {
          const esHoyCol = i === hoyIdx;
          const nEventos =
            eventosPorDia[i].todoElDia.length + eventosPorDia[i].layout.length;
          return (
            <div
              key={i}
              className="border-l px-1 py-2 text-center"
              style={{ borderColor: "var(--color-border)" }}
            >
              <div
                className="text-[10px] uppercase tracking-wider"
                style={{
                  color: esHoyCol
                    ? "var(--color-accent)"
                    : "var(--color-text-faint)",
                }}
              >
                {DIAS_SEMANA[i]}
              </div>
              {/* Sprint 5.4: dia actual con badge circular verde */}
              {esHoyCol ? (
                <div
                  className="mx-auto mt-0.5 flex h-7 w-7 items-center justify-center rounded-full text-sm"
                  style={{
                    background: "var(--color-accent)",
                    color: "white",
                    fontWeight: 600,
                    boxShadow: "0 2px 8px rgba(16, 185, 129, 0.4)",
                  }}
                >
                  {d.getDate()}
                </div>
              ) : (
                <div
                  className="text-sm"
                  style={{ color: "var(--color-text)", fontWeight: 400 }}
                >
                  {d.getDate()}
                </div>
              )}
              {/* Contador de eventos si hay */}
              {nEventos > 0 && (
                <div
                  className="mt-0.5 text-[9px]"
                  style={{ color: "var(--color-text-faint)" }}
                >
                  {nEventos} {nEventos === 1 ? "evento" : "eventos"}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Sprint 5.4: fila de eventos "todo el día" — separados de la
          parrilla horaria para no invadirla y hacerlos ilegibles.
          Solo se muestra si algun dia tiene todoElDia. */}
      {eventosPorDia.some((g) => g.todoElDia.length > 0) && (
        <div
          className="grid border-b"
          style={{
            gridTemplateColumns: "44px repeat(7, 1fr)",
            borderColor: "var(--color-border)",
            background: "rgba(255, 255, 255, 0.015)",
          }}
        >
          <div
            className="flex items-center justify-end pr-2 text-[9px] uppercase tracking-wider"
            style={{ color: "var(--color-text-faint)" }}
          >
            Todo el día
          </div>
          {dias.map((_, diaIdx) => (
            <div
              key={diaIdx}
              className="border-l px-1 py-1"
              style={{
                borderColor: "var(--color-border)",
                minHeight: "28px",
                background:
                  diaIdx === hoyIdx
                    ? "rgba(16, 185, 129, 0.025)"
                    : "transparent",
              }}
            >
              <div className="flex flex-col gap-0.5">
                {eventosPorDia[diaIdx].todoElDia.map((ev) => {
                  const estilos = estilosPorPrioridad(ev.prioridad, ev.nombre);
                  return (
                    <button
                      key={ev.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleClickEvento(ev, diaIdx, e.currentTarget);
                      }}
                      className="calendar-event apple-tap truncate rounded px-1.5 py-0.5 text-left text-[10px]"
                      style={{
                        background: estilos.background,
                        borderLeft: `2px solid ${estilos.borderColor}`,
                        color: "var(--color-text)",
                        cursor: "pointer",
                      }}
                      title={ev.nombre}
                    >
                      {ev.nombre}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

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
          {dias.map((_, diaIdx) => {
            const esColHoy = diaIdx === hoyIdx;
            return (
              <div
                key={diaIdx}
                className="relative border-l"
                style={{
                  borderColor: "var(--color-border)",
                  // Sprint 5.4: highlight muy sutil de la columna de hoy
                  background: esColHoy
                    ? "rgba(16, 185, 129, 0.025)"
                    : "transparent",
                }}
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

                {esColHoy && posicionAhora !== null && (
                  <LineaAhora top={posicionAhora} hora={ahora} />
                )}

                {eventosPorDia[diaIdx].layout.map((item, evIdx) => {
                  const ev = item.ev;
                  const esSeleccionado = eventoSeleccionado?.evento.id === ev.id;
                  const estilos = estilosPorPrioridad(ev.prioridad, ev.nombre);
                  // Ancho por columna dentro del cluster
                  const anchoPct = 100 / item.totalColumnas;
                  const leftPct = anchoPct * item.columna;
                  return (
                    <button
                      key={ev.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleClickEvento(ev, diaIdx, e.currentTarget);
                      }}
                      className="calendar-event absolute overflow-hidden rounded-md px-1.5 py-1 text-left"
                      style={{
                        top: `${item.top}px`,
                        height: `${item.height}px`,
                        // 1px de padding izq/der para que se vean separados
                        left: `calc(${leftPct}% + 1px)`,
                        width: `calc(${anchoPct}% - 2px)`,
                        background: estilos.background,
                        borderLeft: `2.5px solid ${estilos.borderColor}`,
                        outline: esSeleccionado
                          ? `1.5px solid ${estilos.borderColor}`
                          : "none",
                        cursor: "pointer",
                        zIndex: esSeleccionado ? 15 : 5,
                        animationDelay: `${Math.min(evIdx * 30, 240)}ms`,
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
            );
          })}

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

// Linea horizontal "ahora" estilo Google Calendar + Apple.
// Sprint 5.4: dot rojo con pulso expansivo (ring), label glowing.
function LineaAhora({ top, hora }: { top: number; hora: Date }) {
  const horaTexto = `${String(hora.getHours()).padStart(2, "0")}:${String(hora.getMinutes()).padStart(2, "0")}`;

  return (
    <div
      className="pointer-events-none absolute left-0 right-0 z-20 flex items-center"
      style={{ top: `${top}px`, transform: "translateY(-50%)" }}
    >
      <div
        className="absolute -left-[44px] rounded px-1.5 text-[9px] font-medium leading-tight"
        style={{
          background: "#ef4444",
          color: "white",
          boxShadow: "0 0 8px rgba(239, 68, 68, 0.5)",
        }}
      >
        {horaTexto}
      </div>
      <div className="relative flex h-2 w-2 flex-shrink-0 items-center justify-center">
        {/* Pulso expansivo detras del dot */}
        <div
          className="absolute h-4 w-4 rounded-full"
          style={{
            background: "#ef4444",
            opacity: 0.25,
            animation: "now-dot-pulse 2s var(--ease-standard) infinite",
          }}
        />
        <div
          className="h-2 w-2 rounded-full"
          style={{ background: "#ef4444" }}
        />
      </div>
      <div
        className="h-[1.5px] flex-1"
        style={{
          background: "#ef4444",
          boxShadow: "0 0 4px rgba(239, 68, 68, 0.3)",
        }}
      />
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

/**
 * Sprint 5.4 Calendario: algoritmo de columnas para eventos solapados.
 *
 * Problema: eventos que solapan en tiempo se pintaban con el mismo
 * left/right absolute, uno tapando al otro. El del fondo era inclickable.
 *
 * Solución (estilo Google Calendar / Apple Calendar):
 * 1. Ordenamos eventos por hora de inicio.
 * 2. Recorremos y asignamos cada uno a la primera "columna" (0..N) donde
 *    no hay solape con lo ya asignado.
 * 3. Cada evento devuelve {columna, total_columnas_del_cluster}.
 * 4. Al pintar: cada evento ocupa 1/total del ancho, offset por columna.
 *
 * Ademas: el algoritmo agrupa "clusters" — eventos que se cadenan por
 * solape indirecto (A solapa B, B solapa C aunque A y C no solapen).
 * Todos los del cluster comparten total_columnas para que se lean iguales.
 */
interface EventoConLayout {
  ev: Evento;
  top: number;
  height: number;
  columna: number;
  totalColumnas: number;
}

function organizarPorColumnas(eventos: Evento[]): EventoConLayout[] {
  // 1. Calcular rectangulos y descartar los que no entran en jornada
  type Item = { ev: Evento; top: number; height: number; startMs: number; endMs: number };
  const items: Item[] = [];
  for (const ev of eventos) {
    const rect = calcularRectangulo(ev);
    if (!rect) continue;
    items.push({
      ev,
      top: rect.top,
      height: rect.height,
      startMs: new Date(ev.fecha_inicio).getTime(),
      endMs: new Date(ev.fecha_fin).getTime(),
    });
  }
  items.sort((a, b) => a.startMs - b.startMs || b.endMs - a.endMs);

  // 2. Detectar clusters (grupos conectados por solape)
  //    y asignar columnas dentro de cada cluster.
  const resultado: EventoConLayout[] = [];
  let cluster: Item[] = [];
  let clusterEndMs = 0;

  function flushCluster() {
    if (cluster.length === 0) return;
    // Asignar columnas por "greedy": para cada evento, primera columna
    // libre en su intervalo.
    const columnas: number[] = []; // fin (endMs) del ultimo evento de cada columna
    const asignadas: number[] = [];
    for (const it of cluster) {
      let col = -1;
      for (let c = 0; c < columnas.length; c++) {
        if (columnas[c] <= it.startMs) {
          col = c;
          break;
        }
      }
      if (col === -1) {
        col = columnas.length;
        columnas.push(it.endMs);
      } else {
        columnas[col] = it.endMs;
      }
      asignadas.push(col);
    }
    const total = columnas.length;
    for (let i = 0; i < cluster.length; i++) {
      resultado.push({
        ev: cluster[i].ev,
        top: cluster[i].top,
        height: cluster[i].height,
        columna: asignadas[i],
        totalColumnas: total,
      });
    }
    cluster = [];
  }

  for (const it of items) {
    if (cluster.length === 0) {
      cluster.push(it);
      clusterEndMs = it.endMs;
      continue;
    }
    if (it.startMs < clusterEndMs) {
      // Solapa con el cluster actual
      cluster.push(it);
      clusterEndMs = Math.max(clusterEndMs, it.endMs);
    } else {
      flushCluster();
      cluster = [it];
      clusterEndMs = it.endMs;
    }
  }
  flushCluster();

  return resultado;
}

/**
 * Paleta ampliada para colorear eventos por interlocutor o categoria.
 * 8 colores base con soft variants — suficiente para no repetir en un
 * dia normal y crear jerarquia visual real (antes: todo naranja).
 *
 * Se elige por hash simple del nombre del evento (o del interlocutor si
 * viene). Estable: mismo nombre = mismo color siempre.
 */
const PALETA_EVENTOS = [
  { border: "#F59E0B", bg: "rgba(245, 158, 11, 0.14)" }, // amber
  { border: "#10B981", bg: "rgba(16, 185, 129, 0.14)" }, // emerald
  { border: "#3B82F6", bg: "rgba(59, 130, 246, 0.14)" }, // blue
  { border: "#8B5CF6", bg: "rgba(139, 92, 246, 0.14)" }, // violet
  { border: "#EC4899", bg: "rgba(236, 72, 153, 0.14)" }, // pink
  { border: "#14B8A6", bg: "rgba(20, 184, 166, 0.14)" }, // teal
  { border: "#F97316", bg: "rgba(249, 115, 22, 0.14)" }, // orange
  { border: "#84CC16", bg: "rgba(132, 204, 22, 0.14)" }, // lime
];

function hashCadena(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/**
 * Devuelve colores CSS para pintar un evento.
 *
 * - Si tiene prioridad "alta" o "baja", se usa la paleta semantica
 *   (rojo, verde) para respetar la señal visual.
 * - Si no tiene prioridad o es "media", se usa color de la paleta
 *   ampliada basado en hash del nombre. Asi cada persona/reunion
 *   recurrente tiene su color propio, y solapados se distinguen a la
 *   primera.
 */
function estilosPorPrioridad(prioridad?: string, nombre?: string): {
  background: string;
  borderColor: string;
} {
  if (prioridad === "alta") {
    return {
      background: "var(--color-prio-alta-soft)",
      borderColor: "var(--color-prio-alta)",
    };
  }
  if (prioridad === "baja") {
    return {
      background: "var(--color-prio-baja-soft)",
      borderColor: "var(--color-prio-baja)",
    };
  }
  // media / undefined -> color por hash
  if (nombre) {
    const i = hashCadena(nombre) % PALETA_EVENTOS.length;
    return {
      background: PALETA_EVENTOS[i].bg,
      borderColor: PALETA_EVENTOS[i].border,
    };
  }
  return {
    background: "var(--color-prio-media-soft)",
    borderColor: "var(--color-prio-media)",
  };
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
