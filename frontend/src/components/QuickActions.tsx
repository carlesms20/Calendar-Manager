interface Props {
  onSelect: (texto: string) => void;
  disabled: boolean;
}

// Chips de acciones rapidas encima del composer.
// - Confirmar: activa confirmar_operaciones_pendientes (buffer eventos).
// - Que tengo hoy: consulta eventos del dia.
// - Estoy libre esta tarde: consulta huecos libres tarde.
// - Que estoy esperando: activa follow_up_waiting (Sprint 4). Devuelve
//   Waiting con vencidos + proximos con next_action sugerida.
// - Puedo delegar algo: pregunta abierta que el agente responde
//   listando tareas activas evaluables via evaluar_delegacion (Sprint 4).
const ACCIONES = [
  "¿Qué tengo hoy?",
  "¿Estoy libre esta tarde?",
  "¿Qué estoy esperando?",
  "¿Puedo delegar algo?",
];

export default function QuickActions({ onSelect, disabled }: Props) {
  return (
    <div className="mb-3 flex flex-wrap gap-2">
      {/* Confirmar destacado, accion mas frecuente */}
      <button
        onClick={() => onSelect("confirma")}
        disabled={disabled}
        className="apple-tap rounded-full px-3 py-1.5 text-xs font-medium transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed"
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

      {ACCIONES.map((accion) => (
        <button
          key={accion}
          onClick={() => onSelect(accion)}
          disabled={disabled}
          className="apple-tap rounded-full border px-3 py-1.5 text-xs transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed"
          style={{
            background: "transparent",
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
          onMouseEnter={(e) => {
            if (!disabled) {
              e.currentTarget.style.background = "var(--color-surface-hover)";
              e.currentTarget.style.color = "var(--color-text)";
              e.currentTarget.style.borderColor = "var(--color-user-bubble-border)";
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--color-text-muted)";
            e.currentTarget.style.borderColor = "var(--color-border)";
          }}
        >
          {accion}
        </button>
      ))}
    </div>
  );
}
