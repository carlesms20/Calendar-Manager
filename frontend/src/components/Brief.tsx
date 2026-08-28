import { X, RefreshCw, AlertTriangle, CheckCircle2, Clock, Users, MessageSquare, Target, Zap, Calendar, ChevronRight } from "lucide-react";
import type {
  BriefEjecutivo,
  ItemTareaBrief,
  KeyOutcome,
  ItemConversacion,
  ItemCalendario,
  IntegrityFinding,
  ReminderItem,
  BloquePropuesto,
} from "../lib/types";

interface Props {
  abierto: boolean;
  onCerrar: () => void;
  brief: BriefEjecutivo | null;
  cargando: boolean;
  error: string | null;
  onRecargar: () => void;
}

/**
 * Modal del Executive Brief diario (PHASE 1 §4).
 *
 * Renderiza las 13 secciones en orden. Diseno pensado para lectura
 * rapida al arrancar el dia: cada seccion es una tarjeta separada,
 * las que estan vacias no se muestran (no queremos que el CEO scrollee
 * por "Delegadas: 0 items").
 *
 * Excepciones que SIEMPRE se muestran aunque esten vacias:
 * - Executive Summary (siempre hay algo que decir).
 * - Calendar Overview (siempre hay dia).
 * - Integrity Check (si esta vacio, es tranquilizador).
 */
