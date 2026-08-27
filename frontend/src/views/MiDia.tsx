import { useState } from "react";
import { FileText, Sparkles } from "lucide-react";
import Chat from "../components/Chat";
import Composer from "../components/Composer";
import FocusDelDia from "../components/FocusDelDia";
import Brief from "../components/Brief";
import PanelLateralDia from "../components/PanelLateralDia";
import { enviarTexto, enviarAudio } from "../lib/api";
import { useBrief } from "../lib/useBrief";
import type { Message, Tarea, Evento } from "../lib/types";

interface Props {
  mensajes: Message[];
  procesando: boolean;
  onAnadirMensaje: (role: "user" | "assistant", content: string) => void;
  onSetProcesando: (v: boolean) => void;
  onInvalidarDatos: () => void;
  tareas: Tarea[];
  tareasCargando: boolean;
  eventos: Evento[];
  onIrATareas: () => void;
  onIrACalendario: () => void;
}

/**
 * Vista "Mi día". Home del CEO.
 *
 * Composicion (Sprint 5.1 UX pass):
 * 1. Cabecera con boton "Executive Brief" PROMINENTE (accent, apple-lift,
 *    animation ambient sutil), y titulo "Mi día".
 * 2. Grid 2 columnas en desktop (>= 1100px):
 *    - Columna principal (flex-1): FocusDelDia + Chat + Composer.
 *    - Columna derecha (280px): PanelLateralDia con proximo evento,
 *      proximos deadlines y KPIs. Consume eventos+tareas cached, sin
 *      llamar al brief.
 *    En mobile/tablet el panel se oculta para no ahogar la pantalla.
 * 3. Modal Brief con las 16 secciones (13 base + capacity + reminders +
 *    forecast del Sprint 5).
 */
export default function MiDia({
  mensajes,
  procesando,
  onAnadirMensaje,
  onSetProcesando,
  onInvalidarDatos,
  tareas,
  tareasCargando,
  eventos,
  onIrATareas,
  onIrACalendario,
}: Props) {
  const [briefAbierto, setBriefAbierto] = useState(false);
  const briefState = useBrief();

  function handleAbrirBrief() {
    setBriefAbierto(true);
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

  const briefFresco =
    briefState.brief !== null && !briefState.cargando && !briefState.error;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Cabecera con CTA prominente al Brief */}
      <div
        className="flex items-center justify-between border-b px-6 py-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="flex items-baseline gap-3">
          <h2
            className="text-xl leading-none"
            style={{
              fontFamily: "var(--font-display)",
              color: "var(--color-text)",
            }}
          >
            Mi día
          </h2>
          <span
            className="text-xs"
            style={{ color: "var(--color-text-faint)" }}
          >
            {_saludo()}
          </span>
        </div>

        <button
          onClick={handleAbrirBrief}
          className={`apple-lift flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium ${
            !briefFresco ? "animate-ambient" : ""
          }`}
          style={{
            background: "var(--color-accent-soft)",
            borderColor: "var(--color-user-bubble-border)",
            color: "var(--color-accent)",
            boxShadow: "0 2px 12px rgba(16, 185, 129, 0.12)",
          }}
          title="Executive Brief · las 13 secciones del día"
        >
          <div className="flex h-6 w-6 items-center justify-center">
            <Sparkles size={16} />
          </div>
          <div className="flex flex-col items-start leading-tight">
            <span>Executive Brief</span>
            <span
              className="text-[10px] font-normal opacity-70"
              style={{ color: "var(--color-accent)" }}
            >
              {briefFresco ? "actualizado ahora" : "generar brief del día"}
            </span>
          </div>
        </button>
      </div>

      {/* Layout 2 columnas: chat centrado + panel lateral derecho en desktop */}
      <div
        className="mx-auto flex w-full flex-1 gap-4 overflow-hidden pt-4"
        style={{ maxWidth: "1300px" }}
      >
        {/* Columna principal (chat + focus) */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-6">
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

        {/* Panel lateral derecho — solo en desktop grande (>=1100px) */}
        <div
          className="hidden overflow-y-auto"
          style={{ width: "280px", flexShrink: 0 }}
          // Tailwind lg = 1024, pero queremos umbral algo mas alto para no
          // ahogar el chat en portátiles pequeños.
          data-lateral-panel
        >
          <PanelLateralDia
            eventos={eventos}
            tareas={tareas}
            onIrACalendario={onIrACalendario}
            onIrATareas={onIrATareas}
          />
        </div>
      </div>

      {/* Media query para mostrar el panel cuando hay ancho */}
      <style>{`
        @media (min-width: 1100px) {
          [data-lateral-panel] {
            display: block !important;
          }
        }
      `}</style>

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

function _saludo(): string {
  const h = new Date().getHours();
  if (h < 6) return "Aún es de madrugada.";
  if (h < 12) return "Buenos días.";
  if (h < 20) return "Buenas tardes.";
  return "Buenas noches.";
}
