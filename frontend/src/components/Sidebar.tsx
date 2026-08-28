import { Home, ListChecks, Calendar, Sparkles } from "lucide-react";

export type Seccion = "mi-dia" | "tareas" | "calendario" | "recomendaciones";

interface Props {
  seccionActiva: Seccion;
  onCambiar: (s: Seccion) => void;
}

interface Item {
  id: Seccion;
  label: string;
  icono: React.ReactNode;
}

const ITEMS: Item[] = [
  { id: "mi-dia", label: "Mi día", icono: <Home size={18} /> },
  { id: "tareas", label: "Tareas", icono: <ListChecks size={18} /> },
  { id: "calendario", label: "Calendario", icono: <Calendar size={18} /> },
  { id: "recomendaciones", label: "Recomendaciones", icono: <Sparkles size={18} /> },
];

// Altura de cada item + gap. Sincronizado con clases Tailwind del boton:
// py-2 (16px) + contenido 34px + border 2px = ~52px, gap-1 (4px).
const ITEM_HEIGHT = 56;

/**
 * Sidebar con "píldora deslizante" estilo iOS Settings.
 *
 * Un div absoluto verde clarito se desliza vertical entre items segun la
 * seccion activa, en lugar de tres estados independientes on/off. El
 * cambio se hace con transform: translateY (GPU) para mantener 60fps.
 *
 * Los items base solo cambian de color (transitions suaves), no de
 * background: eso lo aporta la pildora.
 */
export default function Sidebar({ seccionActiva, onCambiar }: Props) {
  const indiceActivo = ITEMS.findIndex((i) => i.id === seccionActiva);

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

      <nav className="relative flex flex-col gap-1 px-1">
        {/* Pildora deslizante: fondo absoluto que se mueve entre items */}
        <div
          className="pointer-events-none absolute left-1 right-1 rounded-lg border"
          style={{
            top: 0,
            height: `${ITEM_HEIGHT - 4}px`,
            background: "var(--color-accent-soft)",
            borderColor: "var(--color-user-bubble-border)",
            transform: `translateY(${indiceActivo * ITEM_HEIGHT}px)`,
            transition: "transform 380ms var(--ease-out-quart)",
            willChange: "transform",
          }}
        />

        {ITEMS.map((item) => {
          const activo = item.id === seccionActiva;
          return (
            <button
              key={item.id}
              onClick={() => onCambiar(item.id)}
              className="apple-tap relative z-10 flex w-14 flex-col items-center gap-1 rounded-lg py-2 text-[10px] leading-tight"
              style={{
                background: "transparent",
                color: activo
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
                border: "1px solid transparent",
                transition: "color 260ms var(--ease-standard)",
              }}
              onMouseEnter={(e) => {
                if (!activo) {
                  e.currentTarget.style.color = "var(--color-text)";
                }
              }}
              onMouseLeave={(e) => {
                if (!activo) {
                  e.currentTarget.style.color = "var(--color-text-muted)";
                }
              }}
              title={item.label}
            >
              {item.icono}
              <span className="text-center">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div
        className="mt-auto flex flex-col items-center gap-1 pb-1 text-[9px]"
        style={{ color: "var(--color-text-faint)" }}
      >
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
