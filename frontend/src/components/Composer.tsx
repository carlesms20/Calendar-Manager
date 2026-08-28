import { useRef, useState } from "react";
import { Send } from "lucide-react";
import VoiceButton from "./VoiceButton";
import QuickActions from "./QuickActions";

interface Props {
  onEnviarTexto: (texto: string) => void;
  onEnviarAudio: (blob: Blob) => void;
  disabled: boolean;
}

// Compositor inferior con animaciones Apple-style:
// - Contenedor con focus ring suave cuando el textarea tiene el foco.
// - Boton enviar con lift + scale-tap + estado disabled visualmente claro.
// - Al enviar, el input se vacia con una micro-animation de fade.
export default function Composer({ onEnviarTexto, onEnviarAudio, disabled }: Props) {
  const [texto, setTexto] = useState("");
  const [foco, setFoco] = useState(false);
  const [swipeSend, setSwipeSend] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function handleEnviar() {
    if (!texto.trim() || disabled) return;
    onEnviarTexto(texto);
    setTexto("");
    // Trigger swipe animation: incrementamos key para forzar re-render del span
    setSwipeSend((k) => k + 1);
    textareaRef.current?.focus();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleEnviar();
    }
  }

  function handleQuickAction(accion: string) {
    onEnviarTexto(accion);
  }

  const puedeEnviar = !disabled && texto.trim().length > 0;

  return (
    <div className="py-4">
      <QuickActions onSelect={handleQuickAction} disabled={disabled} />

      <div
        className="flex items-end gap-2 rounded-2xl border p-2"
        style={{
          background: "var(--color-surface)",
          borderColor: foco
            ? "var(--color-user-bubble-border)"
            : "var(--color-border)",
          boxShadow: foco
            ? "0 0 0 3px rgba(16, 185, 129, 0.08)"
            : "none",
          transition:
            "border-color var(--duration-base) var(--ease-standard), " +
            "box-shadow var(--duration-base) var(--ease-standard)",
        }}
      >
        <textarea
          ref={textareaRef}
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFoco(true)}
          onBlur={() => setFoco(false)}
          placeholder="Escribe o mantén pulsado el micrófono..."
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent px-3 py-2 text-[15px] outline-none disabled:opacity-40"
          style={{ color: "var(--color-text)", maxHeight: "160px" }}
        />

        <VoiceButton onGrabacionCompleta={onEnviarAudio} disabled={disabled} />

        <button
          onClick={handleEnviar}
          disabled={!puedeEnviar}
          className="apple-lift flex h-10 w-10 items-center justify-center rounded-full disabled:cursor-not-allowed"
          style={{
            background: puedeEnviar
              ? "var(--color-accent)"
              : "var(--color-surface-hover)",
            color: puedeEnviar ? "white" : "var(--color-text-faint)",
            boxShadow: puedeEnviar
              ? "0 2px 8px rgba(16, 185, 129, 0.25)"
              : "none",
          }}
          title="Enviar"
          aria-label="Enviar mensaje"
        >
          <Send
            key={swipeSend}
            size={16}
            className={swipeSend > 0 ? "animate-send-swipe" : ""}
            style={{ transform: "translateX(1px)" }}
          />
        </button>
      </div>
    </div>
  );
}
