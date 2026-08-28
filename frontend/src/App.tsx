import { useEffect, useMemo, useState } from "react";
import Sidebar, { type Seccion } from "./components/Sidebar";
import Topbar from "./components/Topbar";
import MiDia from "./views/MiDia";
import Tareas from "./views/Tareas";
import Calendario from "./views/Calendario";
import Recomendaciones from "./views/Recomendaciones";
import { inicializarAuth } from "./lib/auth";
import { useTareas } from "./lib/useTareas";
import { useEventos } from "./lib/useEventos";
import type { Message } from "./lib/types";

/**
 * Shell principal de la app.
 *
 * Routing simple por estado (no react-router): guardamos la seccion
 * activa en useState y renderizamos condicionalmente.
 *
 * Estado del chat vive AQUI para que sobreviva a cambios de seccion.
 *
 * Estado de tareas y eventos ALSO vive aqui: lo levantamos del componente
 * concreto para poder pasarlo tambien a Topbar (chip contexto) y a MiDia
 * (focus panel), evitando triple fetch al mismo endpoint. Cada vista
 * consume el subset que necesita via props.
 *
 * Refresh: cuando el agente confirma una accion, invalidarDatos() dispara
 * refetch de tareas Y de eventos.
 */
export default function App() {
  const [seccion, setSeccion] = useState<Seccion>("mi-dia");
  const [mensajes, setMensajes] = useState<Message[]>([]);
  const [procesando, setProcesando] = useState(false);

  useEffect(() => {
    inicializarAuth();
  }, []);

  // Fuente unica: tareas activas del CEO.
  const {
    tareas,
    cargando: tareasCargando,
    error: tareasError,
    refrescar: refrescarTareas,
    mutarEstado,
    truncado: tareasTruncado,
  } = useTareas({ solo_activos: true, limite: 200 });

  // Rango de la semana actual para los contadores de topbar y para
  // Calendario. Calendario tiene su propia gestion de offset semanal
  // (nav prev/next), asi que aqui solo pillamos la semana en curso
  // para las metricas. Los eventos del calendario reales los pide la
  // vista Calendario con su propio hook.
  const rangoSemanaActual = useMemo(() => {
    const hoy = new Date();
    const diaSemana = hoy.getDay();
    const diffAlLunes = diaSemana === 0 ? -6 : 1 - diaSemana;
    const lunes = new Date(hoy);
    lunes.setDate(hoy.getDate() + diffAlLunes);
    lunes.setHours(0, 0, 0, 0);
    const domingo = new Date(lunes);
    domingo.setDate(lunes.getDate() + 6);
    domingo.setHours(23, 59, 59, 999);
    return { desde: lunes, hasta: domingo };
  }, []);
  const { eventos, refrescar: refrescarEventos } = useEventos(
    rangoSemanaActual.desde,
    rangoSemanaActual.hasta,
  );

  // Contador de eventos hoy: parseamos ISO y comparamos por dia local.
  const eventosHoy = useMemo(() => {
    const hoyStr = new Date().toISOString().slice(0, 10);
    return eventos.filter((e) => e.fecha_inicio?.slice(0, 10) === hoyStr).length;
  }, [eventos]);

  // Contador de atencion requerida: bloqueadas + waiting/delegated
  // vencidos (review_date pasada) + deadline dentro de 3 dias.
  // Espeja la logica de FocusDelDia bucket a bucket; aqui solo
  // necesitamos el numero total, no la lista. Sprint 3 introdujo el
  // campo `deadline` distinto de `review_date` (PHASE 1 §8.1); el
  // tercer bucket usa `deadline` real, no la fecha de supervision.
  const atencionRequerida = useMemo(() => {
    const ahora = new Date();
    const en3dias = new Date(ahora.getTime() + 3 * 24 * 60 * 60 * 1000);
    let n = 0;
    for (const t of tareas) {
      if (t.status_eos === "Blocked") { n++; continue; }
      if ((t.status_eos === "Waiting" || t.status_eos === "Delegated") && t.review_date) {
        const rev = new Date(t.review_date);
        if (!isNaN(rev.getTime()) && rev <= ahora) { n++; continue; }
      }
      if (t.deadline) {
        const dl = new Date(t.deadline);
        if (!isNaN(dl.getTime()) && dl > ahora && dl <= en3dias) { n++; }
      }
    }
    return n;
  }, [tareas]);

  function anadirMensaje(role: "user" | "assistant", content: string) {
    setMensajes((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role, content, timestamp: Date.now() },
    ]);
  }

  function invalidarDatos() {
    refrescarTareas();
    refrescarEventos();
  }

  return (
    <div
      className="flex h-full w-full overflow-hidden"
      style={{ background: "var(--color-bg)" }}
    >
      <Sidebar seccionActiva={seccion} onCambiar={setSeccion} />

      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar
          tareasActivas={tareas.length + (tareasTruncado ? 0 : 0)}
          eventosHoy={eventosHoy}
          atencionRequerida={atencionRequerida}
        />

        <main className="flex-1 overflow-hidden">
          {/* Wrapper con key=seccion para que React remonte y anime la
              entrada. animate-view-in aplica fade-in-up de 280ms. */}
          <div key={seccion} className="animate-view-in h-full">
            {seccion === "mi-dia" && (
              <MiDia
                mensajes={mensajes}
                procesando={procesando}
                onAnadirMensaje={anadirMensaje}
                onSetProcesando={setProcesando}
                onInvalidarDatos={invalidarDatos}
                tareas={tareas}
                tareasCargando={tareasCargando}
                eventos={eventos}
                onIrATareas={() => setSeccion("tareas")}
                onIrACalendario={() => setSeccion("calendario")}
              />
            )}

            {seccion === "tareas" && (
              <Tareas
                tareas={tareas}
                cargando={tareasCargando}
                error={tareasError}
                truncado={tareasTruncado}
                onRefrescar={refrescarTareas}
                onMutarEstado={mutarEstado}
                onInvalidarDatos={invalidarDatos}
              />
            )}

            {seccion === "calendario" && (
              <Calendario onInvalidarSemanaActual={refrescarEventos} />
            )}

            {seccion === "recomendaciones" && <Recomendaciones />}
          </div>
        </main>
      </div>
    </div>
  );
}
