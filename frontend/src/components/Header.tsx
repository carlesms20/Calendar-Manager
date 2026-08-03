import { Sparkles } from "lucide-react";

// Cabecera fija con avatar circular + titulo en serif elegante.
// El titulo usa Instrument Serif para dar caracter; el resto de la app
// usa Geist (sans) para legibilidad.
export default function Header() {
  return (
    <header
      className="flex items-center gap-3 border-b px-6 py-4"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div
        className="flex h-9 w-9 items-center justify-center rounded-full"
        style={{ background: "var(--color-accent-soft)" }}
      >
        <Sparkles size={18} style={{ color: "var(--color-accent)" }} />
      </div>
      <div>
        <h1
          className="text-xl leading-none"
          style={{
            fontFamily: "var(--font-display)",
            color: "var(--color-text)",
            letterSpacing: "0.01em",
          }}
        >
          Asistente
        </h1>
        <p
          className="mt-0.5 text-xs"
          style={{ color: "var(--color-text-faint)" }}
        >
          SYNCROSFERA
        </p>
      </div>
    </header>
  );
}
