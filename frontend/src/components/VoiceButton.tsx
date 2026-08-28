import { useEffect, useRef, useState } from "react";
import { Mic, Square } from "lucide-react";

interface Props {
  onGrabacionCompleta: (blob: Blob) => void;
  disabled: boolean;
}

/**
 * Boton hold-to-record con feedback visual completo.
 *
 * UX:
 * - Idle: circulo con icono microfono, animation apple-tap (scale 0.96 al click).
 * - Grabando: rojo con anillo pulsante expansivo (recording-active), icono
 *   cambia a stop, y aparece flotando encima un contador "0:12" con la
 *   duracion. Sin esto no habia forma de saber si estaba grabando.
 * - Rechazo (grabacion <1s): shake horizontal breve para indicar que se
 *   ignoro por accidente.
 *
 * Interaccion: mantener pulsado / touch. Al soltar envia; si el user
 * arrastra fuera del boton (mouseleave) tambien envia — mismo comportamiento
 * que Telegram/WhatsApp.
 */
export default function VoiceButton({ onGrabacionCompleta, disabled }: Props) {
  const [grabando, setGrabando] = useState(false);
  const [segundos, setSegundos] = useState(0);
  const [shake, setShake] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const inicioRef = useRef<number>(0);
  const tickRef = useRef<number | null>(null);

  // Contador de segundos mientras grabamos. Se actualiza cada 200ms para no
  // agotar bateria; suficiente para mostrar segundos enteros con precision.
  useEffect(() => {
    if (grabando) {
      inicioRef.current = Date.now();
      setSegundos(0);
      tickRef.current = window.setInterval(() => {
        setSegundos(Math.floor((Date.now() - inicioRef.current) / 1000));
      }, 200);
    } else {
      if (tickRef.current) {
        window.clearInterval(tickRef.current);
        tickRef.current = null;
      }
      setSegundos(0);
    }
    return () => {
      if (tickRef.current) window.clearInterval(tickRef.current);
    };
  }, [grabando]);

  async function empezarGrabacion() {
    if (disabled || grabando) return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];

      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        // Ignoramos grabaciones muy cortas y damos feedback visual (shake).
        if (blob.size > 1000) {
          onGrabacionCompleta(blob);
        } else {
          setShake(true);
          window.setTimeout(() => setShake(false), 320);
        }
        streamRef.current?.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      };

      mediaRecorder.start();
      setGrabando(true);
    } catch (err) {
      console.error("[voz] error accediendo al microfono:", err);
      alert("No he podido acceder al micrófono. Comprueba los permisos del navegador.");
    }
  }

  function pararGrabacion() {
    if (!grabando || !mediaRecorderRef.current) return;
    mediaRecorderRef.current.stop();
    setGrabando(false);
  }

  const mm = String(Math.floor(segundos / 60)).padStart(1, "0");
  const ss = String(segundos % 60).padStart(2, "0");

  return (
    <div className="relative">
      {/* Contador flotante encima del boton mientras graba, con waveform bars */}
      {grabando && (
        <div
          className="animate-fade-in-up pointer-events-none absolute -top-9 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-mono tabular-nums shadow-lg"
          style={{
            background: "#ef4444",
            color: "white",
            whiteSpace: "nowrap",
          }}
        >
          {/* Waveform: 4 barras verticales con delays distintos */}
          <div className="flex items-end gap-0.5" style={{ height: 10 }}>
            <span
              className="waveform-bar-1 inline-block"
              style={{
                width: 2,
                height: 10,
                background: "white",
                borderRadius: 1,
                transformOrigin: "bottom",
              }}
            />
            <span
              className="waveform-bar-2 inline-block"
              style={{
                width: 2,
                height: 10,
                background: "white",
                borderRadius: 1,
                transformOrigin: "bottom",
              }}
            />
            <span
              className="waveform-bar-3 inline-block"
              style={{
                width: 2,
                height: 10,
                background: "white",
                borderRadius: 1,
                transformOrigin: "bottom",
              }}
            />
            <span
              className="waveform-bar-4 inline-block"
              style={{
                width: 2,
                height: 10,
                background: "white",
                borderRadius: 1,
                transformOrigin: "bottom",
              }}
            />
          </div>
          <span>
            {mm}:{ss}
          </span>
        </div>
      )}

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
        className={`apple-tap flex h-10 w-10 items-center justify-center rounded-full disabled:opacity-40 disabled:cursor-not-allowed ${
          grabando ? "recording-active" : ""
        } ${shake ? "animate-shake" : ""}`}
        style={{
          background: grabando ? undefined : "var(--color-surface)",
          border: grabando ? "1px solid transparent" : "1px solid var(--color-border)",
          color: grabando ? undefined : "var(--color-text-muted)",
        }}
        title={grabando ? "Suelta para enviar" : "Mantén pulsado para grabar"}
        aria-label={grabando ? "Grabando audio" : "Grabar audio"}
      >
        {grabando ? <Square size={16} fill="white" /> : <Mic size={18} />}
      </button>
    </div>
  );
}
