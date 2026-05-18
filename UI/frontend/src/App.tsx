import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import AllocationPanel from "./components/AllocationPanel";
import BurnPanel from "./components/BurnPanel";
import GlobalBurnBar from "./components/GlobalBurnBar";
import MachineCard from "./components/MachineCard";
import SchedulePanel from "./components/SchedulePanel";
import {
  extractErrorMessage,
  fetchAllocation,
  fetchBurnStatus,
  fetchMachines,
  fetchWaveforms,
  openEventSocket
} from "./api/client";
import { AppStateContext, initialState, reducer } from "./state/AppState";
import type { SlurmAllocation, WsEvent } from "./types";

interface Toast {
  id: number;
  message: string;
  kind: "info" | "error" | "success";
}

type ThemeMode = "light" | "dark";

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [allocation, setAllocation] = useState<SlurmAllocation>({ active: false, status: "none", nodes: [] });
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setTheme] = useState<ThemeMode>(() => getInitialTheme());
  const [refreshMs, setRefreshMs] = useState(() => getInitialRefreshMs());

  const addToast = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, kind }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  const handleWsEvent = useCallback((event: WsEvent) => {
    if (event.event === "allocation_changed") {
      setAllocation(event);
      if (!event.active) {
        dispatch({ type: "setMachines", machines: [] });
        dispatch({ type: "setBurnJobs", jobs: [] });
      }
      return;
    }
    if (event.event === "machine_status") {
      dispatch({
        type: "setMachineStatus",
        machineId: event.id,
        status: event.status,
        message: event.message
      });
      return;
    }
    if (event.event === "hw_info") {
      dispatch({
        type: "setHwInfo",
        machineId: event.id,
        hwInfo: {
          cpu_model: event.cpu_model,
          cpu_tdp: event.cpu_tdp,
          gpu_tdp: event.gpu_tdp,
          gpus: event.gpus,
          cpu_count: event.cpu_count,
          cpu_socket_count: event.cpu_socket_count,
          cpu_tdp_per_socket_watts: event.cpu_tdp_per_socket_watts,
          cpu_tdp_total_watts: event.cpu_tdp_total_watts,
          memory_total_gb: event.memory_total_gb,
          ip_address: event.ip_address,
          slurm_node: event.slurm_node,
          worker_status: event.worker_status,
          last_heartbeat: event.last_heartbeat,
          latest_power: event.latest_power
        }
      });
      return;
    }
    if (event.event === "burn_started") {
      dispatch({
        type: "burnStarted",
        job: {
          job_id: event.job_id ?? event.id,
          machine_id: event.id,
          pid: event.pid,
          started_at: event.started_at ?? Date.now() / 1000,
          duration_seconds: event.duration_seconds,
          burn_cpu: event.burn_cpu,
          burn_gpu: event.burn_gpu,
          delay_seconds: event.delay_seconds,
          waveform_name: event.waveform_name,
          sync_mode: event.sync_mode
        }
      });
      return;
    }
    if (event.event === "burn_stopped") {
      dispatch({ type: "burnStopped", jobId: event.job_id, machineId: event.id });
      return;
    }
    if (event.event === "update_log") {
      dispatch({ type: "appendUpdateLog", machineId: event.id, line: event.line });
      return;
    }
    if (event.event === "update_done") {
      dispatch({ type: "setUpdateDone", machineId: event.id, exitCode: event.exit_code });
      return;
    }
    if (event.event === "sampling_build_log") {
      dispatch({ type: "appendSamplingBuildLog", machineId: event.id, line: event.line });
      return;
    }
    if (event.event === "sampling_build_progress") {
      dispatch({
        type: "setSamplingBuildProgress",
        machineId: event.id,
        samplingMs: event.sampling_ms,
        status: event.status,
        step: event.step,
        progress: event.progress
      });
      return;
    }
    if (event.event === "sampling_build_done") {
      dispatch({
        type: "setSamplingBuildDone",
        machineId: event.id,
        samplingMs: event.sampling_ms,
        exitCode: event.exit_code,
        status: event.status,
        message: event.message
      });
      return;
    }
    if (event.event === "sampling_build_complete") {
      dispatch({
        type: "samplingBuildComplete",
        samplingMs: event.sampling_ms,
        exitCode: event.exit_code,
        message: event.message
      });
      addToast(
        event.exit_code === 0 ? "Sampling time applied and rebuilt." : event.message || "Sampling rebuild failed.",
        event.exit_code === 0 ? "success" : "error"
      );
    }
  }, [addToast]);

  useEffect(() => {
    async function load() {
      try {
        const [machines, waveforms, jobs] = await Promise.all([
          fetchMachines(),
          fetchWaveforms(),
          fetchBurnStatus()
        ]);
        const currentAllocation = await fetchAllocation();
        setAllocation(currentAllocation);
        dispatch({ type: "setMachines", machines });
        dispatch({ type: "setWaveforms", waveforms });
        const preferred = waveforms.find((waveform) => waveform.name === "sine") ?? waveforms[0];
        if (preferred) {
          dispatch({ type: "setGlobalWaveform", points: preferred.points, name: preferred.name });
        }
        dispatch({ type: "setBurnJobs", jobs });
      } catch (error) {
        addToast(extractErrorMessage(error), "error");
      }
    }
    void load();
  }, [addToast]);

  useEffect(() => {
    let inFlight = false;
    const refresh = () => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      void Promise.all([fetchMachines(), fetchBurnStatus(), fetchAllocation()])
        .then(([machines, jobs, currentAllocation]) => {
          dispatch({ type: "setMachines", machines });
          dispatch({ type: "setBurnJobs", jobs });
          setAllocation(currentAllocation);
        })
        .catch(() => undefined)
        .finally(() => {
          inFlight = false;
        });
    };
    const timer = window.setInterval(() => {
      refresh();
    }, refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs]);

  useEffect(() => {
    return openEventSocket(
      handleWsEvent,
      (connected) => dispatch({ type: "setWsConnected", value: connected })
    );
  }, [handleWsEvent]);

  const machines = useMemo(() => Object.values(state.machines), [state.machines]);
  const connectedCount = useMemo(
    () => machines.filter((machine) => machine.connectionStatus === "connected").length,
    [machines]
  );
  const selectedCount = useMemo(
    () => machines.filter((machine) => machine.burnEnabled).length,
    [machines]
  );
  const gpuCount = useMemo(
    () => machines.reduce((total, machine) => total + (machine.hwInfo?.gpus.length ?? 0), 0),
    [machines]
  );
  const activeJobs = Object.values(state.burnJobs).filter(
    (job) => job.started_at <= Date.now() / 1000 && Date.now() / 1000 < job.started_at + job.duration_seconds
  ).length;
  const scheduledJobs = Object.values(state.burnJobs).filter((job) => job.started_at > Date.now() / 1000).length;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("burner-theme", theme);
    window.dispatchEvent(new CustomEvent("burner-theme-change"));
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem("burner-ui-refresh-ms-v2", String(refreshMs));
  }, [refreshMs]);

  useEffect(() => {
    document.documentElement.dataset.runMode = state.runMode;
    window.dispatchEvent(new CustomEvent("burner-theme-change"));
  }, [state.runMode]);

  function toggleTheme() {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }

  return (
    <AppStateContext.Provider value={{ state, dispatch }}>
      <div className={`app-shell run-mode-${state.runMode}`}>
        {!state.wsConnected && (
          <div className="connection-banner">Server connection lost. Reconnecting...</div>
        )}
        <header className="app-header">
          <div className="brand-lockup">
            <span className="eyebrow">POWER LAB</span>
            <h1>Burner Control Deck</h1>
            <p>Shaheen SLURM CPU load orchestration</p>
          </div>
          <div className="header-actions">
            <div className="header-controls">
              <button type="button" className="theme-toggle" onClick={toggleTheme}>
                {theme === "dark" ? "Day Mode" : "Night Mode"}
              </button>
              <RunModeSwitch
                value={state.runMode}
                disabled={state.samplingBuild.running}
                onChange={(value) =>
                  dispatch({
                    type: "setRunMode",
                    value,
                    scheduledStartLocal:
                      value === "schedule" && !state.scheduledStartLocal
                        ? formatLocalDateTime(new Date(Date.now() + 5 * 60 * 1000))
                        : undefined
                  })
                }
              />
            </div>
            <GlobalBurnBar onToast={addToast} />
          </div>
        </header>

        <section className="status-deck" aria-label="system status">
          <StatusTile label="Allocation" value={allocation.nodes_requested ?? 0} detail={allocation.job_id ? `job ${allocation.job_id}` : "none"} />
          <StatusTile label="Ready Nodes" value={connectedCount} detail={`${allocation.nodes_ready ?? 0}/${allocation.nodes_requested ?? 0} workers`} />
          <StatusTile label="GPU Inventory" value={gpuCount} detail="Shaheen CPU-only" />
          <StatusTile label="Active Jobs" value={activeJobs} detail={scheduledJobs > 0 ? `${scheduledJobs} scheduled` : "idle"} />
        </section>

        <AllocationPanel
          allocation={allocation}
          refreshMs={refreshMs}
          onRefreshMsChange={setRefreshMs}
          onAllocationChange={setAllocation}
          onToast={addToast}
        />

        <SchedulePanel onToast={addToast} />

        <BurnPanel onToast={addToast} />

        <section className="machine-section">
          <div className="section-heading">
            <h2>Machines</h2>
            <span className="muted">{machines.length} allocated</span>
          </div>
          {machines.length === 0 ? (
            <div className="empty-state">
              Submit a SLURM allocation to start workers and populate node information.
            </div>
          ) : (
            <div className="machine-grid">
              {machines.map((machine) => (
                <MachineCard key={machine.config.id} machine={machine} />
              ))}
            </div>
          )}
        </section>

        <div className="toast-stack" aria-live="polite">
          {toasts.map((toast) => (
            <div className={`toast ${toast.kind}`} key={toast.id}>
              {toast.message}
            </div>
          ))}
        </div>
      </div>
    </AppStateContext.Provider>
  );
}

