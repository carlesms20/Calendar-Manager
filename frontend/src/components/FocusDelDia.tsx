import { useMemo } from "react";
import { AlertOctagon, Clock, Timer, Sparkles } from "lucide-react";
import type { Tarea } from "../lib/types";

interface Props {
  tareas: Tarea[];
  cargando: boolean;
  onIrATareas?: () => void;
}

/**
 * Panel "Focus del día" que aparece encima del chat en Mi día.
 *
 * Es un preview honesto de lo que en el futuro haran:
 *   - Sprint 3 Executive Brief (PHASE 1 §4 secciones 3, 4, 5, 7, 8)
 *   - Sprint 5 Reminder Engine (PHASE 6 Doc 3 §13)
 *
 * Hoy solo enseñamos 3 buckets calculados client-side a partir de la
 * lista de tareas activas del CEO:
 *   - Bloqueadas: status_eos === 'Blocked' (rojo, mas urgente).
 *   - En espera vencida: Waiting o Delegated con review_date ≤ hoy.
 *   - Deadline próximo: review_date entre hoy y hoy+3 dias.
 *
 * Si los tres buckets estan vacios, mostramos un estado "Todo bajo
 * control" con Sparkles verde en vez de esconder el panel: el CEO
 * quiere ver que el motor esta vigilando, no que ha desaparecido.
 *
 * Cada tarjeta es clickable y navega a la pestaña Tareas (opcional,
 * si se pasa onIrATareas). Podemos evolucionar a filtro pre-aplicado
 * cuando anadamos router.
 */
export default function FocusDelDia({ tareas, cargando, onIrATareas }: Props) {
  const { bloqueadas, esperandoVencidas, deadlineProximo } = useMemo(() => {
    const ahora = new Date();
    const en3dias = new Date(ahora.getTime() + 3 * 24 * 60 * 60 * 1000);

    const bloqueadas = tareas.filter((t) => t.status_eos === "Blocked");

    const esperandoVencidas = tareas.filter((t) => {
      if (t.status_eos !== "Waiting" && t.status_eos !== "Delegated") return false;
      if (!t.review_date) return false;
      const rev = new Date(t.review_date);
      return !isNaN(rev.getTime()) && rev <= ahora;
    });

    const deadlineProximo = tareas.filter((t) => {
      if (!t.review_date) return false;
      const rev = new Date(t.review_date);
      if (isNaN(rev.getTime())) return false;
      // Excluimos ya vencidas (se muestran en su bucket si aplica)
      return rev > ahora && rev <= en3dias;
    });

    return { bloqueadas, esperandoVencidas, deadlineProximo };
  }, [tareas]);

  const totalUrgente =
    bloqueadas.length + esperandoVencidas.length + deadlineProximo.length;

  if (cargando && tareas.length === 0) {
    return (
      <div
        className="mb-4 flex items-center justify-center rounded-xl border py-6 text-xs"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
          color: "var(--color-text-faint)",
        }}
      >
        Cargando focus del día…
      </div>
    );
  }

  // Estado "todo bajo control"
  if (totalUrgente === 0) {
    return (
      <div
        className="mb-4 flex items-center gap-3 rounded-xl border px-4 py-3"
        style={{
          background: "var(--color-accent-soft)",
          borderColor: "var(--color-user-bubble-border)",
        }}
      >
        <Sparkles size={16} style={{ color: "var(--color-accent)" }} />
        <div className="min-w-0">
          <div
            className="text-sm"
            style={{ color: "var(--color-accent)", fontFamily: "var(--font-display)" }}
          >
            Todo bajo control
          </div>
          <div className="text-[11px]" style={{ color: "var(--color-text-muted)" }}>
            Sin tareas bloqueadas, sin follow-ups vencidos, sin deadlines en los próximos 3 días.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mb-4">
      <div className="mb-2 flex items-center justify-between">
        <h3
          className="text-xs uppercase"
          style={{
            color: "var(--color-text-muted)",
            letterSpacing: "0.16em",
          }}
        >
          Focus del día
        </h3>
        {onIrATareas && (
          <button
            onClick={onIrATareas}
            className="text-[11px] transition-colors"
            style={{ color: "var(--color-text-muted)" }}
            onMouseEnter={(e) => (e.currentTarget.style.color = "var(--color-accent)")}
            onMouseLeave={(e) => (e.currentTarget.style.color = "var(--color-text-muted)")}
          >
            Ver todas las tareas →
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <BucketCard
          titulo="Bloqueadas"
          icono={<AlertOctagon size={14} />}
          tareas={bloqueadas}
          color="var(--color-prio-alta)"
          softColor="var(--color-prio-alta-soft)"
          descripcionVacio="Ninguna tarea bloqueada."
          onClick={onIrATareas}
        />
        <BucketCard
          titulo="En espera vencida"
          icono={<Timer size={14} />}
          tareas={esperandoVencidas}
          color="var(--color-prio-media)"
          softColor="var(--color-prio-media-soft)"
          descripcionVacio="Sin follow-ups pendientes."
          onClick={onIrATareas}
        />
        <BucketCard
          titulo="Deadline en 3 días"
          icono={<Clock size={14} />}
          tareas={deadlineProximo}
          color="var(--color-accent)"
          softColor="var(--color-accent-soft)"
          descripcionVacio="Sin deadlines próximos."
          onClick={onIrATareas}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

interface BucketCardProps {
  titulo: string;
  icono: React.ReactNode;
  tareas: Tarea[];
  color: string;
  softColor: string;
  descripcionVacio: string;
  onClick?: () => void;
}

function BucketCard({
  titulo,
  icono,
  tareas,
  color,
  softColor,
  descripcionVacio,
  onClick,
}: BucketCardProps) {
  const vacio = tareas.length === 0;
  const preview = tareas.slice(0, 2);
  const restantes = Math.max(0, tareas.length - preview.length);

  return (
    <button
      onClick={vacio ? undefined : onClick}
      disabled={vacio}
      className="rounded-xl border p-3 text-left transition-colors disabled:cursor-default"
      style={{
        background: "var(--color-surface)",
        borderColor: vacio ? "var(--color-border)" : color,
        opacity: vacio ? 0.55 : 1,
      }}
      onMouseEnter={(e) => {
        if (!vacio) e.currentTarget.style.background = "var(--color-surface-hover)";
      }}
      onMouseLeave={(e) => {
        if (!vacio) e.currentTarget.style.background = "var(--color-surface)";
      }}
    >
      <div className="mb-2 flex items-center gap-2">
        <div
          className="flex h-6 w-6 items-center justify-center rounded-md"
          style={{ background: softColor, color }}
        >
          {icono}
        </div>
        <span
          className="text-[11px] uppercase"
          style={{ color, letterSpacing: "0.12em" }}
        >
          {titulo}
        </span>
        <span
          className="ml-auto rounded-full px-2 py-0.5 text-[10px] tabular-nums"
          style={{
            background: softColor,
            color,
          }}
        >
          {tareas.length}
        </span>
      </div>

      {vacio ? (
        <p className="text-[11px]" style={{ color: "var(--color-text-faint)" }}>
          {descripcionVacio}
        </p>
      ) : (
        <ul className="space-y-1">
          {preview.map((t) => (
            <li
              key={t.id}
              className="truncate text-xs"
              style={{ color: "var(--color-text)" }}
              title={t.title}
            >
              • {t.title}
            </li>
          ))}
          {restantes > 0 && (
            <li className="text-[11px]" style={{ color: "var(--color-text-muted)" }}>
              +{restantes} más
            </li>
          )}
        </ul>
      )}
    </button>
  );
}
