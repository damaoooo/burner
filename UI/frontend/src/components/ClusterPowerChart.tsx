import { useEffect, useMemo, useState } from "react";
import TelemetryLineChart from "./TelemetryLineChart";
import type { MachineState } from "../types";

interface Props {
  machines: MachineState[];
}

interface PowerPoint {
  key: string;
  label: string;
  watts: number;
}

export default function ClusterPowerChart({ machines }: Props) {
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
        <TelemetryLineChart
          points={history.map((point) => ({ label: point.label, value: point.watts }))}
          yMax={Math.max(latest?.watts ?? 1, totalClusterTdp(machines), 1)}
          yAxisLabel="Cluster Estimated CPU Power (W)"
          valueFormatter={formatWatts}
          emptyText="Waiting for node telemetry"
        />
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
