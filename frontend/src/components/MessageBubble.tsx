import { useRef, useState, useEffect } from "react";
import { Play, Pause, Loader2, VolumeX } from "lucide-react";
import type { Message } from "../lib/types";
import { obtenerTTS } from "../lib/api";

interface Props {
  mensaje: Message;
}

type EstadoAudio = "idle" | "cargando" | "reproduciendo" | "error";

// Burbuja de mensaje. Usuario a la derecha con acento verde suave.
// Asistente a la izquierda sobre fondo neutro. Sin avatares para look limpio.
// En los mensajes del asistente aparece un boton pequeño "play" para
// escuchar la respuesta en voz alta (TTS via backend).
export default function MessageBubble({ mensaje }: Props) {
  const esUsuario = mensaje.role === "user";

  // Estado del audio para mensajes del asistente. Cacheamos la URL del
  // blob para que un segundo play sobre el mismo mensaje no vuelva a
  // gastar cuota TTS (limite de 10/dia en free tier de Gemini).
  const [estado, setEstado] = useState<EstadoAudio>("idle");
  const [urlAudio, setUrlAudio] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Liberar el objeto URL al desmontar para no dejar memoria colgada.
  useEffect(() => {
    return () => {
      if (urlAudio) URL.revokeObjectURL(urlAudio);
    };
  }, [urlAudio]);

  async function handleClickReproducir() {
    // Si ya esta sonando, paramos.
    if (estado === "reproduciendo" && audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      setEstado("idle");
      return;
    }

    // Si ya lo bajamos antes, tiramos del cache.
    if (urlAudio) {
      reproducirDesde(urlAudio);
      return;
    }

    // Primer play: fetch al backend, cache, reproducir.
    setEstado("cargando");
    try {
      const blob = await obtenerTTS(mensaje.content);
      const url = URL.createObjectURL(blob);
      setUrlAudio(url);
      reproducirDesde(url);
    } catch (err) {
      console.error("TTS fallido:", err);
      setEstado("error");
      // Volvemos a idle a los 3s para que el usuario pueda reintentar.
      setTimeout(() => setEstado("idle"), 3000);
    }
  }

  function reproducirDesde(url: string) {
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.onended = () => setEstado("idle");
    audio.onerror = () => setEstado("error");
    audio.play().catch(() => setEstado("error"));
    setEstado("reproduciendo");
  }

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
    <div className={`flex ${esUsuario ? "justify-end" : "justify-start"}`}>
      <div className="max-w-[85%] rounded-2xl px-4 py-2.5" style={estilos}>
        <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed">
          {mensaje.content}
        </p>

        {!esUsuario && (
          <div className="mt-1.5 flex justify-end">
            <BotonAudio estado={estado} onClick={handleClickReproducir} />
          </div>
        )}
      </div>
    </div>
  );
}

// Boton compacto para reproducir el audio de la respuesta del asistente.
// El icono cambia segun el estado, y el aria-label sigue siendo descriptivo
// para lectores de pantalla.
function BotonAudio({
  estado,
  onClick,
}: {
  estado: EstadoAudio;
  onClick: () => void;
}) {
  const { icono, label, disabled, color } = (() => {
    switch (estado) {
      case "cargando":
        return {
          icono: <Loader2 size={14} className="animate-spin" />,
          label: "Generando audio",
          disabled: true,
          color: "var(--color-accent)",
        };
      case "reproduciendo":
        return {
          icono: <Pause size={14} />,
          label: "Detener audio",
          disabled: false,
          color: "var(--color-accent)",
        };
      case "error":
        return {
          icono: <VolumeX size={14} />,
          label: "Error de audio",
          disabled: true,
          color: "var(--color-text-muted)",
        };
      case "idle":
      default:
        return {
          icono: <Play size={14} />,
          label: "Escuchar respuesta",
          disabled: false,
          color: "var(--color-accent)",
        };
    }
  })();

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className="flex h-7 w-7 items-center justify-center rounded-full transition-all hover:brightness-125 disabled:opacity-40"
      style={{
        background: "var(--color-accent-soft)",
        color,
      }}
    >
      {icono}
    </button>
  );
}
