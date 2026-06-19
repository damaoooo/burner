export type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";
export type SyncMode = "immediate" | "delayed" | "scheduled";
export type RunMode = "realtime" | "schedule";
export type SamplingBuildStatus = "idle" | "queued" | "running" | "success" | "failed";
export type WorkloadSetupStatus = "queued" | "running" | "success" | "failed";
export type WorkloadType = "crypto" | "compress" | "compile" | "python-cpu";
export type GpuWorkloadType =
  | "gemm"
  | "memory-bandwidth"
  | "cv-train"
  | "cv-infer"
  | "llm-infer"
  | "embedding-infer"
  | "diffusion-infer"
  | "video-transcode"
  | "video-analytics"
  | "faiss-search";

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

export interface WorkloadScenarioSummary {
  name: string;
  seed: number;
  total_window_seconds: number;
  jobs: number;
}

export interface WorkloadScenarioJob {
  machine_id: string;
  workload: WorkloadType;
  delay_seconds: number;
  duration_seconds: number;
  workers: number;
}

export interface WorkloadScenario {
  name: string;
  seed: number;
  total_window_seconds: number;
  jobs: WorkloadScenarioJob[];
}

export interface WorkloadJobInfo {
  job_id: string;
  scenario_name: string;
  machine_id: string;
  pid: number;
  started_at: number;
  duration_seconds: number;
  elapsed_seconds?: number;
  delay_seconds: number;
  workload: WorkloadType;
  workers: number;
  log_path: string;
}

export interface WorkloadSetupMachineState {
  status: WorkloadSetupStatus;
  step: string;
  logs: string[];
  exitCode?: number;
  message?: string;
}

export interface WorkloadSetupState {
  running: boolean;
  targetMachineIds: string[];
  machines: Record<string, WorkloadSetupMachineState>;
  exitCode?: number;
  message?: string;
}

export interface GpuWorkloadScenarioSummary {
  name: string;
  tasks: number;
  total_duration_seconds: number;
}

export interface GpuWorkloadTask {
  workload: GpuWorkloadType;
  duration_seconds: number;
  model?: string;
  batch_size: number;
  input_shape: number[];
  precision: "fp16" | "fp32" | "bf16";
  params: Record<string, unknown>;
}

export interface GpuWorkloadScenario {
  name: string;
  total_duration_seconds: number;
  tasks: GpuWorkloadTask[];
}

export interface GpuWorkloadJobInfo {
  job_id: string;
  machine_id: string;
  scenario_name: string;
  pid: number;
  container_name: string;
  image: string;
  gpu_index: number;
  started_at: number;
  duration_seconds: number;
  elapsed_seconds?: number;
  log_path: string;
}

export interface GpuWorkloadSetupState {
  running: boolean;
  targetMachineId?: string;
  machines: Record<string, WorkloadSetupMachineState>;
  exitCode?: number;
  message?: string;
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
  workloadScenarios: WorkloadScenarioSummary[];
  workloadScenario?: WorkloadScenario;
  workloadJobs: Record<string, WorkloadJobInfo>;
  workloadSetup: WorkloadSetupState;
  gpuWorkloadScenarios: GpuWorkloadScenarioSummary[];
  gpuWorkloadScenario?: GpuWorkloadScenario;
  gpuWorkloadJobs: Record<string, GpuWorkloadJobInfo>;
  gpuWorkloadSetup: GpuWorkloadSetupState;
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
  | ({ event: "workload_started" } & WorkloadJobInfo)
  | {
      event: "workload_stopped";
      job_id: string;
      id: string;
      exit_code: number;
    }
  | {
      event: "workload_setup_log";
      id: string;
      line: string;
    }
  | {
      event: "workload_setup_progress";
      id: string;
      step: string;
      status: WorkloadSetupStatus;
    }
  | {
      event: "workload_setup_done";
      id: string;
      status: WorkloadSetupStatus;
      exit_code: number;
      message?: string;
    }
  | {
      event: "workload_setup_complete";
      exit_code: number;
      message?: string;
    }
  | ({ event: "gpu_workload_started" } & GpuWorkloadJobInfo)
  | {
      event: "gpu_workload_stopped";
      job_id: string;
      id: string;
      exit_code: number;
    }
  | {
      event: "gpu_workload_setup_log";
      id: string;
      line: string;
    }
  | {
      event: "gpu_workload_setup_progress";
      id: string;
      step: string;
      status: WorkloadSetupStatus;
    }
  | {
      event: "gpu_workload_setup_done";
      id: string;
      status: WorkloadSetupStatus;
      exit_code: number;
      message?: string;
    }
  | {
      event: "gpu_workload_setup_complete";
      exit_code: number;
      message?: string;
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

export interface WorkloadGenerateRequest {
  name: string;
  machine_ids?: string[];
  seed: number;
  total_window_seconds: number;
  min_duration_seconds: number;
  max_duration_seconds: number;
  min_workers: number;
  max_workers: number;
}

export interface GpuWorkloadSetupRequest {
  machine_id: string;
  gpu_index: number;
  image: string;
  no_cache: boolean;
}

export interface GpuWorkloadStartRequest {
  machine_id: string;
  scenario_name: string;
  gpu_index: number;
  image: string;
}
