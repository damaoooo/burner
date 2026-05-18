import { useEffect, useState } from "react";
import TelemetryLineChart from "./TelemetryLineChart";
import { useAppState } from "../state/AppState";
import type { MachineState } from "../types";

interface Props {
  machine: MachineState;
  showPowerChart: boolean;
}

interface PowerPoint {
  timestamp: string;
  label: string;
  watts: number;
}

export default function MachineCard({ machine, showPowerChart }: Props) {
  const { state } = useAppState();
  const machineJobs = Object.values(state.burnJobs).filter((job) => job.machine_id === machine.config.id);
  const isBurning = machineJobs.length > 0;
  const activeJobKey = machineJobs.map((job) => job.job_id).sort().join(",");
  const hw = machine.hwInfo;
  const latestPower = hw?.latest_power;
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
      return next.slice(-60);
    });
  }, [displayWatts, latestPower?.timestamp]);

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

      {showPowerChart && (
        <div className="power-chart-panel">
          <div className="power-chart-header">
            <span>{latestPower?.cpu_watts_source === "rapl" ? "CPU Power" : "Estimated CPU Power"}</span>
            <span>{powerHistory.length ? `${powerHistory.length} samples` : "waiting"}</span>
          </div>
          <div className="power-chart">
            <TelemetryLineChart
              points={powerHistory.map((point) => ({ label: point.label, value: point.watts }))}
              yMax={Math.max(hw?.cpu_tdp_total_watts ?? machine.config.cpu_tdp ?? 1, 1)}
              yAxisLabel={latestPower?.cpu_watts_source === "rapl" ? "CPU Power (W)" : "Estimated CPU Power (W)"}
            />
          </div>
        </div>
      )}

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
