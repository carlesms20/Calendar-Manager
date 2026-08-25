import { useState } from "react";
import { FileText } from "lucide-react";
import Chat from "../components/Chat";
import Composer from "../components/Composer";
import FocusDelDia from "../components/FocusDelDia";
import Brief from "../components/Brief";
import { enviarTexto, enviarAudio } from "../lib/api";
import { useBrief } from "../lib/useBrief";
import type { Message, Tarea } from "../lib/types";

interface Props {
  mensajes: Message[];
  procesando: boolean;
  onAnadirMensaje: (role: "user" | "assistant", content: string) => void;
  onSetProcesando: (v: boolean) => void;
  onInvalidarDatos: () => void;
  tareas: Tarea[];
  tareasCargando: boolean;
  onIrATareas: () => void;
}

/**
 * Vista "Mi día". Home del CEO.
 *
 * Composicion:
 * 1. Cabecera de vista con boton "Ver Brief".
 * 2. FocusDelDia: 3 buckets con lo urgente (bloqueado, vencido, deadline).
 *    Reemplaza la sensacion de "chat vacio" con contexto operativo real.
 * 3. Chat con el agente + composer.
 *
 * "Ver Brief" abre el modal con el Executive Brief completo (Sprint 3,
 * PHASE 1 §4). El brief se genera bajo demanda — coste ~500 tokens
 * Anthropic + latencia 3-8s.
 */
export default function MiDia({
  mensajes,
  procesando,
  onAnadirMensaje,
  onSetProcesando,
  onInvalidarDatos,
  tareas,
  tareasCargando,
  onIrATareas,
}: Props) {
  const [briefAbierto, setBriefAbierto] = useState(false);
  const briefState = useBrief();

  function handleAbrirBrief() {
    setBriefAbierto(true);
    // Cargar bajo demanda. Si ya hay uno cargado (misma sesion), no lo
    // regeneramos automaticamente: el usuario puede pulsar el boton de
    // refresh si quiere uno nuevo. Ahorra tokens.
    if (!briefState.brief && !briefState.cargando) {
      briefState.cargar();
    }
  }

  async function handleEnviarTexto(texto: string) {
    if (!texto.trim() || procesando) return;
    onAnadirMensaje("user", texto);
    onSetProcesando(true);
    try {
      const respuesta = await enviarTexto(texto);
      onAnadirMensaje("assistant", respuesta.reply);
      if (respuesta.agenda_modificada) onInvalidarDatos();
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      onAnadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
    } finally {
      onSetProcesando(false);
    }
  }

  async function handleEnviarAudio(blob: Blob) {
    if (procesando) return;
    onAnadirMensaje("user", "🎤 Audio enviado");
    onSetProcesando(true);
    try {
      const respuesta = await enviarAudio(blob);
      onAnadirMensaje("assistant", respuesta.reply);
      if (respuesta.agenda_modificada) onInvalidarDatos();
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      onAnadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
    } finally {
      onSetProcesando(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Cabecera con CTA al Brief */}
      <div
        className="flex items-center justify-between border-b px-6 py-3"
        style={{ borderColor: "var(--color-border)" }}
      >
        <h2
          className="text-lg leading-none"
          style={{
            fontFamily: "var(--font-display)",
            color: "var(--color-text)",
          }}
        >
          Mi día
        </h2>

        <button
          onClick={handleAbrirBrief}
          className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors"
          style={{
            background: "var(--color-surface-hover)",
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-text)";
            e.currentTarget.style.borderColor = "var(--color-user-bubble-border)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-muted)";
            e.currentTarget.style.borderColor = "var(--color-border)";
          }}
          title="Executive Brief matutino"
        >
          <FileText size={12} />
          Ver Brief
        </button>
      </div>

      {/* Contenido scrollable: Focus + chat centrado */}
      <div className="mx-auto flex w-full max-w-[860px] flex-1 flex-col overflow-hidden px-6 pt-4">
        <FocusDelDia
          tareas={tareas}
          cargando={tareasCargando}
          onIrATareas={onIrATareas}
        />

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <Chat mensajes={mensajes} procesando={procesando} />
          <Composer
            onEnviarTexto={handleEnviarTexto}
            onEnviarAudio={handleEnviarAudio}
            disabled={procesando}
          />
        </div>
      </div>

      <Brief
        abierto={briefAbierto}
        onCerrar={() => setBriefAbierto(false)}
        brief={briefState.brief}
        cargando={briefState.cargando}
        error={briefState.error}
        onRecargar={briefState.recargar}
      />
    </div>
  );
}
