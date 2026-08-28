import { useEffect, useState } from "react";
import { CheckCircle2, AlertCircle, Info } from "lucide-react";

export type ToastKind = "success" | "info" | "error";

export interface ToastData {
  id: number;
  kind: ToastKind;
  message: string;
}

interface Props {
  toast: ToastData;
  onClose: (id: number) => void;
  duracion?: number;
}

/**
 * Toast simple estilo iOS AirPods-connect: entra desde abajo derecha con
 * spring, se auto-cierra a los N segundos con slide-out.
 *
 * Sprint 5.3 UX. Ver ToastContainer para el estado global (App level).
 */
export default function Toast({ toast, onClose, duracion = 3000 }: Props) {
  const [saliendo, setSaliendo] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => {
      setSaliendo(true);
      window.setTimeout(() => onClose(toast.id), 240);
    }, duracion);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const icono =
    toast.kind === "success" ? (
      <CheckCircle2 size={16} style={{ color: "var(--color-accent)" }} />
    ) : toast.kind === "error" ? (
      <AlertCircle size={16} style={{ color: "var(--color-prio-alta)" }} />
    ) : (
      <Info size={16} style={{ color: "var(--color-text-muted)" }} />
    );

  return (
    <div
      className={`flex items-center gap-2 rounded-xl border px-4 py-3 shadow-lg ${
        saliendo ? "animate-toast-out" : "animate-toast-in"
      }`}
      style={{
        background: "var(--color-surface)",
        borderColor: "var(--color-border)",
        boxShadow: "0 12px 32px rgba(0, 0, 0, 0.3)",
        minWidth: "260px",
      }}
    >
      {icono}
      <div
        className="flex-1 text-sm"
        style={{ color: "var(--color-text)" }}
      >
        {toast.message}
      </div>
    </div>
  );
}
