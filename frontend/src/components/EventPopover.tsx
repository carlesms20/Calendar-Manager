import { X } from "lucide-react";
import type { Evento } from "../lib/types";

interface Props {
  evento: Evento;
  posicion: { top: number; left: number; ladoIzquierdo: boolean };
  onCerrar: () => void;
}

// Popover flotante que muestra el detalle de un evento al clicar sobre el.
// Se posiciona a la derecha del evento por defecto, o a la izquierda si
// el evento esta cerca del borde derecho del calendario.
export default function EventPopover({ evento, posicion, onCerrar }: Props) {
  const inicio = new Date(evento.fecha_inicio);
  const fin = new Date(evento.fecha_fin);
  const duracionMin = Math.round((fin.getTime() - inicio.getTime()) / 60000);
  const duracionTexto = formatearDuracion(duracionMin);

  return (
    <div
      className="absolute z-50 w-[260px] rounded-xl p-4 shadow-2xl"
      style={{
        top: `${posicion.top}px`,
        left: posicion.ladoIzquierdo ? "auto" : `${posicion.left}px`,
        right: posicion.ladoIzquierdo
          ? `calc(100% - ${posicion.left}px)`
          : "auto",
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-2">
        <h3
          className="text-sm font-semibold leading-snug"
          style={{ color: "var(--color-text)" }}
        >
          {evento.nombre}
        </h3>
        <button
          onClick={onCerrar}
          className="flex-shrink-0 opacity-60 transition-opacity hover:opacity-100"
          style={{ color: "var(--color-text-muted)" }}
          title="Cerrar"
        >
          <X size={14} />
        </button>
      </div>

      <div
        className="mt-3 flex items-center gap-2 text-xs"
        style={{ color: "var(--color-text-muted)" }}
      >
        <span>{formatearFechaLarga(inicio)}</span>
      </div>

      <div
        className="mt-1 flex items-center gap-2 text-xs"
        style={{ color: "var(--color-text-muted)" }}
      >
        <span>
          {formatearHora(inicio)} – {formatearHora(fin)}
        </span>
        <span style={{ color: "var(--color-text-faint)" }}>·</span>
        <span>{duracionTexto}</span>
      </div>

      {evento.descripcion && evento.descripcion.trim() && (
        <div
          className="mt-3 border-t pt-3 text-xs leading-relaxed"
          style={{
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
        >
          <p className="whitespace-pre-wrap break-words">
            {evento.descripcion.length > 200
              ? evento.descripcion.slice(0, 200) + "..."
              : evento.descripcion}
          </p>
        </div>
      )}
    </div>
  );
}

function formatearHora(d: Date): string {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatearFechaLarga(d: Date): string {
  const dias = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"];
  const meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  return `${dias[d.getDay()]} ${d.getDate()} de ${meses[d.getMonth()]}`;
}

function formatearDuracion(min: number): string {
  if (min < 60) return `${min} min`;
  const horas = Math.floor(min / 60);
  const restMin = min % 60;
  if (restMin === 0) return `${horas}h`;
  return `${horas}h ${restMin}min`;
}
