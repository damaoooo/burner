import { useEffect, useMemo, useState } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
  type ChartData,
  type ChartOptions
} from "chart.js";
import { Line } from "react-chartjs-2";
import type { MachineState } from "../types";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip);

interface Props {
  machines: MachineState[];
}

interface PowerPoint {
  key: string;
  label: string;
  watts: number;
}

interface ChartTheme {
  axis: string;
  grid: string;
  line: string;
  point: string;
  pointBorder: string;
  tick: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
}

export default function ClusterPowerChart({ machines }: Props) {
  const chartTheme = useChartTheme();
  const [history, setHistory] = useState<PowerPoint[]>([]);
  const latest = useMemo(() => summarizeClusterPower(machines), [machines]);

  useEffect(() => {
    if (machines.length === 0) {
      setHistory([]);
    }
  }, [machines.length]);

  useEffect(() => {
    if (!latest) {
      return;
    }
    setHistory((current) => {
      if (current[current.length - 1]?.key === latest.key) {
        return current;
      }
      return [...current, latest].slice(-180);
    });
  }, [latest]);

  const data = useMemo<ChartData<"line", number[], string>>(
    () => ({
      labels: history.map((point) => point.label),
      datasets: [
        {
          label: "Cluster Estimated Power",
          data: history.map((point) => point.watts),
          borderColor: chartTheme.line,
          backgroundColor: chartTheme.line,
          pointBackgroundColor: chartTheme.point,
          pointBorderColor: chartTheme.pointBorder,
          pointRadius: 0,
          pointHoverRadius: 3,
          borderWidth: 2,
          tension: 0.25
        }
      ]
    }),
    [chartTheme, history]
  );

  const options = useMemo<ChartOptions<"line">>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          border: { color: chartTheme.axis },
          grid: { display: false },
          ticks: {
            color: chartTheme.tick,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8
          }
        },
        y: {
          beginAtZero: true,
          suggestedMax: Math.max(latest?.watts ?? 1, totalClusterTdp(machines), 1),
          border: { color: chartTheme.axis },
          grid: { color: chartTheme.grid },
          ticks: {
            color: chartTheme.tick,
            callback: (value) => formatWatts(Number(value))
          }
        }
      },
      plugins: {
        tooltip: {
          backgroundColor: chartTheme.tooltipBg,
          bodyColor: chartTheme.tooltipText,
          borderColor: chartTheme.tooltipBorder,
          borderWidth: 1,
          titleColor: chartTheme.tooltipText,
          callbacks: {
            label: (context) => formatWatts(Number(context.parsed.y ?? 0))
          }
        }
      }
    }),
    [chartTheme, latest?.watts, machines]
  );

  return (
    <section className="cluster-power-panel">
      <div className="section-heading">
        <div>
          <h2>Cluster Estimated Power</h2>
          <span className="muted">{machines.length} allocated nodes</span>
        </div>
        <strong>{latest ? formatWatts(latest.watts) : "-"}</strong>
      </div>
      <div className="cluster-power-chart">
        {history.length > 1 ? (
          <Line data={data} options={options} />
        ) : (
          <div className="power-chart-empty">Waiting for node telemetry</div>
        )}
      </div>
    </section>
  );
}

function summarizeClusterPower(machines: MachineState[]): PowerPoint | null {
  const samples = machines
    .map((machine) => machine.hwInfo?.latest_power)
    .filter((sample): sample is NonNullable<typeof sample> => Boolean(sample?.timestamp));
  if (samples.length === 0) {
    return null;
  }

  const watts = samples.reduce((total, sample) => {
    const value = sample.cpu_watts_display ?? sample.cpu_watts ?? sample.cpu_watts_estimated ?? 0;
    return total + value;
  }, 0);
  const timestamp = samples
    .map((sample) => sample.timestamp ?? "")
    .sort()
    .at(-1) ?? new Date().toISOString();

  return {
    key: `${timestamp}:${watts.toFixed(2)}`,
    label: formatTimeLabel(timestamp),
    watts
  };
}

function totalClusterTdp(machines: MachineState[]): number {
  return machines.reduce((total, machine) => total + (machine.hwInfo?.cpu_tdp_total_watts ?? machine.config.cpu_tdp ?? 0), 0);
}

function formatWatts(value: number): string {
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)} MW`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)} kW`;
  }
  return `${value.toFixed(1)} W`;
}

function formatTimeLabel(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }
  return date.toLocaleTimeString([], { hour12: false, minute: "2-digit", second: "2-digit" });
}

function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(() => readChartTheme());

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setTheme(readChartTheme());
    update();
    media.addEventListener("change", update);
    window.addEventListener("burner-theme-change", update);
    return () => {
      media.removeEventListener("change", update);
      window.removeEventListener("burner-theme-change", update);
    };
  }, []);

  return theme;
}

function readChartTheme(): ChartTheme {
  return {
    axis: cssVar("--chart-axis", "#aab3c2"),
    grid: cssVar("--chart-grid", "#d8dee8"),
    line: cssVar("--chart-line", "#2563eb"),
    point: cssVar("--chart-point", "#111827"),
    pointBorder: cssVar("--chart-point-border", "#ffffff"),
    tick: cssVar("--chart-tick", "#64748b"),
    tooltipBg: cssVar("--chart-tooltip-bg", "#111827"),
    tooltipBorder: cssVar("--chart-tooltip-border", "#2563eb"),
    tooltipText: cssVar("--chart-tooltip-text", "#ffffff")
  };
}

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
