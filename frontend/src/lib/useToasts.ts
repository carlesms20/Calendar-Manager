import { useSyncExternalStore } from "react";
import type { ToastData, ToastKind } from "../components/Toast";

/**
 * Estado global de toasts sin librerias externas. useSyncExternalStore
 * de React 18: no hay Context, funciona en cualquier componente.
 *
 * Uso:
 *   const { toasts, show, dismiss } = useToasts();
 *   show("success", "Tarea creada");
 *
 * Sprint 5.3 UX.
 */

let _toasts: ToastData[] = [];
let _nextId = 1;
const _listeners = new Set<() => void>();

function _emit() {
  _listeners.forEach((l) => l());
}

function _subscribe(l: () => void): () => void {
  _listeners.add(l);
  return () => {
    _listeners.delete(l);
  };
}

export function showToast(kind: ToastKind, message: string): number {
  const id = _nextId++;
  _toasts = [..._toasts, { id, kind, message }];
  _emit();
  return id;
}

export function dismissToast(id: number): void {
  _toasts = _toasts.filter((t) => t.id !== id);
  _emit();
}

export function useToasts() {
  const toasts = useSyncExternalStore(
    _subscribe,
    () => _toasts,
    () => _toasts,
  );
  return { toasts, show: showToast, dismiss: dismissToast };
}
