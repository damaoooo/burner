import { createContext, useContext, type Dispatch } from "react";
import type {
  AppState,
  ConnectionStatus,
  HwInfo,
  JobInfo,
  MachineApiRecord,
  Point,
  RunMode,
  SamplingBuildStatus,
  WaveformInfo
} from "../types";

export type Action =
  | { type: "setMachines"; machines: MachineApiRecord[] }
  | { type: "setWaveforms"; waveforms: WaveformInfo[] }
  | { type: "setGlobalWaveform"; points: Point[]; name?: string }
  | { type: "setPerMachineWaveform"; machineId: string; points: Point[]; name?: string }
  | { type: "setUsePerMachineWaveform"; value: boolean }
  | { type: "setMachineStatus"; machineId: string; status: ConnectionStatus; message?: string }
  | { type: "setHwInfo"; machineId: string; hwInfo: HwInfo }
  | { type: "setMachineOption"; machineId: string; key: "burnEnabled" | "burnCpu" | "burnGpu"; value: boolean }
  | { type: "setMachineDelay"; machineId: string; value: number }
  | { type: "setRunMode"; value: RunMode; scheduledStartLocal?: string }
  | { type: "setBurnParams"; duration?: string; period?: string; scheduledStartLocal?: string; samplingMs?: string }
  | { type: "startSamplingBuild"; machineIds: string[]; samplingMs: number }
  | { type: "appendSamplingBuildLog"; machineId: string; line: string }
  | {
      type: "setSamplingBuildProgress";
      machineId: string;
      samplingMs: number;
      status: SamplingBuildStatus;
      step: string;
      progress: number;
    }
  | {
      type: "setSamplingBuildDone";
      machineId: string;
      samplingMs: number;
      exitCode: number;
      status: SamplingBuildStatus;
      message?: string;
    }
  | { type: "samplingBuildComplete"; samplingMs: number; exitCode: number; message?: string }
  | { type: "samplingBuildFailedToStart"; message: string }
  | { type: "setBurnJobs"; jobs: JobInfo[] }
  | { type: "burnStarted"; job: JobInfo }
  | { type: "burnStopped"; jobId?: string; machineId?: string }
  | { type: "clearUpdateLog"; machineId: string }
  | { type: "appendUpdateLog"; machineId: string; line: string }
  | { type: "setUpdateDone"; machineId: string; exitCode: number }
  | { type: "setUpdateRunning"; machineId: string }
  | { type: "setWsConnected"; value: boolean };

