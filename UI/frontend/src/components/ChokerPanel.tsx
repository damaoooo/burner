import { useMemo, useState } from "react";
import { extractErrorMessage, fetchChokerStatus, startChoker, stopChoker } from "../api/client";
import { useAppState } from "../state/AppState";
import type { ChokerMachineStatus, ChokerStrategy } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function ChokerPanel({ onToast }: Props) {
  const { state } = useAppState();
  const [strategy, setStrategy] = useState<ChokerStrategy>("complement");
  const [target, setTarget] = useState("100");
  const [threshold, setThreshold] = useState("10");
  const [windowMs, setWindowMs] = useState("1000");
  const [busy, setBusy] = useState<"start" | "stop" | "status" | null>(null);
  const [statuses, setStatuses] = useState<ChokerMachineStatus[]>([]);

  const connectedMachineIds = useMemo(
    () =>
      Object.values(state.machines)
        .filter((machine) => machine.connectionStatus === "connected")
        .map((machine) => machine.config.id),
    [state.machines]
  );
  const summary = summarize(statuses);

  async function handleRefresh() {
    setBusy("status");
    try {
      setStatuses(await fetchChokerStatus());
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStart() {
    const parsed = parseConfig();
    if (!parsed) {
      return;
    }
    setBusy("start");
    try {
      const result = await startChoker({
        machine_ids: connectedMachineIds,
        strategy,
        target: parsed.target,
        threshold: parsed.threshold,
        window_ms: parsed.windowMs
      });
      setStatuses(result);
      toastResult(result, "Choker started.", "Choker start failed on");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStop() {
    setBusy("stop");
    try {
      const result = await stopChoker(connectedMachineIds);
      setStatuses(result);
      toastResult(result, "Choker stopped.", "Choker stop failed on");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  function parseConfig(): { target: number; threshold: number; windowMs: number } | undefined {
    const parsedTarget = Number(target);
    const parsedThreshold = Number(threshold);
    const parsedWindow = Number(windowMs);
    if (!Number.isFinite(parsedTarget) || parsedTarget < 0 || parsedTarget > 100) {
      onToast("Choker target must be between 0 and 100.", "error");
      return undefined;
    }
    if (!Number.isFinite(parsedThreshold) || parsedThreshold < 0 || parsedThreshold > 100) {
      onToast("Choker threshold must be between 0 and 100.", "error");
      return undefined;
    }
    if (!Number.isInteger(parsedWindow) || parsedWindow <= 0) {
      onToast("Choker window must be a positive integer in ms.", "error");
      return undefined;
    }
    if (connectedMachineIds.length === 0) {
      onToast("Connect at least one machine before starting choker.", "error");
      return undefined;
    }
    return { target: parsedTarget, threshold: parsedThreshold, windowMs: parsedWindow };
  }

  function toastResult(result: ChokerMachineStatus[], successMessage: string, failurePrefix: string) {
    const failures = result.filter((item) => item.state === "error" || (item.exit_code ?? 0) !== 0);
    if (failures.length > 0) {
      onToast(`${failurePrefix} ${failures.length} machine${failures.length === 1 ? "" : "s"}.`, "error");
      return;
    }
    onToast(successMessage, "success");
  }

  return (
    <section className="workload-panel choker-panel">
      <div className="section-heading">
        <div>
          <h2>Choker</h2>
          <span className="muted">fills idle CPU capacity while burn is running</span>
        </div>
        <div className="workload-actions">
          <button
            type="button"
            className="primary-button"
            disabled={busy !== null || connectedMachineIds.length === 0}
            onClick={() => void handleStart()}
          >
            {busy === "start" ? "Starting" : "Start Choker All"}
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={busy !== null || connectedMachineIds.length === 0}
            onClick={() => void handleStop()}
          >
            {busy === "stop" ? "Stopping" : "Stop Choker All"}
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={busy !== null || connectedMachineIds.length === 0}
            onClick={() => void handleRefresh()}
          >
            {busy === "status" ? "Refreshing" : "Status"}
          </button>
        </div>
      </div>

      <div className="workload-grid">
        <div className="workload-config">
          <div className="workload-fields">
            <label className="compact-number" htmlFor="choker-strategy">
              <span>Strategy</span>
              <select
                id="choker-strategy"
                className="field"
                value={strategy}
                onChange={(event) => setStrategy(event.target.value as ChokerStrategy)}
              >
                <option value="complement">complement</option>
                <option value="idle">idle</option>
              </select>
            </label>
            <NumberField label="Target (%)" value={target} onChange={setTarget} />
            <NumberField label="Threshold (%)" value={threshold} onChange={setThreshold} />
            <NumberField label="Window (ms)" value={windowMs} onChange={setWindowMs} />
          </div>
        </div>

        <div className="workload-summary">
          <strong>{connectedMachineIds.length} connected machines</strong>
          <span>{summary.running} running</span>
          <span>{summary.stopped} stopped</span>
          <span>{summary.problem} stale/error</span>
        </div>
      </div>

      {statuses.length > 0 && <ChokerStatusTable statuses={statuses} />}
    </section>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = `choker-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <label className="compact-number" htmlFor={id}>
      <span>{label}</span>
      <input id={id} className="field" type="number" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function ChokerStatusTable({ statuses }: { statuses: ChokerMachineStatus[] }) {
  return (
    <div className="workload-table choker-status-table" aria-label="choker status">
      <div className="workload-row workload-row-head choker-status-row">
        <span>Machine</span>
        <span>Status</span>
        <span>PID</span>
        <span>Message</span>
      </div>
      {statuses.map((item) => (
        <div className="workload-row choker-status-row" key={item.id}>
          <span>{item.name || item.id}</span>
          <span className={`choker-state ${item.state}`}>{item.state}</span>
          <span>{item.pid ?? "-"}</span>
          <span>{item.message || item.stderr || item.stdout || "-"}</span>
        </div>
      ))}
    </div>
  );
}

function summarize(statuses: ChokerMachineStatus[]) {
  return {
    running: statuses.filter((item) => item.state === "running").length,
    stopped: statuses.filter((item) => item.state === "stopped").length,
    problem: statuses.filter((item) => item.state === "stale" || item.state === "invalid" || item.state === "error").length
  };
}
