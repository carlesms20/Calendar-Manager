import { Home, ListChecks, Calendar, Sparkles } from "lucide-react";

/**
 * Secciones disponibles. El union type se exporta para que App.tsx
 * lo use como fuente unica de verdad del routing.
 */
export type Seccion = "mi-dia" | "tareas" | "calendario" | "recomendaciones";

interface Props {
  seccionActiva: Seccion;
  onCambiar: (s: Seccion) => void;
}

interface Item {
  id: Seccion;
  label: string;
  icono: React.ReactNode;
  /**
   * true si esta seccion es solo placeholder (no hay backend real hoy).
   * Por ahora Recomendaciones (Sprint 5 PHASE 6 Doc 3 §13) es la unica.
   * Mostramos un puntito discreto en el sidebar para que el CEO sepa
   * que ahi no va a encontrar algo funcional aun.
   */
  placeholder?: boolean;
}

const ITEMS: Item[] = [
  { id: "mi-dia", label: "Mi día", icono: <Home size={18} /> },
  { id: "tareas", label: "Tareas", icono: <ListChecks size={18} /> },
  { id: "calendario", label: "Calendario", icono: <Calendar size={18} /> },
  {
    id: "recomendaciones",
    label: "Recomendaciones",
    icono: <Sparkles size={18} />,
    placeholder: true,
  },
];

export default function Sidebar({ seccionActiva, onCambiar }: Props) {
  return (
    <aside
      className="flex w-[72px] flex-shrink-0 flex-col items-center border-r py-4"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      {/* Logo compacto arriba */}
      <div
        className="mb-6 flex h-10 w-10 items-center justify-center rounded-full text-sm font-bold"
        style={{
          background: "var(--color-accent-soft)",
          color: "var(--color-accent)",
        }}
        title="SYNCROSFERA"
      >
        S
      </div>

      <nav className="flex flex-col gap-1 px-1">
        {ITEMS.map((item) => {
          const activo = item.id === seccionActiva;
          return (
            <button
              key={item.id}
              onClick={() => onCambiar(item.id)}
              className="relative flex w-14 flex-col items-center gap-1 rounded-lg py-2 text-[10px] leading-tight transition-colors"
              style={{
                background: activo ? "var(--color-accent-soft)" : "transparent",
                color: activo ? "var(--color-accent)" : "var(--color-text-muted)",
                border: activo ? "1px solid var(--color-user-bubble-border)" : "1px solid transparent",
              }}
              onMouseEnter={(e) => {
                if (!activo) {
                  e.currentTarget.style.background = "var(--color-surface-hover)";
                  e.currentTarget.style.color = "var(--color-text)";
                }
              }}
              onMouseLeave={(e) => {
                if (!activo) {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.color = "var(--color-text-muted)";
                }
              }}
              title={item.label}
            >
              {item.icono}
              <span className="text-center">{item.label}</span>
              {item.placeholder && (
                <span
                  className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full"
                  style={{ background: "var(--color-prio-media)" }}
                  title="En construcción — Sprint 5"
                />
              )}
            </button>
          );
        })}
      </nav>

      {/* Estado sync abajo, ornamental */}
      <div className="mt-auto flex flex-col items-center gap-1 pb-1 text-[9px]" style={{ color: "var(--color-text-faint)" }}>
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: "var(--color-accent)" }}
          title="Sesión activa"
        />
        <span>MVP</span>
      </div>
    </aside>
  );
}
