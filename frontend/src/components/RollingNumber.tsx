import { useEffect, useRef, useState } from "react";

interface Props {
  value: number;
  duracion?: number;
  className?: string;
  style?: React.CSSProperties;
}

/**
 * Numero que "rueda" cuando cambia de valor, estilo widget de iOS.
 *
 * Estrategia simple y GPU-friendly: renderizamos DOS spans absolutamente
 * posicionados. Cuando el valor cambia:
 *  1. El span nuevo entra desde arriba (translateY -100% -> 0).
 *  2. El span viejo sale por abajo (0 -> translateY 100%).
 *
 * Sin librerias, sin webs de digitos por dígito (complejidad overkill
 * para KPIs de 1-3 cifras). Coste por cambio: 2 elementos animados, ~320ms.
 * Si el valor no cambia, cero overhead.
 */
export default function RollingNumber({
  value,
  duracion = 320,
  className,
  style,
}: Props) {
  const [display, setDisplay] = useState(value);
  const [saliente, setSaliente] = useState<number | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (value === display) return;
    setSaliente(display);
    setDisplay(value);
    // Limpiar el saliente cuando termine la animacion para no dejar restos
    // en el DOM. Coste: un setTimeout por cambio.
    if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => {
      setSaliente(null);
    }, duracion);
    return () => {
      if (timeoutRef.current) window.clearTimeout(timeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return (
    <span
      className={className}
      style={{
        position: "relative",
        display: "inline-block",
        overflow: "hidden",
        verticalAlign: "baseline",
        lineHeight: "1em",
        minWidth: "1ch",
        ...style,
      }}
    >
      {/* Span invisible que fija el ancho para que otros elementos no
          salten cuando el numero cambia (ej. 9 -> 10) */}
      <span style={{ opacity: 0 }} aria-hidden>
        {display}
      </span>

      {/* Span entrante */}
      <span
        key={display}
        className="animate-number-roll"
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "flex-start",
        }}
      >
        {display}
      </span>

      {/* Span saliente (solo mientras dura la animacion) */}
      {saliente !== null && (
        <span
          key={`out-${saliente}`}
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-start",
            animation: `number-roll-out ${duracion}ms var(--ease-out-quart) forwards`,
          }}
        >
          {saliente}
        </span>
      )}

      <style>{`
        @keyframes number-roll-out {
          from { transform: translateY(0); opacity: 1; }
          to   { transform: translateY(100%); opacity: 0; }
        }
      `}</style>
    </span>
  );
}