export default function Brief({
  abierto,
  onCerrar,
  brief,
  cargando,
  error,
  onRecargar,
}: Props) {
  if (!abierto) return null;

  return (
    <div
      className="animate-backdrop fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 pt-8"
      style={{
        background: "rgba(0, 0, 0, 0.55)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
      }}
      onClick={onCerrar}
    >
      <div
        className="animate-modal-spring w-full max-w-2xl rounded-2xl border"
        style={{
          background: "var(--color-surface)",
          borderColor: "var(--color-border)",
          boxShadow: "0 24px 64px rgba(0, 0, 0, 0.4)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Cabecera */}
        <div
          className="sticky top-0 z-10 flex items-center justify-between gap-3 rounded-t-2xl border-b px-6 py-4"
          style={{
            background: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-full"
              style={{ background: "var(--color-prio-alta-soft)" }}
            >
              <Target size={18} style={{ color: "var(--color-prio-alta)" }} />
            </div>
            <div>
              <h2
                className="text-lg leading-tight"
                style={{
                  fontFamily: "var(--font-display)",
                  color: "var(--color-text)",
                }}
              >
                Executive Brief
              </h2>
              <p className="mt-0.5 font-mono text-[11px]" style={{ color: "var(--color-text-muted)" }}>
                {brief ? _formatearFecha(brief.fecha_ref) : "PHASE 1 §4 · Sprint 3"}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={onRecargar}
              disabled={cargando}
              className="rounded-md p-2 transition-colors disabled:opacity-40"
              style={{ color: "var(--color-text-muted)" }}
              onMouseEnter={(e) => {
                if (!cargando) {
                  e.currentTarget.style.background = "var(--color-surface-hover)";
                  e.currentTarget.style.color = "var(--color-text)";
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "var(--color-text-muted)";
              }}
              aria-label="Regenerar brief"
              title="Regenerar brief"
            >
              <RefreshCw size={16} className={cargando ? "animate-spin" : ""} />
            </button>
            <button
              onClick={onCerrar}
              className="rounded-md p-2 transition-colors"
              style={{ color: "var(--color-text-muted)" }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "var(--color-surface-hover)";
                e.currentTarget.style.color = "var(--color-text)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
                e.currentTarget.style.color = "var(--color-text-muted)";
              }}
              aria-label="Cerrar"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Cuerpo */}
        <div className="px-6 py-5">
          {cargando && !brief && <SkeletonCarga />}
          {error && !cargando && <BloqueError error={error} onReintentar={onRecargar} />}
          {brief && !error && <ContenidoBrief brief={brief} />}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Contenido principal (cuando brief != null)
// ---------------------------------------------------------------------------

function ContenidoBrief({ brief }: { brief: BriefEjecutivo }) {
  const ov = brief.calendar_overview;
  const bufferBajo = ov.buffer_pct < 30;

  return (
    <div className="space-y-5">
      {/* 1. Executive Summary */}
      <Seccion titulo="Situación" icono={<Zap size={14} />}>
        <p
          className="text-sm leading-relaxed"
          style={{ color: "var(--color-text)" }}
        >
          {brief.executive_summary || "[NO DATA]"}
        </p>
      </Seccion>

      {/* 2. Calendar Overview */}
      <Seccion titulo="Calendario" icono={<Calendar size={14} />}>
        <div className="grid grid-cols-4 gap-2 text-center">
          <MetricaCalendario valor={ov.confirmados.length} etiqueta="Confirmados" />
          <MetricaCalendario valor={ov.propuestos.length} etiqueta="Propuestas" tono="media" />
          <MetricaCalendario valor={ov.bloques_protegidos.length} etiqueta="Bloques" />
          <MetricaCalendario
            valor={`${ov.buffer_pct}%`}
            etiqueta="Buffer libre"
            tono={bufferBajo ? "alta" : undefined}
          />
        </div>
        {ov.conflictos.length > 0 && (
          <div className="mt-3 rounded-md border px-3 py-2 text-xs" style={_estiloAviso("alta")}>
            <strong>{ov.conflictos.length} conflicto{ov.conflictos.length !== 1 ? "s" : ""}:</strong>{" "}
            {ov.conflictos.slice(0, 2).join("; ")}
            {ov.conflictos.length > 2 && ` +${ov.conflictos.length - 2} más`}
          </div>
        )}
        {ov.riesgo_fragmentacion && (
          <div className="mt-2 text-xs" style={{ color: "var(--color-prio-media)" }}>
            ⚠ Día fragmentado: muchos huecos cortos entre eventos.
          </div>
        )}
        <div className="mt-3 space-y-1">
          {[...ov.confirmados, ...ov.propuestos, ...ov.bloques_protegidos]
            .sort((a, b) => a.fecha_inicio.localeCompare(b.fecha_inicio))
            .slice(0, 6)
            .map((it) => (
              <ItemCalendarioRow key={`${it.tipo}-${it.id}`} item={it} />
            ))}
        </div>
      </Seccion>

      {/* 3. Three Key Outcomes */}
      {brief.three_key_outcomes.length > 0 && (
        <Seccion
          titulo={`Los resultados del día (${brief.three_key_outcomes.length}/3)`}
          icono={<Target size={14} />}
        >
          <div className="space-y-3">
            {brief.three_key_outcomes.map((o, i) => (
              <KeyOutcomeCard key={i} outcome={o} indice={i + 1} />
            ))}
          </div>
        </Seccion>
      )}

      {/* 5. People Blocked */}
      {brief.people_blocked.length > 0 && (
        <Seccion
          titulo={`Personas esperándote (${brief.people_blocked.length})`}
          icono={<Users size={14} />}
          tono="alta"
        >
          <div className="space-y-2">
            {brief.people_blocked.map((t) => (
              <ItemTareaRow key={t.id} item={t} />
            ))}
          </div>
        </Seccion>
      )}

      {/* 6. Executive Conversations */}
      {brief.executive_conversations.length > 0 && (
        <Seccion
          titulo={`Conversaciones a agrupar (${brief.executive_conversations.length})`}
          icono={<MessageSquare size={14} />}
        >
          <div className="space-y-3">
            {brief.executive_conversations.map((c, i) => (
              <ConversacionCard key={i} conv={c} />
            ))}
          </div>
        </Seccion>
      )}

      {/* 4. Quick Actions */}
      {brief.quick_actions.length > 0 && (
        <Seccion
          titulo={`Quick actions (${brief.quick_actions.length})`}
          icono={<Zap size={14} />}
        >
          <div className="space-y-2">
            {brief.quick_actions.map((t) => (
              <ItemTareaRow key={t.id} item={t} compacto />
            ))}
          </div>
        </Seccion>
      )}

      {/* 8. Waiting */}
      {brief.waiting.length > 0 && (
        <Seccion
          titulo={`Waiting (${brief.waiting.length})`}
          icono={<Clock size={14} />}
        >
          <div className="space-y-2">
            {brief.waiting.map((t) => (
              <ItemTareaRow key={t.id} item={t} compacto />
            ))}
          </div>
        </Seccion>
      )}

      {/* 7. Delegated Supervision */}
      {brief.delegated_supervision.length > 0 && (
        <Seccion
          titulo={`Delegadas bajo supervisión (${brief.delegated_supervision.length})`}
          icono={<Users size={14} />}
        >
          <div className="space-y-2">
            {brief.delegated_supervision.map((t) => (
              <ItemTareaRow key={t.id} item={t} />
            ))}
          </div>
        </Seccion>
      )}

      {/* Sprint 5 - Reminders priorizados §13. Va justo despues de key
          outcomes: son las alertas de accion inmediata. */}
      {brief.reminders.length > 0 && (
        <Seccion
          titulo={`Recordatorios (${brief.reminders.length})`}
          icono={<AlertTriangle size={14} />}
          tono={brief.reminders.some((r) => r.prioridad_num <= 2) ? "alta" : "media"}
        >
          <div className="space-y-2">
            {brief.reminders.slice(0, 8).map((r, i) => (
              <ReminderRow key={i} reminder={r} />
            ))}
          </div>
        </Seccion>
      )}

      {/* 9. Proposed Work Blocks — Sprint 5 estructurados */}
      {brief.proposed_work_blocks.length > 0 && (
        <Seccion
          titulo={
            brief.capacidad_hoy && !brief.capacidad_hoy.tiene_bloque_estrategico
              ? "Bloques propuestos (sin ventana estratégica)"
              : "Bloques propuestos para hoy"
          }
          icono={<Clock size={14} />}
        >
          <div className="space-y-2">
            {brief.proposed_work_blocks.map((b, i) => (
              <BloqueRow key={i} bloque={b} />
            ))}
          </div>
          {brief.capacidad_hoy && (
            <div
              className="mt-2 text-[11px]"
              style={{ color: "var(--color-text-faint)" }}
            >
              {brief.capacidad_hoy.libre_min} min libres · buffer{" "}
              {brief.capacidad_hoy.buffer_pct}%
            </div>
          )}
        </Seccion>
      )}

      {/* Sprint 5 - Forecast próxima semana (§14) */}
      {brief.forecast_proxima_semana &&
        brief.forecast_proxima_semana.riesgos.length > 0 && (
          <Seccion
            titulo="Semana que viene"
            icono={<AlertTriangle size={14} />}
            tono={
              brief.forecast_proxima_semana.riesgos.some(
                (r) => r.severidad === "alta",
              )
                ? "alta"
                : "media"
            }
          >
            <div className="mb-2 flex items-center gap-3 text-xs">
              <span style={{ color: "var(--color-text-muted)" }}>
                Ratio de carga:
              </span>
              <span
                className="font-mono"
                style={{
                  color:
                    brief.forecast_proxima_semana.ratio_carga > 1.0
                      ? "var(--color-prio-alta)"
                      : brief.forecast_proxima_semana.ratio_carga > 0.85
                      ? "var(--color-prio-media)"
                      : "var(--color-text)",
                }}
              >
                {Math.round(brief.forecast_proxima_semana.ratio_carga * 100)}%
              </span>
              <span style={{ color: "var(--color-text-faint)" }}>·</span>
              <span style={{ color: "var(--color-text-muted)" }}>
                {brief.forecast_proxima_semana.n_deadlines_esa_semana} deadlines
              </span>
            </div>
            <div className="space-y-1.5">
              {brief.forecast_proxima_semana.riesgos.map((r, i) => (
                <div
                  key={i}
                  className="rounded-md border px-2 py-1.5 text-xs"
                  style={{
                    background: "var(--color-surface)",
                    borderColor:
                      r.severidad === "alta"
                        ? "var(--color-prio-alta)"
                        : "var(--color-border)",
                  }}
                >
                  <span
                    className="uppercase text-[9px] mr-2"
                    style={{
                      color:
                        r.severidad === "alta"
                          ? "var(--color-prio-alta)"
                          : "var(--color-prio-media)",
                      fontWeight: 700,
                    }}
                  >
                    {r.severidad}
                  </span>
                  <span style={{ color: "var(--color-text)" }}>
                    {r.descripcion}
                  </span>
                </div>
              ))}
            </div>
          </Seccion>
        )}

      {/* 10. Not Today */}
      {brief.not_today.length > 0 && (
        <Seccion
          titulo={`Not today (${brief.not_today.length})`}
          icono={<ChevronRight size={14} />}
        >
          <p className="mb-2 text-xs" style={{ color: "var(--color-text-faint)" }}>
            No requieren atención hoy. Siguen en el inventario para revisión posterior.
          </p>
          <div className="space-y-1">
            {brief.not_today.slice(0, 5).map((t) => (
              <div
                key={t.id}
                className="text-sm"
                style={{ color: "var(--color-text-muted)" }}
              >
                • {t.title}
              </div>
            ))}
            {brief.not_today.length > 5 && (
              <div className="text-xs" style={{ color: "var(--color-text-faint)" }}>
                +{brief.not_today.length - 5} más
              </div>
            )}
          </div>
        </Seccion>
      )}

      {/* 11. Remaining Inventory (contador) */}
      <Seccion titulo="Inventario completo">
        <div className="flex items-center justify-between text-sm">
          <span style={{ color: "var(--color-text-muted)" }}>
            Total de tareas activas
          </span>
          <span
            className="font-mono text-lg"
            style={{ color: "var(--color-text)" }}
          >
            {brief.remaining_inventory_total}
          </span>
        </div>
        {Object.keys(brief.remaining_inventory_por_tipo).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2">
            {Object.entries(brief.remaining_inventory_por_tipo).map(([tipo, n]) => (
              <span
                key={tipo}
                className="rounded-md px-2 py-0.5 text-[11px]"
                style={{
                  background: "var(--color-surface-hover)",
                  color: "var(--color-text-muted)",
                }}
              >
                {tipo}: {n}
              </span>
            ))}
          </div>
        )}
      </Seccion>

      {/* 12. Missing Information */}
      {brief.missing_information.length > 0 && (
        <Seccion
          titulo={`Info incompleta (${brief.missing_information.length})`}
          icono={<AlertTriangle size={14} />}
          tono="media"
        >
          <ul className="space-y-1">
            {brief.missing_information.slice(0, 6).map((m, i) => (
              <li
                key={i}
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                • {m}
              </li>
            ))}
            {brief.missing_information.length > 6 && (
              <li className="text-xs" style={{ color: "var(--color-text-faint)" }}>
                +{brief.missing_information.length - 6} más
              </li>
            )}
          </ul>
        </Seccion>
      )}

      {/* 13. Integrity Check */}
      <Seccion titulo="Integrity Check" icono={<CheckCircle2 size={14} />}>
        <IntegrityLista findings={brief.integrity_check} />
      </Seccion>

      {/* Footer con timestamp de generacion */}
      <div
        className="border-t pt-3 text-center text-[11px]"
        style={{
          borderColor: "var(--color-border)",
          color: "var(--color-text-faint)",
        }}
      >
        Generado {_formatearHora(brief.generado_en)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Subcomponentes
// ---------------------------------------------------------------------------

function Seccion({
  titulo,
  icono,
  children,
  tono,
}: {
  titulo: string;
  icono?: React.ReactNode;
  children: React.ReactNode;
  tono?: "alta" | "media";
}) {
  return (
    <div
      className="rounded-lg border p-3"
      style={{
        background: tono === "alta"
          ? "var(--color-prio-alta-soft)"
          : tono === "media"
          ? "var(--color-prio-media-soft)"
          : "var(--color-surface-hover)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="mb-2 flex items-center gap-1.5">
        {icono && (
          <span style={{
            color: tono === "alta"
              ? "var(--color-prio-alta)"
              : tono === "media"
              ? "var(--color-prio-media)"
              : "var(--color-text-muted)",
          }}>
            {icono}
          </span>
        )}
        <h3
          className="text-[13px] uppercase tracking-wider"
          style={{
            color: "var(--color-text-muted)",
            fontWeight: 600,
          }}
        >
          {titulo}
        </h3>
      </div>
      {children}
    </div>
  );
}

function MetricaCalendario({
  valor,
  etiqueta,
  tono,
}: {
  valor: number | string;
  etiqueta: string;
  tono?: "alta" | "media";
}) {
  return (
    <div
      className="rounded-md px-2 py-2"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
      }}
    >
      <div
        className="font-mono text-xl leading-none"
        style={{
          color: tono === "alta"
            ? "var(--color-prio-alta)"
            : tono === "media"
            ? "var(--color-prio-media)"
            : "var(--color-text)",
        }}
      >
        {valor}
      </div>
      <div
        className="mt-1 text-[10px] uppercase tracking-wide"
        style={{ color: "var(--color-text-faint)" }}
      >
        {etiqueta}
      </div>
    </div>
  );
}

function ItemCalendarioRow({ item }: { item: ItemCalendario }) {
  const hi = _horaCorta(item.fecha_inicio);
  const hf = _horaCorta(item.fecha_fin);
  const badge = _badgeTipoCalendario(item.tipo);
  return (
    <div
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
      }}
    >
      <span
        className="font-mono"
        style={{ color: "var(--color-text-muted)", minWidth: "82px" }}
      >
        {hi}–{hf}
      </span>
      <span
        className="rounded px-1.5 py-0.5 text-[10px] uppercase"
        style={badge.estilo}
      >
        {badge.texto}
      </span>
      <span
        className="flex-1 truncate"
        style={{ color: "var(--color-text)" }}
      >
        {item.nombre}
      </span>
      {item.involucrado && (
        <span
          className="text-[11px]"
          style={{ color: "var(--color-text-faint)" }}
        >
          @{item.involucrado}
        </span>
      )}
    </div>
  );
}

function KeyOutcomeCard({
  outcome,
  indice,
}: {
  outcome: KeyOutcome;
  indice: number;
}) {
  return (
    <div
      className="rounded-md border p-3"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-xs"
          style={{
            background: "var(--color-accent)",
            color: "white",
          }}
        >
          {indice}
        </div>
        <div className="flex-1 min-w-0">
          <div
            className="text-sm leading-snug"
            style={{ color: "var(--color-text)", fontWeight: 500 }}
          >
            {outcome.resultado}
          </div>
          <div
            className="mt-1 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            {outcome.razon}
          </div>
          <div className="mt-2 flex items-center gap-2 text-[10px] uppercase tracking-wide">
            <span
              className="rounded-full px-2 py-0.5"
              style={{
                background: "var(--color-surface-hover)",
                color: "var(--color-text-muted)",
              }}
            >
              {outcome.mecanismo.replace(/_/g, " ")}
            </span>
            {outcome.items_relacionados.length > 0 && (
              <span style={{ color: "var(--color-text-faint)" }}>
                Tareas: {outcome.items_relacionados.join(", ")}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function ConversacionCard({ conv }: { conv: ItemConversacion }) {
  const recomBadge = _badgeRecomendacion(conv.recomendacion_asistencia);
  return (
    <div
      className="rounded-md border p-3"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <div
          className="text-sm"
          style={{ color: "var(--color-text)", fontWeight: 500 }}
        >
          {conv.interlocutor}
        </div>
        <div className="flex items-center gap-2 text-[11px]">
          <span
            className="rounded-full px-2 py-0.5 uppercase"
            style={_estiloPrioridad(conv.prioridad)}
          >
            {conv.prioridad}
          </span>
          <span
            className="font-mono"
            style={{ color: "var(--color-text-muted)" }}
          >
            {conv.duracion_estimada_min}min
          </span>
        </div>
      </div>
      <ul className="mt-2 space-y-0.5">
        {conv.temas.map((t, i) => (
          <li
            key={i}
            className="text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            • {t}
          </li>
        ))}
      </ul>

      {/* Sprint 4 Bloque C - Meeting Delegation Rule §7.4 */}
      {conv.recomendacion_asistencia !== "asistir" && (
        <div
          className="mt-2 flex items-center gap-2 rounded px-2 py-1.5 text-[11px]"
          style={{
            background: recomBadge.bg,
            border: `1px solid ${recomBadge.border}`,
          }}
        >
          <span style={{ color: recomBadge.color, fontWeight: 600 }}>
            {recomBadge.label}
          </span>
          {conv.razon_recomendacion && (
            <span style={{ color: "var(--color-text-muted)" }}>
              {conv.razon_recomendacion}
            </span>
          )}
        </div>
      )}

      {conv.impacto_no_celebrarla && (
        <div
          className="mt-2 text-[11px]"
          style={{ color: "var(--color-text-faint)", fontStyle: "italic" }}
        >
          {conv.impacto_no_celebrarla}
        </div>
      )}
    </div>
  );
}

function _badgeRecomendacion(rec: string): {
  label: string;
  color: string;
  bg: string;
  border: string;
} {
  // Sprint 4 - Meeting Delegation Rule §7.4
  if (rec === "delegar") {
    return {
      label: "💡 Puedes delegar esta reunión",
      color: "var(--color-prio-baja)",
      bg: "var(--color-prio-baja-soft)",
      border: "var(--color-prio-baja)",
    };
  }
  if (rec === "decidir_asincrono") {
    return {
      label: "💡 Decisión asíncrona posible",
      color: "var(--color-prio-media)",
      bg: "var(--color-prio-media-soft)",
      border: "var(--color-prio-media)",
    };
  }
  return {
    label: "Asistir",
    color: "var(--color-text-muted)",
    bg: "var(--color-surface-hover)",
    border: "var(--color-border)",
  };
}

function ItemTareaRow({
  item,
  compacto,
}: {
  item: ItemTareaBrief;
  compacto?: boolean;
}) {
  const deadline = item.deadline ? _fechaCorta(item.deadline) : null;
  const followup = item.review_date ? _fechaCorta(item.review_date) : null;
  const followupVencido = item.razon?.includes("vencido");
  const esWaiting = item.status_eos === "Waiting";
  const esDelegated = item.status_eos === "Delegated";

  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div
            className="text-sm leading-snug truncate"
            style={{ color: "var(--color-text)" }}
          >
            {item.title}
          </div>

          {/* Sprint 4: Owner chip para delegadas */}
          {esDelegated && item.owner_nombre && (
            <div
              className="mt-1 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px]"
              style={{
                background: "var(--color-surface-hover)",
                color: "var(--color-text-muted)",
              }}
            >
              👤 {item.owner_nombre}
            </div>
          )}

          {/* Sprint 4: waiting_for como linea principal para Waiting */}
          {esWaiting && item.waiting_for && (
            <div
              className="mt-1 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              Esperando: {item.waiting_for}
            </div>
          )}

          {!compacto && item.next_action && !esWaiting && (
            <div
              className="mt-1 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              → {item.next_action}
            </div>
          )}

          {item.razon && (
            <div
              className="mt-1 text-[11px]"
              style={{
                color: followupVencido
                  ? "var(--color-prio-alta)"
                  : "var(--color-text-faint)",
                fontStyle: "italic",
              }}
            >
              {item.razon}
            </div>
          )}

          {/* Sprint 4: escalation_condition visible para delegadas */}
          {!compacto && esDelegated && item.escalation_condition && (
            <div
              className="mt-1 text-[11px]"
              style={{ color: "var(--color-prio-media)" }}
              title="Escalation condition"
            >
              ⚡ {item.escalation_condition}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-0.5 text-[10px]">
          {/* Sprint 4: badge dias_vencido para Waiting con follow-up pasado */}
          {esWaiting && item.dias_vencido !== null && item.dias_vencido > 0 && (
            <span
              className="rounded-full px-2 py-0.5 font-mono"
              style={{
                background: "var(--color-prio-alta-soft)",
                color: "var(--color-prio-alta)",
              }}
            >
              +{item.dias_vencido}d
            </span>
          )}
          {deadline && (
            <span
              className="font-mono"
              style={{ color: "var(--color-prio-alta)" }}
              title="Deadline"
            >
              ⏱ {deadline}
            </span>
          )}
          {followup && !deadline && (
            <span
              className="font-mono"
              style={{ color: "var(--color-text-muted)" }}
              title="Review date"
            >
              ↻ {followup}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function IntegrityLista({ findings }: { findings: IntegrityFinding[] }) {
  const fallos = findings.filter((f) => !f.ok);
  const ok = findings.filter((f) => f.ok);

  if (fallos.length === 0) {
    return (
      <div
        className="flex items-center gap-2 text-sm"
        style={{ color: "var(--color-text-muted)" }}
      >
        <CheckCircle2 size={14} style={{ color: "var(--color-prio-baja)" }} />
        Todo en orden: {ok.length} checks pasados.
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {fallos.map((f, i) => (
        <div
          key={i}
          className="flex items-start gap-2 text-xs"
          style={{ color: "var(--color-text)" }}
        >
          <AlertTriangle
            size={12}
            className="mt-0.5 shrink-0"
            style={{ color: "var(--color-prio-media)" }}
          />
          <div>
            <div style={{ fontWeight: 500 }}>{f.check}</div>
            {f.detalle && (
              <div
                className="mt-0.5"
                style={{ color: "var(--color-text-muted)" }}
              >
                {f.detalle}
              </div>
            )}
          </div>
        </div>
      ))}
      <div
        className="mt-2 text-[11px]"
        style={{ color: "var(--color-text-faint)" }}
      >
        + {ok.length} checks pasados
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Estados de carga y error
// ---------------------------------------------------------------------------

function SkeletonCarga() {
  return (
    <div className="space-y-3">
      <div
        className="h-4 w-3/4 animate-shimmer rounded"
      />
      <div
        className="h-20 animate-shimmer rounded-lg"
      />
      <div
        className="h-24 animate-shimmer rounded-lg"
      />
      <div
        className="h-16 animate-shimmer rounded-lg"
      />
      <p
        className="text-center text-xs"
        style={{ color: "var(--color-text-faint)" }}
      >
        Generando brief (recopilando calendario, tareas, bloques y llamando al modelo)…
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
    <div className="py-6 text-center">
      <AlertTriangle
        size={32}
        className="mx-auto mb-3"
        style={{ color: "var(--color-prio-alta)" }}
      />
      <p
        className="text-sm"
        style={{ color: "var(--color-text)" }}
      >
        No pude generar el brief
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
          background: "var(--color-accent)",
          color: "white",
        }}
      >
        Reintentar
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers de formato
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Componentes Sprint 5
// ---------------------------------------------------------------------------

function ReminderRow({ reminder }: { reminder: ReminderItem }) {
  const badge = _badgeReminder(reminder.prioridad_num, reminder.categoria);
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor: reminder.prioridad_num <= 2 ? badge.color : "var(--color-border)",
      }}
    >
      <div className="flex items-start gap-2">
        <span
          className="mt-0.5 rounded px-1.5 py-0.5 font-mono text-[10px] shrink-0"
          style={{
            background: badge.bg,
            color: badge.color,
            minWidth: "26px",
            textAlign: "center",
            fontWeight: 700,
          }}
          title={badge.label}
        >
          P{reminder.prioridad_num}
        </span>
        <div className="flex-1 min-w-0">
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
      </div>
    </div>
  );
}

function _badgeReminder(
  prioridad: number,
  categoria: string,
): { label: string; color: string; bg: string } {
  const cats: Record<string, string> = {
    persona_bloqueada: "Persona bloqueada",
    decision: "Decisión pendiente",
    dependencia_externa: "Dependencia externa",
    revision_comprometida: "Revisión pactada",
    riesgo_incumplimiento: "Riesgo de deadline",
    reunion_propuesta_no_confirmada: "Reunión sin confirmar",
  };
  const label = cats[categoria] || categoria;
  if (prioridad === 1)
    return { label, color: "var(--color-prio-alta)", bg: "var(--color-prio-alta-soft)" };
  if (prioridad === 2)
    return { label, color: "var(--color-prio-alta)", bg: "var(--color-prio-alta-soft)" };
  if (prioridad === 3)
    return { label, color: "var(--color-prio-media)", bg: "var(--color-prio-media-soft)" };
  if (prioridad === 4)
    return { label, color: "var(--color-prio-media)", bg: "var(--color-prio-media-soft)" };
  return { label, color: "var(--color-text-muted)", bg: "var(--color-surface-hover)" };
}

function BloqueRow({ bloque }: { bloque: BloquePropuesto }) {
  const hi = _horaCorta(bloque.inicio);
  const hf = _horaCorta(bloque.fin);
  const esEstrat = bloque.tipo === "estrategico";
  return (
    <div
      className="rounded-md border px-3 py-2"
      style={{
        background: "var(--color-surface)",
        borderColor: esEstrat
          ? "var(--color-accent, var(--color-user-bubble-border))"
          : "var(--color-border)",
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
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
            style={{ color: "var(--color-text)", fontWeight: esEstrat ? 500 : 400 }}
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
      </div>
    </div>
  );
}

function _formatearFecha(iso: string): string {
  try {
    const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
    return d.toLocaleDateString("es-ES", {
      weekday: "long",
      day: "numeric",
      month: "long",
    });
  } catch {
    return iso;
  }
}

function _formatearHora(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
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

function _fechaCorta(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("es-ES", {
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return iso;
  }
}

function _badgeTipoCalendario(tipo: string): {
  texto: string;
  estilo: React.CSSProperties;
} {
  switch (tipo) {
    case "propuesto":
      return {
        texto: "Prop",
        estilo: {
          background: "var(--color-prio-media-soft)",
          color: "var(--color-prio-media)",
          fontSize: "9px",
        },
      };
    case "bloque_protegido":
      return {
        texto: "Bloq",
        estilo: {
          background: "var(--color-surface-hover)",
          color: "var(--color-text-muted)",
          fontSize: "9px",
        },
      };
    default:
      return {
        texto: "OK",
        estilo: {
          background: "var(--color-prio-baja-soft)",
          color: "var(--color-prio-baja)",
          fontSize: "9px",
        },
      };
  }
}

function _estiloPrioridad(prio: string): React.CSSProperties {
  switch (prio) {
    case "alta":
      return {
        background: "var(--color-prio-alta-soft)",
        color: "var(--color-prio-alta)",
      };
    case "baja":
      return {
        background: "var(--color-prio-baja-soft)",
        color: "var(--color-prio-baja)",
      };
    default:
      return {
        background: "var(--color-prio-media-soft)",
        color: "var(--color-prio-media)",
      };
  }
}

function _estiloAviso(tono: "alta" | "media"): React.CSSProperties {
  if (tono === "alta") {
    return {
      background: "var(--color-prio-alta-soft)",
      color: "var(--color-prio-alta)",
      borderColor: "var(--color-prio-alta)",
    };
  }
  return {
    background: "var(--color-prio-media-soft)",
    color: "var(--color-prio-media)",
    borderColor: "var(--color-prio-media)",
  };
}
