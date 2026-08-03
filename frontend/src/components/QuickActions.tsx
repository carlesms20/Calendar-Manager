interface Props {
  onSelect: (texto: string) => void;
  disabled: boolean;
}

// Chips de acciones rapidas encima del composer.
// El primer chip (confirmar) va destacado en verde porque es la accion
// mas recurrente en el uso diario del agente.
const ACCIONES = [
  "¿Qué tengo hoy?",
  "¿Estoy libre esta tarde?",
  "Resumen semana",
  "Cancelar todo",
];

export default function QuickActions({ onSelect, disabled }: Props) {
  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {/* Confirmar destacado, es la accion mas frecuente */}
      <button
        onClick={() => onSelect("confirma")}
        disabled={disabled}
        className="rounded-full px-3 py-1.5 text-xs font-medium transition-opacity disabled:opacity-40"
        style={{
          background: "var(--color-accent-soft)",
          border: "1px solid var(--color-user-bubble-border)",
          color: "var(--color-accent)",
        }}
        onMouseEnter={(e) => {
          if (!disabled) {
            e.currentTarget.style.background = "rgba(16, 185, 129, 0.25)";
          }
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "var(--color-accent-soft)";
        }}
      >
        Confirmar
      </button>

      {/* Resto de acciones con estilo neutro */}
      {ACCIONES.map((accion) => (
        <button
          key={accion}
          onClick={() => onSelect(accion)}
          disabled={disabled}
          className="rounded-full border px-3 py-1.5 text-xs transition-colors disabled:opacity-40"
          style={{
            background: "transparent",
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
          onMouseEnter={(e) => {
            if (!disabled) {
              e.currentTarget.style.background = "var(--color-surface-hover)";
              e.currentTarget.style.color = "var(--color-text)";
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--color-text-muted)";
          }}
        >
          {accion}
        </button>
      ))}
    </div>
  );
}