export const initialState: AppState = {
  machines: {},
  waveforms: [],
  globalWaveform: [],
  globalWaveformName: "",
  perMachineWaveforms: {},
  perMachineWaveformNames: {},
  usePerMachineWaveform: false,
  duration: "16",
  period: "1",
  runMode: "realtime",
  scheduledStartLocal: "",
  samplingMs: "100",
  appliedSamplingMs: 100,
  samplingBuild: {
    running: false,
    targetMachineIds: [],
    samplingMs: 100,
    machines: {}
  },
  burnJobs: {},
  updateLogs: {},
  updateStatus: {},
  wsConnected: false
};

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "setMachines": {
      const nextMachines: AppState["machines"] = {};
      const activeMachineIds = new Set(action.machines.map((record) => record.id));
      const nextJobs: AppState["burnJobs"] = Object.fromEntries(
        Object.entries(state.burnJobs).filter(([, job]) => activeMachineIds.has(job.machine_id))
      );
      for (const record of action.machines) {
        const previous = state.machines[record.id];
        nextMachines[record.id] = {
          config: {
            id: record.id,
            name: record.name,
            host: record.host,
            port: record.port,
            username: record.username,
            identity_file: record.identity_file,
            workdir: record.workdir,
            cpu_tdp: record.cpu_tdp,
            gpu_tdp: record.gpu_tdp,
            conda_env: record.conda_env
          },
          connectionStatus: record.connection_status,
          errorMessage: record.error_message ?? undefined,
          workerStatus: record.worker_status,
          hwInfo: record.hw_info ?? previous?.hwInfo,
          burnEnabled: previous?.burnEnabled ?? true,
          burnCpu: previous?.burnCpu ?? true,
          burnGpu: false,
          delaySeconds: previous?.delaySeconds ?? 0
        };
        if (record.job) {
          nextJobs[record.job.job_id] = record.job;
        }
      }
      return { ...state, machines: nextMachines, burnJobs: nextJobs };
    }
    case "setWaveforms":
      return { ...state, waveforms: action.waveforms };
    case "setGlobalWaveform":
      return {
        ...state,
        globalWaveform: normalizePoints(action.points),
        globalWaveformName: action.name ?? ""
      };
    case "setPerMachineWaveform":
      return {
        ...state,
        perMachineWaveforms: {
          ...state.perMachineWaveforms,
          [action.machineId]: normalizePoints(action.points)
        },
        perMachineWaveformNames: {
          ...state.perMachineWaveformNames,
          [action.machineId]: action.name ?? ""
        }
      };
    case "setUsePerMachineWaveform":
      return { ...state, usePerMachineWaveform: action.value };
    case "setMachineStatus": {
      const machine = state.machines[action.machineId];
      if (!machine) {
        return state;
      }
      return {
        ...state,
        machines: {
          ...state.machines,
          [action.machineId]: {
            ...machine,
            connectionStatus: action.status,
            errorMessage: action.message
          }
        }
      };
    }
    case "setHwInfo": {
      const machine = state.machines[action.machineId];
      if (!machine) {
        return state;
      }
      return {
        ...state,
        machines: {
          ...state.machines,
          [action.machineId]: { ...machine, hwInfo: action.hwInfo }
        }
      };
    }
    case "setMachineOption": {
      const machine = state.machines[action.machineId];
      if (!machine) {
        return state;
      }
      return {
        ...state,
        machines: {
          ...state.machines,
          [action.machineId]: { ...machine, [action.key]: action.value }
        }
      };
    }
    case "setMachineDelay": {
      const machine = state.machines[action.machineId];
      if (!machine) {
        return state;
      }
      return {
        ...state,
        machines: {
          ...state.machines,
          [action.machineId]: { ...machine, delaySeconds: action.value }
        }
      };
    }
    case "setRunMode":
      return {
        ...state,
        runMode: action.value,
        scheduledStartLocal: action.scheduledStartLocal ?? state.scheduledStartLocal
      };
    case "setBurnParams":
      return {
        ...state,
        duration: action.duration ?? state.duration,
        period: action.period ?? state.period,
        scheduledStartLocal: action.scheduledStartLocal ?? state.scheduledStartLocal,
        samplingMs: action.samplingMs ?? state.samplingMs
      };
    case "startSamplingBuild":
      return {
        ...state,
        samplingBuild: {
          running: true,
          targetMachineIds: action.machineIds,
          samplingMs: action.samplingMs,
          exitCode: undefined,
          message: undefined,
          machines: Object.fromEntries(
            action.machineIds.map((machineId) => [
              machineId,
              {
                status: "queued",
                step: "queued",
                progress: 0,
                logs: []
              }
            ])
          )
        }
      };
    case "appendSamplingBuildLog": {
      const current = state.samplingBuild.machines[action.machineId] ?? {
        status: "running" as const,
        step: "running",
        progress: 0,
        logs: []
      };
      return {
        ...state,
        samplingBuild: {
          ...state.samplingBuild,
          machines: {
            ...state.samplingBuild.machines,
            [action.machineId]: {
              ...current,
              logs: [...current.logs, action.line]
            }
          }
        }
      };
    }
    case "setSamplingBuildProgress": {
      const current = state.samplingBuild.machines[action.machineId] ?? {
        status: action.status,
        step: action.step,
        progress: action.progress,
        logs: []
      };
      return {
        ...state,
        samplingBuild: {
          ...state.samplingBuild,
          running: true,
          samplingMs: action.samplingMs,
          machines: {
            ...state.samplingBuild.machines,
            [action.machineId]: {
              ...current,
              status: action.status,
              step: action.step,
              progress: action.progress
            }
          }
        }
      };
    }
    case "setSamplingBuildDone": {
      const current = state.samplingBuild.machines[action.machineId] ?? {
        status: action.status,
        step: action.status,
        progress: 1,
        logs: []
      };
      return {
        ...state,
        samplingBuild: {
          ...state.samplingBuild,
          samplingMs: action.samplingMs,
          machines: {
            ...state.samplingBuild.machines,
            [action.machineId]: {
              ...current,
              status: action.status,
              step: action.status,
              progress: 1,
              exitCode: action.exitCode,
              message: action.message
            }
          }
        }
      };
    }
    case "samplingBuildComplete":
      return {
        ...state,
        appliedSamplingMs: action.exitCode === 0 ? action.samplingMs : state.appliedSamplingMs,
        samplingBuild: {
          ...state.samplingBuild,
          running: false,
          exitCode: action.exitCode,
          message: action.message
        }
      };
    case "samplingBuildFailedToStart":
      return {
        ...state,
        samplingBuild: {
          running: false,
          targetMachineIds: [],
          samplingMs: state.appliedSamplingMs,
          exitCode: 1,
          message: action.message,
          machines: {
            all: {
              status: "failed",
              step: "failed",
              progress: 1,
              logs: [action.message],
              exitCode: 1,
              message: action.message
            }
          }
        }
      };
    case "setBurnJobs":
      return {
        ...state,
        burnJobs: Object.fromEntries(action.jobs.map((job) => [job.job_id, job]))
      };
    case "burnStarted":
      return {
        ...state,
        burnJobs: {
          ...state.burnJobs,
          [action.job.job_id]: action.job
        }
      };
    case "burnStopped": {
      const nextJobs = { ...state.burnJobs };
      if (action.jobId) {
        delete nextJobs[action.jobId];
      } else if (action.machineId) {
        for (const [jobId, job] of Object.entries(nextJobs)) {
          if (job.machine_id === action.machineId) {
            delete nextJobs[jobId];
          }
        }
      }
      return { ...state, burnJobs: nextJobs };
    }
    case "clearUpdateLog":
      return {
        ...state,
        updateLogs: { ...state.updateLogs, [action.machineId]: [] },
        updateStatus: { ...state.updateStatus, [action.machineId]: "idle" }
      };
    case "appendUpdateLog":
      return {
        ...state,
        updateLogs: {
          ...state.updateLogs,
          [action.machineId]: [...(state.updateLogs[action.machineId] ?? []), action.line]
        }
      };
    case "setUpdateRunning":
      return {
        ...state,
        updateStatus: { ...state.updateStatus, [action.machineId]: "running" }
      };
    case "setUpdateDone":
      return {
        ...state,
        updateStatus: {
          ...state.updateStatus,
          [action.machineId]: action.exitCode === 0 ? "success" : "failed"
        }
      };
    case "setWsConnected":
      return { ...state, wsConnected: action.value };
    default:
      return state;
  }
}

