import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import type { Tarea, EstadoEOS, CambioEstadoTarea } from "../lib/types";

interface Props {
  tareas: Tarea[];
  cargando: boolean;
  error: string | null;
  truncado: boolean;
  onRefrescar: () => void;
  onMutarEstado: (id: number, cambios: CambioEstadoTarea) => Promise<{ ok: boolean; error?: string }>;
  onInvalidarDatos: () => void;
}

const GRUPOS_ESTADOS: { id: string; label: string; estados: EstadoEOS[] }[] = [
  { id: "todas", label: "Todas activas", estados: [] },
  { id: "en-curso", label: "En curso", estados: ["In Progress", "Scheduled"] },
  { id: "pendientes", label: "Sin empezar", estados: ["New"] },
  { id: "delegadas", label: "Delegadas", estados: ["Delegated"] },
  { id: "esperando", label: "Esperando", estados: ["Waiting", "Blocked"] },
];

/**
 * Panel funcional de Tareas.
 *
 * Recibe la lista de tareas via props desde App (single source of truth
 * para no hacer double-fetch entre MiDia y Tareas). Los filtros por grupo
 * de estados se aplican client-side sobre esa lista, ya que el backend
 * devuelve las activas por defecto y con <100 tareas por CEO no vale la
 * pena penalizar con refetch al cambiar chip.
 *
 * Lo que NO hace este panel (respetando "solo lo que esta en phase"):
 * - No hay filtro Eisenhower (no esta en Tarea model, PHASE 1 §8).
 * - No hay formulario manual de crear tarea con owner/deadline: el flujo
 *   canonico es chat -> agente parsea -> crear_tarea.
 * - No mezcla recomendaciones AI: eso vive en su propia seccion.
 */
