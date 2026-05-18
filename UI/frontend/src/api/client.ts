import axios from "axios";
import type {
  BurnStartRequest,
  JobInfo,
  MachineApiRecord,
  Point,
  SlurmAllocation,
  WaveformInfo,
  WsEvent
} from "../types";

const http = axios.create({
  baseURL: "/api",
  timeout: 20000
});

function toPointList(points: Array<[number, number]>): Point[] {
  return points.map(([x, y]) => ({ x, y }));
}

function toWaveform(raw: { name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }): WaveformInfo {
  return {
    name: raw.name,
    source: raw.source,
    points: toPointList(raw.points)
  };
}

export async function fetchMachines(): Promise<MachineApiRecord[]> {
  const { data } = await http.get<MachineApiRecord[]>("/machines");
  return data;
}

export async function connectMachine(id: string): Promise<void> {
  await http.post(`/machines/${encodeURIComponent(id)}/connect`);
}

export async function disconnectMachine(id: string): Promise<void> {
  await http.post(`/machines/${encodeURIComponent(id)}/disconnect`);
}

export async function refreshHwInfo(id: string): Promise<void> {
  await http.get(`/machines/${encodeURIComponent(id)}/hwinfo`);
}

export async function fetchWaveforms(): Promise<WaveformInfo[]> {
  const { data } = await http.get<Array<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>>(
    "/waveforms"
  );
  return data.map(toWaveform);
}

export async function fetchWaveform(name: string): Promise<WaveformInfo> {
  const { data } = await http.get<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>(
    `/waveforms/${encodeURIComponent(name)}`
  );
  return toWaveform(data);
}

export async function saveWaveform(name: string, points: Point[]): Promise<WaveformInfo> {
  const { data } = await http.post<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>(
    "/waveforms",
    {
      name,
      points: points.map((point) => [point.x, point.y])
    }
  );
  return toWaveform(data);
}

export async function startBurn(payload: BurnStartRequest): Promise<JobInfo[]> {
  const { data } = await http.post<JobInfo[]>("/burn/start", payload);
  return data;
}

export async function stopBurn(machineIds: string[] | "all"): Promise<void> {
  await http.post("/burn/stop", { machine_ids: machineIds });
}

export async function stopJobs(jobIds: string[] | "all"): Promise<void> {
  await http.post("/burn/stop", { job_ids: jobIds });
}

export async function fetchBurnStatus(): Promise<JobInfo[]> {
  const { data } = await http.get<JobInfo[]>("/burn/status");
  return data;
}

export async function fetchAllocation(): Promise<SlurmAllocation> {
  const { data } = await http.get<SlurmAllocation>("/slurm/allocation");
  return data;
}

export async function submitAllocation(nodes: number, timeLimit: string, pollMs: number, sampleMs: number): Promise<SlurmAllocation> {
  const { data } = await http.post<SlurmAllocation>("/slurm/submit", {
    nodes,
    time_limit: timeLimit,
    poll_ms: pollMs,
    sample_ms: sampleMs
  });
  return data;
}

export async function releaseAllocation(): Promise<SlurmAllocation> {
  const { data } = await http.post<SlurmAllocation>("/slurm/release");
  return data;
}

export async function runUpdate(id: string): Promise<void> {
  await http.post(`/update/${encodeURIComponent(id)}`);
}

export async function applySamplingTime(samplingMs: number, machineIds: string[]): Promise<void> {
  await http.post("/sampling/apply", {
    sampling_ms: samplingMs,
    machine_ids: machineIds
  });
}

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (error.message) {
      return error.message;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export function openEventSocket(
  onMessage: (event: WsEvent) => void,
  onConnectionChange: (connected: boolean) => void
): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let closed = false;

  const connect = () => {
    if (closed) {
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    socket.onopen = () => onConnectionChange(true);
    socket.onmessage = (message) => onMessage(JSON.parse(message.data) as WsEvent);
    socket.onclose = () => {
      onConnectionChange(false);
      if (!closed) {
        reconnectTimer = window.setTimeout(connect, 1500);
      }
    };
    socket.onerror = () => {
      socket?.close();
    };
  };

  connect();

  return () => {
    closed = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
    }
    socket?.close();
  };
}
