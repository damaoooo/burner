import TelemetryLineChart from "./TelemetryLineChart";
import type { LoadSeries, MachineState } from "../types";

interface Props {
  machines: MachineState[];
  loadSeries: LoadSeries | null;
  loading: boolean;
  burnActive: boolean;
}

export default function ClusterPowerChart({ machines, loadSeries, loading, burnActive }: Props) {
  const points = loadSeries?.cluster.points ?? [];
  const latestWatts = points.at(-1)?.watts;
  const yMax = Math.max(maxPointWatts(points), totalClusterTdp(machines), 1);

  return (
    <section className="cluster-power-panel">
      <div className="section-heading">
        <div>
          <h2>Cluster Estimated Power</h2>
          <span className="muted">{chartSubtitle(loadSeries, loading, burnActive, machines.length)}</span>
        </div>
        <strong>{latestWatts == null ? "-" : formatWatts(latestWatts)}</strong>
      </div>
      <div className="cluster-power-chart">
        <TelemetryLineChart
          points={points.map((point) => ({ label: formatTimeLabel(point.timestamp), value: point.watts }))}
          yMax={yMax}
          yAxisLabel="Cluster Estimated CPU Power (W)"
          valueFormatter={formatWatts}
          emptyText={emptyText(loading, burnActive)}
        />
      </div>
    </section>
  );
}

function chartSubtitle(loadSeries: LoadSeries | null, loading: boolean, burnActive: boolean, nodeCount: number): string {
  if (loading) {
    return "loading completed CSV samples";
  }
  if (burnActive) {
    return "burn running; chart renders from CSV after completion";
  }
  if (loadSeries) {
    return `${loadSeries.nodes.length} nodes, ${loadSeries.cluster.sample_count} CSV samples`;
  }
  return `${nodeCount} allocated nodes`;
}

function emptyText(loading: boolean, burnActive: boolean): string {
  if (loading) {
    return "Loading completed CSV samples";
  }
  if (burnActive) {
    return "Chart will render from CSV after burn completion";
  }
  return "Waiting for completed CSV samples";
}

function totalClusterTdp(machines: MachineState[]): number {
  return machines.reduce((total, machine) => total + (machine.hwInfo?.cpu_tdp_total_watts ?? machine.config.cpu_tdp ?? 0), 0);
}

function maxPointWatts(points: Array<{ watts: number }>): number {
  return points.reduce((max, point) => Math.max(max, point.watts), 0);
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
