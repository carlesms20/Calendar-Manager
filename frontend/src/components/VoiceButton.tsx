import { useRef, useState } from "react";
import { Mic } from "lucide-react";

interface Props {
  onGrabacionCompleta: (blob: Blob) => void;
  disabled: boolean;
}

// Boton hold-to-record. Mantener pulsado inicia grabacion, soltar la envia.
// Usa MediaRecorder nativo del navegador (formato webm).
export default function VoiceButton({ onGrabacionCompleta, disabled }: Props) {
  const [grabando, setGrabando] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  async function empezarGrabacion() {
    if (disabled || grabando) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];

      const mediaRecorder = new MediaRecorder(stream, {
        mimeType: "audio/webm",
      });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        // Ignoramos grabaciones muy cortas (probable pulsacion accidental)
        if (blob.size > 1000) {
          onGrabacionCompleta(blob);
        }
        // Cerrar el stream para apagar el led del microfono
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      };

      mediaRecorder.start();
      setGrabando(true);
    } catch (err) {
      console.error("[voz] error accediendo al microfono:", err);
      alert("No he podido acceder al microfono. Comprueba los permisos del navegador.");
    }
  }

  function pararGrabacion() {
    if (!grabando || !mediaRecorderRef.current) return;
    mediaRecorderRef.current.stop();
    setGrabando(false);
  }

  return (
    <button
      onMouseDown={empezarGrabacion}
      onMouseUp={pararGrabacion}
      onMouseLeave={pararGrabacion}
      onTouchStart={(e) => {
        e.preventDefault();
        empezarGrabacion();
      }}
      onTouchEnd={(e) => {
        e.preventDefault();
        pararGrabacion();
      }}
      disabled={disabled}
      className={`flex h-10 w-10 items-center justify-center rounded-full transition-colors disabled:opacity-40 ${
        grabando ? "recording" : ""
      }`}
      style={{
        background: grabando ? "#ef4444" : "var(--color-surface)",
        border: "1px solid var(--color-border)",
        color: grabando ? "white" : "var(--color-text-muted)",
      }}
      title="Mantén pulsado para grabar"
    >
      <Mic size={18} />
    </button>
  );
}
