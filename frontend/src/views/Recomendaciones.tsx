import { useEffect } from "react";
import {
  Sparkles,
  AlertTriangle,
  TrendingUp,
  Users,
  ClipboardList,
  RefreshCw,
  Clock,
  CheckCircle2,
  ChevronRight,
} from "lucide-react";
import { useBrief } from "../lib/useBrief";
import DonutRatio from "../components/DonutRatio";
import type {
  ReminderItem,
  CategoriaReminder,
  BloquePropuesto,
  RiesgoForecast,
  ForecastSemana,
} from "../lib/types";

/**
 * Vista Recomendaciones (Sprint 5 real, PHASE 6 Doc 3 §13 + §10 + §14).
 *
 * Consume /api/brief y muestra SOLO las secciones Sprint 5:
 *   1. Reminders priorizados §13 (agrupados por categoria)
 *   2. Bloques propuestos §10 (time blocking del dia con objetivo)
 *   3. Forecast semana proxima §14 (riesgos anticipados)
 *
 * Auto-carga al montar (a diferencia del modal Brief, que es lazy).
 * El CEO entra a esta vista para VER recomendaciones, no para descubrir
 * que tiene que pulsar un boton.
 */
export default function Recomendaciones() {
  const briefState = useBrief();

  useEffect(() => {
    if (!briefState.brief && !briefState.cargando) {
      briefState.cargar();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div
        className="flex items-center justify-between border-b px-6 py-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="flex items-center gap-3">
          <h2
            className="text-lg leading-none"
            style={{
              fontFamily: "var(--font-display)",
              color: "var(--color-text)",
            }}
          >
            Recomendaciones
          </h2>
          <span
            className="rounded-full px-2 py-0.5 text-[10px]"
            style={{
              background: "var(--color-prio-media-soft)",
              color: "var(--color-prio-media)",
            }}
          >
            PHASE 6 · Sprint 5
          </span>
        </div>
        <button
          onClick={() => briefState.recargar()}
          disabled={briefState.cargando}
          className="rounded-md p-2 transition-colors disabled:opacity-40"
          style={{ color: "var(--color-text-muted)" }}
          onMouseEnter={(e) => {
            if (!briefState.cargando) {
              e.currentTarget.style.background = "var(--color-surface-hover)";
              e.currentTarget.style.color = "var(--color-text)";
            }
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "var(--color-text-muted)";
          }}
          aria-label="Regenerar"
          title="Regenerar recomendaciones"
        >
          <RefreshCw
            size={16}
            className={briefState.cargando ? "animate-spin" : ""}
          />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-3xl">
          {briefState.cargando && !briefState.brief && <SkeletonRecomendaciones />}
          {briefState.error && !briefState.cargando && (
            <BloqueError
              error={briefState.error}
              onReintentar={() => briefState.recargar()}
            />
          )}
          {briefState.brief && !briefState.error && (
            <ContenidoRecomendaciones brief={briefState.brief} />
          )}
        </div>
      </div>
    </div>
  );
}

function ContenidoRecomendaciones({
  brief,
}: {
  brief: NonNullable<ReturnType<typeof useBrief>["brief"]>;
}) {
  const reminders = brief.reminders;
  const bloques = brief.proposed_work_blocks;
  const fc = brief.forecast_proxima_semana;
  const hayContenido =
    reminders.length > 0 ||
    bloques.length > 0 ||
    (fc && fc.riesgos.length > 0);

  if (!hayContenido) {
    return <VacioEstadoLimpio />;
  }

  return (
    <div className="space-y-6">
      {reminders.length > 0 && <SeccionReminders reminders={reminders} />}
      {bloques.length > 0 && (
        <SeccionBloques
          bloques={bloques}
          bufferPct={brief.capacidad_hoy?.buffer_pct}
          libreMin={brief.capacidad_hoy?.libre_min}
          tieneEstrategico={brief.capacidad_hoy?.tiene_bloque_estrategico}
        />
      )}
      {fc && fc.riesgos.length > 0 && <SeccionForecast fc={fc} />}
    </div>
  );
}

const CATEGORIAS_META: Record<
  CategoriaReminder,
  { label: string; icono: React.ReactNode; color: string; softColor: string; orden: number }
> = {
  persona_bloqueada: {
    label: "Personas bloqueadas por ti",
    icono: <Users size={16} />,
    color: "var(--color-prio-alta)",
    softColor: "var(--color-prio-alta-soft)",
    orden: 1,
  },
  decision: {
    label: "Decisiones pendientes",
    icono: <ClipboardList size={16} />,
    color: "var(--color-prio-alta)",
    softColor: "var(--color-prio-alta-soft)",
    orden: 2,
  },
  dependencia_externa: {
    label: "Dependencias externas",
    icono: <AlertTriangle size={16} />,
    color: "var(--color-prio-media)",
    softColor: "var(--color-prio-media-soft)",
    orden: 3,
  },
  revision_comprometida: {
    label: "Revisiones comprometidas",
    icono: <TrendingUp size={16} />,
    color: "var(--color-prio-media)",
    softColor: "var(--color-prio-media-soft)",
    orden: 4,
  },
  riesgo_incumplimiento: {
    label: "Riesgos de incumplimiento",
    icono: <Sparkles size={16} />,
    color: "var(--color-prio-alta)",
    softColor: "var(--color-prio-alta-soft)",
    orden: 5,
  },
  reunion_propuesta_no_confirmada: {
    label: "Reuniones sin confirmar",
    icono: <ClipboardList size={16} />,
    color: "var(--color-text-muted)",
    softColor: "var(--color-surface-hover)",
    orden: 6,
  },
};

function SeccionReminders({ reminders }: { reminders: ReminderItem[] }) {
  const grupos = new Map<string, ReminderItem[]>();
  for (const r of reminders) {
    const arr = grupos.get(r.categoria) || [];
    arr.push(r);
    grupos.set(r.categoria, arr);
  }
  const gruposOrdenados = Array.from(grupos.entries()).sort(([a], [b]) => {
    const oa = CATEGORIAS_META[a]?.orden ?? 99;
    const ob = CATEGORIAS_META[b]?.orden ?? 99;
    return oa - ob;
  });

  return (
    <div>
      <h3
        className="mb-3 text-[11px] uppercase tracking-wider"
        style={{ color: "var(--color-text-muted)", fontWeight: 600 }}
      >
        Reminder Engine
      </h3>
      <div className="space-y-4">
        {gruposOrdenados.map(([categoria, items]) => {
          const meta = CATEGORIAS_META[categoria] || {
            label: categoria,
            icono: <ChevronRight size={16} />,
            color: "var(--color-text-muted)",
            softColor: "var(--color-surface-hover)",
            orden: 99,
          };
          return (
            <div key={categoria}>
              <div className="mb-2 flex items-center gap-2">
                <div
                  className="flex h-7 w-7 items-center justify-center rounded-full"
                  style={{ background: meta.softColor, color: meta.color }}
                >
                  {meta.icono}
                </div>
                <h4
                  className="text-sm"
                  style={{ color: "var(--color-text)", fontWeight: 500 }}
                >
                  {meta.label}
                </h4>
                <span
                  className="rounded-full px-2 py-0.5 text-[10px] font-mono"
                  style={{ background: meta.softColor, color: meta.color }}
                >
                  {items.length}
                </span>
              </div>
              <div className="space-y-1.5 pl-9">
                {items.map((r, i) => (
                  <ReminderCard key={i} reminder={r} color={meta.color} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReminderCard({
  reminder,
  color,
}: {
  reminder: ReminderItem;
  color: string;
}) {
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor: reminder.prioridad_num <= 2 ? color : "var(--color-border)",
      }}
    >
      <div
        className="text-sm leading-snug"
        style={{ color: "var(--color-text)" }}
      >
        {reminder.titulo}
      </div>
      {reminder.detalle && (
        <div
          className="mt-0.5 text-[11px]"
          style={{ color: "var(--color-text-muted)" }}
        >
          {reminder.detalle}
        </div>
      )}
      {reminder.accion_sugerida && (
        <div
          className="mt-1 text-[11px]"
          style={{ color: "var(--color-text-faint)", fontStyle: "italic" }}
        >
          → {reminder.accion_sugerida}
        </div>
      )}
    </div>
  );
}

function SeccionBloques({
  bloques,
  bufferPct,
  libreMin,
  tieneEstrategico,
}: {
  bloques: BloquePropuesto[];
  bufferPct?: number;
  libreMin?: number;
  tieneEstrategico?: boolean;
}) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <Clock size={14} style={{ color: "var(--color-text-muted)" }} />
        <h3
          className="text-[11px] uppercase tracking-wider"
          style={{ color: "var(--color-text-muted)", fontWeight: 600 }}
        >
          Distribución del día
        </h3>
        {tieneEstrategico === false && (
          <span
            className="rounded-full px-2 py-0.5 text-[10px]"
            style={{
              background: "var(--color-prio-media-soft)",
              color: "var(--color-prio-media)",
            }}
          >
            sin ventana estratégica
          </span>
        )}
      </div>
      <div className="space-y-2">
        {bloques.map((b, i) => (
          <BloqueCard key={i} bloque={b} />
        ))}
      </div>
      {(bufferPct !== undefined || libreMin !== undefined) && (
        <div
          className="mt-2 text-[11px]"
          style={{ color: "var(--color-text-faint)" }}
        >
          {libreMin ?? 0} min libres · buffer {bufferPct ?? 0}%
        </div>
      )}
    </div>
  );
}

function BloqueCard({ bloque }: { bloque: BloquePropuesto }) {
  const hi = _horaCorta(bloque.inicio);
  const hf = _horaCorta(bloque.fin);
  const esEstrat = bloque.tipo === "estrategico";
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor: esEstrat
          ? "var(--color-prio-baja)"
          : "var(--color-border)",
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className="font-mono text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          {hi}–{hf}
        </span>
        <span
          className="rounded px-1.5 py-0.5 text-[9px] uppercase"
          style={{
            background: esEstrat
              ? "var(--color-prio-baja-soft)"
              : "var(--color-surface-hover)",
            color: esEstrat
              ? "var(--color-prio-baja)"
              : "var(--color-text-muted)",
          }}
        >
          {bloque.categoria}
        </span>
      </div>
      <div
        className="mt-1 text-sm leading-snug"
        style={{
          color: "var(--color-text)",
          fontWeight: esEstrat ? 500 : 400,
        }}
      >
        {bloque.objetivo}
      </div>
      {bloque.contexto && (
        <div
          className="mt-0.5 text-[11px]"
          style={{ color: "var(--color-text-faint)" }}
        >
          {bloque.contexto}
        </div>
      )}
      {bloque.resultado_esperado && (
        <div
          className="mt-1 text-[11px]"
          style={{ color: "var(--color-text-muted)", fontStyle: "italic" }}
        >
          Resultado: {bloque.resultado_esperado}
        </div>
      )}
    </div>
  );
}

function SeccionForecast({ fc }: { fc: ForecastSemana }) {
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle size={14} style={{ color: "var(--color-text-muted)" }} />
        <h3
          className="text-[11px] uppercase tracking-wider"
          style={{ color: "var(--color-text-muted)", fontWeight: 600 }}
        >
          Semana que viene
        </h3>
      </div>
      {/* Header con donut ratio + KPIs a la derecha */}
      <div
        className="mb-3 flex items-center gap-4 rounded-md border px-4 py-3"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <DonutRatio ratio={fc.ratio_carga} size={72} strokeWidth={7} />
        <div className="flex-1">
          <div
            className="text-[10px] uppercase tracking-wider"
            style={{ color: "var(--color-text-faint)", fontWeight: 600 }}
          >
            Ratio de carga
          </div>
          <div
            className="mt-2 flex flex-col gap-1 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            <div>
              <span
                className="font-mono"
                style={{ color: "var(--color-text)" }}
              >
                {fc.n_deadlines_esa_semana}
              </span>{" "}
              deadlines
            </div>
            <div>
              <span
                className="font-mono"
                style={{ color: "var(--color-text)" }}
              >
                {fc.n_eventos_agendados}
              </span>{" "}
              eventos agendados
            </div>
          </div>
        </div>
      </div>
      <div className="space-y-1.5">
        {fc.riesgos.map((r, i) => (
          <RiesgoCard key={i} riesgo={r} />
        ))}
      </div>
    </div>
  );
}

function RiesgoCard({ riesgo }: { riesgo: RiesgoForecast }) {
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor:
          riesgo.severidad === "alta"
            ? "var(--color-prio-alta)"
            : "var(--color-border)",
      }}
    >
      <div className="flex items-start gap-2">
        <span
          className="mt-0.5 rounded px-1.5 py-0.5 text-[9px] uppercase font-mono shrink-0"
          style={{
            background:
              riesgo.severidad === "alta"
                ? "var(--color-prio-alta-soft)"
                : riesgo.severidad === "media"
                ? "var(--color-prio-media-soft)"
                : "var(--color-surface-hover)",
            color:
              riesgo.severidad === "alta"
                ? "var(--color-prio-alta)"
                : riesgo.severidad === "media"
                ? "var(--color-prio-media)"
                : "var(--color-text-muted)",
            fontWeight: 700,
          }}
        >
          {riesgo.severidad}
        </span>
        <div
          className="flex-1 text-xs leading-snug"
          style={{ color: "var(--color-text)" }}
        >
          {riesgo.descripcion}
          {riesgo.dia_afectado && (
            <span
              className="ml-2 font-mono text-[10px]"
              style={{ color: "var(--color-text-faint)" }}
            >
              ({riesgo.dia_afectado})
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function SkeletonRecomendaciones() {
  return (
    <div className="space-y-6">
      <div>
        <div
          className="mb-3 h-3 w-32 animate-shimmer rounded"
        />
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-16 animate-shimmer rounded-md"
            />
          ))}
        </div>
      </div>
      <div>
        <div
          className="mb-3 h-3 w-40 animate-shimmer rounded"
        />
        <div className="space-y-2">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-14 animate-shimmer rounded-md"
            />
          ))}
        </div>
      </div>
      <p
        className="text-center text-xs"
        style={{ color: "var(--color-text-faint)" }}
      >
        Analizando tareas, calendario y proyección semanal…
      </p>
    </div>
  );
}

