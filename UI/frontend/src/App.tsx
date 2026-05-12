import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import BurnPanel from "./components/BurnPanel";
import GlobalBurnBar from "./components/GlobalBurnBar";
import MachineCard from "./components/MachineCard";
import SchedulePanel from "./components/SchedulePanel";
import {
  extractErrorMessage,
  fetchBurnStatus,
  fetchMachines,
  fetchWaveforms,
  openEventSocket
} from "./api/client";
import { AppStateContext, initialState, reducer } from "./state/AppState";
import type { WsEvent } from "./types";

interface Toast {
  id: number;
  message: string;
  kind: "info" | "error" | "success";
}

type ThemeMode = "light" | "dark";

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [theme, setTheme] = useState<ThemeMode>(() => getInitialTheme());

  const addToast = useCallback((message: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, message, kind }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
    }, 5000);
  }, []);

  const handleWsEvent = useCallback((event: WsEvent) => {
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
          gpus: event.gpus
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
    }
  }, []);

  useEffect(() => {
    async function load() {
      try {
        const [machines, waveforms, jobs] = await Promise.all([
          fetchMachines(),
          fetchWaveforms(),
          fetchBurnStatus()
        ]);
        dispatch({ type: "setMachines", machines });
        dispatch({ type: "setWaveforms", waveforms });
        const preferred = waveforms.find((waveform) => waveform.name === "sine") ?? waveforms[0];
        if (preferred) {
          dispatch({ type: "setGlobalWaveform", points: preferred.points, name: preferred.name });
        }
        jobs.forEach((job) => {
          dispatch({ type: "burnStarted", job });
        });
      } catch (error) {
        addToast(extractErrorMessage(error), "error");
      }
    }
    void load();
  }, [addToast]);

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

  function toggleTheme() {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }

  return (
    <AppStateContext.Provider value={{ state, dispatch }}>
      <div className="app-shell">
        {!state.wsConnected && (
          <div className="connection-banner">Server connection lost. Reconnecting...</div>
        )}
        <header className="app-header">
          <div className="brand-lockup">
            <span className="eyebrow">POWER LAB</span>
            <h1>Burner Control Deck</h1>
            <p>Remote CPU / GPU power load orchestration</p>
          </div>
          <div className="header-actions">
            <button type="button" className="theme-toggle" onClick={toggleTheme}>
              {theme === "dark" ? "Day Mode" : "Night Mode"}
            </button>
            <GlobalBurnBar onToast={addToast} />
          </div>
        </header>

        <section className="status-deck" aria-label="system status">
          <StatusTile label="Machines" value={machines.length} detail={`${selectedCount} enabled`} />
          <StatusTile label="Connected" value={connectedCount} detail={state.wsConnected ? "websocket live" : "reconnecting"} />
          <StatusTile label="GPU Inventory" value={gpuCount} detail="detected devices" />
          <StatusTile label="Active Jobs" value={activeJobs} detail={scheduledJobs > 0 ? `${scheduledJobs} scheduled` : "idle"} />
        </section>

        <SchedulePanel onToast={addToast} />

        <BurnPanel onToast={addToast} />

        <section className="machine-section">
          <div className="section-heading">
            <h2>Machines</h2>
            <span className="muted">{machines.length} configured</span>
          </div>
          {machines.length === 0 ? (
            <div className="empty-state">
              Add machines in <code>UI/machines.json</code>, then refresh this page.
            </div>
          ) : (
            <div className="machine-grid">
              {machines.map((machine) => (
                <MachineCard key={machine.config.id} machine={machine} onToast={addToast} />
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

function getInitialTheme(): ThemeMode {
  const stored = window.localStorage.getItem("burner-theme");
  if (stored === "dark" || stored === "light") {
    return stored;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
