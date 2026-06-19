import { useEffect, useMemo, useState } from "react";
import {
  extractErrorMessage,
  fetchGpuWorkloadScenario,
  setupGpuWorkload,
  startGpuWorkload,
  stopGpuWorkloads
} from "../api/client";
import { useAppState } from "../state/AppState";
import type { GpuWorkloadJobInfo, GpuWorkloadScenario, GpuWorkloadSetupState, MachineState } from "../types";

interface Props {
  onToast: (message: string, kind?: "info" | "error" | "success") => void;
}

const DEFAULT_IMAGE = "burner-gpu-workloads:latest";

export default function GpuWorkloadPanel({ onToast }: Props) {
  const { state, dispatch } = useAppState();
  const [scenarioName, setScenarioName] = useState("single-gpu-default");
  const [machineId, setMachineId] = useState("");
  const [gpuIndex, setGpuIndex] = useState("0");
  const [image, setImage] = useState(DEFAULT_IMAGE);
  const [noCache, setNoCache] = useState(false);
  const [busy, setBusy] = useState<"setup" | "start" | "stop" | null>(null);

  const machines = useMemo(() => Object.values(state.machines), [state.machines]);
  const gpuMachines = machines.filter((machine) => hasGpu(machine));
  const scenario = state.gpuWorkloadScenario;
  const jobs = useMemo(() => Object.values(state.gpuWorkloadJobs), [state.gpuWorkloadJobs]);

  useEffect(() => {
    if (!machineId && gpuMachines.length > 0) {
      setMachineId(gpuMachines[0].config.id);
    }
  }, [gpuMachines, machineId]);

  useEffect(() => {
    const existing = state.gpuWorkloadScenarios.find((item) => item.name === scenarioName);
    if (!existing || state.gpuWorkloadScenario?.name === scenarioName) {
      return;
    }
    void fetchGpuWorkloadScenario(scenarioName)
      .then((loaded) => dispatch({ type: "setGpuWorkloadScenario", scenario: loaded }))
      .catch(() => undefined);
  }, [dispatch, scenarioName, state.gpuWorkloadScenario?.name, state.gpuWorkloadScenarios]);

  async function handleSetup() {
    const selected = selectedMachine();
    const parsedGpu = parseGpuIndex();
    if (!selected || parsedGpu === undefined) {
      return;
    }
    dispatch({ type: "startGpuWorkloadSetup", machineId: selected.config.id });
    setBusy("setup");
    try {
      await setupGpuWorkload({
        machine_id: selected.config.id,
        gpu_index: parsedGpu,
        image,
        no_cache: noCache
      });
      onToast("GPU workload image setup started.", "info");
    } catch (error) {
      const message = extractErrorMessage(error);
      dispatch({ type: "gpuWorkloadSetupFailedToStart", message });
      onToast(message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStart() {
    const selected = selectedMachine();
    const parsedGpu = parseGpuIndex();
    if (!selected || parsedGpu === undefined) {
      return;
    }
    setBusy("start");
    try {
      const job = await startGpuWorkload({
        machine_id: selected.config.id,
        scenario_name: scenarioName,
        gpu_index: parsedGpu,
        image
      });
      dispatch({ type: "gpuWorkloadStarted", job });
      onToast("GPU workload scenario started.", "success");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleStop() {
    setBusy("stop");
    try {
      await stopGpuWorkloads("all");
    } catch (error) {
      onToast(extractErrorMessage(error), "error");
    } finally {
      setBusy(null);
    }
  }

  function selectedMachine(): MachineState | undefined {
    const selected = machines.find((machine) => machine.config.id === machineId);
    if (!selected) {
      onToast("Select a connected GPU machine first.", "error");
      return undefined;
    }
    if (selected.connectionStatus !== "connected") {
      onToast("Selected GPU machine is not connected.", "error");
      return undefined;
    }
    return selected;
  }

  function parseGpuIndex(): number | undefined {
    if (!/^[0-9]+$/.test(gpuIndex.trim())) {
      onToast("GPU index must be a non-negative integer.", "error");
      return undefined;
    }
    return Number(gpuIndex);
  }

  return (
    <section className="workload-panel gpu-workload-panel">
      <div className="section-heading">
        <div>
          <h2>GPU Workload</h2>
          <span className="muted">Dockerized real workloads on one GPU</span>
        </div>
        <div className="workload-actions">
          <button type="button" className="secondary-button" disabled={!machineId || state.gpuWorkloadSetup.running || busy !== null} onClick={() => void handleSetup()}>
            {state.gpuWorkloadSetup.running ? "Building" : "Setup Image"}
          </button>
          <button type="button" className="primary-button" disabled={!machineId || busy !== null} onClick={() => void handleStart()}>
            {busy === "start" ? "Starting" : "Start GPU Scenario"}
          </button>
          <button type="button" className="danger-button" disabled={jobs.length === 0 || busy !== null} onClick={() => void handleStop()}>
            Stop GPU Jobs
          </button>
        </div>
      </div>

      <div className="workload-grid">
        <div className="workload-config">
          <label className="label" htmlFor="gpu-machine-select">
            Machine
          </label>
          <select id="gpu-machine-select" className="field" value={machineId} onChange={(event) => setMachineId(event.target.value)}>
            <option value="">Select GPU machine</option>
            {gpuMachines.map((machine) => (
              <option key={machine.config.id} value={machine.config.id}>
                {machine.config.name}
              </option>
            ))}
          </select>

          <div className="workload-fields">
            <label className="compact-number" htmlFor="gpu-scenario-select">
              <span>Scenario</span>
              <select id="gpu-scenario-select" className="field" value={scenarioName} onChange={(event) => setScenarioName(event.target.value)}>
                {state.gpuWorkloadScenarios.length === 0 ? (
                  <option value="single-gpu-default">single-gpu-default</option>
                ) : (
                  state.gpuWorkloadScenarios.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))
                )}
              </select>
            </label>
            <label className="compact-number" htmlFor="gpu-index-input">
              <span>GPU Index</span>
              <input id="gpu-index-input" className="field" type="number" min={0} step={1} value={gpuIndex} onChange={(event) => setGpuIndex(event.target.value)} />
            </label>
            <label className="compact-number" htmlFor="gpu-image-input">
              <span>Image</span>
              <input id="gpu-image-input" className="field" value={image} onChange={(event) => setImage(event.target.value)} />
            </label>
          </div>

          <label className="toggle-row">
            <input type="checkbox" checked={noCache} onChange={(event) => setNoCache(event.target.checked)} />
            <span>Build image without Docker cache</span>
          </label>
        </div>

        <div className="workload-summary">
          <strong>{scenario ? `${scenario.tasks.length} tasks` : "No GPU scenario loaded"}</strong>
          <span>{gpuMachines.length} connected GPU-capable machines</span>
          <span>{jobs.length} active GPU workload jobs</span>
          <span>{scenario ? formatSeconds(scenario.total_duration_seconds) : "-"}</span>
        </div>
      </div>

      {scenario && <GpuScenarioTable scenario={scenario} />}
      <GpuSetupStatusTable setup={state.gpuWorkloadSetup} />
      {jobs.length > 0 && <GpuJobTable jobs={jobs} />}
    </section>
  );
}

function GpuScenarioTable({ scenario }: { scenario: GpuWorkloadScenario }) {
  return (
    <div className="workload-table gpu-task-table" aria-label="GPU workload scenario tasks">
      <div className="workload-row workload-row-head gpu-task-row">
        <span>Workload</span>
        <span>Duration</span>
        <span>Model</span>
        <span>Batch</span>
        <span>Precision</span>
      </div>
      {scenario.tasks.map((task, index) => (
        <div className="workload-row gpu-task-row" key={`${task.workload}-${index}`}>
          <span>{task.workload}</span>
          <span>{formatSeconds(task.duration_seconds)}</span>
          <span>{task.model ?? "-"}</span>
          <span>{task.batch_size}</span>
          <span>{task.precision}</span>
        </div>
      ))}
    </div>
  );
}

function GpuSetupStatusTable({ setup }: { setup: GpuWorkloadSetupState }) {
  const rows = Object.entries(setup.machines);
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="workload-table setup-table" aria-label="GPU workload setup status">
      <div className="workload-row workload-row-head">
        <span>Setup Machine</span>
        <span>Status</span>
        <span>Step</span>
        <span>Last Log</span>
      </div>
      {rows.map(([machineId, item]) => (
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

function GpuJobTable({ jobs }: { jobs: GpuWorkloadJobInfo[] }) {
  const now = Date.now() / 1000;
  return (
    <div className="workload-table" aria-label="GPU workload jobs">
      <div className="workload-row workload-row-head">
        <span>Machine</span>
        <span>Scenario</span>
        <span>GPU</span>
        <span>End</span>
        <span>Container</span>
      </div>
      {jobs.map((job) => (
        <div className="workload-row" key={job.job_id}>
          <span>{job.machine_id}</span>
          <span>{job.scenario_name}</span>
          <span>{job.gpu_index}</span>
          <span>{job.started_at + job.duration_seconds > now ? formatClock(job.started_at + job.duration_seconds) : "finishing"}</span>
          <span>{job.container_name}</span>
        </div>
      ))}
    </div>
  );
}

function hasGpu(machine: MachineState): boolean {
  return machine.connectionStatus === "connected" && ((machine.hwInfo?.gpus.length ?? 0) > 0 || machine.config.gpu_tdp > 0);
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
