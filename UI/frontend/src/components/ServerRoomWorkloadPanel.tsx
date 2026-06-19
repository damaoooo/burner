import { useEffect, useMemo, useState } from "react";
import {
  extractErrorMessage,
  fetchWorkloadScenario,
  fetchWorkloadScenarios,
  generateWorkloadScenario,
  setupWorkloads,
  startWorkloads,
  stopWorkloads
} from "../api/client";
import { useAppState } from "../state/AppState";
import type { WorkloadGenerateRequest, WorkloadJobInfo, WorkloadScenario, WorkloadSetupState } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

export default function ServerRoomWorkloadPanel({ onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [scenarioName, setScenarioName] = useState("server-room");
  const [seed, setSeed] = useState("20260618");
  const [totalWindow, setTotalWindow] = useState("1800");
  const [minDuration, setMinDuration] = useState("300");
  const [maxDuration, setMaxDuration] = useState("1200");
  const [minWorkers, setMinWorkers] = useState("1");
  const [maxWorkers, setMaxWorkers] = useState("4");
  const [busy, setBusy] = useState<"generate" | "start" | "stop" | null>(null);

  const machines = useMemo(() => Object.values(state.machines), [state.machines]);
  const connectedMachineIds = machines
    .filter((machine) => machine.connectionStatus === "connected")
    .map((machine) => machine.config.id);
  const scenario = state.workloadScenario;
  const workloadJobs = useMemo(() => Object.values(state.workloadJobs), [state.workloadJobs]);
  const setupRunning = state.workloadSetup.running;

  useEffect(() => {
    const existing = state.workloadScenarios.find((item) => item.name === scenarioName);
    if (!existing || state.workloadScenario?.name === scenarioName) {
      return;
    }
    void fetchWorkloadScenario(scenarioName)
      .then((loaded) => dispatch({ type: "setWorkloadScenario", scenario: loaded }))
      .catch(() => undefined);
  }, [dispatch, scenarioName, state.workloadScenario?.name, state.workloadScenarios]);

  async function refreshScenarios() {
    const scenarios = await fetchWorkloadScenarios();
    dispatch({ type: "setWorkloadScenarios", scenarios });
  }

  async function handleSetup() {
    if (connectedMachineIds.length === 0) {
      onToast("Connect at least one machine before workload setup.", "error");
      return;
    }
    dispatch({ type: "startWorkloadSetup", machineIds: connectedMachineIds });
    try {
      await setupWorkloads(connectedMachineIds);
      onToast("Workload setup started.", "info");
    } catch (error) {
      const message = extractErrorMessage(error);
      dispatch({ type: "workloadSetupFailedToStart", message });
      onToast(message, "error");
    }
  }

  async function handleGenerate() {
    if (connectedMachineIds.length === 0) {
      onToast("Connect at least one machine before generating a workload scenario.", "error");
      return;
    }
    const payload = buildGeneratePayload();
    if (!payload) {
      return;
    }
    setBusy("generate");
    try {
      const generated = await generateWorkloadScenario(payload);
      dispatch({ type: "setWorkloadScenario", scenario: generated });
      await refreshScenarios();
      onToast("Workload scenario generated.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStart() {
    if (!scenario) {
      onToast("Generate or load a workload scenario first.", "error");
      return;
    }
    setBusy("start");
    try {
      const jobs = await startWorkloads(scenario.name);
      jobs.forEach((job) => dispatch({ type: "workloadStarted", job }));
      onToast("Workload scenario started.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStop() {
    setBusy("stop");
    try {
      await stopWorkloads("all");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  function buildGeneratePayload(): WorkloadGenerateRequest | undefined {
    const parsed = {
      seed: parseInteger(seed),
      totalWindow: parsePositiveNumber(totalWindow),
      minDuration: parsePositiveNumber(minDuration),
      maxDuration: parsePositiveNumber(maxDuration),
      minWorkers: parseInteger(minWorkers),
      maxWorkers: parseInteger(maxWorkers)
    };
    if (
      parsed.seed === undefined ||
      parsed.totalWindow === undefined ||
      parsed.minDuration === undefined ||
      parsed.maxDuration === undefined ||
      parsed.minWorkers === undefined ||
      parsed.maxWorkers === undefined
    ) {
      onToast("Workload generator values must be positive numbers.", "error");
      return undefined;
    }
    if (parsed.maxDuration < parsed.minDuration || parsed.maxWorkers < parsed.minWorkers) {
      onToast("Workload generator max values must be greater than or equal to min values.", "error");
      return undefined;
    }
    return {
      name: scenarioName.trim() || "server-room",
      machine_ids: connectedMachineIds,
      seed: parsed.seed,
      total_window_seconds: parsed.totalWindow,
      min_duration_seconds: parsed.minDuration,
      max_duration_seconds: parsed.maxDuration,
      min_workers: parsed.minWorkers,
      max_workers: parsed.maxWorkers
    };
  }

  return (
    <section className="workload-panel">
      <div className="section-heading">
        <div>
          <h2>Server Room Workload</h2>
          <span className="muted">real CPU jobs across connected nodes</span>
        </div>
        <div className="workload-actions">
          <button
            type="button"
            className="secondary-button"
            disabled={setupRunning || connectedMachineIds.length === 0}
            onClick={() => void handleSetup()}
          >
            {setupRunning ? "Setting Up" : "Setup Dependencies"}
          </button>
          <button
            type="button"
            className="danger-button"
            disabled={busy === "stop" || workloadJobs.length === 0}
            onClick={() => void handleStop()}
          >
            Stop Workloads
          </button>
        </div>
      </div>

      <div className="workload-grid">
        <div className="workload-config">
          <label className="label" htmlFor="workload-name">
            Scenario
          </label>
          <input id="workload-name" className="field" value={scenarioName} onChange={(event) => setScenarioName(event.target.value)} />

          <div className="workload-fields">
            <NumberField label="Seed" value={seed} onChange={setSeed} />
            <NumberField label="Window (s)" value={totalWindow} onChange={setTotalWindow} />
            <NumberField label="Min Duration" value={minDuration} onChange={setMinDuration} />
            <NumberField label="Max Duration" value={maxDuration} onChange={setMaxDuration} />
            <NumberField label="Min Workers" value={minWorkers} onChange={setMinWorkers} />
            <NumberField label="Max Workers" value={maxWorkers} onChange={setMaxWorkers} />
          </div>

          <div className="workload-command-row">
            <button type="button" className="primary-button" disabled={busy !== null} onClick={() => void handleGenerate()}>
              {busy === "generate" ? "Generating" : "Generate Scenario"}
            </button>
            <button type="button" className="primary-button" disabled={!scenario || busy !== null} onClick={() => void handleStart()}>
              {busy === "start" ? "Starting" : "Start Scenario"}
            </button>
          </div>
        </div>

        <div className="workload-summary">
          <strong>{scenario ? `${scenario.jobs.length} jobs` : "No scenario loaded"}</strong>
          <span>{connectedMachineIds.length} connected machines</span>
          <span>{workloadJobs.length} active or scheduled workload jobs</span>
          {state.workloadScenarios.length > 0 && (
            <select
              className="field"
              value={scenarioName}
              onChange={(event) => setScenarioName(event.target.value)}
              aria-label="saved workload scenarios"
            >
              {state.workloadScenarios.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      {scenario && <ScenarioTable scenario={scenario} machineNames={machineNameMap(machines)} />}
      <SetupStatusTable setup={state.workloadSetup} />
      {workloadJobs.length > 0 && <WorkloadJobTable jobs={workloadJobs} />}
    </section>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = `workload-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <label className="compact-number" htmlFor={id}>
      <span>{label}</span>
      <input id={id} className="field" type="number" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function ScenarioTable({ scenario, machineNames }: { scenario: WorkloadScenario; machineNames: Record<string, string> }) {
  return (
    <div className="workload-table" aria-label="generated workload scenario">
      <div className="workload-row workload-row-head">
        <span>Machine</span>
        <span>Workload</span>
        <span>Delay</span>
        <span>Duration</span>
        <span>Workers</span>
      </div>
      {scenario.jobs.map((job) => (
        <div className="workload-row" key={job.machine_id}>
          <span>{machineNames[job.machine_id] ?? job.machine_id}</span>
          <span>{job.workload}</span>
          <span>{formatSeconds(job.delay_seconds)}</span>
          <span>{formatSeconds(job.duration_seconds)}</span>
          <span>{job.workers}</span>
        </div>
      ))}
    </div>
  );
}

function SetupStatusTable({ setup }: { setup: WorkloadSetupState }) {
  const machines = Object.entries(setup.machines);
  if (machines.length === 0) {
    return null;
  }
  return (
    <div className="workload-table setup-table" aria-label="workload setup status">
      <div className="workload-row workload-row-head">
        <span>Setup Machine</span>
        <span>Status</span>
        <span>Step</span>
        <span>Last Log</span>
      </div>
      {machines.map(([machineId, item]) => (
        <div className="workload-row setup-row" key={machineId}>
          <span>{machineId}</span>
          <span>{item.status}</span>
          <span>{item.step}</span>
          <span>{item.logs[item.logs.length - 1] ?? item.message ?? "-"}</span>
        </div>
      ))}
    </div>
  );
}

function WorkloadJobTable({ jobs }: { jobs: WorkloadJobInfo[] }) {
  const now = Date.now() / 1000;
  return (
    <div className="workload-table" aria-label="active workload jobs">
      <div className="workload-row workload-row-head">
        <span>Machine</span>
        <span>Workload</span>
        <span>Start</span>
        <span>End</span>
        <span>Workers</span>
      </div>
      {jobs.map((job) => (
        <div className="workload-row" key={job.job_id}>
          <span>{job.machine_id}</span>
          <span>{job.workload}</span>
          <span>{job.started_at > now ? `in ${formatSeconds(job.started_at - now)}` : "running"}</span>
          <span>{formatClock(job.started_at + job.duration_seconds)}</span>
          <span>{job.workers}</span>
        </div>
      ))}
    </div>
  );
}

function machineNameMap(machines: Array<{ config: { id: string; name: string } }>): Record<string, string> {
  return Object.fromEntries(machines.map((machine) => [machine.config.id, machine.config.name]));
}

function parseInteger(value: string): number | undefined {
  if (!/^[0-9]+$/.test(value.trim())) {
    return undefined;
  }
  return Number(value);
}

function parsePositiveNumber(value: string): number | undefined {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    return undefined;
  }
  return number;
}

function formatSeconds(value: number): string {
  if (value >= 60) {
    return `${(value / 60).toFixed(1)}m`;
  }
  return `${value.toFixed(1)}s`;
}

function formatClock(value: number): string {
  return new Date(value * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  });
}
