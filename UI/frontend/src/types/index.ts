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
  cpu_count?: number;
  memory_total_gb?: number;
  ip_address?: string;
  slurm_node?: string;
  worker_status?: string;
  last_heartbeat?: string;
  latest_power?: {
    timestamp?: string;
    cpu_watts?: number | null;
    cpu_watts_estimated?: number | null;
    cpu_watts_display?: number | null;
    cpu_watts_source?: "rapl" | "estimated" | "unavailable" | string;
    cpu_utilization_percent?: number | null;
    cpu_freq_mhz_avg?: number | null;
    cpu_freq_mhz_min?: number | null;
    cpu_freq_mhz_max?: number | null;
    cpu_freq_sample_count?: number;
    cpu_tdp_total_watts?: number;
    loadavg_1m?: number | null;
    loadavg_per_cpu_percent?: number | null;
    status?: string;
  } | null;
  cpu_socket_count?: number;
  cpu_tdp_per_socket_watts?: number;
  cpu_tdp_total_watts?: number;
}

export interface MachineApiRecord extends MachineConfig {
  connection_status: ConnectionStatus;
  error_message?: string | null;
  worker_status?: string;
  hw_info?: HwInfo | null;
  job?: JobInfo | null;
}

export interface MachineState {
  config: MachineConfig;
  connectionStatus: ConnectionStatus;
  errorMessage?: string;
  workerStatus?: string;
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

export interface LoadSeriesPoint {
  timestamp: string;
  watts: number;
  cpu_watts?: number | null;
  cpu_watts_estimated?: number | null;
  cpu_utilization_percent?: number | null;
  cpu_freq_mhz_avg?: number | null;
  cpu_freq_mhz_min?: number | null;
  cpu_freq_mhz_max?: number | null;
  loadavg_1m?: number | null;
  nodes_reported?: number;
}

export interface NodeLoadSeries {
  node_id: string;
  sample_count: number;
  points: LoadSeriesPoint[];
}

export interface LoadSeries {
  session_id: string;
  job_id: string;
  generated_at: string;
  node_count?: number;
  nodes: NodeLoadSeries[];
  cluster: {
    sample_count: number;
    points: LoadSeriesPoint[];
  };
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

export interface SlurmAllocation {
  active: boolean;
  status: string;
  session_id?: string;
  job_id?: string;
  session_dir?: string;
  nodes_requested?: number;
  nodes_ready?: number;
  poll_ms?: number;
  sample_ms?: number;
  time_limit?: string;
  created_at?: number;
  nodes?: MachineApiRecord[];
}

export type WsEvent =
  | ({
      event: "allocation_changed";
    } & SlurmAllocation)
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