function StatusTile({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div className="status-tile">
      <span className="status-label">{label}</span>
      <strong>{value}</strong>
      <span className="status-detail">{detail}</span>
    </div>
  );
}

function RunModeSwitch({
  value,
  disabled,
  onChange
}: {
  value: "realtime" | "schedule";
  disabled: boolean;
  onChange: (value: "realtime" | "schedule") => void;
}) {
  return (
    <div className="top-mode-switch" aria-label="run mode">
      <button
        type="button"
        className={value === "realtime" ? "selected realtime" : "realtime"}
        disabled={disabled}
        onClick={() => onChange("realtime")}
      >
        Realtime
      </button>
      <button
        type="button"
        className={value === "schedule" ? "selected schedule" : "schedule"}
        disabled={disabled}
        onClick={() => onChange("schedule")}
      >
        Schedule
      </button>
    </div>
  );
}

function formatLocalDateTime(date: Date): string {
  const pad = (item: number) => String(item).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
    ":",
    pad(date.getSeconds())
  ].join("");
}

function getInitialTheme(): ThemeMode {
  const stored = window.localStorage.getItem("burner-theme");
  if (stored === "dark" || stored === "light") {
    return stored;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function getInitialRefreshMs(): number {
  const raw = window.localStorage.getItem("burner-ui-refresh-ms-v2");
  const parsed = raw ? Number(raw) : 50;
  return Number.isInteger(parsed) && parsed >= 30 && parsed <= 10000 ? parsed : 50;
}