export default function Tareas({
  tareas,
  cargando,
  error,
  truncado,
  onRefrescar,
  onMutarEstado,
  onInvalidarDatos,
}: Props) {
  const [grupoId, setGrupoId] = useState("todas");
  const [expandidas, setExpandidas] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string | null>(null);

  const grupo = GRUPOS_ESTADOS.find((g) => g.id === grupoId) ?? GRUPOS_ESTADOS[0];

  const tareasFiltradas = useMemo(() => {
    if (grupo.estados.length === 0) return tareas;
    const set = new Set(grupo.estados);
    return tareas.filter((t) => t.status_eos && set.has(t.status_eos));
  }, [tareas, grupo]);

  function toggleExpandir(id: number) {
    setExpandidas((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function accion(id: number, nuevo: EstadoEOS) {
    const res = await onMutarEstado(id, { nuevo_estado: nuevo });
    if (!res.ok) {
      setToast(res.error ?? "Error aplicando el cambio");
      setTimeout(() => setToast(null), 4000);
    } else {
      onInvalidarDatos();
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header: titulo + count + refrescar + chips filtro */}
      <div
        className="flex flex-col gap-3 border-b px-6 py-4"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h2
              className="text-lg leading-none"
              style={{
                fontFamily: "var(--font-display)",
                color: "var(--color-text)",
              }}
            >
              Tareas
            </h2>
            <span
              className="rounded-full px-2 py-0.5 text-[11px]"
              style={{
                background: "var(--color-accent-soft)",
                color: "var(--color-accent)",
              }}
            >
              {tareasFiltradas.length}
              {truncado && "+"}
            </span>
          </div>

          <button
            onClick={onRefrescar}
            disabled={cargando}
            className="flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors disabled:opacity-40"
            style={{
              background: "var(--color-surface-hover)",
              borderColor: "var(--color-border)",
              color: "var(--color-text-muted)",
            }}
            title="Refrescar"
          >
            <RefreshCw size={12} className={cargando ? "animate-spin" : ""} />
            {cargando ? "Cargando..." : "Refrescar"}
          </button>
        </div>

        <div className="flex flex-wrap gap-2">
          {GRUPOS_ESTADOS.map((g) => {
            const activo = g.id === grupoId;
            return (
              <button
                key={g.id}
                onClick={() => setGrupoId(g.id)}
                className="rounded-full px-3 py-1 text-xs transition-colors"
                style={{
                  background: activo ? "var(--color-accent-soft)" : "transparent",
                  border: activo
                    ? "1px solid var(--color-user-bubble-border)"
                    : "1px solid var(--color-border)",
                  color: activo ? "var(--color-accent)" : "var(--color-text-muted)",
                }}
              >
                {g.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {error && (
          <div
            className="mb-4 flex items-start gap-2 rounded-lg border p-3 text-sm"
            style={{
              background: "var(--color-prio-alta-soft)",
              borderColor: "var(--color-prio-alta)",
              color: "var(--color-text)",
            }}
          >
            <AlertCircle size={16} style={{ color: "var(--color-prio-alta)" }} />
            <span>{error}</span>
          </div>
        )}

        {!cargando && tareasFiltradas.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <p
              className="text-lg"
              style={{
                fontFamily: "var(--font-display)",
                color: "var(--color-text)",
              }}
            >
              Sin tareas en este grupo.
            </p>
            <p className="mt-2 text-sm" style={{ color: "var(--color-text-muted)" }}>
              Ve a "Mi día" y pídele al agente que cree una.
            </p>
          </div>
        )}

        <div className="stagger flex flex-col gap-2">
          {tareasFiltradas.map((t) => (
            <div key={t.id} className="card-hover-lift">
              <TareaFila
                tarea={t}
                expandida={expandidas.has(t.id)}
                onToggle={() => toggleExpandir(t.id)}
                onCompletar={() => accion(t.id, "Completed")}
                onCancelar={() => accion(t.id, "Cancelled")}
              />
            </div>
          ))}
        </div>
      </div>

      {toast && (
        <div
          className="pointer-events-none fixed bottom-6 left-1/2 -translate-x-1/2 rounded-lg border px-4 py-2 text-sm shadow-lg"
          style={{
            background: "var(--color-surface)",
            borderColor: "var(--color-prio-alta)",
            color: "var(--color-text)",
          }}
        >
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

interface FilaProps {
  tarea: Tarea;
  expandida: boolean;
  onToggle: () => void;
  onCompletar: () => void;
  onCancelar: () => void;
}

function TareaFila({ tarea, expandida, onToggle, onCompletar, onCancelar }: FilaProps) {
  const color = colorPorEstado(tarea.status_eos);
  const reviewCerca = tarea.review_date ? diasHasta(tarea.review_date) <= 2 : false;

  return (
    <div
      className="rounded-xl border transition-colors"
      style={{
        background: expandida ? "var(--color-surface-hover)" : "var(--color-surface)",
        borderColor: expandida ? "var(--color-user-bubble-border)" : "var(--color-border)",
      }}
    >
      <button
        onClick={onToggle}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <div
          className="mt-1 h-8 w-1 flex-shrink-0 rounded-full"
          style={{ background: color }}
        />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {expandida ? (
              <ChevronDown size={14} style={{ color: "var(--color-text-muted)" }} />
            ) : (
              <ChevronRight size={14} style={{ color: "var(--color-text-muted)" }} />
            )}
            <h3
              className="truncate text-sm font-medium"
              style={{ color: "var(--color-text)" }}
            >
              {tarea.title}
            </h3>
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-5">
            <Tag>{tarea.status_eos ?? "sin estado"}</Tag>
            {tarea.task_type && <Tag>{tarea.task_type}</Tag>}
            {tarea.primary_interlocutor && <Tag>{"@ " + tarea.primary_interlocutor}</Tag>}
            {tarea.review_date && (
              <Tag danger={reviewCerca}>
                <Clock size={10} className="inline" />{" "}
                {formatearFecha(tarea.review_date)}
              </Tag>
            )}
            {tarea.requires_conversation && <Tag>Conversación</Tag>}
            {tarea.meeting_candidate && <Tag>Meeting candidate</Tag>}
          </div>
        </div>

        <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
          <ActionBtn
            icon={<CheckCircle2 size={14} />}
            title="Marcar completada"
            onClick={onCompletar}
            accent="var(--color-accent)"
          />
          <ActionBtn
            icon={<XCircle size={14} />}
            title="Cancelar tarea"
            onClick={onCancelar}
            accent="var(--color-prio-alta)"
          />
        </div>
      </button>

      {expandida && (
        <div
          className="border-t px-4 py-3 pl-9"
          style={{ borderColor: "var(--color-border)" }}
        >
          <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
            <Detalle label="Next action" valor={tarea.next_action} />
            <Detalle label="Expected result" valor={tarea.expected_result} />
            <Detalle label="Alexander role" valor={tarea.alexander_role} />
            <Detalle label="Escalation condition" valor={tarea.escalation_condition} />
            <Detalle label="Risk" valor={tarea.risk} />
            <Detalle label="Source" valor={tarea.source} />
            <Detalle label="Conversation purpose" valor={tarea.conversation_purpose} />
            <Detalle label="Expected decision" valor={tarea.expected_decision} />
          </div>
          <div
            className="mt-3 flex items-center justify-between border-t pt-2 text-[10px]"
            style={{ borderColor: "var(--color-border)", color: "var(--color-text-faint)" }}
          >
            <span>id Bitrix: {tarea.id}</span>
            {tarea.related_meeting_id && (
              <span>Consolidada en: {tarea.related_meeting_id}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Tag({ children, danger }: { children: React.ReactNode; danger?: boolean }) {
  return (
    <span
      className="rounded-full border px-1.5 py-0.5 text-[10px]"
      style={{
        background: danger ? "var(--color-prio-alta-soft)" : "var(--color-surface-hover)",
        borderColor: danger ? "var(--color-prio-alta)" : "var(--color-border)",
        color: danger ? "var(--color-prio-alta)" : "var(--color-text-muted)",
      }}
    >
      {children}
    </span>
  );
}

function ActionBtn({
  icon,
  title,
  onClick,
  accent,
}: {
  icon: React.ReactNode;
  title: string;
  onClick: () => void;
  accent: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className="flex h-7 w-7 items-center justify-center rounded-md border transition-colors"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
        color: "var(--color-text-muted)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--color-surface-hover)";
        e.currentTarget.style.color = accent;
        e.currentTarget.style.borderColor = accent;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "var(--color-surface)";
        e.currentTarget.style.color = "var(--color-text-muted)";
        e.currentTarget.style.borderColor = "var(--color-border)";
      }}
    >
      {icon}
    </button>
  );
}

function Detalle({ label, valor }: { label: string; valor: string | null | undefined }) {
  return (
    <div>
      <div
        className="mb-0.5 text-[10px] uppercase tracking-wider"
        style={{ color: "var(--color-text-faint)" }}
      >
        {label}
      </div>
      <div style={{ color: valor ? "var(--color-text)" : "var(--color-text-faint)" }}>
        {valor || "—"}
      </div>
    </div>
  );
}

function colorPorEstado(estado: EstadoEOS | null): string {
  switch (estado) {
    case "Blocked":
      return "var(--color-prio-alta)";
    case "Waiting":
    case "Delegated":
      return "var(--color-prio-media)";
    case "In Progress":
    case "Scheduled":
      return "var(--color-accent)";
    case "New":
      return "var(--color-text-muted)";
    default:
      return "var(--color-text-faint)";
  }
}

function formatearFecha(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("es-ES", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

function diasHasta(iso: string): number {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 999;
  const diffMs = d.getTime() - Date.now();
  return Math.ceil(diffMs / (1000 * 60 * 60 * 24));
}
