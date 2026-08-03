import { useEffect, useMemo, useState } from "react";
import Header from "./components/Header";
import Chat from "./components/Chat";
import Composer from "./components/Composer";
import Calendar from "./components/Calendar";
import { enviarTexto, enviarAudio } from "./lib/api";
import { inicializarAuth } from "./lib/auth";
import { useEventos } from "./lib/useEventos";
import type { Message } from "./lib/types";

/**
 * Calcula el rango [lunes 00:00, domingo 23:59] de la semana relativa
 * al offset dado. offset=0 -> semana actual, offset=-1 -> semana anterior,
 * offset=+1 -> proxima semana.
 */
function calcularRangoSemana(offset: number): { desde: Date; hasta: Date } {
  const hoy = new Date();
  const diaSemana = hoy.getDay(); // 0=dom, 1=lun...
  const diffAlLunes = diaSemana === 0 ? -6 : 1 - diaSemana;

  const lunes = new Date(hoy);
  lunes.setDate(hoy.getDate() + diffAlLunes + offset * 7);
  lunes.setHours(0, 0, 0, 0);

  const domingo = new Date(lunes);
  domingo.setDate(lunes.getDate() + 6);
  domingo.setHours(23, 59, 59, 999);

  return { desde: lunes, hasta: domingo };
}

export default function App() {
  const [mensajes, setMensajes] = useState<Message[]>([]);
  const [procesando, setProcesando] = useState(false);

  // Offset de semana visible en el calendario. 0 = semana actual.
  const [semanaOffset, setSemanaOffset] = useState(0);
  const { desde, hasta } = useMemo(
    () => calcularRangoSemana(semanaOffset),
    [semanaOffset],
  );
  const { eventos, cargando, error, refrescar } = useEventos(desde, hasta);

  useEffect(() => {
    inicializarAuth();
  }, []);

  function anadirMensaje(role: "user" | "assistant", content: string) {
    setMensajes((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role, content, timestamp: Date.now() },
    ]);
  }

  async function handleEnviarTexto(texto: string) {
    if (!texto.trim() || procesando) return;

    anadirMensaje("user", texto);
    setProcesando(true);

    try {
      const respuesta = await enviarTexto(texto);
      anadirMensaje("assistant", respuesta.reply);
      if (respuesta.agenda_modificada) {
        refrescar();
      }
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      anadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
    } finally {
      setProcesando(false);
    }
  }

  async function handleEnviarAudio(blob: Blob) {
    if (procesando) return;

    anadirMensaje("user", "🎤 Audio enviado");
    setProcesando(true);

    try {
      const respuesta = await enviarAudio(blob);
      anadirMensaje("assistant", respuesta.reply);
      if (respuesta.agenda_modificada) {
        refrescar();
      }
    } catch (err) {
      const mensaje = err instanceof Error ? err.message : "Error desconocido";
      anadirMensaje("assistant", `Ha habido un problema: ${mensaje}`);
    } finally {
      setProcesando(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <main className="mx-auto flex w-full max-w-[720px] flex-1 flex-col overflow-hidden px-4 xl:ml-auto xl:mr-0">
          <Chat mensajes={mensajes} procesando={procesando} />
          <Composer
            onEnviarTexto={handleEnviarTexto}
            onEnviarAudio={handleEnviarAudio}
            disabled={procesando}
          />
        </main>

        <div className="hidden xl:block xl:w-[480px] xl:flex-shrink-0">
          <Calendar
            eventos={eventos}
            cargando={cargando}
            error={error}
            onRefrescar={refrescar}
            semanaOffset={semanaOffset}
            onCambiarSemana={(delta) => setSemanaOffset((prev) => prev + delta)}
            onIrAHoy={() => setSemanaOffset(0)}
          />
        </div>
      </div>
    </div>
  );
}
