"use client";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  testId?: string;
}

/**
 * Minimal inline price sparkline. Renders nothing until at least two samples
 * have accumulated from the SSE stream, so rows fill in progressively.
 */
export default function Sparkline({
  data,
  width = 80,
  height = 24,
  color,
  testId,
}: SparklineProps) {
  if (data.length < 2) {
    return <div style={{ width, height }} data-testid={testId} />;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const lineColor = color ?? (data[data.length - 1] >= data[0] ? "#26a641" : "#f85149");

  const coords = data.map((val, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((val - min) / range) * (height - 2) - 1;
    return [x, y] as const;
  });

  const points = coords.map(([x, y]) => `${x},${y}`).join(" ");
  const areaPath = [
    `M ${coords[0][0]},${height}`,
    ...coords.map(([x, y]) => `L ${x},${y}`),
    `L ${width},${height}`,
    "Z",
  ].join(" ");
  const [lastX, lastY] = coords[coords.length - 1];

  return (
    <svg
      width={width}
      height={height}
      className="inline-block align-middle"
      data-testid={testId}
      role="img"
      aria-label="price sparkline"
    >
      <path d={areaPath} fill={lineColor} fillOpacity={0.12} stroke="none" />
      <polyline
        points={points}
        fill="none"
        stroke={lineColor}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={lastX} cy={lastY} r={1.75} fill={lineColor} />
    </svg>
  );
}
