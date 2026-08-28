import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import Chat from "../components/Chat";
import Composer from "../components/Composer";
import FocusDelDia from "../components/FocusDelDia";
import Brief from "../components/Brief";
import PanelLateralDia from "../components/PanelLateralDia";
import Toast from "../components/Toast";
import { enviarTexto, enviarAudio } from "../lib/api";
import { useBrief } from "../lib/useBrief";
import { useToasts } from "../lib/useToasts";
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
  const { toasts, show, dismiss } = useToasts();

  // Focus mode: doble-tap en el chat atenúa todo lo demás durante 3s.
  // Toggle. Se sale con tap simple, tap fuera o auto en 3s.
  const [focusMode, setFocusMode] = useState(false);
  const focusModeTimer = useRef<number | null>(null);
  const chatWrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (focusMode) {
      focusModeTimer.current = window.setTimeout(() => {
        setFocusMode(false);
      }, 3500);
    }
    return () => {
      if (focusModeTimer.current) window.clearTimeout(focusModeTimer.current);
    };
  }, [focusMode]);

  function handleDoubleClickChat() {
    setFocusMode((prev) => !prev);
  }

  // Header con blur al scrollear (Tanda C.9). Detectamos scroll del
  // container principal y aplicamos clase header-scrolled.
  const [headerScrolled, setHeaderScrolled] = useState(false);
  const mainRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    function onScroll() {
      setHeaderScrolled((el?.scrollTop ?? 0) > 24);
    }
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

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
      if (respuesta.agenda_modificada) {
        onInvalidarDatos();
        show("success", "Agenda actualizada");
      }
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      onAnadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
      show("error", "No se pudo procesar tu mensaje");
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
      if (respuesta.agenda_modificada) {
        onInvalidarDatos();
        show("success", "Agenda actualizada");
      }
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      onAnadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
      show("error", "No se pudo procesar el audio");
    } finally {
      onSetProcesando(false);
    }
  }

  const briefFresco =
    briefState.brief !== null && !briefState.cargando && !briefState.error;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Cabecera con CTA prominente al Brief. Blur al scrollear (Sprint 5.3) */}
      <div
        className={`sticky top-0 z-20 flex items-center justify-between border-b px-6 py-4 ${
          headerScrolled ? "header-scrolled" : ""
        } ${focusMode ? "focus-mode-dimmed" : ""}`}
        style={{
          borderColor: "var(--color-border)",
          background: headerScrolled ? undefined : "var(--color-bg)",
          transition:
            "background 240ms var(--ease-standard), backdrop-filter 240ms var(--ease-standard)",
        }}
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
        ref={mainRef}
        className="mx-auto flex w-full flex-1 gap-4 overflow-hidden pt-4"
        style={{ maxWidth: "1300px" }}
      >
        {/* Columna principal (chat + focus) */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-6">
          <div className={focusMode ? "focus-mode-dimmed" : ""}>
            <FocusDelDia
              tareas={tareas}
              cargando={tareasCargando}
              onIrATareas={onIrATareas}
            />
          </div>

          {/* Chat wrapper con doble-click para toggle focus mode */}
          <div
            ref={chatWrapperRef}
            onDoubleClick={handleDoubleClickChat}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <Chat mensajes={mensajes} procesando={procesando} />
            <Composer
              onEnviarTexto={handleEnviarTexto}
              onEnviarAudio={handleEnviarAudio}
              disabled={procesando}
            />
          </div>
        </div>

        {/* Panel lateral derecho — solo en desktop grande (>=1100px).
            Se dimea también con focus mode. */}
        <div
          className={`hidden overflow-y-auto ${focusMode ? "focus-mode-dimmed" : ""}`}
          style={{ width: "280px", flexShrink: 0 }}
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

      {/* Toast container esquina inferior derecha (Sprint 5.3) */}
      <div className="pointer-events-none fixed bottom-6 right-6 z-40 flex flex-col-reverse gap-2">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto">
            <Toast toast={t} onClose={dismiss} />
          </div>
        ))}
      </div>

      {/* Hint focus mode (aparece brevemente cuando se activa) */}
      {focusMode && (
        <div
          className="pointer-events-none fixed left-1/2 top-8 z-50 -translate-x-1/2 rounded-full px-4 py-2 text-xs shadow-lg animate-fade-in-up"
          style={{
            background: "rgba(0,0,0,0.75)",
            color: "white",
            backdropFilter: "blur(8px)",
          }}
        >
          Focus mode · doble-tap para salir
        </div>
      )}

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
