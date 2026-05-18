export interface TelemetryPoint {
  label: string;
  value: number;
}

interface Props {
  points: TelemetryPoint[];
  yMax?: number;
  yAxisLabel: string;
  xAxisLabel?: string;
  emptyText?: string;
  valueFormatter?: (value: number) => string;
}

const WIDTH = 640;
const HEIGHT = 220;
const LEFT = 58;
const RIGHT = 16;
const TOP = 18;
const BOTTOM = 42;

export default function TelemetryLineChart({
  points,
  yMax,
  yAxisLabel,
  xAxisLabel = "Time (local, HH:MM:SS)",
  emptyText = "Waiting for telemetry",
  valueFormatter = (value) => `${value.toFixed(1)} W`
}: Props) {
  if (points.length < 2) {
    return <div className="power-chart-empty">{emptyText}</div>;
  }

  const plotWidth = WIDTH - LEFT - RIGHT;
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const maxPoint = Math.max(...points.map((point) => point.value), 1);
  const maxY = niceMax(Math.max(yMax ?? 0, maxPoint));
  const line = points
    .map((point, index) => {
      const x = LEFT + (index / Math.max(1, points.length - 1)) * plotWidth;
      const y = TOP + plotHeight - (Math.max(0, point.value) / maxY) * plotHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => maxY * ratio);
  const xTicks = chooseXTicks(points);

  return (
    <svg className="telemetry-chart-svg" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={yAxisLabel}>
      <text className="telemetry-axis-title" x={LEFT} y={12}>
        {yAxisLabel}
      </text>
      {yTicks.map((tick) => {
        const y = TOP + plotHeight - (tick / maxY) * plotHeight;
        return (
          <g key={tick}>
            <line className="telemetry-grid" x1={LEFT} x2={WIDTH - RIGHT} y1={y} y2={y} />
            <text className="telemetry-tick" x={LEFT - 8} y={y + 4} textAnchor="end">
              {valueFormatter(tick)}
            </text>
          </g>
        );
      })}
      <line className="telemetry-axis" x1={LEFT} x2={WIDTH - RIGHT} y1={TOP + plotHeight} y2={TOP + plotHeight} />
      <line className="telemetry-axis" x1={LEFT} x2={LEFT} y1={TOP} y2={TOP + plotHeight} />
      <polyline className="telemetry-line" points={line} />
      {xTicks.map((tick) => {
        const x = LEFT + (tick.index / Math.max(1, points.length - 1)) * plotWidth;
        return (
          <text className="telemetry-tick" key={`${tick.index}-${tick.label}`} x={x} y={HEIGHT - 24} textAnchor="middle">
            {tick.label}
          </text>
        );
      })}
      <text className="telemetry-axis-title" x={LEFT + plotWidth / 2} y={HEIGHT - 4} textAnchor="middle">
        {xAxisLabel}
      </text>
    </svg>
  );
}

function chooseXTicks(points: TelemetryPoint[]): Array<{ index: number; label: string }> {
  const indexes = new Set<number>([0, points.length - 1]);
  if (points.length > 4) {
    indexes.add(Math.floor((points.length - 1) / 2));
  }
  if (points.length > 12) {
    indexes.add(Math.floor((points.length - 1) / 4));
    indexes.add(Math.floor(((points.length - 1) * 3) / 4));
  }
  return [...indexes]
    .sort((left, right) => left - right)
    .map((index) => ({ index, label: points[index].label }));
}

function niceMax(value: number): number {
  if (value <= 0) {
    return 1;
  }
  const exponent = Math.floor(Math.log10(value));
  const base = 10 ** exponent;
  const scaled = value / base;
  if (scaled <= 2) {
    return 2 * base;
  }
  if (scaled <= 5) {
    return 5 * base;
  }
  return 10 * base;
}
