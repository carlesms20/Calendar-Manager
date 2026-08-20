import { useState } from "react";
import { Sparkles, AlertTriangle, TrendingUp, Users, ClipboardList } from "lucide-react";
import PlaceholderNoImplementado from "../components/PlaceholderNoImplementado";

/**
 * Vista Recomendaciones (Sprint 5, PHASE 6 Doc 3 §13 - Reminder Engine).
 *
 * Hoy es puro placeholder honesto: mostramos las 5 categorias de
 * recomendaciones que el Reminder Engine producira segun la spec (§13
 * define el orden de prioridad: personas bloqueadas por CEO, decisiones
 * pendientes, dependencias externas, revisiones comprometidas, riesgos
 * de incumplimiento) y al clicar en cualquier tarjeta abrimos el modal
 * "no implementado" con la referencia al sprint.
 *
 * Cuando Sprint 5 este hecho, este archivo pasara a hacer fetch de
 * /api/recomendaciones y pintara las tarjetas con datos reales. El
 * layout se puede reutilizar tal cual.
 */

const CATEGORIAS = [
  {
    id: "bloqueados",
    label: "Personas bloqueadas por el CEO",
    descripcion: "Nadie debería estar esperando una decisión tuya que ya podía tomarse.",
    icono: <Users size={18} />,
    color: "var(--color-prio-alta)",
    softColor: "var(--color-prio-alta-soft)",
  },
  {
    id: "decisiones",
    label: "Decisiones pendientes",
    descripcion: "Tareas con Alexander Role = Decision o Approval sin resolver.",
    icono: <ClipboardList size={18} />,
    color: "var(--color-prio-media)",
    softColor: "var(--color-prio-media-soft)",
  },
  {
    id: "dependencias",
    label: "Dependencias externas",
    descripcion: "Waiting items con next_follow_up vencido.",
    icono: <AlertTriangle size={18} />,
    color: "var(--color-prio-media)",
    softColor: "var(--color-prio-media-soft)",
  },
  {
    id: "revisiones",
    label: "Revisiones comprometidas",
    descripcion: "Tareas delegadas con review_date próxima o vencida.",
    icono: <TrendingUp size={18} />,
    color: "var(--color-accent)",
    softColor: "var(--color-accent-soft)",
  },
  {
    id: "riesgos",
    label: "Riesgos de incumplimiento",
    descripcion: "Deadlines cerca y capacidad insuficiente para cumplirlos.",
    icono: <Sparkles size={18} />,
    color: "var(--color-prio-alta)",
    softColor: "var(--color-prio-alta-soft)",
  },
];

export default function Recomendaciones() {
  const [modalAbierto, setModalAbierto] = useState(false);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between border-b px-6 py-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="flex items-center gap-3">
          <h2
            className="text-lg leading-none"
            style={{
              fontFamily: "var(--font-display)",
              color: "var(--color-text)",
            }}
          >
            Recomendaciones
          </h2>
          <span
            className="rounded-full px-2 py-0.5 text-[10px]"
            style={{
              background: "var(--color-prio-media-soft)",
              color: "var(--color-prio-media)",
            }}
          >
            PHASE 6 · Sprint 5
          </span>
        </div>
      </div>

      {/* Contenido */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          <div
            className="mb-6 rounded-xl border p-4"
            style={{
              background: "var(--color-surface)",
              borderColor: "var(--color-border)",
            }}
          >
            <p className="text-sm leading-relaxed" style={{ color: "var(--color-text-muted)" }}>
              El motor de recomendaciones (Reminder Engine, PHASE 6 Doc 3 §13) revisará
              las 5 categorías siguientes cada mañana y propondrá acciones proactivas.
              Aún no está implementado — se entrega en Sprint 5. Puedes ver el layout
              previsto abajo; al hacer click en cualquier tarjeta se abre el detalle.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {CATEGORIAS.map((cat) => (
              <button
                key={cat.id}
                onClick={() => setModalAbierto(true)}
                className="flex items-start gap-3 rounded-xl border p-4 text-left transition-colors"
                style={{
                  background: "var(--color-surface)",
                  borderColor: "var(--color-border)",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "var(--color-surface-hover)";
                  e.currentTarget.style.borderColor = cat.color;
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "var(--color-surface)";
                  e.currentTarget.style.borderColor = "var(--color-border)";
                }}
              >
                <div
                  className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full"
                  style={{ background: cat.softColor, color: cat.color }}
                >
                  {cat.icono}
                </div>
                <div className="min-w-0 flex-1">
                  <h3
                    className="mb-1 text-sm font-medium"
                    style={{ color: "var(--color-text)" }}
                  >
                    {cat.label}
                  </h3>
                  <p className="text-xs leading-relaxed" style={{ color: "var(--color-text-muted)" }}>
                    {cat.descripcion}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <PlaceholderNoImplementado
        abierto={modalAbierto}
        onCerrar={() => setModalAbierto(false)}
        titulo="Motor de Recomendaciones"
        referenciaPhase="PHASE 6 Doc 3 §13 · Sprint 5"
        descripcion="Reminder Engine proactivo con las 5 prioridades: personas bloqueadas por CEO, decisiones pendientes, dependencias externas, revisiones comprometidas y riesgos de incumplimiento. Se integrará también con el Executive Brief matutino (Sprint 3) y el Forecast Engine (§14)."
      />
    </div>
  );
}
