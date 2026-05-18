import { useAppState } from "../state/AppState";
import type { MachineState } from "../types";

interface Props {
  machine: MachineState;
}

export default function MachineCard({ machine }: Props) {
  const { state } = useAppState();
  const machineJobs = Object.values(state.burnJobs).filter((job) => job.machine_id === machine.config.id);
  const isBurning = machineJobs.length > 0;
  const hw = machine.hwInfo;
  const latestPower = hw?.latest_power;

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
          <span className="hardware-label">CPU TDP</span>
          <span>{formatNumber(hw?.cpu_tdp || machine.config.cpu_tdp)} W</span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">CPU Count</span>
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
          <span className="hardware-label">Power</span>
          <span>
            {latestPower?.cpu_watts == null ? "-" : `${latestPower.cpu_watts.toFixed(2)} W`}
          </span>
        </div>
        <div className="hardware-row">
          <span className="hardware-label">Watcher</span>
          <span>{latestPower?.status ?? "waiting"}</span>
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
