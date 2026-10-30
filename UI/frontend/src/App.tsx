import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import BurnPanel from "./components/BurnPanel";
import ChokerPanel from "./components/ChokerPanel";
import GpuWorkloadPanel from "./components/GpuWorkloadPanel";
import GlobalBurnBar from "./components/GlobalBurnBar";
import MachineCard from "./components/MachineCard";
import SchedulePanel from "./components/SchedulePanel";
import ServerRoomWorkloadPanel from "./components/ServerRoomWorkloadPanel";
import {
  connectMachine,
  disconnectMachine,
  extractErrorMessage,
  fetchBurnStatus,
  fetchGpuWorkloadScenarios,
  fetchGpuWorkloadStatus,
  fetchMachines,
  fetchWorkloadScenarios,
  fetchWorkloadStatus,
  fetchWaveformAssignments,
  fetchWaveforms,
  openEventSocket,
  saveWaveformAssignments
} from "./api/client";
import { AppStateContext, initialState, reducer } from "./state/AppState";
import type { WaveformAssignments, WsEvent } from "./types";

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
  const [tab, setTab] = useState<TabId>(() => getInitialTab());
  // Per-machine waveform choices are saved on the server; undefined until loaded,
  // so the empty initial state never overwrites what was saved.
  const savedAssignments = useRef<string | undefined>(undefined);
  const [connectingAll, setConnectingAll] = useState(false);
  const [disconnectingAll, setDisconnectingAll] = useState(false);

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
    if (event.event === "workload_started") {
      dispatch({ type: "workloadStarted", job: event });
      return;
    }
    if (event.event === "workload_stopped") {
      dispatch({ type: "workloadStopped", jobId: event.job_id, machineId: event.id });
      return;
    }
    if (event.event === "workload_setup_log") {
      dispatch({ type: "appendWorkloadSetupLog", machineId: event.id, line: event.line });
      return;
    }
    if (event.event === "workload_setup_progress") {
      dispatch({
        type: "setWorkloadSetupProgress",
        machineId: event.id,
        status: event.status,
        step: event.step
      });
      return;
    }
    if (event.event === "workload_setup_done") {
      dispatch({
        type: "setWorkloadSetupDone",
        machineId: event.id,
        status: event.status,
        exitCode: event.exit_code,
        message: event.message
      });
      return;
    }
    if (event.event === "workload_setup_complete") {
      dispatch({
        type: "workloadSetupComplete",
        exitCode: event.exit_code,
        message: event.message
      });
      addToast(
        event.exit_code === 0 ? "Workload dependencies are ready." : event.message || "Workload setup failed.",
        event.exit_code === 0 ? "success" : "error"
      );
      return;
    }
    if (event.event === "gpu_workload_started") {
      dispatch({ type: "gpuWorkloadStarted", job: event });
      return;
    }
    if (event.event === "gpu_workload_stopped") {
      dispatch({ type: "gpuWorkloadStopped", jobId: event.job_id, machineId: event.id });
      return;
    }
    if (event.event === "gpu_workload_setup_log") {
      dispatch({ type: "appendGpuWorkloadSetupLog", machineId: event.id, line: event.line });
      return;
    }
    if (event.event === "gpu_workload_setup_progress") {
      dispatch({
        type: "setGpuWorkloadSetupProgress",
        machineId: event.id,
        status: event.status,
        step: event.step
      });
      return;
    }
    if (event.event === "gpu_workload_setup_done") {
      dispatch({
        type: "setGpuWorkloadSetupDone",
        machineId: event.id,
        status: event.status,
        exitCode: event.exit_code,
        message: event.message
      });
      return;
    }
    if (event.event === "gpu_workload_setup_complete") {
      dispatch({
        type: "gpuWorkloadSetupComplete",
        exitCode: event.exit_code,
        message: event.message
      });
      addToast(
        event.exit_code === 0 ? "GPU workload image is ready." : event.message || "GPU workload setup failed.",
        event.exit_code === 0 ? "success" : "error"
      );
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
        const [
          machines,
          waveforms,
          jobs,
          workloadScenarios,
          workloadJobs,
          gpuWorkloadScenarios,
          gpuWorkloadJobs
        ] = await Promise.all([
          fetchMachines(),
          fetchWaveforms(),
          fetchBurnStatus(),
          fetchWorkloadScenarios(),
          fetchWorkloadStatus(),
          fetchGpuWorkloadScenarios(),
          fetchGpuWorkloadStatus()
        ]);
        dispatch({ type: "setMachines", machines });
        dispatch({ type: "setWaveforms", waveforms });
        dispatch({ type: "setWorkloadScenarios", scenarios: workloadScenarios });
        dispatch({ type: "setGpuWorkloadScenarios", scenarios: gpuWorkloadScenarios });
        const preferred = waveforms.find((waveform) => waveform.name === "sine") ?? waveforms[0];
        if (preferred) {
          dispatch({ type: "setGlobalWaveform", points: preferred.points, name: preferred.name });
        }
        jobs.forEach((job) => {
          dispatch({ type: "burnStarted", job });
        });
        workloadJobs.forEach((job) => {
          dispatch({ type: "workloadStarted", job });
        });
        gpuWorkloadJobs.forEach((job) => {
          dispatch({ type: "gpuWorkloadStarted", job });
        });

        const saved = await fetchWaveformAssignments();
        const waveformsByName = new Map(waveforms.map((waveform) => [waveform.name, waveform]));
        const machineIds = new Set(machines.map((machine) => machine.id));
        for (const [machineId, name] of Object.entries(saved.assignments)) {
          const waveform = waveformsByName.get(name);
          if (waveform && machineIds.has(machineId)) {
            dispatch({ type: "setPerMachineWaveform", machineId, points: waveform.points, name });
          }
        }
        dispatch({ type: "setUsePerMachineWaveform", value: saved.use_per_machine });
        savedAssignments.current = serializeAssignments(saved);
      } catch (error) {
        addToast(extractErrorMessage(error), "error");
      }
    }
    void load();
  }, [addToast]);

  useEffect(() => {
    if (savedAssignments.current === undefined) {
      return;
    }
    const current: WaveformAssignments = {
      use_per_machine: state.usePerMachineWaveform,
      assignments: state.perMachineWaveformNames
    };
    const serialized = serializeAssignments(current);
    if (serialized === savedAssignments.current) {
      return;
    }
    const timer = window.setTimeout(() => {
      saveWaveformAssignments(current)
        .then((saved) => {
          savedAssignments.current = serializeAssignments(saved);
        })
        .catch((error) => addToast(`Saving waveform assignments failed: ${extractErrorMessage(error)}`, "error"));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [addToast, state.perMachineWaveformNames, state.usePerMachineWaveform]);

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
  const connectableMachines = useMemo(
    () =>
      machines.filter(
        (machine) => machine.connectionStatus !== "connected" && machine.connectionStatus !== "connecting"
      ),
    [machines]
  );
  const disconnectableMachines = useMemo(
    () => machines.filter((machine) => machine.connectionStatus === "connected"),
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
  const activeWorkloads = Object.values(state.workloadJobs).filter(
    (job) => job.started_at <= Date.now() / 1000 && Date.now() / 1000 < job.started_at + job.duration_seconds
  ).length;
  const activeGpuWorkloads = Object.values(state.gpuWorkloadJobs).filter(
    (job) => job.started_at <= Date.now() / 1000 && Date.now() / 1000 < job.started_at + job.duration_seconds
  ).length;
  const tabBadges: Record<TabId, string | undefined> = {
    burn: activeJobs + scheduledJobs > 0 ? String(activeJobs + scheduledJobs) : undefined,
    machines: `${connectedCount}/${machines.length}`,
    workloads: activeWorkloads > 0 ? String(activeWorkloads) : undefined,
    gpu: activeGpuWorkloads > 0 ? String(activeGpuWorkloads) : undefined,
    choker: undefined
  };

  useEffect(() => {
    try {
      window.localStorage.setItem(TAB_STORAGE_KEY, tab);
    } catch {
      // Remembering the tab is a convenience only.
    }
  }, [tab]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("burner-theme", theme);
    window.dispatchEvent(new CustomEvent("burner-theme-change"));
  }, [theme]);

  useEffect(() => {
    document.documentElement.dataset.runMode = state.runMode;
    window.dispatchEvent(new CustomEvent("burner-theme-change"));
  }, [state.runMode]);

  function toggleTheme() {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }

  async function handleConnectAll() {
    if (connectableMachines.length === 0) {
      addToast("All machines are already connected.", "info");
      return;
    }

    setConnectingAll(true);
    connectableMachines.forEach((machine) => {
      dispatch({
        type: "setMachineStatus",
        machineId: machine.config.id,
        status: "connecting"
      });
    });

    try {
      const results = await Promise.allSettled(
        connectableMachines.map((machine) => connectMachine(machine.config.id))
      );
      const failures = results.filter((result) => result.status === "rejected");
      const refreshedMachines = await fetchMachines();
      dispatch({ type: "setMachines", machines: refreshedMachines });

      if (failures.length > 0) {
        addToast(
          `Connect all finished with ${failures.length} failed and ${results.length - failures.length} started.`,
          "error"
        );
        return;
      }
      addToast(`Connect all started for ${results.length} machines.`, "success");
    } catch (error) {
      addToast(extractErrorMessage(error), "error");
    } finally {
      setConnectingAll(false);
    }
  }

  async function handleDisconnectAll() {
    if (disconnectableMachines.length === 0) {
      addToast("No connected machines to disconnect.", "info");
      return;
    }

    setDisconnectingAll(true);
    try {
      const results = await Promise.allSettled(
        disconnectableMachines.map((machine) => disconnectMachine(machine.config.id))
      );
      const failures = results.filter((result) => result.status === "rejected");
      const refreshedMachines = await fetchMachines();
      dispatch({ type: "setMachines", machines: refreshedMachines });

      if (failures.length > 0) {
        addToast(
          `Disconnect all finished with ${failures.length} failed and ${results.length - failures.length} disconnected.`,
          "error"
        );
        return;
      }
      addToast(`Disconnected ${results.length} machines.`, "success");
    } catch (error) {
      addToast(extractErrorMessage(error), "error");
    } finally {
      setDisconnectingAll(false);
    }
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
            <p>Remote CPU / GPU power load orchestration</p>
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
          <StatusTile label="Machines" value={machines.length} detail={`${selectedCount} enabled`} />
          <StatusTile label="Connected" value={connectedCount} detail={state.wsConnected ? "websocket live" : "reconnecting"} />
          <StatusTile label="GPU Inventory" value={gpuCount} detail="detected devices" />
          <StatusTile
            label="Active Jobs"
            value={activeJobs + activeWorkloads + activeGpuWorkloads}
            detail={scheduledJobs > 0 ? `${scheduledJobs} burn scheduled` : `${activeWorkloads + activeGpuWorkloads} workloads`}
          />
        </section>

        <nav className="tab-bar" role="tablist" aria-label="sections">
          {TABS.map((item) => {
            const badge = tabBadges[item.id];
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`tab-${item.id}`}
                aria-controls={`panel-${item.id}`}
                aria-selected={tab === item.id}
                className={tab === item.id ? "tab-button selected" : "tab-button"}
                onClick={() => setTab(item.id)}
              >
                {item.label}
                {badge && <span className="tab-badge">{badge}</span>}
              </button>
            );
          })}
        </nav>

        {/* Panels stay mounted so their form state and polling survive tab switches. */}
        <div role="tabpanel" id="panel-burn" aria-labelledby="tab-burn" hidden={tab !== "burn"}>
          <SchedulePanel onToast={addToast} />
          <BurnPanel onToast={addToast} />
        </div>

        <div role="tabpanel" id="panel-workloads" aria-labelledby="tab-workloads" hidden={tab !== "workloads"}>
          <ServerRoomWorkloadPanel onToast={addToast} />
        </div>

        <div role="tabpanel" id="panel-gpu" aria-labelledby="tab-gpu" hidden={tab !== "gpu"}>
          <GpuWorkloadPanel onToast={addToast} />
        </div>

        <div role="tabpanel" id="panel-choker" aria-labelledby="tab-choker" hidden={tab !== "choker"}>
          <ChokerPanel onToast={addToast} />
        </div>

        <section
          className="machine-section"
          role="tabpanel"
          id="panel-machines"
          aria-labelledby="tab-machines"
          hidden={tab !== "machines"}
        >
          <div className="section-heading">
            <h2>Machines</h2>
            <div className="machine-section-actions">
              <span className="muted">{machines.length} configured</span>
              <button
                type="button"
                className="primary-button"
                disabled={connectingAll || disconnectingAll || connectableMachines.length === 0}
                onClick={() => void handleConnectAll()}
              >
                {connectingAll ? "Connecting..." : "Connect All"}
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={connectingAll || disconnectingAll || disconnectableMachines.length === 0}
                onClick={() => void handleDisconnectAll()}
              >
                {disconnectingAll ? "Disconnecting..." : "Disconnect All"}
              </button>
            </div>
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

type TabId = "burn" | "machines" | "workloads" | "gpu" | "choker";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "burn", label: "Burn" },
  { id: "machines", label: "Machines" },
  { id: "workloads", label: "Server Room" },
  { id: "gpu", label: "GPU Workload" },
  { id: "choker", label: "Choker" }
];

const TAB_STORAGE_KEY = "burner-tab";

function serializeAssignments(value: WaveformAssignments): string {
  const assignments = Object.entries(value.assignments)
    .filter(([, name]) => name)
    .sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify([value.use_per_machine, assignments]);
}

function getInitialTab(): TabId {
  try {
    const stored = window.localStorage.getItem(TAB_STORAGE_KEY);
    if (TABS.some((item) => item.id === stored)) {
      return stored as TabId;
    }
  } catch {
    // Storage can be unavailable; fall back to the first tab.
  }
  return "burn";
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
