import { useState } from "react";
import { Send } from "lucide-react";
import VoiceButton from "./VoiceButton";
import QuickActions from "./QuickActions";

interface Props {
  onEnviarTexto: (texto: string) => void;
  onEnviarAudio: (blob: Blob) => void;
  disabled: boolean;
}

// Compositor inferior: chips de acciones rapidas + input de texto +
// boton de grabacion + boton de enviar.
export default function Composer({ onEnviarTexto, onEnviarAudio, disabled }: Props) {
  const [texto, setTexto] = useState("");

  function handleEnviar() {
    if (!texto.trim() || disabled) return;
    onEnviarTexto(texto);
    setTexto("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter envia, Shift+Enter hace salto de linea
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleEnviar();
    }
  }

  function handleQuickAction(accion: string) {
    // El chip envia el texto directamente
    onEnviarTexto(accion);
  }

  return (
    <div className="py-4">
      <QuickActions onSelect={handleQuickAction} disabled={disabled} />

      <div
        className="flex items-end gap-2 rounded-2xl border p-2"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <textarea
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Escribe o mantén pulsado el micrófono..."
          rows={1}
          disabled={disabled}
          className="flex-1 resize-none bg-transparent px-3 py-2 text-[15px] outline-none disabled:opacity-40"
          style={{ color: "var(--color-text)", maxHeight: "160px" }}
        />

        <VoiceButton onGrabacionCompleta={onEnviarAudio} disabled={disabled} />

        <button
          onClick={handleEnviar}
          disabled={disabled || !texto.trim()}
          className="flex h-10 w-10 items-center justify-center rounded-full transition-opacity disabled:opacity-30"
          style={{
            background: "var(--color-accent)",
            color: "white",
          }}
          title="Enviar"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
}
