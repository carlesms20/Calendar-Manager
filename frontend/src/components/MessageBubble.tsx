import type { Message } from "../lib/types";

interface Props {
  mensaje: Message;
}

// Burbuja de mensaje. Usuario a la derecha con acento verde suave.
// Asistente a la izquierda sobre fondo neutro. Sin avatares para look limpio.
export default function MessageBubble({ mensaje }: Props) {
  const esUsuario = mensaje.role === "user";

  const estilos = esUsuario
    ? {
        background: "var(--color-user-bubble)",
        border: "1px solid var(--color-user-bubble-border)",
        color: "var(--color-text)",
      }
    : {
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        color: "var(--color-text)",
      };

  return (
    <div
      className={`flex ${esUsuario ? "justify-end" : "justify-start"}`}
    >
      <div
        className="max-w-[85%] rounded-2xl px-4 py-2.5"
        style={estilos}
      >
        <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed">
          {mensaje.content}
        </p>
      </div>
    </div>
  );
}
