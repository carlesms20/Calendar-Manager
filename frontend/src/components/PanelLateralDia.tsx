import { useMemo } from "react";
import { Calendar, Clock, TrendingUp } from "lucide-react";
import type { Evento, Tarea } from "../lib/types";

interface Props {
  eventos: Evento[];
  tareas: Tarea[];
  onIrACalendario: () => void;
  onIrATareas: () => void;
}

/**
 * Panel lateral derecho de MiDia.
 *
 * Aprovecha datos YA cargados en App.tsx (eventos + tareas) sin llamar
 * al brief: cheap, no latencia. Muestra:
 * - Proximo evento con "en X min/h"
 * - Proximos 3 deadlines con distancia en dias
 * - KPIs del dia (contadores)
 *
 * Estilo minimal a la izquierda del chat en desktop; se oculta en mobile
 * y tablet (< lg) para no ahogar la pantalla.
 */
export default function PanelLateralDia({
  eventos,
  tareas,
  onIrACalendario,
  onIrATareas,
}: Props) {
  const { proximoEvento, minutosHastaProximo } = useMemo(() => {
    const ahora = new Date();
    const futuros = eventos
      .filter((e) => new Date(e.fecha_inicio) > ahora)
      .sort(
        (a, b) =>
          new Date(a.fecha_inicio).getTime() -
          new Date(b.fecha_inicio).getTime(),
      );
    if (!futuros.length) return { proximoEvento: null, minutosHastaProximo: 0 };
    const prox = futuros[0];
    const min = Math.floor(
      (new Date(prox.fecha_inicio).getTime() - ahora.getTime()) / 60000,
    );
    return { proximoEvento: prox, minutosHastaProximo: min };
  }, [eventos]);

  const proximosDeadlines = useMemo(() => {
    const ahora = new Date();
    return tareas
      .filter((t) => t.deadline && new Date(t.deadline) > ahora)
      .sort(
        (a, b) =>
          new Date(a.deadline!).getTime() - new Date(b.deadline!).getTime(),
      )
      .slice(0, 3);
  }, [tareas]);

  const kpis = useMemo(() => {
    const ahora = new Date();
    const finDia = new Date(ahora);
    finDia.setHours(23, 59, 59, 999);
    const eventosHoy = eventos.filter((e) => {
      const fi = new Date(e.fecha_inicio);
      return fi >= new Date(ahora.toDateString()) && fi <= finDia;
    });
    const bloqueadas = tareas.filter((t) => t.status_eos === "Blocked").length;
    const waitingVencidos = tareas.filter((t) => {
      if (t.status_eos !== "Waiting" && t.status_eos !== "Delegated") return false;
      if (!t.review_date) return false;
      return new Date(t.review_date) <= ahora;
    }).length;
    return {
      eventosHoy: eventosHoy.length,
      tareasActivas: tareas.length,
      bloqueadas,
      waitingVencidos,
    };
  }, [eventos, tareas]);

  return (
    <aside className="stagger flex flex-col gap-3 pt-4 pr-6">
      {/* Próximo evento */}
      <Card
        icono={<Calendar size={13} />}
        titulo="PRÓXIMO EVENTO"
        onClick={onIrACalendario}
      >
        {proximoEvento ? (
          <>
            <div
              className="text-sm leading-snug"
              style={{ color: "var(--color-text)", fontWeight: 500 }}
            >
              {proximoEvento.nombre}
            </div>
            <div
              className="mt-1 flex items-center gap-2 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              <span className="font-mono">
                {_horaCorta(proximoEvento.fecha_inicio)}
              </span>
              <span>·</span>
              <span>{_distanciaHumana(minutosHastaProximo)}</span>
            </div>
          </>
        ) : (
          <div className="text-sm" style={{ color: "var(--color-text-faint)" }}>
            Sin eventos próximos
          </div>
        )}
      </Card>

      {/* Próximos deadlines */}
      <Card
        icono={<Clock size={13} />}
        titulo="PRÓXIMOS DEADLINES"
        onClick={onIrATareas}
      >
        {proximosDeadlines.length > 0 ? (
          <div className="space-y-2">
            {proximosDeadlines.map((t) => (
              <DeadlineRow key={t.id} tarea={t} />
            ))}
          </div>
        ) : (
          <div className="text-sm" style={{ color: "var(--color-text-faint)" }}>
            Sin deadlines cercanos
          </div>
        )}
      </Card>

      {/* KPIs del día */}
      <Card icono={<TrendingUp size={13} />} titulo="RESUMEN">
        <div className="grid grid-cols-2 gap-2">
          <Kpi label="Eventos hoy" valor={kpis.eventosHoy} />
          <Kpi label="Tareas activas" valor={kpis.tareasActivas} />
          <Kpi
            label="Bloqueadas"
            valor={kpis.bloqueadas}
            tono={kpis.bloqueadas > 0 ? "alta" : undefined}
          />
          <Kpi
            label="Waiting vencidos"
            valor={kpis.waitingVencidos}
            tono={kpis.waitingVencidos > 0 ? "media" : undefined}
          />
        </div>
      </Card>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Componentes internos
// ---------------------------------------------------------------------------

function Card({
  icono,
  titulo,
  children,
  onClick,
}: {
  icono: React.ReactNode;
  titulo: string;
  children: React.ReactNode;
  onClick?: () => void;
}) {
  const clickable = !!onClick;
  return (
    <div
      onClick={onClick}
      className={`rounded-xl border p-3 ${
        clickable ? "smooth-hover cursor-pointer" : ""
      }`}
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
      onMouseEnter={(e) => {
        if (clickable) {
          e.currentTarget.style.borderColor = "var(--color-user-bubble-border)";
        }
      }}
      onMouseLeave={(e) => {
        if (clickable) {
          e.currentTarget.style.borderColor = "var(--color-border)";
        }
      }}
    >
      <div
        className="mb-2 flex items-center gap-1.5"
        style={{ color: "var(--color-text-muted)" }}
      >
        {icono}
        <span
          className="text-[10px] uppercase tracking-wider"
          style={{ fontWeight: 600 }}
        >
          {titulo}
        </span>
      </div>
      {children}
    </div>
  );
}

function DeadlineRow({ tarea }: { tarea: Tarea }) {
  const dias = Math.max(
    0,
    Math.ceil(
      (new Date(tarea.deadline!).getTime() - Date.now()) / (1000 * 60 * 60 * 24),
    ),
  );
  const critico = dias <= 2;
  return (
    <div className="flex items-start gap-2">
      <div
        className="mt-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-mono shrink-0"
        style={{
          background: critico
            ? "var(--color-prio-alta-soft)"
            : "var(--color-surface-hover)",
          color: critico
            ? "var(--color-prio-alta)"
            : "var(--color-text-muted)",
          minWidth: "36px",
          textAlign: "center",
          fontWeight: 600,
        }}
      >
        {dias === 0 ? "hoy" : `+${dias}d`}
      </div>
      <div
        className="flex-1 text-xs leading-snug truncate"
        style={{ color: "var(--color-text)" }}
        title={tarea.title}
      >
        {tarea.title}
      </div>
    </div>
  );
}

function Kpi({
  label,
  valor,
  tono,
}: {
  label: string;
  valor: number;
  tono?: "alta" | "media";
}) {
  return (
    <div
      className="rounded-md px-2 py-1.5"
      style={{
        background:
          tono === "alta"
            ? "var(--color-prio-alta-soft)"
            : tono === "media"
            ? "var(--color-prio-media-soft)"
            : "var(--color-surface-hover)",
      }}
    >
      <div
        className="font-mono text-lg leading-none"
        style={{
          color:
            tono === "alta"
              ? "var(--color-prio-alta)"
              : tono === "media"
              ? "var(--color-prio-media)"
              : "var(--color-text)",
        }}
      >
        {valor}
      </div>
      <div
        className="mt-1 text-[10px] uppercase tracking-wide leading-none"
        style={{ color: "var(--color-text-faint)" }}
      >
        {label}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _horaCorta(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function _distanciaHumana(minutos: number): string {
  if (minutos < 1) return "ahora mismo";
  if (minutos < 60) return `en ${minutos} min`;
  const horas = Math.floor(minutos / 60);
  const mins = minutos % 60;
  if (horas < 24) {
    return mins > 0 ? `en ${horas}h ${mins}min` : `en ${horas}h`;
  }
  const dias = Math.floor(horas / 24);
  return `en ${dias} día${dias !== 1 ? "s" : ""}`;
}
