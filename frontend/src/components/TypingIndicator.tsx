// Indicador "escribiendo..." con tres puntos animados en burbuja del asistente.
export default function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div
        className="flex items-center gap-1.5 rounded-2xl px-4 py-3"
        style={{
          background: "var(--color-surface)",
          border: "1px solid var(--color-border)",
        }}
      >
        <span
          className="dot-1 h-2 w-2 rounded-full"
          style={{ background: "var(--color-text-muted)" }}
        />
        <span
          className="dot-2 h-2 w-2 rounded-full"
          style={{ background: "var(--color-text-muted)" }}
        />
        <span
          className="dot-3 h-2 w-2 rounded-full"
          style={{ background: "var(--color-text-muted)" }}
        />
      </div>
    </div>
  );
}
