import { useEffect, useRef, useState } from "react";

interface Props {
  /** Ratio 0..1+. Ejemplo: 0.78 = 78%, 1.15 = 115% (sobrecarga). */
  ratio: number;
  size?: number;
  strokeWidth?: number;
  /** Umbrales para colores. Default: sobrecarga si >1.0, alto si >0.85. */
  colorSobrecarga?: string;
  colorAlto?: string;
  colorNormal?: string;
}

/**
 * Donut circular con animacion de fill al aparecer, estilo Health app.
 *
 * Renderiza un anillo SVG con stroke-dasharray dinamico. La animacion se
 * dispara al montar (o al cambiar ratio) via stroke-dashoffset con CSS
 * transition — GPU friendly y sin JS por frame.
 *
 * Colores segun umbrales:
 *  - >1.0  rojo (sobrecarga)
 *  - >0.85 ambar (justo)
 *  - resto verde
 *
 * Si ratio >1, el anillo se llena completo (100% visual) y el numero
 * central sigue mostrando el porcentaje real (ej. 115%).
 */
export default function DonutRatio({
  ratio,
  size = 80,
  strokeWidth = 8,
  colorSobrecarga = "var(--color-prio-alta)",
  colorAlto = "var(--color-prio-media)",
  colorNormal = "var(--color-accent)",
}: Props) {
  const [visible, setVisible] = useState(false);
  const ref = useRef<SVGCircleElement>(null);

  useEffect(() => {
    // Pequeno delay para asegurar mount + trigger de transition
    const id = window.setTimeout(() => setVisible(true), 50);
    return () => window.clearTimeout(id);
  }, []);

  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;

  // Cuanto del anillo debe estar visible. Cap a 100% visual pero el numero
  // sigue siendo el real (permite mostrar "115%" con anillo lleno).
  const filled = Math.min(1, Math.max(0, ratio));
  const targetOffset = circumference * (1 - filled);

  const color =
    ratio > 1.0 ? colorSobrecarga : ratio > 0.85 ? colorAlto : colorNormal;

  const percent = Math.round(ratio * 100);

  return (
    <div
      style={{
        position: "relative",
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <svg
        width={size}
        height={size}
        style={{ transform: "rotate(-90deg)" }}
      >
        {/* Track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-border)"
          strokeWidth={strokeWidth}
        />
        {/* Progress */}
        <circle
          ref={ref}
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={visible ? targetOffset : circumference}
          style={{
            transition: "stroke-dashoffset 900ms var(--ease-out-quart), stroke 300ms",
          }}
        />
      </svg>
      <div
        style={{
          position: "absolute",
          textAlign: "center",
          fontFamily: "var(--font-mono, ui-monospace)",
          color,
          fontWeight: 600,
        }}
      >
        <div style={{ fontSize: size * 0.24, lineHeight: 1 }}>{percent}%</div>
      </div>
    </div>
  );
}