function BloqueError({
  error,
  onReintentar,
}: {
  error: string;
  onReintentar: () => void;
}) {
  return (
    <div className="py-12 text-center">
      <AlertTriangle
        size={32}
        className="mx-auto mb-3"
        style={{ color: "var(--color-prio-alta)" }}
      />
      <p className="text-sm" style={{ color: "var(--color-text)" }}>
        No pude generar las recomendaciones
      </p>
      <p
        className="mt-1 text-xs"
        style={{ color: "var(--color-text-muted)" }}
      >
        {error}
      </p>
      <button
        onClick={onReintentar}
        className="mt-4 rounded-lg px-4 py-2 text-sm"
        style={{
          background: "var(--color-accent, var(--color-user-bubble-border))",
          color: "white",
        }}
      >
        Reintentar
      </button>
    </div>
  );
}

function VacioEstadoLimpio() {
  return (
    <div className="py-16 text-center">
      <CheckCircle2
        size={40}
        className="mx-auto mb-4"
        style={{ color: "var(--color-prio-baja)" }}
      />
      <p
        className="text-base"
        style={{ color: "var(--color-text)", fontWeight: 500 }}
      >
        Nada urgente ahora mismo
      </p>
      <p
        className="mt-2 text-sm"
        style={{ color: "var(--color-text-muted)" }}
      >
        Sin bloqueos activos, decisiones abiertas ni riesgos en la semana que
        viene. Buen momento para trabajo estratégico.
      </p>
    </div>
  );
}

function _horaCorta(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
