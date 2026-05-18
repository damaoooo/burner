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
import { useAppState } from "../state/AppState";
import type { MachineState } from "../types";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip);

interface Props {
  machine: MachineState;
}

interface PowerPoint {
  timestamp: string;
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

export default function MachineCard({ machine }: Props) {
  const { state } = useAppState();
  const machineJobs = Object.values(state.burnJobs).filter((job) => job.machine_id === machine.config.id);
  const isBurning = machineJobs.length > 0;
  const activeJobKey = machineJobs.map((job) => job.job_id).sort().join(",");
  const hw = machine.hwInfo;
  const latestPower = hw?.latest_power;
  const chartTheme = useChartTheme();
  const [powerHistory, setPowerHistory] = useState<PowerPoint[]>([]);
  const displayWatts =
    latestPower?.cpu_watts_display ?? latestPower?.cpu_watts ?? latestPower?.cpu_watts_estimated ?? null;

  useEffect(() => {
    setPowerHistory([]);
  }, [machine.config.id, activeJobKey]);

  useEffect(() => {
    const timestamp = latestPower?.timestamp;
    if (!timestamp || displayWatts == null) {
      return;
    }
    setPowerHistory((current) => {
      if (current[current.length - 1]?.timestamp === timestamp) {
        return current;
      }
      const next = [
        ...current,
        {
          timestamp,
          label: formatTimeLabel(timestamp),
          watts: displayWatts
        }
      ];
      return next.slice(-90);
    });
  }, [displayWatts, latestPower?.timestamp]);

  const chartData = useMemo<ChartData<"line", number[], string>>(
    () => ({
      labels: powerHistory.map((point) => point.label),
      datasets: [
        {
          label: latestPower?.cpu_watts_source === "rapl" ? "CPU Power" : "Estimated CPU Power",
          data: powerHistory.map((point) => point.watts),
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
    [chartTheme, latestPower?.cpu_watts_source, powerHistory]
  );

  const chartOptions = useMemo<ChartOptions<"line">>(
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
            maxTicksLimit: 4
          }
        },
        y: {
          beginAtZero: true,
          suggestedMax: Math.max(hw?.cpu_tdp_total_watts ?? machine.config.cpu_tdp ?? 1, 1),
          border: { color: chartTheme.axis },
          grid: { color: chartTheme.grid },
          ticks: {
            color: chartTheme.tick,
            callback: (value) => `${value} W`
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
            label: (context) => `${Number(context.parsed.y ?? 0).toFixed(1)} W`
          }
        }
      }
    }),
    [chartTheme, hw?.cpu_tdp_total_watts, machine.config.cpu_tdp]
  );

  return (
    <article className={`machine-card ${isBurning ? "burning" : ""}`}>
      <div className="machine-header">
        <div>
          <div className="machine-title-row">
            <span className={`status-dot ${machine.connectionStatus}`} />
            <h3>{machine.config.name}</h3>
          </div>
          <div className="machine-subtitle">
            {hw?.ip_address || machine.config.host || "IP unknown"}
          </div>
        </div>
        <span className={`sampling-status-badge ${machine.connectionStatus === "connected" ? "success" : "running"}`}>
          {hw?.worker_status ?? machine.workerStatus ?? machine.connectionStatus}
        </span>
      </div>

      <div className="hardware-block">
        <div className="hardware-row">
          <span className="hardware-label">CPU</span>
          <span>{hw?.cpu_model || "Unknown"}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Sockets</span>
          <span>{formatSockets(hw?.cpu_socket_count, hw?.cpu_tdp_per_socket_watts)}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Node TDP</span>
          <span>{formatNumber(hw?.cpu_tdp_total_watts || hw?.cpu_tdp || machine.config.cpu_tdp)} W</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Threads</span>
          <span>{hw?.cpu_count ?? "-"}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Memory</span>
          <span>{formatNumber(hw?.memory_total_gb)} GB</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">SLURM Node</span>
          <span>{hw?.slurm_node ?? machine.config.id}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Power Est</span>
          <span>{displayWatts == null ? "-" : `${displayWatts.toFixed(1)} W (${latestPower?.cpu_watts_source ?? "unknown"})`}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Util</span>
          <span>{formatPercent(latestPower?.cpu_utilization_percent)}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Freq</span>
          <span>{formatFrequency(latestPower?.cpu_freq_mhz_avg, latestPower?.cpu_freq_mhz_min, latestPower?.cpu_freq_mhz_max)}</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Load</span>
          <span>
            {latestPower?.loadavg_1m == null ? "-" : `${latestPower.loadavg_1m.toFixed(2)} (${formatPercent(latestPower.loadavg_per_cpu_percent)})`}
          </span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Watcher</span>
          <span>{latestPower?.status ?? "waiting"}</span>
        </div>
      </div>

      <div className="power-chart-panel">
        <div className="power-chart-header">
          <span>{latestPower?.cpu_watts_source === "rapl" ? "CPU Power" : "Estimated CPU Power"}</span>
          <span>{powerHistory.length ? `${powerHistory.length} samples` : "waiting"}</span>
        </div>
        <div className="power-chart">
          {powerHistory.length > 1 ? (
            <Line data={chartData} options={chartOptions} />
          ) : (
            <div className="power-chart-empty">Waiting for watcher samples</div>
          )}
        </div>
      </div>

      <div className="machine-options">
        <label className="toggle-row">
          <input type="checkbox" checked readOnly />
          <span>Burn CPU</span>
        </label>
        <label className="toggle-row disabled-row">
          <input type="checkbox" checked={false} disabled readOnly />
          <span>GPU disabled on Shaheen</span>
        </label>
      </div>

      <div className="machine-footer">
        {machine.errorMessage && <span className="error-text">{machine.errorMessage}</span>}
        {isBurning && <span className="burn-pill">{machineJobs.length} active job{machineJobs.length > 1 ? "s" : ""}</span>}
      </div>
    </article>
  );
}

function formatNumber(value: number | undefined): string {
  if (value === undefined || value <= 0) {
    return "-";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function formatSockets(count: number | undefined, watts: number | undefined): string {
  if (!count || !watts) {
    return "-";
  }
  return `${count} x ${formatNumber(watts)} W`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null) {
    return "-";
  }
  return `${value.toFixed(1)}%`;
}

function formatFrequency(avg: number | null | undefined, min: number | null | undefined, max: number | null | undefined): string {
  if (avg == null) {
    return "-";
  }
  if (min == null || max == null || min === max) {
    return `${avg.toFixed(0)} MHz`;
  }
  return `${avg.toFixed(0)} MHz (${min.toFixed(0)}-${max.toFixed(0)})`;
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
