import { useEffect, useRef } from "react";
import MessageBubble from "./MessageBubble";
import TypingIndicator from "./TypingIndicator";
import type { Message } from "../lib/types";

interface Props {
  mensajes: Message[];
  procesando: boolean;
}

// Lista scrollable de mensajes. Auto-scroll al fondo cuando llega uno nuevo.
export default function Chat({ mensajes, procesando }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensajes, procesando]);

  return (
    <div className="flex-1 overflow-y-auto py-6">
      {mensajes.length === 0 && !procesando && <MensajeVacio />}
      <div className="flex flex-col gap-4">
        {mensajes.map((m) => (
          <MessageBubble key={m.id} mensaje={m} />
        ))}
        {procesando && <TypingIndicator />}
      </div>
      <div ref={bottomRef} />
    </div>
  );
}

// Placeholder cuando aun no hay mensajes.
function MensajeVacio() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <p
        className="text-2xl"
        style={{
          fontFamily: "var(--font-display)",
          color: "var(--color-text)",
        }}
      >
        Buenos días.
      </p>
      <p
        className="mt-2 text-sm"
        style={{ color: "var(--color-text-muted)" }}
      >
        ¿En qué puedo ayudarte hoy?
      </p>
    </div>
  );
}