export function normalizePoints(points: Point[]): Point[] {
  const sorted = [...points]
    .map((point) => ({
      x: clamp(point.x, 0, 1),
      y: clamp(point.y, 0, 1)
    }))
    .sort((left, right) => left.x - right.x);

  if (sorted.length === 0) {
    return [
      { x: 0, y: 0.5 },
      { x: 1, y: 0.5 }
    ];
  }

  const deduped: Point[] = [];
  for (const point of sorted) {
    const previous = deduped[deduped.length - 1];
    if (previous && Math.abs(previous.x - point.x) < 0.000001) {
      previous.y = point.y;
    } else {
      deduped.push(point);
    }
  }

  if (deduped[0].x > 0) {
    deduped.unshift({ x: 0, y: deduped[0].y });
  } else {
    deduped[0] = { ...deduped[0], x: 0 };
  }

  const last = deduped[deduped.length - 1];
  if (last.x < 1) {
    deduped.push({ x: 1, y: last.y });
  } else {
    deduped[deduped.length - 1] = { ...last, x: 1 };
  }

  if (deduped.length === 1) {
    deduped.push({ x: 1, y: deduped[0].y });
  }
  return deduped;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export const AppStateContext = createContext<{
  state: AppState;
  dispatch: Dispatch<Action>;
} | null>(null);

export function useAppState() {
  const context = useContext(AppStateContext);
  if (!context) {
    throw new Error("useAppState must be used within AppStateContext.Provider");
  }
  return context;
}
