export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";
export type SyncMode = "immediate" | "delayed" | "scheduled";
export type RunMode = "realtime" | "schedule";
export type SamplingBuildStatus = "idle" | "queued" | "running" | "success" | "failed";

export interface Point {
  x: number;
  y: number;
}

export interface MachineConfig {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  identity_file: string;
  workdir: string;
  cpu_tdp: number;
  gpu_tdp: number;
  conda_env: string;
}

export interface GpuInfo {
  index: number;
  name: string;
  tdp_watts: number;
}

export interface HwInfo {
  cpu_model: string;
  cpu_tdp: number;
  gpu_tdp: number;
  gpus: GpuInfo[];
}

export interface MachineApiRecord extends MachineConfig {
  connection_status: ConnectionStatus;
  error_message?: string | null;
  hw_info?: HwInfo | null;
  job?: JobInfo | null;
}

export interface MachineState {
  config: MachineConfig;
  connectionStatus: ConnectionStatus;
  errorMessage?: string;
  hwInfo?: HwInfo;
  burnEnabled: boolean;
  burnCpu: boolean;
  burnGpu: boolean;
  delaySeconds: number;
}

export interface WaveformInfo {
  name: string;
  source: "fixtures" | "custom";
  points: Point[];
}

export interface JobInfo {
  job_id: string;
  machine_id: string;
  pid: number;
  started_at: number;
  duration_seconds: number;
  elapsed_seconds?: number;
  burn_cpu?: boolean;
  burn_gpu?: boolean;
  delay_seconds?: number;
  waveform_name?: string;
  sync_mode?: SyncMode;
}

export interface AppState {
  machines: Record<string, MachineState>;
  waveforms: WaveformInfo[];
  globalWaveform: Point[];
  globalWaveformName: string;
  perMachineWaveforms: Record<string, Point[]>;
  perMachineWaveformNames: Record<string, string>;
  usePerMachineWaveform: boolean;
  duration: string;
  period: string;
  runMode: RunMode;
  scheduledStartLocal: string;
  samplingMs: string;
  appliedSamplingMs: number;
  samplingBuild: SamplingBuildState;
  burnJobs: Record<string, JobInfo>;
  updateLogs: Record<string, string[]>;
  updateStatus: Record<string, "idle" | "running" | "success" | "failed">;
  wsConnected: boolean;
}

export interface SamplingBuildMachineState {
  status: SamplingBuildStatus;
  step: string;
  progress: number;
  logs: string[];
  exitCode?: number;
  message?: string;
}

export interface SamplingBuildState {
  running: boolean;
  targetMachineIds: string[];
  samplingMs: number;
  machines: Record<string, SamplingBuildMachineState>;
  exitCode?: number;
  message?: string;
}

export type WsEvent =
  | {
      event: "machine_status";
      id: string;
      status: ConnectionStatus;
      message?: string;
    }
  | ({ event: "hw_info"; id: string } & HwInfo)
  | {
      event: "burn_started";
      job_id?: string;
      id: string;
      pid: number;
      started_at?: number;
      duration_seconds: number;
      burn_cpu?: boolean;
      burn_gpu?: boolean;
      delay_seconds?: number;
      waveform_name?: string;
      sync_mode?: SyncMode;
    }
  | {
      event: "burn_stopped";
      job_id?: string;
      id: string;
      exit_code: number;
    }
  | {
      event: "update_log";
      id: string;
      line: string;
    }
  | {
      event: "update_done";
      id: string;
      exit_code: number;
      message?: string;
    }
  | {
      event: "sampling_build_log";
      id: string;
      line: string;
    }
  | {
      event: "sampling_build_progress";
      id: string;
      sampling_ms: number;
      step: string;
      status: SamplingBuildStatus;
      completed: number;
      total: number;
      progress: number;
    }
  | {
      event: "sampling_build_done";
      id: string;
      sampling_ms: number;
      exit_code: number;
      status: SamplingBuildStatus;
      message?: string;
    }
  | {
      event: "sampling_build_complete";
      sampling_ms: number;
      exit_code: number;
      message?: string;
    };

export interface BurnStartRequest {
  sync_mode: SyncMode;
  start_time_utc?: string;
  duration: string;
  period: string;
  tick_seconds: number;
  machines: Array<{
    id: string;
    enabled: boolean;
    burn_cpu: boolean;
    burn_gpu: boolean;
    delay_seconds: number;
    waveform_name: string;
  }>;
}
