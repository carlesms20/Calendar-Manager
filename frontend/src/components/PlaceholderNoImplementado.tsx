import { X, Construction } from "lucide-react";

interface Props {
  abierto: boolean;
  onCerrar: () => void;
  /**
   * Titulo del modal. Ej: "Executive Brief matutino".
   */
  titulo: string;
  /**
   * Referencia a phase/sprint que documenta esta funcionalidad. Se
   * muestra tal cual, sin envoltorios: da trazabilidad honesta al CEO
   * de por que esto aun no funciona.
   * Ej: "PHASE 1 §4 · Sprint 3".
   */
  referenciaPhase: string;
  /**
   * Descripcion breve de que hace o hara esta funcionalidad cuando este
   * implementada. Una o dos frases.
   */
  descripcion: string;
}

/**
 * Modal reusable que aparece cuando el CEO interactua con algo que
 * esta previsto en algun PHASE pero aun no implementado. Muestra
 * la referencia explicita al phase/sprint para que sepa donde esta
 * ubicado en el roadmap, en lugar de "coming soon" ambiguo.
 *
 * Uso:
 *   const [abierto, setAbierto] = useState(false);
 *   <button onClick={() => setAbierto(true)}>Ver brief</button>
 *   <PlaceholderNoImplementado
 *     abierto={abierto}
 *     onCerrar={() => setAbierto(false)}
 *     titulo="Executive Brief matutino"
 *     referenciaPhase="PHASE 1 §4 · Sprint 3"
 *     descripcion="Resumen ejecutivo de 13 secciones..."
 *   />
 */
export default function PlaceholderNoImplementado({
  abierto,
  onCerrar,
  titulo,
  referenciaPhase,
  descripcion,
}: Props) {
  if (!abierto) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0, 0, 0, 0.65)", backdropFilter: "blur(4px)" }}
      onClick={onCerrar}
    >
      <div
        className="w-full max-w-md rounded-2xl border p-6"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-full"
              style={{ background: "var(--color-prio-media-soft)" }}
            >
              <Construction size={18} style={{ color: "var(--color-prio-media)" }} />
            </div>
            <div>
              <h2
                className="text-lg leading-tight"
                style={{
                  fontFamily: "var(--font-display)",
                  color: "var(--color-text)",
                }}
              >
                {titulo}
              </h2>
              <p className="mt-0.5 font-mono text-[11px]" style={{ color: "var(--color-prio-media)" }}>
                {referenciaPhase}
              </p>
            </div>
          </div>
          <button
            onClick={onCerrar}
            className="rounded-md p-1 transition-colors"
            style={{ color: "var(--color-text-muted)" }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--color-surface-hover)";
              e.currentTarget.style.color = "var(--color-text)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "transparent";
              e.currentTarget.style.color = "var(--color-text-muted)";
            }}
            aria-label="Cerrar"
          >
            <X size={16} />
          </button>
        </div>

        <p className="mb-4 text-sm leading-relaxed" style={{ color: "var(--color-text-muted)" }}>
          {descripcion}
        </p>

        <div
          className="rounded-md border px-3 py-2 text-xs"
          style={{
            background: "var(--color-surface-hover)",
            borderColor: "var(--color-border)",
            color: "var(--color-text-faint)",
          }}
        >
          Esta funcionalidad está definida en el roadmap pero aún no está implementada.
          Está previsto entregarla en el sprint indicado arriba.
        </div>

        <div className="mt-5 flex justify-end">
          <button
            onClick={onCerrar}
            className="rounded-lg px-4 py-2 text-sm transition-opacity"
            style={{
              background: "var(--color-accent)",
              color: "white",
            }}
          >
            Entendido
          </button>
        </div>
      </div>
    </div>
  );
}
